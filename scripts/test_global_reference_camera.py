#!/usr/bin/env python3
"""Temporary synthetic Blender smoke for both global reference renders."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from render_blender_global_reference import (
    add_camera, build_top_plan_overlays, fit_perspective, global_cutaway_edges,
    material, object_edge_key, scene_bounds,
)
from render_blender_room_reference import aim, apply_plan_render_materials
from geometry_utils import edge_key


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path,
                        help="optional new workspace work/ directory for visual inspection")
    args = parser.parse_args(argv)
    scene = bpy.context.scene
    # Blender factory-startup includes a visible default cube; remove all
    # startup objects so only deliberately constructed fixture geometry renders.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    engines = scene.render.bl_rna.properties["engine"].enum_items.keys()
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 800, 600
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "AgX"
    scene.render.film_transparent = False
    rooms = [
        {"id": "A", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]},
        {"id": "B", "polygon": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]]},
    ]
    data = {"rooms": rooms, "wall_height_mm": 2800, "furniture": [
        {"id": "T01", "room_id": "A", "name": "table", "type": "table"},
        {"id": "K01", "room_id": "B", "name": "cabinet", "type": "cabinet"},
    ]}
    bounds = scene_bounds(data)
    assert bounds == (0.0, 7.0, 0.0, 3.0), bounds
    cutaways = global_cutaway_edges(data)
    assert len(cutaways) == 4, cutaways
    cx, cy = 3.5, 1.5
    target = (cx, cy, .56)
    wall_h = 2.8
    anchors = [(x, y, z) for room in rooms for x, y in [(p[0]*.001, p[1]*.001) for p in room["polygon"]]
               for z in (0, wall_h)]
    bpy.ops.mesh.primitive_cube_add(size=1, location=(2, 1.5, .4))
    furniture = bpy.context.object
    furniture.dimensions = (1.2, .7, .8)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    anchors.extend(furniture.matrix_world @ Vector(corner) for corner in furniture.bound_box)
    camera_data = bpy.data.cameras.new("TEST_GLOBAL")
    camera = bpy.data.objects.new("TEST_GLOBAL", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (-4, -5, 6)
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    steps = fit_perspective(scene, camera, anchors, target)
    projected = [world_to_camera_view(scene, camera, Vector(p)) for p in anchors]
    assert all(p.z > 0 and .069 <= p.x <= .931 and .069 <= p.y <= .931 for p in projected)
    assert steps > 0
    bpy.data.objects.remove(furniture, do_unlink=True)
    bpy.data.objects.remove(camera, do_unlink=True)
    bpy.data.cameras.remove(camera_data)
    scene.camera = None

    wall_mat = material("Test wall", (.72, .74, .73))
    floor_mat = material("Test floor", (.82, .78, .70))
    furniture_mat = material("Test furniture", (.30, .42, .34))
    for room in rooms:
        cx_room = (min(p[0] for p in room["polygon"]) +
                   max(p[0] for p in room["polygon"])) / 2000
        bpy.ops.mesh.primitive_cube_add(size=1, location=(cx_room, 1.5, -.05))
        floor = bpy.context.object
        floor.name = f"{room['id']}_floor"
        floor.dimensions = (4 if room["id"] == "A" else 3, 3, .1)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        floor.data.materials.append(floor_mat)
        floor["model_role"] = "floor"
        floor["source_room_id"] = room["id"]

    def wall(name, center, dims, source_key):
        bpy.ops.mesh.primitive_cube_add(size=1, location=center)
        obj = bpy.context.object
        obj.name = name
        obj.dimensions = dims
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(wall_mat)
        obj["model_role"] = "wall"
        obj["source_edge_key"] = json.dumps(source_key)
        return obj

    # Outer shell plus a shared partition makes two connected room volumes.
    wall("south_A", (2, 0, 1.4), (4.12, .12, 2.8), edge_key([0, 0], [4000, 0]))
    wall("south_B", (5.5, 0, 1.4), (3.12, .12, 2.8), edge_key([4000, 0], [7000, 0]))
    wall("north_A", (2, 3, 1.4), (4.12, .12, 2.8), edge_key([4000, 3000], [0, 3000]))
    wall("north_B", (5.5, 3, 1.4), (3.12, .12, 2.8), edge_key([7000, 3000], [4000, 3000]))
    wall("outer_west", (0, 1.5, 1.4), (.12, 3, 2.8), edge_key([0, 0], [0, 3000]))
    wall("outer_east", (7, 1.5, 1.4), (.12, 3, 2.8), edge_key([7000, 0], [7000, 3000]))
    wall("shared_partition", (4, 1.5, 1.4), (.12, 3, 2.8), edge_key([4000, 0], [4000, 3000]))
    for name, center, dims, room_id, source_id in (
        ("table", (2, 1.5, .38), (1.2, .7, .76), "A", "T01"),
        ("cabinet", (5.5, 1.5, 1.0), (.8, .5, 2.0), "B", "K01"),
    ):
        bpy.ops.mesh.primitive_cube_add(size=1, location=center)
        obj = bpy.context.object
        obj.name = name
        obj.dimensions = dims
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.data.materials.append(furniture_mat)
        obj["model_role"] = "furniture"
        obj["source_room_id"] = room_id
        obj["source_furniture_id"] = source_id

    center = (3.5, 1.5, .56)
    all_points = [(x, y, z) for room in rooms for x, y in
                  [(p[0] * .001, p[1] * .001) for p in room["polygon"]]
                  for z in (0, 2.8)]
    for obj in bpy.data.objects:
        if obj.get("model_role") == "furniture":
            all_points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)

    def render_and_check(folder):
        camera = add_camera(scene, "GLOBAL_TEST_AXON", (-2.8, -3.4, 6.5), center)
        fit_perspective(scene, camera, all_points, center)
        for obj in bpy.data.objects:
            if obj.get("model_role") == "wall" and object_edge_key(obj) in cutaways:
                obj.hide_render = True
        light_data = bpy.data.lights.new("Test area", "AREA")
        light = bpy.data.objects.new("Test area", light_data)
        scene.collection.objects.link(light)
        light.location = (3.5, -1, 7)
        light_data.energy = 1800
        light_data.size = 6
        aim(light, (3.5, 1.5, 0))
        axon = folder / "global-axonometric.png"
        scene.render.filepath = str(axon)
        bpy.ops.render.render(write_still=True)
        check_image(axon)

        for obj in bpy.data.objects:
            if obj.get("model_role") == "wall":
                obj.hide_render = True
        plan_wall = material("Test plan wall", (.12, .16, .17))
        plan_window = material("Test plan window", (.08, .4, .46))
        for room in rooms:
            build_top_plan_overlays(room, plan_wall, plan_window)
        restore_plan_materials = apply_plan_render_materials(data)
        top = add_camera(scene, "GLOBAL_TEST_TOP", (3.5, 1.5, 10), (3.5, 1.5, 0),
                         ortho_scale=8.1)
        top_color_transform = scene.view_settings.view_transform
        scene.view_settings.view_transform = "Standard"
        scene.render.filepath = str(folder / "global-top-layout.png")
        bpy.ops.render.render(write_still=True)
        restore_plan_materials()
        scene.view_settings.view_transform = top_color_transform
        check_image(folder / "global-top-layout.png")
        return axon, folder / "global-top-layout.png"

    def with_folder():
        if args.artifact_dir:
            folder = args.artifact_dir.expanduser().resolve()
            if folder.exists():
                raise FileExistsError(f"Refusing existing test artifact directory: {folder}")
            folder.mkdir(parents=True)
            return render_and_check(folder), str(folder)
        with tempfile.TemporaryDirectory(prefix="global-reference-render-test-") as temp:
            return render_and_check(Path(temp)), "temporary (auto-cleaned)"

    paths, location = with_folder()
    print("GLOBAL_REFERENCE_RENDER_OK", {"room_count": len(rooms), "anchor_count": len(anchors),
                                         "fit_steps": steps, "cutaway_edges": len(cutaways),
                                         "images": [p.name for p in paths],
                                         "location": location})


def check_image(path):
    image = bpy.data.images.load(str(path), check_existing=False)
    width, height = image.size
    pixels = list(image.pixels[::max(1, int(len(image.pixels) / 5000))])
    bpy.data.images.remove(image)
    if width != 800 or height != 600 or len(set(round(v, 3) for v in pixels)) < 8:
        raise AssertionError(f"Render is blank or has unexpected size: {path}, {width}x{height}")
    if path.stat().st_size < 5000:
        raise AssertionError(f"Render image unexpectedly small: {path}")


if __name__ == "__main__":
    main()
