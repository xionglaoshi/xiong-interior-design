#!/usr/bin/env python3
"""Create a provenance-bound index of plan annotator marks; never infer CAD geometry."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("批注文件必须是 schema_version=1 的对象")
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("缺少 project_id")
    canvas = payload.get("canvas_px")
    if (not isinstance(canvas, list) or len(canvas) != 2 or
            any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in canvas)):
        raise ValueError("canvas_px 必须是正整数 [宽, 高]")
    width, height = canvas
    for key in ("existing_hash", "layout_hash"):
        value = payload.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError(f"{key} 必须是64位SHA-256")
    views = payload.get("views")
    if not isinstance(views, dict):
        raise ValueError("缺少 views")
    for layer in ("existing", "layout"):
        marks = views.get(layer)
        if not isinstance(marks, list) or len(marks) > 1000:
            raise ValueError(f"views.{layer} 必须是最多1000项的数组")
        for i, mark in enumerate(marks, 1):
            label = f"views.{layer}[{i}]"
            if not isinstance(mark, dict) or mark.get("kind") not in {"rect", "circle", "line", "text"}:
                raise ValueError(f"{label}: 不支持的标注类型")
            kind = mark["kind"]
            if kind == "line":
                coords = [mark.get(k) for k in ("x1", "y1", "x2", "y2")]
                if not all(number(v) for v in coords) or not (0 <= coords[0] <= width and 0 <= coords[2] <= width and 0 <= coords[1] <= height and 0 <= coords[3] <= height):
                    raise ValueError(f"{label}: 线段坐标超出画布或无效")
                if ((coords[2]-coords[0])**2 + (coords[3]-coords[1])**2)**0.5 < 8:
                    raise ValueError(f"{label}: 线段长度不足8像素")
            elif kind == "text":
                if not number(mark.get("x")) or not number(mark.get("y")) or not (0 <= mark["x"] <= width and 0 <= mark["y"] <= height) or not isinstance(mark.get("text"), str) or not 0 < len(mark["text"]) <= 200:
                    raise ValueError(f"{label}: 文字或坐标无效")
            else:
                x, y, w, h = (mark.get(k) for k in ("x", "y", "w", "h"))
                if not all(number(v) for v in (x, y, w, h)) or w < 8 or h < 8 or x < 0 or y < 0 or x+w > width or y+h > height:
                    raise ValueError(f"{label}: 图形越界或尺寸不足8像素")
                if kind == "circle" and abs(w-h) >= 0.1:
                    raise ValueError(f"{label}: 圆形宽高不一致")


def summarize(payload: dict, source_path: Path, source_bytes: bytes) -> dict:
    digest = sha256_bytes(source_bytes)
    project_id = payload["project_id"]
    marks: list[dict] = []
    ledger: list[dict] = []
    for layer in ("existing", "layout"):
        for ordinal, mark in enumerate(payload["views"][layer], 1):
            source_id = f"annotation:{project_id}:{layer}:{ordinal:03d}"
            if mark["kind"] == "line":
                geometry = {k: mark[k] for k in ("x1", "y1", "x2", "y2")}
                bounds = [min(mark["x1"], mark["x2"]), min(mark["y1"], mark["y2"]), max(mark["x1"], mark["x2"]), max(mark["y1"], mark["y2"])]
            elif mark["kind"] == "text":
                geometry = {"x": mark["x"], "y": mark["y"]}
                bounds = [mark["x"], mark["y"], mark["x"], mark["y"]]
            else:
                geometry = {k: mark[k] for k in ("x", "y", "w", "h")}
                bounds = [mark["x"], mark["y"], mark["x"]+mark["w"], mark["y"]+mark["h"]]
            item = {"source_id": source_id, "layer": layer, "ordinal": ordinal, "kind": mark["kind"], "geometry_px": geometry, "bounds_px": bounds}
            if mark["kind"] == "text":
                item["text_exact"] = mark["text"]
            locator = f"views.{layer}[{ordinal}]，{mark['kind']}，像素边界 {bounds}；须结合底图与相邻批注人工判读"
            item["source_record"] = {"id": source_id, "kind": "annotation", "locator": locator, "artifact": str(source_path), "sha256": digest}
            marks.append(item)
            ledger.append(item["source_record"])
    return {
        "schema_version": 1,
        "report_type": "floorplan_annotation_source_index",
        "project_id": project_id,
        "project": payload.get("project"),
        "annotation_file": str(source_path),
        "annotation_sha256": digest,
        "canvas_px": payload["canvas_px"],
        "existing_hash": payload["existing_hash"],
        "layout_hash": payload["layout_hash"],
        "existing_state": payload.get("existing_state"),
        "exported_at": payload.get("exported_at"),
        "interpretation_status": "needs_interpretation",
        "notice": "本索引只保留原始标注类型、精确文字、像素坐标及来源哈希；不判断标注语义，不换算CAD尺寸，不生成或确认任何几何。每项须由模型/设计者结合底图解释并由用户核对。",
        "marks": marks,
        "source_ledger": ledger,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="读取注释器JSON并建立可追溯标注索引；不推断语义或CAD几何。默认仅预演。")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="写入新文件；拒绝覆盖")
    args = parser.parse_args()
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    validate(payload)
    report = summarize(payload, source, raw)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.execute:
        if output.exists():
            raise FileExistsError(f"拒绝覆盖已有文件: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        print(f"ANNOTATION_INDEX_WRITTEN {output} marks={len(report['marks'])} source_sha256={report['annotation_sha256']}")
    else:
        print(f"DRY_RUN marks={len(report['marks'])} project_id={report['project_id']} source_sha256={report['annotation_sha256']} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
