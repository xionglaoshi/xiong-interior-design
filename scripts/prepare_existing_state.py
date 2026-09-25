#!/usr/bin/env python3
"""Prepare a CAD screenshot for review using only reversible pixel operations.

Default behavior is dry-run. This script never detects or classifies walls,
doors, structure, or demolition status. Cropping and tone conversion must be
explicitly selected by the operator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps


def parse_crop(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("裁剪格式应为 left,top,right,bottom 整数") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("裁剪格式应为 left,top,right,bottom")
    left, top, right, bottom = parts
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise argparse.ArgumentTypeError("裁剪范围必须为非负且宽高大于零")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description="裁切/增强 CAD 截图，不识别或重绘工程要素。")
    parser.add_argument("--input", required=True, type=Path, help="源 PNG/JPEG")
    parser.add_argument("--output", required=True, type=Path, help="新的 PNG 输出路径")
    parser.add_argument("--report", type=Path, help="可选的 JSON 校验记录路径；实际执行时与 PNG 一并新建")
    parser.add_argument("--crop", type=parse_crop, help="可选像素裁切框：left,top,right,bottom")
    parser.add_argument(
        "--tone", choices=("preserve", "cad-dark"), default="preserve",
        help="preserve 保留像素色彩；cad-dark 转灰度并反相，适用于黑底CAD截图",
    )
    parser.add_argument("--execute", action="store_true", help="实际写出文件；省略时只预演")
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    target = args.output.expanduser().resolve()
    report_path = args.report.expanduser().resolve() if args.report else None
    if not source.is_file():
        parser.error(f"找不到输入图片：{source}")
    if source == target:
        parser.error("输出不能覆盖源图")
    if target.exists():
        parser.error(f"输出已存在，拒绝覆盖：{target}")
    if target.suffix.lower() != ".png":
        parser.error("输出扩展名必须为 .png")
    if report_path:
        if report_path in {source, target}:
            parser.error("校验记录路径必须与源图和PNG输出不同")
        if report_path.suffix.lower() != ".json":
            parser.error("校验记录扩展名必须为 .json")
        if report_path.exists():
            parser.error(f"校验记录已存在，拒绝覆盖：{report_path}")

    with Image.open(source) as opened:
        original_size = opened.size
        orientation = opened.getexif().get(274, 1)
        if type(orientation) is not int or orientation not in range(1, 9):
            parser.error(f"源图 EXIF 方向值无效：{orientation!r}")
        display_image = ImageOps.exif_transpose(opened)
        display_size = display_image.size
        bounds = args.crop or (0, 0, *display_size)
        left, top, right, bottom = bounds
        if right > display_size[0] or bottom > display_size[1]:
            parser.error(f"裁剪框超出按 EXIF 方向显示的原图 {display_size[0]}x{display_size[1]}")
        output_size = (right - left, bottom - top)

        result = display_image.copy().crop(bounds)
        if args.tone == "cad-dark":
            if result.mode == "RGBA":
                background = Image.new("RGBA", result.size, (0, 0, 0, 255))
                background.alpha_composite(result)
                result = background.convert("RGB")
            else:
                result = result.convert("RGB")
            result = ImageOps.invert(ImageOps.grayscale(result))

        report = {
            "input": str(source),
            "output": str(target),
            "source_size_px": list(original_size),
            "source_display_size_px": list(display_size),
            "exif_orientation": orientation,
            "crop_coordinate_frame": "top-left pixels after EXIF orientation normalization",
            "crop_px": list(bounds),
            "output_size_px": list(output_size),
            "tone": args.tone,
            "execute": args.execute,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "geometry_pixels_rescaled": False,
            "warning": "本工具不识别墙体/结构/门窗，也不判断可拆改；须目视核对输出。",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.execute:
            return 0

        target.parent.mkdir(parents=True, exist_ok=True)
        result.save(target, format="PNG", optimize=True)
        with Image.open(target) as written:
            if written.size != output_size:
                target.unlink(missing_ok=True)
                raise RuntimeError("输出尺寸回读不符；已移除本次生成文件")
        report["output_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(f"截图校验记录：{report_path}")
        print(f"写出并回读验证：{target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # concise CLI failure for automation
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
