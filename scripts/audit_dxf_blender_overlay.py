#!/usr/bin/env python3
"""Create a pixel-aligned DXF vs Blender top-projection audit image.

The output is diagnostic only: DXF is drawn black, Blender mesh edges cyan,
and a combined overlay is provided to reveal alignment differences.
"""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import ezdxf
from PIL import Image, ImageDraw, ImageFont


BLENDER_EXPORT = r'''import bpy, json, sys
from mathutils import Vector
args = sys.argv[sys.argv.index("--") + 1:]
target = args[0]
items = []
for obj in bpy.data.objects:
    role = obj.get("model_role")
    if role not in {"wall", "opening", "furniture", "fixed"} or obj.type != "MESH":
        continue
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
    verts = [obj.matrix_world @ v.co for v in mesh.vertices]
    for edge in mesh.edges:
        a, b = (verts[i] for i in edge.vertices)
        items.append({"a": [a.x * 1000, a.y * 1000], "b": [b.x * 1000, b.y * 1000], "role": role})
    obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
with open(target, "w", encoding="utf-8") as f:
    json.dump({"geometry_sha256": bpy.context.scene.get("interior_geometry_sha256"), "items": items}, f)
print("BLENDER_PROJECTION_EXPORTED", len(items), "GEOMETRY_SHA256", bpy.context.scene.get("interior_geometry_sha256"))
'''


def dxf_geometry_sha256(doc):
    notes = list(doc.modelspace().query('TEXT[layer=="A-NOTE"]'))
    if len(notes) != 1 or not notes[0].has_xdata("XIONG_META"):
        raise ValueError("DXF is missing its geometry provenance metadata")
    hashes = [tag.value.split(":", 1)[1] for tag in notes[0].get_xdata("XIONG_META")
              if tag.code == 1000 and tag.value.startswith("geometry_sha256:")]
    if len(hashes) != 1 or len(hashes[0]) != 64 or any(c not in "0123456789abcdef" for c in hashes[0]):
        raise ValueError("DXF geometry provenance SHA-256 is missing or invalid")
    return hashes[0]


def verify_geometry_provenance(dxf_doc, blend_hash):
    dxf_hash = dxf_geometry_sha256(dxf_doc)
    if not isinstance(blend_hash, str) or len(blend_hash) != 64:
        raise ValueError("Blender scene is missing its geometry provenance SHA-256")
    if any(c not in "0123456789abcdef" for c in blend_hash):
        raise ValueError("Blender scene geometry provenance SHA-256 is invalid")
    if dxf_hash != blend_hash:
        raise ValueError("DXF and Blender model come from different geometry JSON versions")
    return dxf_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--blend", required=True, type=Path)
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true", help="write the PNG; default only validates inputs")
    parser.add_argument("--force", action="store_true", help="allow replacing an existing output")
    args = parser.parse_args()
    for path in (args.dxf, args.blend, args.blender):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not args.execute:
        print(f"DRY_RUN dxf={args.dxf} blend={args.blend} output={args.output}")
        return
    if args.output.exists() and not args.force:
        raise FileExistsError(f"Refusing to overwrite: {args.output}")

    with tempfile.TemporaryDirectory(prefix="dxf-blender-audit-") as temp:
        temp = Path(temp)
        extractor = temp / "export_projection.py"
        projection_path = temp / "projection.json"
        extractor.write_text(BLENDER_EXPORT, encoding="utf-8")
        result = subprocess.run([
            str(args.blender), "-b", str(args.blend), "--python", str(extractor),
            "--", str(projection_path),
        ], check=True, capture_output=True, text=True)
        print(result.stdout.strip())
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        if not isinstance(projection, dict) or not isinstance(projection.get("items"), list):
            raise ValueError("Blender projection output has an invalid format")
        blend_hash = projection.get("geometry_sha256")
        blender_lines = projection["items"]

    doc = ezdxf.readfile(args.dxf)
    provenance = verify_geometry_provenance(doc, blend_hash)
    dxf_lines = []
    for entity in doc.modelspace():
        if entity.dxftype() == "LINE":
            dxf_lines.append({"a": list(entity.dxf.start)[:2], "b": list(entity.dxf.end)[:2]})
    if not dxf_lines or not blender_lines:
        raise ValueError("Both files must contain planar line/mesh geometry")

    all_points = [p for segment in dxf_lines + blender_lines for p in (segment["a"], segment["b"])]
    min_x, max_x = min(p[0] for p in all_points), max(p[0] for p in all_points)
    min_y, max_y = min(p[1] for p in all_points), max(p[1] for p in all_points)
    pad_x = max((max_x - min_x) * 0.04, 100)
    pad_y = max((max_y - min_y) * 0.04, 100)
    bounds = (min_x - pad_x, min_y - pad_y, max_x + pad_x, max_y + pad_y)
    panel_w, panel_h, margin, gutter = 760, 760, 45, 20
    canvas = Image.new("RGB", (3 * panel_w + 4 * gutter, panel_h + 110), "white")
    draw = ImageDraw.Draw(canvas, "RGBA")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()
    titles = ["DXF (black)", "Blender mesh top projection (cyan)", "Overlay (misalignment = double edges)"]
    panels = []
    for index, title in enumerate(titles):
        x0 = gutter + index * (panel_w + gutter)
        top = 65
        panel = (x0, top, x0 + panel_w, top + panel_h)
        panels.append(panel)
        draw.rectangle(panel, outline=(180, 180, 180, 255), width=1)
        draw.text((x0 + 4, 20), title, fill=(25, 40, 50, 255), font=font)

    x0, y0, x1, y1 = bounds
    usable = panel_w - 2 * margin
    scale = min(usable / (x1 - x0), usable / (y1 - y0))

    def project(point, panel):
        px0, py0, _, _ = panel
        x = px0 + margin + (point[0] - x0) * scale
        y = py0 + margin + (y1 - point[1]) * scale
        return (x, y)

    for panel_index, panel in enumerate(panels):
        for seg in dxf_lines:
            if panel_index in (0, 2):
                draw.line((project(seg["a"], panel), project(seg["b"], panel)), fill=(20, 25, 29, 255), width=2)
        for seg in blender_lines:
            if panel_index in (1, 2):
                draw.line((project(seg["a"], panel), project(seg["b"], panel)), fill=(0, 160, 205, 180), width=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output)
    print(f"OVERLAY_OK geometry_sha256={provenance} dxf_lines={len(dxf_lines)} blender_edges={len(blender_lines)} units=mm output={args.output}")


if __name__ == "__main__":
    main()
