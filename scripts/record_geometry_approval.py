#!/usr/bin/env python3
"""Record explicit user approval for one exact geometry JSON version."""
import argparse
import json
from pathlib import Path

from geometry_approval import create_receipt
from geometry_utils import validate_geometry


def main():
    parser = argparse.ArgumentParser(description="记录用户已明确确认且来源链完整的几何版本；本工具不判断是否真的获得过用户确认。")
    parser.add_argument("--geometry", required=True, help="用户确认的 geometry JSON v1")
    parser.add_argument("--confirmation", required=True, help="简述用户在会话中的明确确认，不得代用户确认")
    parser.add_argument("--output", required=True, help="新建的确认凭据 JSON 路径")
    parser.add_argument("--execute", action="store_true", help="实际写出；默认只预演")
    args = parser.parse_args()
    geometry = Path(args.geometry).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output == geometry:
        parser.error("确认凭据不能覆盖几何JSON")
    try:
        validate_geometry(json.loads(geometry.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid geometry JSON: {geometry}") from exc
    receipt = create_receipt(geometry, args.confirmation)
    print(json.dumps({**receipt, "output": str(output), "execute": args.execute}, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"GEOMETRY_APPROVAL_RECORDED {output}")


if __name__ == "__main__":
    main()
