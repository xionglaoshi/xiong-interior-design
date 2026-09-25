#!/usr/bin/env python3
"""Render whole-plan Blender references from an approved geometry/model pair.

Writes a full-height axonometric overview and an orthographic top-layout PNG.
The source .blend is opened but never saved; actual output requires an exact
geometry approval receipt. This is a spatial-relationship sketch, not a render.
"""
import argparse
import atexit
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geometry_utils import edge_key, validate_geometry
from geometry_approval import validate_receipt_snapshot
from render_blender_room_reference import (
    apply_plan_render_materials, build_top_plan_overlays, material, aim,
)


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="从已确认模型生成全局轴测图与全墙顶视布局图。")
    parser.add_argument("--blend", required=True)
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--approval", help="精确绑定当前几何版本的用户确认凭据")
    parser.add_argument("--output-dir", required=True, help="必须是新的输出目录")
    parser.add_argument("--width", type=int, default=1800)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--execute", action="store_true", help="实际渲染；默认只预演")
    return parser.parse_args(argv)


def scene_bounds(data):
    pts = [point for room in data["rooms"] for point in room["polygon"]]
    xs, ys = [p[0] * .001 for p in pts], [p[1] * .001 for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def global_cutaway_edges(data):
    """Return near-side/minimum-X and minimum-Y edges for a southwest overview."""
    result = set()
    for room in data["rooms"]:
        points = room["polygon"]
        min_x = min(point[0] for point in points)
        min_y = min(point[1] for point in points)
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            if ((start[0] == min_x and end[0] == min_x) or
                    (start[1] == min_y and end[1] == min_y)):
                result.add(edge_key(start, end))
    return result


def object_edge_key(obj):
    raw = obj.get("source_edge_key")
    if not raw:
        return None
    try:
        value = json.loads(raw)
        if len(value) != 2 or any(len(point) != 2 for point in value):
            return None
        return edge_key(value[0], value[1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def check_model(data, geometry_hash):
    tagged = [obj for obj in bpy.data.objects if obj.get("model_role")]
    floor_rooms = {obj.get("source_room_id") for obj in tagged if obj.get("model_role") == "floor"}
    expected_rooms = {room["id"] for room in data["rooms"]}
    if floor_rooms != expected_rooms:
        raise ValueError(f"Blend room floors do not match geometry: expected={expected_rooms}, found={floor_rooms}")
    expected_furniture = {str(item.get("id", item.get("name"))) for item in data.get("furniture", [])}
    actual_furniture = {str(obj.get("source_furniture_id")) for obj in tagged if obj.get("model_role") == "furniture"}
    if not expected_furniture.issubset(actual_furniture):
        raise ValueError(f"Blend is missing furniture IDs: {sorted(expected_furniture - actual_furniture)}")
    expected_fixed = {str(item["id"]) for item in data.get("fixed_elements", [])}
    actual_fixed = {str(obj.get("source_fixed_element_id")) for obj in tagged if obj.get("model_role") == "fixed"}
    if not expected_fixed.issubset(actual_fixed):
        raise ValueError(f"Blend is missing fixed-element IDs: {sorted(expected_fixed - actual_fixed)}")
    scene_hash = bpy.context.scene.get("interior_geometry_sha256")
    if scene_hash != geometry_hash:
        raise ValueError("Blend's embedded geometry SHA-256 does not match geometry JSON")


def add_camera(scene, name, location, target, ortho_scale=None):
    data = bpy.data.cameras.new(name)
    data.lens = 48
    data.type = "ORTHO" if ortho_scale else "PERSP"
    if ortho_scale:
        data.ortho_scale = ortho_scale
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    aim(camera, target)
    scene.camera = camera
    return camera


def fit_perspective(scene, camera, points, target, margin=.07):
    target = Vector(target)
    for count in range(100):
        bpy.context.view_layer.update()
        coords = [world_to_camera_view(scene, camera, Vector(p)) for p in points]
        if all(p.z > 0 and margin <= p.x <= 1-margin and margin <= p.y <= 1-margin for p in coords):
            return count
        camera.location = target + (camera.location - target) * 1.055
    raise ValueError("Could not fit all rooms and furniture into global overview camera")


def main():
    args = parse_args()
    blend_path = os.path.realpath(os.path.expanduser(args.blend))
    geometry_path = os.path.realpath(os.path.expanduser(args.geometry))
    output_dir = os.path.realpath(os.path.expanduser(args.output_dir))
    if not os.path.isfile(blend_path) or not os.path.isfile(geometry_path):
        raise FileNotFoundError("Blend and geometry JSON must both exist")
    geometry_bytes = open(geometry_path, "rb").read()
    geometry_hash = hashlib.sha256(geometry_bytes).hexdigest()
    data = json.loads(geometry_bytes)
    validate_geometry(data)
    if not args.execute:
        print(json.dumps({"status": "dry_run", "rooms": len(data["rooms"]),
                          "furniture": len(data.get("furniture", [])), "geometry_sha256": geometry_hash,
                          "approval_required": True, "output_dir": output_dir}, ensure_ascii=False))
        return
    if not args.approval:
        raise ValueError("--approval is required for rendering from a user-confirmed geometry")
    validate_receipt_snapshot(geometry_path, args.approval, geometry_bytes)
    if os.path.exists(output_dir):
        raise FileExistsError(f"Refusing existing output directory: {output_dir}")
    if args.width < 400 or args.height < 400:
        raise ValueError("Image dimensions must each be at least 400px")
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    check_model(data, geometry_hash)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "AgX"
    scene.render.film_transparent = False
    scene.world.color = (.72, .72, .72)
    floor_objects = [o for o in bpy.data.objects if o.get("model_role") == "floor"]
    furniture = [o for o in bpy.data.objects if o.get("model_role") in {"furniture", "fixed"}]
    if not floor_objects:
        raise ValueError("Blend contains no tagged room floors")
    min_x, max_x, min_y, max_y = scene_bounds(data)
    cx, cy = (min_x + max_x)/2, (min_y + max_y)/2
    span = max(max_x-min_x, max_y-min_y, .1)
    wall_h = data.get("wall_height_mm", 2800) * .001
    target = (cx, cy, wall_h * .2)
    all_xy = [(x, y) for room in data["rooms"] for x, y in [(p[0]*.001, p[1]*.001) for p in room["polygon"]]]
    points = [(x, y, z) for x,y in all_xy for z in (0, wall_h)]
    for obj in furniture:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)

    parent = os.path.dirname(output_dir)
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"Output parent directory must already exist: {parent}")
    if os.path.exists(output_dir):
        raise FileExistsError(f"Refusing existing output directory: {output_dir}")
    staging_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(output_dir)}.rendering-", dir=parent)
    cleanup_staging = lambda: shutil.rmtree(staging_dir, ignore_errors=True)
    atexit.register(cleanup_staging)
    overview_path = os.path.join(staging_dir, "global-axonometric.png")
    top_path = os.path.join(staging_dir, "global-top-layout.png")
    camera = add_camera(scene, "CAM_GLOBAL_AXONOMETRIC", (cx-span*.9, cy-span*1.15, wall_h+span*1.25), target)
    steps = fit_perspective(scene, camera, points, target)
    cutaway_edges = global_cutaway_edges(data)
    for obj in bpy.data.objects:
        if obj.get("model_role") in {"wall", "opening"} and object_edge_key(obj) in cutaway_edges:
            obj.hide_render = True
    key_data = bpy.data.lights.new("Global key soft light", "AREA")
    key = bpy.data.objects.new("Global key soft light", key_data)
    scene.collection.objects.link(key)
    key.location = (cx-span*.2, cy-span*.5, wall_h+span*1.8)
    key_data.energy = 1400 * max(1, span*span/20)
    key_data.shape = "DISK"
    key_data.size = span*.9
    aim(key, (cx, cy, 0))
    scene.render.filepath = overview_path
    bpy.ops.render.render(write_still=True)
    overview_hash = sha256(overview_path)

    for obj in bpy.data.objects:
        if obj.get("model_role") in {"wall", "opening"}:
            obj.hide_render = True
    wall_mat = material("Global plan wall bands", (.14, .17, .18))
    window_mat = material("Global plan window bands", (.10, .38, .43))
    overlays = []
    for room in data["rooms"]:
        overlays.extend(build_top_plan_overlays(room, wall_mat, window_mat))
    restore_plan_materials = apply_plan_render_materials(data)
    top_color_transform = scene.view_settings.view_transform
    scene.view_settings.view_transform = "Standard"
    aspect = args.width / args.height
    ortho_scale = max(span, span / aspect) * 1.12
    add_camera(scene, "CAM_GLOBAL_TOP", (cx, cy, wall_h+span*2.2), (cx, cy, 0), ortho_scale=ortho_scale)
    scene.render.filepath = top_path
    bpy.ops.render.render(write_still=True)
    restore_plan_materials()
    scene.view_settings.view_transform = top_color_transform
    top_hash = sha256(top_path)
    metadata = {
        "schema_version": 1, "geometry_sha256": geometry_hash,
        "blend_sha256": sha256(blend_path), "room_ids": sorted(r["id"] for r in data["rooms"]),
        "furniture_ids": sorted(str(i.get("id", i.get("name"))) for i in data.get("furniture", [])),
        "fixed_element_ids": sorted(str(i["id"]) for i in data.get("fixed_elements", [])),
        "images": {"global_axonometric": os.path.join(output_dir, "global-axonometric.png"),
                   "global_axonometric_sha256": overview_hash,
                   "global_top_layout": os.path.join(output_dir, "global-top-layout.png"),
                   "global_top_layout_sha256": top_hash},
        "axonometric_fit_steps": steps,
        "axonometric_cutaway_edge_count": len(cutaway_edges),
        "warning": "全局空间关系草图；不得替代CAD、确认几何或施工图。"
    }
    try:
        with open(os.path.join(staging_dir, "global-reference.json"), "x", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if os.path.exists(output_dir):
            raise FileExistsError(f"Refusing existing output directory: {output_dir}")
        os.rename(staging_dir, output_dir)
        atexit.unregister(cleanup_staging)
    except BaseException:
        shutil.rmtree(staging_dir)
        raise
    print("GLOBAL_REFERENCE_OK", json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"GLOBAL_REFERENCE_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
