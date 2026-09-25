#!/usr/bin/env python3
"""Bind a manually accepted AI-cleaned image to its source and comparison evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

from atomic_output_bundle import commit_output_bundle, preflight_output_bundle


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}无法读取：{path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}必须是JSON对象：{path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在用户目检接受后，为AI清洗底图生成注释器可验证的版本/来源记录。"
    )
    parser.add_argument("--reference-report", required=True, type=Path, help="prepare_existing_state.py 的执行记录")
    parser.add_argument("--reference", required=True, type=Path, help="清洗前的确定性裁切/色调参考PNG")
    parser.add_argument("--cleaned", required=True, type=Path, help="经用户接受的AI清洗候选PNG")
    parser.add_argument("--comparison-report", required=True, type=Path, help="compare_existing_state_images.py 的执行记录")
    parser.add_argument("--confirmation", required=True, help="当前对话中用户确认接受此清洗图的原意摘要")
    parser.add_argument("--output", required=True, type=Path, help="新建的现状图校验记录JSON")
    parser.add_argument("--execute", action="store_true", help="实际写出；省略时仅预演")
    args = parser.parse_args()

    reference_report_path = args.reference_report.expanduser().resolve()
    reference_path = args.reference.expanduser().resolve()
    cleaned_path = args.cleaned.expanduser().resolve()
    comparison_path = args.comparison_report.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    if not reference_path.is_file() or not cleaned_path.is_file():
        parser.error("参考图和清洗图都必须存在")
    if not reference_report_path.is_file() or not comparison_path.is_file():
        parser.error("现状图准备记录和对比记录都必须存在")
    if len(args.confirmation.strip()) < 4:
        parser.error("必须记录当前对话中用户对这张清洗图的明确接受确认")
    if output_path.suffix.lower() != ".json":
        parser.error("输出扩展名必须为 .json")
    protected = {reference_path, cleaned_path, reference_report_path, comparison_path}
    if output_path in protected:
        parser.error("输出不能覆盖输入图或来源记录")
    try:
        preflight_output_bundle(output_path.parent, [output_path.name])
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    reference_report = read_json(reference_report_path, "原始现状图准备记录")
    comparison = read_json(comparison_path, "清洗图对比记录")
    if reference_report.get("execute") is not True:
        parser.error("原始现状图记录不是实际执行记录")
    try:
        reported_reference_path = Path(reference_report["output"]).expanduser().resolve()
    except (KeyError, TypeError, OSError, RuntimeError):
        parser.error("原始现状图记录缺少有效输出路径")
    reference_hash = sha256(reference_path)
    cleaned_hash = sha256(cleaned_path)
    if reported_reference_path != reference_path or reference_report.get("output_sha256") != reference_hash:
        parser.error("原始现状图与其准备记录路径或哈希不匹配")
    if comparison.get("execute") is not True or comparison.get("canvas_match") is not True:
        parser.error("必须先实际生成同画布的清洗对比图")
    if Path(str(comparison.get("reference", ""))).expanduser().resolve() != reference_path:
        parser.error("清洗对比记录引用了另一张参考图")
    if Path(str(comparison.get("cleaned_candidate", ""))).expanduser().resolve() != cleaned_path:
        parser.error("清洗对比记录引用了另一张清洗图")
    if comparison.get("reference_sha256") != reference_hash or comparison.get("cleaned_sha256") != cleaned_hash:
        parser.error("对比记录中的图片哈希与当前输入不匹配")
    if Path(str(comparison.get("report", ""))).expanduser().resolve() != comparison_path:
        parser.error("清洗对比记录路径与指定文件不匹配")
    try:
        comparison_image = Path(comparison["output"]).expanduser().resolve()
        if sha256(comparison_image) != comparison.get("review_sheet_sha256"):
            parser.error("对比图缺失或与对比记录哈希不匹配")
    except (KeyError, OSError, TypeError, RuntimeError):
        parser.error("清洗对比图缺失或其记录无效")

    with Image.open(reference_path) as reference_image, Image.open(cleaned_path) as cleaned_image:
        reference_size = reference_image.size
        cleaned_size = cleaned_image.size
    if reference_size != cleaned_size:
        parser.error("参考图与清洗图画布不一致；拒绝缩放、拉伸或自动配准")
    if comparison.get("reference_size_px") != list(reference_size) or comparison.get("cleaned_size_px") != list(cleaned_size):
        parser.error("对比记录中的画布尺寸与当前图片不一致")
    if reference_report.get("output_size_px") != list(reference_size):
        parser.error("原始现状图记录的画布尺寸无效")

    record = dict(reference_report)
    record.update({
        "output": str(cleaned_path),
        "output_sha256": cleaned_hash,
        "output_size_px": list(cleaned_size),
        "execute": args.execute,
        "ai_cleanup_review": {
            "method": "AI-edited raster candidate; no automatic geometry verification",
            "reference": str(reference_path),
            "reference_sha256": reference_hash,
            "comparison_report": str(comparison_path),
            "comparison_report_sha256": sha256(comparison_path),
            "comparison_image": str(comparison_image),
            "comparison_image_sha256": comparison.get("review_sheet_sha256"),
            "raw_cleaned_candidate": comparison.get("raw_cleaned_candidate"),
            "raw_cleaned_sha256": comparison.get("raw_cleaned_sha256"),
            "normalization": comparison.get("normalization"),
            "user_confirmation": args.confirmation.strip(),
            "coordinate_alignment": "same canvas dimensions; visual alignment manually accepted, not machine-proven",
        },
        "warning": "AI清洗可能移动或重构线条；必须按来源对比图人工确认。结构/可拆改判断仍以CAD和用户确认标注为准。",
    })
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cleaned-state-acceptance-", dir=output_path.parent) as temp_dir:
        stage = Path(temp_dir)
        staged = stage / output_path.name
        staged.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        commit_output_bundle(stage, output_path.parent, [output_path.name])
    print(f"已写出现状图来源记录：{output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
