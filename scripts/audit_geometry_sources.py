#!/usr/bin/env python3
"""Read-only audit of geometry-to-CAD/user-mark source references."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SOURCE_KINDS = {"cad", "annotation", "user_instruction", "synthetic_fixture"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(data: dict, geometry_path: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    ledger = data.get("source_ledger", [])
    if not isinstance(ledger, list):
        raise ValueError("source_ledger must be a list")

    records: dict[str, dict] = {}
    for index, record in enumerate(ledger):
        label = f"source_ledger[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{label}: must be an object")
            continue
        source_id = record.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"{label}.id: required non-empty string")
            continue
        if source_id in records:
            errors.append(f"{label}.id: duplicate source id {source_id}")
            continue
        records[source_id] = record
        if record.get("kind") not in SOURCE_KINDS:
            errors.append(f"{label}.kind: unsupported source kind {record.get('kind')!r}")
        if not isinstance(record.get("locator"), str) or not record["locator"].strip():
            errors.append(f"{label}.locator: identify CAD entity/dimension, mark, user instruction, or fixture basis")
        artifact = record.get("artifact")
        expected_hash = record.get("sha256")
        if artifact is not None:
            if not isinstance(artifact, str) or not artifact.strip():
                errors.append(f"{label}.artifact: must be a non-empty path when provided")
                continue
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in expected_hash)
            ):
                errors.append(f"{label}.sha256: 64-character hexadecimal SHA-256 required when artifact is provided")
                continue
            artifact_path = Path(artifact).expanduser()
            if not artifact_path.is_absolute():
                artifact_path = geometry_path.parent / artifact_path
            try:
                actual_hash = sha256_file(artifact_path.resolve())
            except OSError:
                errors.append(f"{label}.artifact: source file not readable: {artifact_path}")
                continue
            if actual_hash != expected_hash.lower():
                errors.append(f"{label}.sha256: source artifact hash mismatch: {artifact_path}")

    used: set[str] = set()
    checked_entities = 0

    def refs(obj: object, location: str) -> None:
        nonlocal checked_entities
        checked_entities += 1
        if not isinstance(obj, dict):
            errors.append(f"{location}: expected object")
            return
        values = obj.get("source_refs")
        if not isinstance(values, list) or not values:
            warnings.append(f"{location}: no source_refs; CAD/user basis is not traceable")
            return
        for source_id in values:
            if not isinstance(source_id, str) or source_id not in records:
                errors.append(f"{location}.source_refs: unknown source id {source_id!r}")
            else:
                used.add(source_id)
        geometry_basis = {
            source_id for source_id in values
            if isinstance(source_id, str) and source_id in records
            and records[source_id].get("kind") in {"cad", "user_instruction", "synthetic_fixture"}
        }
        if not geometry_basis:
            warnings.append(
                f"{location}: annotation marks can record design intent but do not by themselves establish CAD dimensions/geometry; "
                "add a CAD or explicit user-confirmed geometry source"
            )

    rooms = data.get("rooms", [])
    if not isinstance(rooms, list):
        raise ValueError("rooms must be a list")
    for room_index, room in enumerate(rooms):
        room_id = room.get("id", f"rooms[{room_index}]") if isinstance(room, dict) else f"rooms[{room_index}]"
        refs(room, f"room {room_id} boundary/size")
        walls = room.get("walls", []) if isinstance(room, dict) else []
        if not isinstance(walls, list):
            errors.append(f"room {room_id}.walls: expected list")
            continue
        for edge_index, wall in enumerate(walls):
            refs(wall, f"room {room_id} wall edge {edge_index + 1}")
            openings = wall.get("openings", []) if isinstance(wall, dict) else []
            if not isinstance(openings, list):
                errors.append(f"room {room_id} wall edge {edge_index + 1}.openings: expected list")
                continue
            for opening_index, opening in enumerate(openings):
                refs(opening, f"room {room_id} wall edge {edge_index + 1} opening {opening_index + 1}")

    for key, label in (("furniture", "furniture"), ("fixed_elements", "fixed element")):
        items = data.get(key, [])
        if not isinstance(items, list):
            errors.append(f"{key}: expected list")
            continue
        for index, item in enumerate(items):
            item_id = item.get("id", f"{key}[{index}]") if isinstance(item, dict) else f"{key}[{index}]"
            refs(item, f"{label} {item_id}")

    unused = sorted(set(records) - used)
    for source_id in unused:
        warnings.append(f"source {source_id}: ledger entry is not referenced by geometry")
    return {
        "status": "errors" if errors else "needs_sources" if warnings else "complete",
        "geometry": str(geometry_path),
        "source_records": len(records),
        "entities_checked": checked_entities,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查几何JSON逐项对应的CAD/用户批注来源；不补猜来源、不改文件。")
    parser.add_argument("--geometry", required=True, type=Path)
    args = parser.parse_args()
    path = args.geometry.expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("geometry root must be an object")
    report = audit(data, path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"complete": 0, "needs_sources": 2, "errors": 1}[report["status"]]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"GEOMETRY_SOURCE_AUDIT_FAILED: {exc}")
