#!/usr/bin/env python3
"""Create a self-contained two-layer plan annotator from aligned raster images."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import mimetypes
import re
from pathlib import Path

from PIL import Image


ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "drawing-viewer"
TEMPLATE_PATH = ASSET_DIR / "blank-template.html"
CSS_PATH = ASSET_DIR / "blank-template.css"
JS_PATH = ASSET_DIR / "blank-template.js"
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def load_image(path: Path) -> tuple[tuple[int, int], str, str]:
    raw = path.read_bytes()
    with Image.open(path) as image:
        image_format = image.format or ""
        size = image.size
    mime = MIME_TYPES.get(image_format)
    if not mime:
        raise ValueError(f"Unsupported image format {image_format!r}: use PNG, JPEG, or WebP")
    digest = hashlib.sha256(raw).hexdigest()
    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return size, digest, data_url


def load_existing_state_report(report_path: Path, image_path: Path, image_size: tuple[int, int], image_hash: str) -> dict:
    report_path = report_path.expanduser().resolve()
    if not report_path.is_file():
        raise ValueError(f"找不到现状图校验记录：{report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"现状图校验记录无法读取：{report_path}") from exc
    if not isinstance(report, dict) or report.get("execute") is not True:
        raise ValueError("现状图校验记录必须来自实际执行的截图准备或清洗接受流程")
    try:
        recorded_output = Path(report["output"]).expanduser().resolve()
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise ValueError("现状图校验记录缺少有效的输出路径") from exc
    if recorded_output != image_path:
        raise ValueError("现状图校验记录指向另一张图片")
    if report.get("output_sha256") != image_hash:
        raise ValueError("现状图与校验记录的 SHA-256 不一致")
    if report.get("output_size_px") != list(image_size):
        raise ValueError("现状图与校验记录的像素尺寸不一致")
    if report.get("geometry_pixels_rescaled") is not False:
        raise ValueError("现状图校验记录未确认几何像素保持原尺寸")

    source_size = report.get("source_size_px")
    display_size = report.get("source_display_size_px", source_size)
    orientation = report.get("exif_orientation", 1)
    crop = report.get("crop_px")
    if (not isinstance(source_size, list) or len(source_size) != 2
            or any(type(value) is not int or value <= 0 for value in source_size)):
        raise ValueError("现状图校验记录中的源图尺寸无效")
    if (not isinstance(display_size, list) or len(display_size) != 2
            or any(type(value) is not int or value <= 0 for value in display_size)):
        raise ValueError("现状图校验记录中的EXIF方向后显示尺寸无效")
    if type(orientation) is not int or orientation not in range(1, 9):
        raise ValueError("现状图校验记录中的EXIF方向值无效")
    expected_display_size = source_size[::-1] if orientation in {5, 6, 7, 8} else source_size
    if display_size != expected_display_size:
        raise ValueError("现状图校验记录中的原始尺寸与EXIF显示方向尺寸矛盾")
    if (not isinstance(crop, list) or len(crop) != 4
            or any(type(value) is not int for value in crop)):
        raise ValueError("现状图校验记录中的裁切坐标无效")
    left, top, right, bottom = crop
    if (left < 0 or top < 0 or right <= left or bottom <= top
            or right > display_size[0] or bottom > display_size[1]
            or [right - left, bottom - top] != list(image_size)):
        raise ValueError("裁切框、源图尺寸与现状图画布尺寸互相矛盾")
    source_hash = report.get("source_sha256")
    if (not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in source_hash)):
        raise ValueError("现状图校验记录中的源图 SHA-256 无效")
    cleanup_review = report.get("ai_cleanup_review")
    if cleanup_review is not None:
        if (not isinstance(cleanup_review, dict)
                or cleanup_review.get("method") != "AI-edited raster candidate; no automatic geometry verification"
                or not isinstance(cleanup_review.get("user_confirmation"), str)
                or len(cleanup_review["user_confirmation"].strip()) < 4
                or cleanup_review.get("coordinate_alignment") != "same canvas dimensions; visual alignment manually accepted, not machine-proven"):
            raise ValueError("AI清洗现状图缺少有效的人工接受与坐标对齐声明")
        comparison_path = Path(str(cleanup_review.get("comparison_report", ""))).expanduser().resolve()
        if not comparison_path.is_file() or hashlib.sha256(comparison_path.read_bytes()).hexdigest() != cleanup_review.get("comparison_report_sha256"):
            raise ValueError("AI清洗对比记录缺失或哈希不匹配")
        comparison_image = Path(str(cleanup_review.get("comparison_image", ""))).expanduser().resolve()
        if not comparison_image.is_file() or hashlib.sha256(comparison_image.read_bytes()).hexdigest() != cleanup_review.get("comparison_image_sha256"):
            raise ValueError("AI清洗对比图缺失或哈希不匹配")
        reference_image = Path(str(cleanup_review.get("reference", ""))).expanduser().resolve()
        if not reference_image.is_file() or hashlib.sha256(reference_image.read_bytes()).hexdigest() != cleanup_review.get("reference_sha256"):
            raise ValueError("AI清洗参考图缺失或哈希不匹配")
    return {
        "sourceSha256": source_hash.lower(),
        "sourceSizePx": source_size,
        "sourceDisplaySizePx": display_size,
        "exifOrientation": orientation,
        "cropCoordinateFrame": report.get("crop_coordinate_frame", "top-left pixels after EXIF orientation normalization"),
        "cropPx": crop,
        "tone": report.get("tone"),
        "outputSha256": image_hash,
        "reportSha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "aiCleanupReview": cleanup_review,
    }


def safe_filename(value: str) -> str:
    return re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-.") or "floorplan"


def main() -> int:
    parser = argparse.ArgumentParser(description="用同尺寸现状图/布置图生成自包含双图层注释器。")
    parser.add_argument("--project", required=True, help="项目名称")
    parser.add_argument("--project-id", help="稳定项目ID；不同方案建议加版本号")
    parser.add_argument("--existing", required=True, type=Path, help="现状图栅格图片")
    parser.add_argument("--existing-state-report", required=True, type=Path,
                        help="prepare_existing_state.py 或经人工接受的AI清洗 PNG 校验记录 JSON")
    parser.add_argument("--layout", type=Path, help="布置图；省略时复制现状图作为初始布置图")
    parser.add_argument("--output", required=True, type=Path, help="新建 HTML 路径")
    parser.add_argument("--execute", action="store_true", help="实际写出；默认只预演")
    args = parser.parse_args()

    existing_path = args.existing.expanduser().resolve()
    layout_path = (args.layout or args.existing).expanduser().resolve()
    target = args.output.expanduser().resolve()
    for path in (existing_path, layout_path):
        if not path.is_file():
            parser.error(f"找不到图片：{path}")
    if target.suffix.lower() != ".html":
        parser.error("输出扩展名必须为 .html")
    if target in {existing_path, layout_path}:
        parser.error("输出不能覆盖输入图片")
    if target.exists():
        parser.error(f"输出已存在，拒绝覆盖：{target}")

    existing_size, existing_hash, existing_url = load_image(existing_path)
    existing_state = load_existing_state_report(args.existing_state_report, existing_path, existing_size, existing_hash)
    layout_size, layout_hash, layout_url = load_image(layout_path)
    if existing_size != layout_size:
        parser.error(f"两张图尺寸必须完全相同以保持坐标重合；现状图={existing_size}，布置图={layout_size}")

    project_id = args.project_id or safe_filename(args.project)
    filename = safe_filename(project_id)
    config = {
        "project": args.project,
        "projectId": project_id,
        "filename": filename,
        "width": existing_size[0],
        "height": existing_size[1],
        "existingHash": existing_hash,
        "layoutHash": layout_hash,
        "existingState": existing_state,
        "existingImage": existing_url,
        "layoutImage": layout_url,
    }
    report = {
        "project": args.project,
        "project_id": project_id,
        "existing_image": str(existing_path),
        "layout_image": str(layout_path),
        "canvas_px": list(existing_size),
        "existing_sha256": existing_hash,
        "existing_state": existing_state,
        "layout_sha256": layout_hash,
        "layout_is_initial_copy": layout_path == existing_path,
        "output": str(target),
        "execute": args.execute,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    template = template.replace("{{PAGE_TITLE}}", html.escape(f"{args.project} · 平面布局注释器"))
    template = template.replace("{{INLINE_CSS}}", CSS_PATH.read_text(encoding="utf-8"))
    template = template.replace("{{CONFIG_JSON}}", json.dumps(config, ensure_ascii=False).replace("<", "\\u003c"))
    template = template.replace("{{INLINE_JS}}", JS_PATH.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        output.write(template)
    print(f"ANNOTATOR_BUILD_OK {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f"错误：{exc}")
