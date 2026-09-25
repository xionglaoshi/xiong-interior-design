#!/usr/bin/env python3
"""Export a simple, explicitly non-construction DXF from geometry JSON v1."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import ezdxf
from ezdxf import appsettings
from geometry_utils import (edge_key, furniture_type_label, rectangle_footprint,
                            shared_edge_owners, validate_geometry)
from geometry_approval import validate_receipt_snapshot
from atomic_output_bundle import commit_output_bundle, preflight_output_bundle


def validate(data):
    validate_geometry(data)


def add_source_refs(entity, source_refs):
    refs = list(dict.fromkeys(ref for ref in (source_refs or []) if isinstance(ref, str) and ref.strip()))
    if refs:
        entity.set_xdata("XIONG_SOURCE", [(1000, ref) for ref in refs])


def line(msp, p1, p2, layer, source_refs=None):
    entity = msp.add_line(p1, p2, dxfattribs={"layer": layer})
    add_source_refs(entity, source_refs)
    return entity


def point_at(a, ux, uy, nx, ny, dist, offset):
    return (a[0] + ux * dist + nx * offset, a[1] + uy * dist + ny * offset)


def _point_strictly_inside(point, polygon):
    x, y = point
    inside = False
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cross) <= 1e-7 and min(a[0], b[0]) - 1e-7 <= x <= max(a[0], b[0]) + 1e-7 and min(
                a[1], b[1]) - 1e-7 <= y <= max(a[1], b[1]) + 1e-7:
            return False
        if (a[1] > y) != (b[1] > y):
            cross_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < cross_x:
                inside = not inside
    return inside


def room_label_point(polygon):
    """Choose an interior label point; vertex averages can land on/outside concave rooms."""
    cross_sum = sum(polygon[i][0] * polygon[(i + 1) % len(polygon)][1] -
                    polygon[(i + 1) % len(polygon)][0] * polygon[i][1]
                    for i in range(len(polygon)))
    if abs(cross_sum) > 1e-9:
        cx = sum((polygon[i][0] + polygon[(i + 1) % len(polygon)][0]) *
                 (polygon[i][0] * polygon[(i + 1) % len(polygon)][1] -
                  polygon[(i + 1) % len(polygon)][0] * polygon[i][1])
                 for i in range(len(polygon))) / (3 * cross_sum)
        cy = sum((polygon[i][1] + polygon[(i + 1) % len(polygon)][1]) *
                 (polygon[i][0] * polygon[(i + 1) % len(polygon)][1] -
                  polygon[(i + 1) % len(polygon)][0] * polygon[i][1])
                 for i in range(len(polygon))) / (3 * cross_sum)
        centroid = (cx, cy)
        if _point_strictly_inside(centroid, polygon):
            return centroid

    # For concave polygons whose area centroid lies outside, choose the middle
    # of the widest interior scanline interval. Sample between vertex heights
    # so the chosen point cannot lie on a horizontal boundary edge.
    best = None
    ys = sorted({float(point[1]) for point in polygon})
    for low, high in zip(ys, ys[1:]):
        y = (low + high) / 2
        crossings = []
        for index, a in enumerate(polygon):
            b = polygon[(index + 1) % len(polygon)]
            if (a[1] > y) != (b[1] > y):
                crossings.append(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]))
        crossings.sort()
        for left, right in zip(crossings[::2], crossings[1::2]):
            candidate = ((left + right) / 2, y)
            width = right - left
            if width > 1e-7 and (best is None or width > best[0]):
                best = (width, candidate)
    if best is None:
        raise ValueError("Could not find an interior point for room label")
    return best[1]


def export(data, output, geometry_sha256=None):
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimeters
    doc.header["$MEASUREMENT"] = 1  # metric linetype hatch defaults
    appsettings.show_lineweight(doc, True)
    doc.appids.new("XIONG_SOURCE")
    if geometry_sha256:
        doc.appids.new("XIONG_META")
    layers = {
        "A-WALL": (7, 35),
        "A-OPENING": (4, 25),
        "A-WINDOW": (4, 18),
        "A-FURNITURE": (3, 18),
        "A-FIXED": (1, 35),
        "A-ROOM-TEXT": (7, 18),
        "A-NOTE": (1, 25),
    }
    for name, (color, weight) in layers.items():
        doc.layers.new(name, dxfattribs={"color": color, "lineweight": weight})
    msp = doc.modelspace()
    edge_owners = shared_edge_owners(data)

    for room in data["rooms"]:
        poly = room["polygon"]
        walls = room.get("walls", [{} for _ in poly])
        room_refs = room.get("source_refs", [])
        half = room.get("wall_thickness_mm", 120) / 2
        for i, a in enumerate(poly):
            b = poly[(i + 1) % len(poly)]
            if edge_owners[edge_key(a, b)] != (room["id"], i):
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            ux, uy = dx / length, dy / length
            nx, ny = -uy, ux
            wall_refs = list(dict.fromkeys([*room_refs, *walls[i].get("source_refs", [])]))
            ops = sorted(walls[i].get("openings", []), key=lambda x: x["start_mm"])
            spans, cursor = [], 0.0
            for op in ops:
                start, finish = op["start_mm"], op["start_mm"] + op["width_mm"]
                if start > cursor:
                    spans.append((cursor, start))
                cursor = finish
            if cursor < length:
                spans.append((cursor, length))
            for offset in (-half, half):
                for s0, s1 in spans:
                    line(msp, point_at(a, ux, uy, nx, ny, s0, offset),
                         point_at(a, ux, uy, nx, ny, s1, offset), "A-WALL", wall_refs)
            # Close wall ends and mark each opening jamb. Swing direction is intentionally unspecified.
            for s in (0.0, length):
                line(msp, point_at(a, ux, uy, nx, ny, s, -half),
                     point_at(a, ux, uy, nx, ny, s, half), "A-WALL", wall_refs)
            for op in ops:
                s0, s1 = op["start_mm"], op["start_mm"] + op["width_mm"]
                for s in (s0, s1):
                    line(msp, point_at(a, ux, uy, nx, ny, s, -half),
                         point_at(a, ux, uy, nx, ny, s, half), "A-OPENING",
                         list(dict.fromkeys([*wall_refs, *op.get("source_refs", [])])))
                if op.get("kind") == "window":
                    inset = min(half / 3, 35)
                    for offset in (-inset, inset):
                        line(msp, point_at(a, ux, uy, nx, ny, s0, offset),
                             point_at(a, ux, uy, nx, ny, s1, offset), "A-WINDOW",
                             list(dict.fromkeys([*wall_refs, *op.get("source_refs", [])])))

        center = room_label_point(poly)
        room_text = msp.add_text(f"{room['id']} {room.get('name', '')}".strip(), dxfattribs={
            "layer": "A-ROOM-TEXT", "height": 150,
            "insert": center,
        })
        add_source_refs(room_text, room_refs)

    for item in data.get("furniture", []):
        cx, cy = item["center_mm"]
        width, depth = item["size_mm"][:2]
        angle = math.radians(item.get("rotation_deg", 0))
        ca, sa = math.cos(angle), math.sin(angle)
        local = [(-width/2, -depth/2), (width/2, -depth/2),
                 (width/2, depth/2), (-width/2, depth/2)]
        corners = [(cx + x*ca - y*sa, cy + x*sa + y*ca) for x, y in local]
        for i, p in enumerate(corners):
            line(msp, p, corners[(i + 1) % 4], "A-FURNITURE", item.get("source_refs"))
        label_parts = [item.get("id"), item.get("name"),
                       furniture_type_label(item.get("type", "box"))]
        label = " ".join(str(part).strip() for part in label_parts if part and str(part).strip())
        if label:
            label_entity = msp.add_text(label, dxfattribs={
                "layer": "A-FURNITURE", "height": 90,
                "insert": (min(p[0] for p in corners), min(p[1] for p in corners) - 110),
            })
            add_source_refs(label_entity, item.get("source_refs"))

    for item in data.get("fixed_elements", []):
        corners = rectangle_footprint(item["center_mm"], item["size_mm"], item.get("rotation_deg", 0))
        for index, point in enumerate(corners):
            line(msp, point, corners[(index + 1) % len(corners)], "A-FIXED", item.get("source_refs"))
        label = item.get("name", item["type"])
        label_entity = msp.add_text(label, dxfattribs={
            "layer": "A-FIXED", "height": 100,
            "insert": (item["center_mm"][0], item["center_mm"][1]),
        })
        add_source_refs(label_entity, item.get("source_refs"))

    box = [[p[0] for room in data["rooms"] for p in room["polygon"]],
           [p[1] for room in data["rooms"] for p in room["polygon"]]]
    note_y = min(box[1]) - 450
    note = msp.add_text("CONCEPT REFERENCE ONLY - NOT FOR CONSTRUCTION", dxfattribs={
        "layer": "A-NOTE", "height": 180,
        "insert": (min(box[0]), note_y),
    })
    if geometry_sha256:
        note.set_xdata("XIONG_META", [(1000, f"geometry_sha256:{geometry_sha256}"),
                                      (1070, 1)])
    drawing_bounds = appsettings.update_extents(doc)
    if not drawing_bounds.has_data:
        raise ValueError("Cannot export a DXF without drawing extents")
    doc.saveas(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="geometry JSON v1")
    parser.add_argument("--output", required=True, help="new .dxf path")
    parser.add_argument("--approval", help="receipt JSON for explicit approval of this exact geometry version")
    parser.add_argument("--execute", action="store_true", help="write the DXF; default is preview only")
    parser.add_argument("--force", action="store_true", help="allow replacing output file")
    args = parser.parse_args()
    input_path = Path(args.input).expanduser().resolve()
    geometry_bytes = input_path.read_bytes()
    geometry_sha256 = hashlib.sha256(geometry_bytes).hexdigest()
    data = json.loads(geometry_bytes.decode("utf-8"))
    validate(data)
    if not args.execute:
        print(f"DRY_RUN rooms={len(data['rooms'])} furniture={len(data.get('furniture', []))} output={args.output} approval_required_for_execute=true")
        return
    if not args.approval:
        raise ValueError("--approval is required for writing a reference DXF")
    validate_receipt_snapshot(input_path, args.approval, geometry_bytes)
    output = Path(os.path.abspath(os.path.expanduser(args.output)))
    if output.suffix.lower() != ".dxf":
        raise ValueError("Output path must use the .dxf extension")
    preflight_output_bundle(output.parent, [output.name], force=args.force)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.stem}.staging-", dir=str(output.parent)) as temporary:
        staged = Path(temporary) / output.name
        export(data, staged, geometry_sha256)
        generated = ezdxf.readfile(staged)
        auditor = generated.audit()
        if auditor.errors:
            raise ValueError(f"Generated DXF failed read-back audit: {auditor.errors[:5]}")
        if generated.header.get("$INSUNITS") != 4:
            raise ValueError("Generated DXF read-back did not preserve millimeter units")
        notes = [entity for entity in generated.modelspace().query('TEXT[layer=="A-NOTE"]')]
        if len(notes) != 1 or "XIONG_META" not in notes[0].xdata:
            raise ValueError("Generated DXF read-back is missing geometry provenance metadata")
        recorded_hashes = [tag.value.split(":", 1)[1] for tag in notes[0].get_xdata("XIONG_META")
                           if tag.code == 1000 and tag.value.startswith("geometry_sha256:")]
        if recorded_hashes != [geometry_sha256]:
            raise ValueError("Generated DXF geometry provenance does not match the loaded input snapshot")
        commit_output_bundle(temporary, output.parent, [output.name], force=args.force)
    print(f"DXF_REFERENCE_OK rooms={len(data['rooms'])} furniture={len(data.get('furniture', []))} output={output}")


if __name__ == "__main__":
    main()
