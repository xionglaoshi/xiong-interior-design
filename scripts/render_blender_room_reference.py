#!/usr/bin/env python3
"""Render a repeatable, editable Blender camera reference for one room.

The input .blend is never overwritten. A new output directory receives the
camera-enabled .blend, reference PNG, and camera metadata JSON. This is a
synthetic/model-grounded reference capture, not a final photoreal render.
"""

import argparse
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
from geometry_utils import camera_profile_parameters, edge_key, validate_geometry
from geometry_approval import validate_receipt_snapshot


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="为指定房间创建可复现的Blender相机参考图。")
    parser.add_argument("--blend", required=True, help="已生成的源 .blend；不会覆盖")
    parser.add_argument("--geometry", required=True, help="与源模型对应的 geometry JSON v1")
    parser.add_argument("--approval", help="receipt JSON for explicit approval of this exact geometry version")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output-dir", required=True, help="必须是新目录")
    parser.add_argument("--corner", choices=("sw", "se", "ne", "nw"), default="sw",
                        help="房间外侧的高位俯斜观察方位")
    parser.add_argument("--cutaway-corner", choices=("none", "sw", "se", "ne", "nw"), default=None,
                        help="仅参考图渲染时移除指定角相邻的两段墙；完整墙体仍保留在Blend模型中")
    parser.add_argument("--view-profile", choices=("interior", "elevated"), default="interior",
                        help="interior=家具更易辨认；elevated=高位总览，保留更多房间边界")
    parser.add_argument("--lens-mm", type=float, default=None, help="覆盖预设焦段")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--execute", action="store_true", help="实际渲染；默认只预演")
    return parser.parse_args(argv)


def polygon_centroid(poly):
    area2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] -
                poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
    if abs(area2) < 1e-8:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = sum((poly[i][0] + poly[(i + 1) % len(poly)][0]) *
             (poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1])
             for i in range(len(poly))) / (3 * area2)
    cy = sum((poly[i][1] + poly[(i + 1) % len(poly)][1]) *
             (poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1])
             for i in range(len(poly))) / (3 * area2)
    return cx, cy


def material(name, color, roughness=0.78):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    return mat


def apply_plan_render_materials(data, room_id=None):
    """Temporarily use flat, category-separated colors for plan-view output."""
    furniture_types = {
        str(item.get("id", item.get("name", ""))): item.get("type", "box")
        for item in data.get("furniture", [])
    }
    palette = {
        "floor": (0.58, 0.61, 0.63, 1.0),
        "table": (0.60, 0.30, 0.10, 1.0),
        "mahjong_table": (0.08, 0.34, 0.25, 1.0),
        "chair": (0.08, 0.30, 0.52, 1.0),
        "armchair": (0.12, 0.32, 0.50, 1.0),
        "sofa": (0.52, 0.21, 0.08, 1.0),
        "bed": (0.44, 0.30, 0.12, 1.0),
        "cabinet": (0.22, 0.25, 0.30, 1.0),
        "sink": (0.10, 0.38, 0.43, 1.0),
        "toilet": (0.20, 0.32, 0.38, 1.0),
        "refrigerator": (0.28, 0.31, 0.36, 1.0),
        "cooktop": (0.14, 0.17, 0.19, 1.0),
        "box": (0.35, 0.25, 0.42, 1.0),
        "fixed": (0.10, 0.12, 0.14, 1.0),
    }
    flat_materials = {}
    for key, color in palette.items():
        mat = bpy.data.materials.new(f"TEMP plan {key}")
        mat.diffuse_color = color
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = color
        mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        flat_materials[key] = mat

    saved_slots = []
    for obj in bpy.data.objects:
        role = obj.get("model_role")
        source_room = obj.get("source_room_id")
        if room_id is not None and source_room != room_id:
            continue
        if role == "floor":
            category = "floor"
        elif role == "furniture":
            category = furniture_types.get(str(obj.get("source_furniture_id", "")), "box")
        elif role == "fixed":
            category = "fixed"
        else:
            continue
        slots = list(obj.data.materials)
        saved_slots.append((obj, slots))
        obj.data.materials.clear()
        obj.data.materials.append(flat_materials.get(category, flat_materials["box"]))

    def restore():
        for obj, slots in saved_slots:
            if obj.name not in bpy.data.objects:
                continue
            obj.data.materials.clear()
            for mat in slots:
                obj.data.materials.append(mat)
        for mat in flat_materials.values():
            if mat.name in bpy.data.materials:
                bpy.data.materials.remove(mat)

    return restore


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def fit_camera_to_points(scene, camera, target, points, margin=0.08, max_iterations=80):
    """Move a perspective camera back until all required points fit its frame."""
    target = Vector(target)
    points = [Vector(point) for point in points]
    if not points or not 0 <= margin < 0.5:
        raise ValueError("Camera framing needs points and a margin in [0, 0.5)")
    for iteration in range(max_iterations + 1):
        bpy.context.view_layer.update()
        projected = [world_to_camera_view(scene, camera, point) for point in points]
        if all(point.z > 0 and margin <= point.x <= 1 - margin and
               margin <= point.y <= 1 - margin for point in projected):
            return iteration
        if iteration == max_iterations:
            break
        relative = camera.location - target
        camera.location = target + relative * 1.06
    raise ValueError("Unable to fit room bounds and furniture into the camera frame")


def camera_fit_points(room, objects, wall_height_m, view_profile):
    """Choose frame anchors according to interior-readability vs full-height view."""
    xs = [point[0] * 0.001 for point in room["polygon"]]
    ys = [point[1] * 0.001 for point in room["polygon"]]
    points = [(x, y, 0.0) for x, y in zip(xs, ys)]
    if view_profile == "elevated":
        points.extend((x, y, wall_height_m) for x, y in zip(xs, ys))
    for obj in objects:
        if obj.get("model_role") not in {"furniture", "fixed"}:
            continue
        if obj.get("source_room_id") != room["id"]:
            continue
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    return points


def build_top_plan_overlays(room, wall_material, window_material, height=0.035):
    """Create temporary plan-symbol wall bands from the confirmed room geometry."""
    scale = 0.001
    thickness = room.get("wall_thickness_mm", 120) * scale
    created = []

    def add_band(name, a, b, z, depth, mat):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        center = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, z)
        bpy.ops.mesh.primitive_cube_add(size=1, location=center)
        obj = bpy.context.object
        obj.name = name
        obj.dimensions = (length, depth, height)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.rotation_euler.z = angle
        obj.data.materials.append(mat)
        created.append(obj)

    for index, a_mm in enumerate(room["polygon"]):
        b_mm = room["polygon"][(index + 1) % len(room["polygon"])]
        a = (a_mm[0] * scale, a_mm[1] * scale)
        b = (b_mm[0] * scale, b_mm[1] * scale)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux
        wall = room.get("walls", [{} for _ in room["polygon"]])[index]
        openings = sorted(wall.get("openings", []), key=lambda op: op["start_mm"])
        cursor = 0.0
        spans = []
        for opening in openings:
            start = opening["start_mm"] * scale
            end = start + opening["width_mm"] * scale
            if start > cursor:
                spans.append((cursor, start))
            cursor = end
        if cursor < length:
            spans.append((cursor, length))

        for segment_index, (start, end) in enumerate(spans, start=1):
            p0 = (a[0] + ux * start, a[1] + uy * start)
            p1 = (a[0] + ux * end, a[1] + uy * end)
            add_band(f"TOP_{room['id']}_E{index + 1}_W{segment_index}", p0, p1, height / 2,
                     thickness, wall_material)

        for opening_index, opening in enumerate(openings, start=1):
            start = opening["start_mm"] * scale
            end = start + opening["width_mm"] * scale
            p0 = (a[0] + ux * start, a[1] + uy * start)
            p1 = (a[0] + ux * end, a[1] + uy * end)
            if opening.get("kind") == "door":
                for jamb_index, station in enumerate((start, end), start=1):
                    center = (a[0] + ux * station, a[1] + uy * station)
                    q0 = (center[0] - nx * thickness / 2, center[1] - ny * thickness / 2)
                    q1 = (center[0] + nx * thickness / 2, center[1] + ny * thickness / 2)
                    add_band(f"TOP_{room['id']}_E{index + 1}_O{opening_index}_JAMB{jamb_index}",
                             q0, q1, height * 1.6, max(0.018, thickness * 0.12), wall_material)
            elif opening.get("kind") == "window":
                for rail_index, offset in enumerate((-thickness * 0.22, thickness * 0.22), start=1):
                    q0 = (p0[0] + nx * offset, p0[1] + ny * offset)
                    q1 = (p1[0] + nx * offset, p1[1] + ny * offset)
                    add_band(f"TOP_{room['id']}_E{index + 1}_O{opening_index}_WINDOW{rail_index}",
                             q0, q1, height * 1.6, max(0.018, thickness * 0.12), window_material)

    return created


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_reference(data, room, corner, cutaway_corner, view_profile, lens_override,
                    width, height, png_path, full_png_path, top_png_path):
    room_id = room["id"]
    scene = bpy.context.scene
    modeled_rooms = {obj.get("source_room_id") for obj in bpy.data.objects if obj.get("model_role") == "floor"}
    if room_id not in modeled_rooms:
        raise ValueError(f"Source .blend has no tagged floor for room {room_id}; rebuild it with the current model script")
    wall_keys = set()
    for obj in bpy.data.objects:
        if obj.get("model_role") != "wall":
            continue
        raw_key = obj.get("source_edge_key")
        if not raw_key:
            raise ValueError(f"Source .blend wall {obj.name} lacks CAD edge metadata; rebuild it with the current model script")
        try:
            key = tuple(tuple(float(v) for v in pt) for pt in json.loads(raw_key))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid CAD edge metadata on wall {obj.name}") from exc
        wall_keys.add(key)
    expected_edges = set()
    room_boundary_edges = set()
    expected_visual_openings = 0
    for index, point in enumerate(room["polygon"]):
        next_point = room["polygon"][(index + 1) % len(room["polygon"])]
        boundary_key = edge_key(point, next_point)
        room_boundary_edges.add(boundary_key)
        length = math.dist(point, next_point)
        wall = room.get("walls", [{} for _ in room["polygon"]])[index]
        openings = sorted(wall.get("openings", []), key=lambda op: op["start_mm"])
        expected_visual_openings += sum(1 for opening in openings
                                        if opening.get("kind") in {"door", "window"})
        completely_open = (len(openings) == 1 and openings[0]["start_mm"] <= 1e-6 and
                           openings[0]["width_mm"] >= length - 1e-6 and
                           openings[0].get("sill_mm", 0) <= 1e-6 and
                           openings[0]["height_mm"] >= data.get("wall_height_mm", 2800) - 1e-6)
        if not completely_open:
            expected_edges.add(boundary_key)
    missing_edges = expected_edges - wall_keys
    if missing_edges:
        raise ValueError(f"Source .blend is missing {len(missing_edges)} CAD boundary edges for room {room_id}; rebuild or reconcile geometry")
    modeled_openings = {str(obj.get("source_opening_id")) for obj in bpy.data.objects
                        if obj.get("model_role") == "opening" and
                        obj.get("source_edge_key") and
                        tuple(tuple(float(v) for v in pt)
                              for pt in json.loads(obj.get("source_edge_key"))) in room_boundary_edges}
    if len(modeled_openings) < expected_visual_openings:
        raise ValueError(f"Source .blend has only {len(modeled_openings)} of {expected_visual_openings} modeled door/window openings for room {room_id}")
    expected_furniture = {str(item.get("id", item.get("name", "Furniture")))
                          for item in data.get("furniture", []) if item.get("room_id") == room_id and
                          (item.get("id") or item.get("name"))}
    modeled_furniture = {str(obj.get("source_furniture_id")) for obj in bpy.data.objects
                         if obj.get("model_role") == "furniture" and obj.get("source_room_id") == room_id}
    missing_furniture = expected_furniture - modeled_furniture
    if missing_furniture:
        raise ValueError(f"Source .blend is missing furniture from geometry JSON: {sorted(missing_furniture)}")
    expected_fixed = {str(item["id"]) for item in data.get("fixed_elements", [])
                      if item.get("room_id") == room_id}
    modeled_fixed = {str(obj.get("source_fixed_element_id")) for obj in bpy.data.objects
                     if obj.get("model_role") == "fixed" and obj.get("source_room_id") == room_id}
    missing_fixed = expected_fixed - modeled_fixed
    if missing_fixed:
        raise ValueError(f"Source .blend is missing fixed elements from geometry JSON: {sorted(missing_fixed)}")
    poly_mm = room["polygon"]
    poly = [(point[0] * 0.001, point[1] * 0.001) for point in poly_mm]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = max_x - min_x, max_y - min_y
    wall_h = data.get("wall_height_mm", 2800) * 0.001
    profile = camera_profile_parameters(max(span_x, span_y), wall_h, view_profile)
    margin = max(profile["offset_m"], room.get("wall_thickness_mm", 120) * 0.001)
    west = corner.endswith("w")
    south = corner.startswith("s")
    camera_xy = (min_x - margin if west else max_x + margin,
                 min_y - margin if south else max_y + margin)
    target_xy = polygon_centroid(poly)
    target_z = min(0.65, wall_h * 0.23)
    camera_z = profile["height_m"]
    lens = lens_override if lens_override is not None else profile["lens_mm"]
    target = (target_xy[0], target_xy[1], target_z)

    engines = scene.render.bl_rna.properties["engine"].enum_items.keys()
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = png_path
    scene.render.image_settings.compression = 15
    scene.view_settings.view_transform = "AgX"
    scene.camera = None

    # Limit the render to the selected room and its boundary edges. Shared
    # edges are matched by CAD-space centerline, irrespective of owner room.
    selected_edges = {
        edge_key(poly_mm[i], poly_mm[(i + 1) % len(poly_mm)]) for i in range(len(poly_mm))
    }
    cutaway_edges = set()
    cutaway_edge_indices = []
    if cutaway_corner != "none":
        bbox_corner = (min_x if cutaway_corner.endswith("w") else max_x,
                       min_y if cutaway_corner.startswith("s") else max_y)
        vertex_index = min(range(len(poly_mm)),
                           key=lambda index: math.dist(poly_mm[index], bbox_corner))
        for edge_index in ((vertex_index - 1) % len(poly_mm), vertex_index):
            cutaway_edges.add(edge_key(poly_mm[edge_index], poly_mm[(edge_index + 1) % len(poly_mm)]))
            cutaway_edge_indices.append(edge_index + 1)
    visibility = {}
    for obj in bpy.data.objects:
        role = obj.get("model_role")
        if role in {"floor", "furniture", "fixed"}:
            visible = obj.get("source_room_id") == room_id
        elif role in {"wall", "opening"}:
            try:
                key = tuple(tuple(float(v) for v in pt) for pt in json.loads(obj.get("source_edge_key", "[]")))
                visible = key in selected_edges and key not in cutaway_edges
            except (TypeError, ValueError, json.JSONDecodeError):
                visible = False
        else:
            visible = False
        visibility[obj.name] = obj.hide_render
        obj.hide_render = not visible

    camera_data = bpy.data.cameras.new(f"CAM_{room_id}_{corner.upper()}")
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (camera_xy[0], camera_xy[1], camera_z)
    camera_data.lens = lens
    camera_data.sensor_width = 36
    aim(camera, target)
    scene.camera = camera
    bpy.context.view_layer.update()
    fit_steps = fit_camera_to_points(
        scene, camera, target,
        camera_fit_points(room, list(bpy.data.objects), wall_h, view_profile),
    )

    world = bpy.data.worlds.new("Reference soft world") if scene.world is None else scene.world
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.72, 0.78, 0.84, 1.0)
    background.inputs["Strength"].default_value = 0.32

    # Broad ceiling-like soft source plus a weaker front fill; geometry remains
    # unchanged and all added lights are placed in a dedicated collection.
    light_collection = bpy.data.collections.new("04_参考渲染灯光")
    bpy.context.scene.collection.children.link(light_collection)
    room_area = max(span_x * span_y, 1.0)
    for name, location, power, size in (
        ("Key softbox", (target_xy[0], target_xy[1], max(wall_h * 0.92, 2.2)), 950 * room_area / 12, max(span_x, span_y) * 0.75),
        ("Camera fill", (camera_xy[0], camera_xy[1], max(wall_h * 0.88, 2.0)), 260 * room_area / 12, max(span_x, span_y) * 0.60),
    ):
        light_data = bpy.data.lights.new(name, "AREA")
        light_data.energy = power
        light_data.shape = "DISK"
        light_data.size = size
        light_obj = bpy.data.objects.new(name, light_data)
        light_collection.objects.link(light_obj)
        light_obj.location = location
        aim(light_obj, target)

    report = {
        "schema_version": 1,
        "room_id": room_id,
        "geometry_sha256": scene.get("interior_geometry_sha256"),
        "corner": corner,
        "cutaway_corner": cutaway_corner,
        "view_profile": view_profile,
        "camera_offset_m": round(margin, 4),
        "camera_height_m": round(camera_z, 4),
        "cutaway_room_edge_indices": cutaway_edge_indices,
        "camera_location_m": [round(v, 4) for v in camera.location],
        "camera_target_m": [round(v, 4) for v in target],
        "frame_fit_steps": fit_steps,
        "frame_margin_fraction": 0.08,
        "lens_mm": lens,
        "resolution_px": [width, height],
        "reference_image": png_path,
        "full_geometry_image": full_png_path,
        "top_geometry_image": top_png_path,
        "note": "Blender模型几何参考视图，不是最终写实效果图。剖切透视图仅为观察室内临时隐藏相机侧两段墙；完整墙体透视图可能受近墙遮挡；全墙顶视图用于核验完整边界、洞口与平面布置。请结合三图并保留完整几何。",
    }
    scene["reference_camera_json"] = json.dumps(report, ensure_ascii=False)
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)

    if cutaway_edges:
        for obj in bpy.data.objects:
            if obj.get("model_role") != "wall":
                continue
            raw_key = obj.get("source_edge_key")
            try:
                key = tuple(tuple(float(v) for v in pt) for pt in json.loads(raw_key))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if key in selected_edges:
                obj.hide_render = False
        scene.render.filepath = full_png_path
        bpy.ops.render.render(write_still=True)

    # Plan-view wall symbols come from confirmed geometry so lintels cannot hide
    # door/window positions. Original 3D walls/openings remain intact in the blend.
    camera_snapshot = (camera.data.type, camera.data.ortho_scale, camera.data.lens,
                       camera.location.copy(), camera.rotation_euler.copy())
    top_color_transform = scene.view_settings.view_transform
    top_visibility = {obj.name: obj.hide_render for obj in bpy.data.objects}
    for obj in bpy.data.objects:
        if obj.get("model_role") in {"wall", "opening"}:
            obj.hide_render = True
    top_wall_material = material("Top plan wall bands", (0.16, 0.19, 0.20), roughness=0.92)
    top_window_material = material("Top plan window marks", (0.03, 0.52, 0.57), roughness=0.75)
    top_overlays = build_top_plan_overlays(room, top_wall_material, top_window_material)
    restore_plan_materials = apply_plan_render_materials(data, room_id)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(span_x, span_y * width / height) * 1.16
    camera.location = (target_xy[0], target_xy[1], wall_h + max(span_x, span_y) * 1.6)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    # AgX is preferred for shaded perspective images, but it compresses the
    # pale proxy materials in a plan view until furniture nearly disappears.
    # Use the neutral Standard transform for this temporary orthographic PNG;
    # restore the scene setting immediately afterward so the saved .blend and
    # perspective references keep their original appearance.
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = top_png_path
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)
    restore_plan_materials()
    scene.view_settings.view_transform = top_color_transform
    for obj in top_overlays:
        bpy.data.objects.remove(obj, do_unlink=True)
    for name, hidden in top_visibility.items():
        obj = bpy.data.objects.get(name)
        if obj:
            obj.hide_render = hidden
    camera.data.type, camera.data.ortho_scale, camera.data.lens = camera_snapshot[:3]
    camera.location, camera.rotation_euler = camera_snapshot[3:]

    # Keep all modeled rooms visible when the companion .blend is reopened.
    for name, hidden in visibility.items():
        obj = bpy.data.objects.get(name)
        if obj:
            obj.hide_render = hidden
    report["images_sha256"] = {
        "reference_image": sha256_file(png_path),
        "full_geometry_image": sha256_file(full_png_path),
        "top_geometry_image": sha256_file(top_png_path),
    }
    return report


def main():
    args = parse_args()
    if args.width < 320 or args.height < 240 or (args.lens_mm is not None and
                                                   (not math.isfinite(args.lens_mm) or args.lens_mm <= 0)):
        raise ValueError("Resolution must be at least 320x240 and lens must be positive")
    blend = os.path.abspath(os.path.expanduser(args.blend))
    geometry_path = os.path.abspath(os.path.expanduser(args.geometry))
    output_dir = os.path.abspath(os.path.expanduser(args.output_dir))
    if not os.path.isfile(blend) or not os.path.isfile(geometry_path):
        raise FileNotFoundError("Both source .blend and geometry JSON must exist")
    if os.path.lexists(output_dir):
        raise FileExistsError(f"Refusing to write into existing output directory: {output_dir}")
    with open(geometry_path, "rb") as stream:
        geometry_bytes = stream.read()
    geometry = json.loads(geometry_bytes.decode("utf-8"))
    geometry_hash = hashlib.sha256(geometry_bytes).hexdigest()
    validate_geometry(geometry)
    rooms = {room["id"]: room for room in geometry["rooms"]}
    if args.room_id not in rooms:
        raise ValueError(f"Unknown room id: {args.room_id}")
    targets = {"png": os.path.join(output_dir, f"{args.room_id}-camera-reference.png"),
               "full_png": os.path.join(output_dir, f"{args.room_id}-camera-full-geometry.png"),
               "top_png": os.path.join(output_dir, f"{args.room_id}-camera-top-layout.png"),
               "blend": os.path.join(output_dir, f"{args.room_id}-camera-reference.blend"),
               "json": os.path.join(output_dir, f"{args.room_id}-camera.json")}
    cutaway_corner = args.corner if args.cutaway_corner is None else args.cutaway_corner
    print(json.dumps({"input_blend": blend, "geometry": geometry_path, "room_id": args.room_id,
                      "corner": args.corner, "cutaway_corner": cutaway_corner,
                      "view_profile": args.view_profile, "lens_override_mm": args.lens_mm,
                      "outputs": targets, "execute": args.execute,
                      "approval_required_for_execute": True}, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    if not args.approval:
        raise ValueError("--approval is required for writing Blender camera/reference outputs")
    validate_receipt_snapshot(geometry_path, args.approval, geometry_bytes)
    bpy.ops.wm.open_mainfile(filepath=blend)
    if bpy.context.scene.get("interior_geometry_sha256") != geometry_hash:
        raise ValueError("Geometry JSON does not exactly match the geometry used to build this .blend; rebuild or choose its original JSON")
    parent = os.path.dirname(output_dir)
    os.makedirs(parent, exist_ok=True)
    staging_dir = tempfile.mkdtemp(prefix=f".{os.path.basename(output_dir)}.rendering-", dir=parent)
    staged = {key: os.path.join(staging_dir, os.path.basename(path)) for key, path in targets.items()}
    try:
        report = build_reference(geometry, rooms[args.room_id], args.corner, cutaway_corner,
                                 args.view_profile, args.lens_mm,
                                 args.width, args.height, staged["png"], staged["full_png"], staged["top_png"])
        report["reference_image"] = targets["png"]
        report["full_geometry_image"] = targets["full_png"]
        report["top_geometry_image"] = targets["top_png"]
        report["images_sha256"] = {
            "reference_image": sha256_file(staged["png"]),
            "full_geometry_image": sha256_file(staged["full_png"]),
            "top_geometry_image": sha256_file(staged["top_png"]),
        }
        bpy.context.scene["reference_camera_json"] = json.dumps(report, ensure_ascii=False)
        with open(staged["json"], "x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        bpy.ops.wm.save_as_mainfile(filepath=staged["blend"])
        if any(not os.path.isfile(staged[key]) or os.path.getsize(staged[key]) < 1024
               for key in ("png", "full_png", "top_png", "blend", "json")):
            raise RuntimeError("Reference bundle missing or unexpectedly small")
        if os.path.lexists(output_dir):
            raise FileExistsError(f"Refusing existing output directory: {output_dir}")
        os.rename(staging_dir, output_dir)
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    print(f"ROOM_REFERENCE_OK image={targets['png']} blend={targets['blend']} metadata={targets['json']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ROOM_REFERENCE_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
