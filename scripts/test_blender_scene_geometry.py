#!/usr/bin/env python3
"""Blender smoke tests for materials, door swings, furniture bounds and GLB export.

Run with: blender -b --python /absolute/path/test_blender_scene_geometry.py
This test writes no project files; its GLB round-trip fixture lives in a temp directory.
"""
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import tempfile

import bpy
from mathutils import Vector

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("build_blender_scene", SCRIPT_DIR / "build_blender_scene.py")
SCENE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCENE)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def world_bounds(obj):
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return tuple((min(point[axis] for point in points), max(point[axis] for point in points)) for axis in range(3))


def mesh_local_size(obj):
    return tuple(max(vertex.co[axis] for vertex in obj.data.vertices) -
                 min(vertex.co[axis] for vertex in obj.data.vertices) for axis in range(3))


def main():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    materials = {
        "frame": SCENE.material("test-frame", (0.2, 0.2, 0.2)),
        "door": SCENE.material("test-door", (0.5, 0.3, 0.2)),
        "glass": SCENE.material("test-glass", (0.3, 0.4, 0.5)),
    }
    furniture_materials = {
        "wood": SCENE.material("test-wood", (0.4, 0.25, 0.15)),
        "dark": SCENE.material("test-dark", (0.12, 0.12, 0.12)),
        "upholstery": SCENE.material("test-upholstery", (0.4, 0.5, 0.4)),
        "green": SCENE.material("test-green", (0.05, 0.3, 0.18)),
        "green_light": SCENE.material("test-green-light", (0.2, 0.5, 0.3)),
        "stone": SCENE.material("test-stone", (0.6, 0.6, 0.6)),
        "ceramic": SCENE.material("test-ceramic", (0.9, 0.9, 0.88)),
        "appliance": SCENE.material("test-appliance", (0.45, 0.47, 0.48)),
    }
    for mat in materials.values():
        shader = next((node for node in mat.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        output = next((node for node in mat.node_tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None)
        check(shader, f"Principled shader missing on {mat.name}")
        check(any(link.from_node == shader and link.to_node == output for link in mat.node_tree.links), f"Material output link missing on {mat.name}")

    room = {"id": "RTEST", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}
    cases = [
        ("start", "left", 1),
        ("start", "right", -1),
        ("end", "left", 1),
        ("end", "right", -1),
    ]
    for index, (hinge, side, sign) in enumerate(cases, start=1):
        local_room = {**room, "id": f"R{index:02d}"}
        opening = {"kind": "door", "start_mm": 500, "width_mm": 900,
                   "sill_mm": 0, "height_mm": 2100,
                   "door_swing": {"hinge": hinge, "side": side}}
        SCENE.build_opening_visuals(local_room, 0, (0, 0), (4000, 0), opening, 1, 0.12, materials)
        leaf = bpy.data.objects.get(f"{local_room['id']}_E1_O1_door_leaf")
        check(leaf is not None, f"Door leaf missing for {hinge}/{side}")
        check(leaf.location.y * sign > 0, f"Door leaf opened to wrong side for {hinge}/{side}")
        check(leaf.get("source_door_hinge") == hinge and leaf.get("source_door_swing_side") == side,
              f"Door source metadata missing for {hinge}/{side}")
        check(abs(leaf.dimensions.x - 0.85) < 1e-5, f"Door leaf width is incorrect for {hinge}/{side}")

    SCENE.build_opening_visuals(room, 0, (0, 0), (4000, 0),
        {"kind": "door", "start_mm": 1800, "width_mm": 900, "sill_mm": 0, "height_mm": 2100},
        2, 0.12, materials)
    check(bpy.data.objects.get("RTEST_E1_O2_door_leaf") is None, "Unconfirmed door swing should not invent a leaf")

    SCENE.build_opening_visuals(room, 1, (4000, 0), (4000, 3000),
        {"kind": "window", "start_mm": 300, "width_mm": 1200, "sill_mm": 900, "height_mm": 1200},
        1, 0.12, materials)
    window_ids = [obj.name for obj in bpy.data.objects if "RTEST_E2_O1" in obj.name]
    check(any("tinted_glazing_proxy" in name for name in window_ids), "Window glazing proxy missing")
    check(not any("door_leaf" in name for name in window_ids), "Window was misclassified as a swinging door")
    window_parts = {obj.name.rsplit("_", 1)[-1]: obj for obj in bpy.data.objects if "RTEST_E2_O1" in obj.name}
    glass = next(obj for obj in window_parts.values() if "tinted_glazing_proxy" in obj.name)
    check(all(abs(actual - expected) < 1e-5 for actual, expected in zip(mesh_local_size(glass), (1.1, 0.0192, 1.1))),
          f"Window glazing proxy dimensions do not match opening geometry: {mesh_local_size(glass)}")
    check(glass.get("source_opening_kind") == "window" and glass.get("source_edge_index") == 2,
          "Window proxy lost its source opening/edge metadata")

    # Verify that wall meshes leave clear volumes for a doorway and a window.
    floor_material = SCENE.material("test-floor", (0.8, 0.8, 0.8))
    wall_material = SCENE.material("test-wall", (0.7, 0.7, 0.7))
    door_geometry = {"id": "DOOR_CUT", "polygon": room["polygon"], "wall_thickness_mm": 120,
        "walls": [{"openings": [{"kind": "door", "start_mm": 500, "width_mm": 900,
                                   "sill_mm": 0, "height_mm": 2100}]}, {}, {}, {}]}
    SCENE.build_room(door_geometry, 2800, floor_material, wall_material,
                     SCENE.shared_edge_owners({"rooms": [door_geometry]}), materials)
    door_walls = [obj for obj in bpy.data.objects if obj.get("model_role") == "wall"
                  and obj.get("source_room_id") == "DOOR_CUT" and obj.get("source_edge_index") == 1]
    check(len(door_walls) == 3, f"Door wall segmentation should create two jamb sides and a lintel, got {len(door_walls)}")
    for obj in door_walls:
        bounds = world_bounds(obj)
        intersects_door = min(bounds[0][1], 1.4) - max(bounds[0][0], 0.5) > 1e-5
        if intersects_door:
            check(bounds[2][0] >= 2.1 - 1e-5, f"Wall mesh blocks the door opening below its head: {obj.name} {bounds}")

    window_geometry = {"id": "WINDOW_CUT", "polygon": room["polygon"], "wall_thickness_mm": 120,
        "walls": [{}, {"openings": [{"kind": "window", "start_mm": 300, "width_mm": 1200,
                                    "sill_mm": 900, "height_mm": 1200}]}, {}, {}]}
    SCENE.build_room(window_geometry, 2800, floor_material, wall_material,
                     SCENE.shared_edge_owners({"rooms": [window_geometry]}), materials)
    window_walls = [obj for obj in bpy.data.objects if obj.get("model_role") == "wall"
                    and obj.get("source_room_id") == "WINDOW_CUT" and obj.get("source_edge_index") == 2]
    for obj in window_walls:
        bounds = world_bounds(obj)
        overlap = [min(bounds[axis][1], upper) - max(bounds[axis][0], lower)
                   for axis, lower, upper in ((1, 0.3, 1.5), (2, 0.9, 2.1))]
        check(not all(size > 1e-5 for size in overlap),
              f"Wall mesh blocks the window opening: {obj.name} {bounds}")

    furniture_cases = [
        ("box", [600, 500, 2000], 1),
        ("table", [1600, 900, 750], 5),
        ("mahjong_table", [900, 900, 760], 6),
        ("chair", [600, 600, 900], 6),
        ("armchair", [700, 700, 950], 12),
        ("sofa", [2200, 900, 850], 9),
        ("bed", [2000, 1800, 600], 3),
        ("sink", [600, 600, 900], 3),
        ("toilet", [380, 700, 800], 4),
        ("refrigerator", [800, 750, 1900], 3),
        ("cooktop", [600, 600, 80], 5),
    ]
    proxy_counts = {}
    expected_proxy_names = []
    expected_proxy_sources = {}
    for kind, size, expected_parts in furniture_cases:
        item = {"id": f"F_{kind}", "type": kind, "center_mm": [2000, 1500],
                "size_mm": size, "rotation_deg": 27}
        made = SCENE.build_furniture(item, furniture_materials)
        for obj in made:
            obj["model_role"] = "furniture"
            obj["source_room_id"] = "RTEST"
            obj["source_furniture_id"] = item["id"]
        expected_proxy_names.extend(obj.name for obj in made)
        expected_proxy_sources.update({obj.name: item["id"] for obj in made})
        proxy_counts[kind] = len(made)
        check(made, f"No Blender proxy objects generated for {kind}")
        check(len(made) == expected_parts, f"Unexpected {kind} component count: {len(made)}")
        bpy.context.view_layer.update()
        width, depth, height = (value * 0.001 for value in size)
        angle = math.radians(item["rotation_deg"])
        cx, cy = (value * 0.001 for value in item["center_mm"])
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj in made:
            check(obj.type == "MESH" and obj.data.materials, f"Invalid mesh/material on {kind} object {obj.name}")
            if any(mod.type == "BEVEL" for mod in obj.modifiers):
                check(any(mod.type == "WEIGHTED_NORMAL" for mod in obj.modifiers),
                      f"Weighted normals missing on beveled furniture part {obj.name}")
            epsilon = 1e-5
            evaluated = obj.evaluated_get(depsgraph)
            evaluated_mesh = evaluated.to_mesh()
            try:
                for vertex in evaluated_mesh.vertices:
                    world = evaluated.matrix_world @ vertex.co
                    dx, dy = world.x - cx, world.y - cy
                    local_x = dx * math.cos(angle) + dy * math.sin(angle)
                    local_y = -dx * math.sin(angle) + dy * math.cos(angle)
                    check(-width / 2 - epsilon <= local_x <= width / 2 + epsilon and
                          -depth / 2 - epsilon <= local_y <= depth / 2 + epsilon and
                          -epsilon <= world.z <= height + epsilon,
                          f"{kind} evaluated vertex escapes declared footprint/height: {obj.name}")
            finally:
                evaluated.to_mesh_clear()
        if kind == "toilet":
            check(any("bowl" in obj.name for obj in made) and any("cistern" in obj.name for obj in made), "Toilet bowl/cistern missing")
        if kind == "refrigerator":
            check(any("handle" in obj.name for obj in made), "Refrigerator handle missing")
        if kind == "cooktop":
            check(sum("burner" in obj.name for obj in made) == 4, "Cooktop needs four burner markers")
        if kind == "sofa":
            check(sum("seat_cushion_" in obj.name for obj in made) == 3,
                  "Wide sofa should have three dimension-bounded seat cushions")
            check(sum("back_cushion_" in obj.name for obj in made) == 3,
                  "Wide sofa should have aligned segmented back cushions")
        if kind == "armchair":
            check(sum("arm_" in obj.name and "support" not in obj.name for obj in made) == 2,
                  "Armchair should have two armrests")
            check(sum("arm_support_" in obj.name for obj in made) == 4,
                  "Armrest boards must connect down to the seat frame")

    with tempfile.TemporaryDirectory(prefix="interior-proxy-glb-") as temp_dir:
        glb_path = Path(temp_dir) / "furniture-proxies.glb"
        bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", use_selection=False,
                                  export_extras=True)
        payload = glb_path.read_bytes()
        check(payload[:4] == b"glTF", "GLB magic header missing")
        version, total_length = struct.unpack_from("<II", payload, 4)
        chunk_length, chunk_type = struct.unpack_from("<I4s", payload, 12)
        check(version == 2 and total_length == len(payload), "Invalid GLB header/length")
        check(chunk_type == b"JSON", "GLB JSON chunk missing")
        gltf = json.loads(payload[20:20 + chunk_length].decode("utf-8").rstrip(" \0"))
        glb_nodes = {node.get("name") for node in gltf.get("nodes", [])}
        glb_node_records = gltf.get("nodes", [])
        glb_materials = {material.get("name") for material in gltf.get("materials", [])}
        missing_nodes = set(expected_proxy_names) - glb_nodes
        check(not missing_nodes, f"Furniture proxy components missing from GLB: {sorted(missing_nodes)}")
        for name in expected_proxy_names:
            candidates = [node.get("extras", {}) for node in glb_node_records
                          if node.get("name") == name or node.get("name") == f"{name}_mesh"]
            expected_id = expected_proxy_sources[name]
            check(any(extras.get("model_role") == "furniture" and
                      extras.get("source_room_id") == "RTEST" and
                      extras.get("source_furniture_id") == expected_id for extras in candidates),
                  f"GLB source metadata missing for {name}: {candidates}; nodes=" +
                  repr([(node.get("name"), node.get("extras")) for node in glb_node_records]))
        check({mat.name for mat in furniture_materials.values()} <= glb_materials,
              "One or more furniture materials were not exported to GLB")

    print("BLENDER_SCENE_GEOMETRY_OK " + json.dumps({
        "blender_version": bpy.app.version_string,
        "door_swing_cases": len(cases),
        "unconfirmed_door_leaf": "omitted",
        "window_glazing_proxy": "present",
        "material_nodes": "linked",
        "furniture_proxy_object_counts": proxy_counts,
        "glb_nodes": len(glb_nodes),
        "glb_materials": len(glb_materials),
        "glb_proxy_components_verified": len(expected_proxy_names),
        "glb_node_source_metadata_verified": len(expected_proxy_names),
        "object_count": len(bpy.data.objects),
        "files_written": 0,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
