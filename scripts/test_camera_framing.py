#!/usr/bin/env python3
"""Blender runtime regression for room/furniture camera framing.

Run: blender --background --factory-startup --python test_camera_framing.py
No project or image files are written.
"""
import importlib.util
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from geometry_utils import camera_profile_parameters
from render_blender_room_reference import apply_plan_render_materials

SPEC = importlib.util.spec_from_file_location(
    "render_room_reference", SCRIPT_DIR / "render_blender_room_reference.py")
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    scene = bpy.context.scene
    scene.render.resolution_percentage = 100
    source_mats = {}
    role_objects = {}
    for role, object_id, location in (("floor", None, (0, 0, -0.05)),
                                      ("furniture", "T01", (1, 1, .4)),
                                      ("furniture", "C01", (2, 1, .4)),
                                      ("fixed", "P01", (3, 1, .4))):
        bpy.ops.mesh.primitive_cube_add(size=1, location=location)
        obj = bpy.context.object
        obj.name = f"palette_{role}_{object_id or 'R01'}"
        obj["model_role"] = role
        obj["source_room_id"] = "R01"
        if object_id:
            obj["source_furniture_id" if role == "furniture" else "source_fixed_element_id"] = object_id
        original = bpy.data.materials.new(f"original_{obj.name}")
        obj.data.materials.append(original)
        source_mats[obj.name] = original
        role_objects[role, object_id] = obj
    palette_data = {"furniture": [
        {"id": "T01", "type": "table"}, {"id": "C01", "type": "chair"},
    ]}
    restore_palette = apply_plan_render_materials(palette_data, "R01")
    expected_palette = {("floor", None): "floor", ("furniture", "T01"): "table",
                        ("furniture", "C01"): "chair", ("fixed", "P01"): "fixed"}
    palette_colors = {}
    for key, palette_name in expected_palette.items():
        obj = role_objects[key]
        mat = obj.data.materials[0]
        check(mat.name == f"TEMP plan {palette_name}", f"Wrong top-plan category color for {key}: {mat.name}")
        emission = next(node for node in mat.node_tree.nodes if node.type == "EMISSION")
        palette_colors[palette_name] = tuple(round(value, 3) for value in emission.inputs["Color"].default_value[:3])
    check(len(set(palette_colors.values())) == len(palette_colors),
          f"Top-plan category colors must be distinct: {palette_colors}")
    restore_palette()
    for key, obj in role_objects.items():
        check(len(obj.data.materials) == 1 and obj.data.materials[0] == source_mats[obj.name],
              f"Temporary top-plan material leaked into the saved scene: {obj.name}")
        bpy.data.objects.remove(obj, do_unlink=True)
    print("PLAN_RENDER_PALETTE_OK", palette_colors)

    cases = [
        ("small-square", 3600, 3600, 1280, 800),
        ("narrow", 3200, 2400, 1280, 800),
        ("long", 9000, 4200, 1280, 800),
        ("portrait", 4200, 7000, 800, 1200),
    ]
    results = []
    for case_id, width_mm, depth_mm, res_x, res_y in cases:
        scene.render.resolution_x, scene.render.resolution_y = res_x, res_y
        span = max(width_mm, depth_mm) * 0.001
        room = {"id": case_id, "polygon": [[0, 0], [width_mm, 0],
                                             [width_mm, depth_mm], [0, depth_mm]]}
        target = (width_mm * 0.0005, depth_mm * 0.0005, 0.65)
        # A table-and-chair cluster deliberately extends near the room center,
        # while its proxies remain inside the room boundary.
        bpy.ops.mesh.primitive_cube_add(size=1, location=(target[0], target[1], 0.8))
        furniture = bpy.context.object
        furniture.name = f"{case_id}_furniture"
        furniture.dimensions = (0.9, 0.9, 1.5)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        furniture["model_role"] = "furniture"
        furniture["source_room_id"] = case_id
        for profile in ("interior", "elevated"):
            params = camera_profile_parameters(span, 2.8, profile)
            offset = max(params["offset_m"], 0.12)
            location = (
                -offset if width_mm >= 0 else 0,
                -offset,
                params["height_m"],
            )
            cam_data = bpy.data.cameras.new(f"CAM_{case_id}_{profile}")
            cam_data.lens = params["lens_mm"]
            cam_data.sensor_width = 36
            cam = bpy.data.objects.new(cam_data.name, cam_data)
            scene.collection.objects.link(cam)
            cam.location = location
            cam.rotation_euler = (Vector(target) - cam.location).to_track_quat("-Z", "Y").to_euler()
            scene.camera = cam
            bpy.context.view_layer.update()
            points = RENDERER.camera_fit_points(room, [furniture], 2.8, profile)
            iterations = RENDERER.fit_camera_to_points(scene, cam, target, points)
            projected = [world_to_camera_view(scene, cam, Vector(point)) for point in points]
            check(all(0.079 <= p.x <= 0.921 and 0.079 <= p.y <= 0.921 and p.z > 0
                      for p in projected),
                  f"Framing excludes required {profile} points for {case_id}")
            expected_count = (8 if profile == "elevated" else 4) + 8
            check(len(points) == expected_count,
                  f"Unexpected frame anchors for {case_id}/{profile}: {len(points)}")
            results.append({"case": case_id, "profile": profile, "fit_steps": iterations})
            bpy.data.objects.remove(cam, do_unlink=True)
            bpy.data.cameras.remove(cam_data)
        bpy.data.objects.remove(furniture, do_unlink=True)

    print("CAMERA_FRAMING_OK", results)


if __name__ == "__main__":
    main()
