#!/usr/bin/env python3
"""Create a non-destructive visual QA sheet for an AI-cleaned CAD screenshot."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from atomic_output_bundle import commit_output_bundle, preflight_output_bundle


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def make_review_sheet(reference: Image.Image, cleaned: Image.Image) -> Image.Image:
    reference = reference.convert("RGB")
    cleaned = cleaned.convert("RGB")
    width, height = reference.size
    gap, header = 16, 44
    sheet = Image.new("RGB", (width * 3 + gap * 4, height + header + gap * 2), "#e7e7e7")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    panels = (
        ("REFERENCE", reference),
        ("AI CLEANED", cleaned),
        ("50% OVERLAY", Image.blend(reference, cleaned, 0.5)),
    )
    for index, (label, panel) in enumerate(panels):
        left = gap + index * (width + gap)
        draw.text((left, gap), label, fill="#111111", font=font)
        sheet.paste(panel, (left, gap + header))
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser(
        description="并排与半透明叠加核对CAD截图清洗结果；不自动判断或修正几何。"
    )
    parser.add_argument("--reference", required=True, type=Path, help="清洗前的确定性裁切/色调参考PNG")
    parser.add_argument("--cleaned", required=True, type=Path, help="模型清洗后的候选PNG")
    parser.add_argument("--output", required=True, type=Path, help="新的三栏核验图PNG")
    parser.add_argument("--report", required=True, type=Path, help="新的核验记录JSON，与PNG放在同一目录")
    parser.add_argument("--normalize-uniform-scale", action="store_true",
                        help="仅当长宽比相同且分辨率不同时，等比例规范到参考画布；须明确启用")
    parser.add_argument("--normalized-cleaned-output", type=Path,
                        help="等比例规范后的新候选PNG路径；使用--normalize-uniform-scale且画布不同才需要")
    parser.add_argument("--execute", action="store_true", help="实际写出；省略时仅预演")
    args = parser.parse_args()

    reference_path = args.reference.expanduser().resolve()
    cleaned_path = args.cleaned.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    report_path = args.report.expanduser().resolve()
    normalized_path = args.normalized_cleaned_output.expanduser().resolve() if args.normalized_cleaned_output else None
    if reference_path == cleaned_path:
        parser.error("参考图与清洗图必须是两个不同文件")
    for source in (reference_path, cleaned_path):
        if not source.is_file():
            parser.error(f"找不到输入图片：{source}")
    if output_path in (reference_path, cleaned_path) or report_path in (reference_path, cleaned_path):
        parser.error("核验输出不得覆盖输入图片")
    if output_path == report_path or output_path.suffix.lower() != ".png" or report_path.suffix.lower() != ".json":
        parser.error("输出必须是不同路径的 .png 和 .json 文件")
    if output_path.parent != report_path.parent:
        parser.error("核验图与记录须放在同一目录，以保证成套提交")
    output_paths = [output_path, report_path]
    if normalized_path is not None:
        if normalized_path.suffix.lower() != ".png" or normalized_path.parent != output_path.parent:
            parser.error("规范化候选PNG须与核验图、记录放在同一目录")
        output_paths.append(normalized_path)
    if len(set(output_paths)) != len(output_paths) or any(path in {reference_path, cleaned_path} for path in output_paths):
        parser.error("各输出路径必须互不相同，且不得覆盖输入图")

    with Image.open(reference_path) as opened_reference, Image.open(cleaned_path) as opened_cleaned:
        reference = opened_reference.copy()
        raw_cleaned = opened_cleaned.copy()
    normalization = {"applied": False, "source_size_px": list(raw_cleaned.size),
                     "target_size_px": list(reference.size), "scale_xy": [1.0, 1.0]}
    normalized_bytes = None
    if reference.size == raw_cleaned.size:
        if args.normalize_uniform_scale or normalized_path is not None:
            parser.error("输入画布已一致，不需要规范化参数或输出路径")
        cleaned = raw_cleaned
        cleaned_path_for_report = cleaned_path
        cleaned_hash = sha256(cleaned_path)
    else:
        if not args.normalize_uniform_scale or normalized_path is None:
            parser.error("画布不一致；只可显式请求同长宽比的等比例规范化，禁止自动缩放或配准")
        ratio_reference = reference.width / reference.height
        ratio_cleaned = raw_cleaned.width / raw_cleaned.height
        ratio_delta = abs(ratio_reference - ratio_cleaned) / ratio_reference
        if ratio_delta > 0.001:
            parser.error(f"长宽比差异 {ratio_delta:.4%}，拒绝裁切、补边或非等比拉伸")
        cleaned = raw_cleaned.convert("RGB").resize(reference.size, Image.Resampling.LANCZOS)
        normalized_bytes = png_bytes(cleaned)
        cleaned_hash = hashlib.sha256(normalized_bytes).hexdigest()
        cleaned_path_for_report = normalized_path
        normalization.update({"applied": True, "scale_xy": [reference.width / raw_cleaned.width,
                                                               reference.height / raw_cleaned.height],
                              "resampling": "Lanczos", "aspect_ratio_relative_delta": ratio_delta,
                              "normalized_output": str(normalized_path)})

    names = [path.name for path in output_paths]
    try:
        preflight_output_bundle(output_path.parent, names)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    sheet = make_review_sheet(reference, cleaned)
    sheet_bytes = png_bytes(sheet)
    report = {
        "reference": str(reference_path),
        "reference_sha256": sha256(reference_path),
        "reference_size_px": list(reference.size),
        "raw_cleaned_candidate": str(cleaned_path),
        "raw_cleaned_sha256": sha256(cleaned_path),
        "raw_cleaned_size_px": list(raw_cleaned.size),
        "cleaned_candidate": str(cleaned_path_for_report),
        "cleaned_sha256": cleaned_hash,
        "cleaned_size_px": list(cleaned.size),
        "normalization": normalization,
        "canvas_match": True,
        "automatic_geometry_verification": False,
        "manual_review_required": [
            "外墙/原始墙线、门窗、楼梯、电梯、卫生间、柱和房间边界位置未移动、增删或重绘",
            "只清理用户指定的外围轴网、尺寸线或界面噪声；不得补造看不清的工程要素",
        ],
        "review_sheet_size_px": list(sheet.size),
        "review_sheet_sha256": hashlib.sha256(sheet_bytes).hexdigest(),
        "execute": args.execute,
        "warning": "本工具只制作对照图，不证明清洗结果几何正确；须人工逐项核对。",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".existing-state-review-", dir=output_path.parent) as temp_dir:
        stage = Path(temp_dir)
        staged_image = stage / output_path.name
        staged_report = stage / report_path.name
        staged_image.write_bytes(sheet_bytes)
        report["output"] = str(output_path)
        report["report"] = str(report_path)
        report["execute"] = True
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if normalized_path is not None and normalized_bytes is not None:
            (stage / normalized_path.name).write_bytes(normalized_bytes)
        with Image.open(staged_image) as check:
            if check.size != tuple(report["review_sheet_size_px"]):
                raise RuntimeError("核验图尺寸回读不符")
        commit_output_bundle(stage, output_path.parent, names)
    print(f"已写出核验图和记录：{output_path}；{report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
