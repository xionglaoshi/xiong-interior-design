#!/usr/bin/env python3
"""Build a Blender scene and GLB from the confirmed v1 interior geometry JSON."""
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
import bpy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geometry_utils import door_leaf_pose, edge_key, shared_edge_owners, validate_geometry
from geometry_approval import validate_receipt_snapshot
from viewer_assets import VIEWER_FILES, copy_viewer_assets, render_viewer_page
from atomic_output_bundle import commit_output_bundle, preflight_output_bundle


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--blend-name", default="interior-scene.blend")
    parser.add_argument("--glb-name", default="interior-scene.glb")
    parser.add_argument("--viewer-name", default="index.html")
    parser.add_argument("--approval", help="receipt JSON for explicit approval of this exact geometry version")
    parser.add_argument("--execute", action="store_true", help="write outputs; default is preview only")
    parser.add_argument("--force", action="store_true", help="allow replacing existing output files")
    return parser.parse_args(argv)


def validate(data):
    validate_geometry(data)


def build_delivery_note(data, geometry_sha256, blend_name, glb_name, viewer_name):
    rooms = "\n".join(f"- {room['id']}：{room.get('name', '未命名房间')}" for room in data["rooms"])
    return f"""# {data.get('project', '室内方案')}｜三维模型包使用说明

## 文件

- `{blend_name}`：Blender 可编辑源场景。
- `{glb_name}`：浏览器查看的三维模型数据。
- `{viewer_name}`：交互查看页面。
- `model-viewer-4.3.1.min.js`：本地查看器运行库；其余许可文件随包附带。

## 浏览器查看

在此目录启动本机静态服务：

```sh
python3 -m http.server 8890 --bind 127.0.0.1
```

然后打开 `http://127.0.0.1:8890/{Path(viewer_name).name}`。页面支持房间聚焦、顶视/斜视切换、旋转和缩放；不依赖外网。浏览器关闭墙体只改变显示，不修改模型文件。

## 本模型内容

几何版本 SHA-256：`{geometry_sha256}`

房间：
{rooms}

## 使用边界

本模型是基于上述已确认几何制作的空间关系草模，家具/设备为简化代理体。配套简易 DXF 和本模型均为设计沟通参考，不是施工图、结构结论或法规/消防/机电审查结果。施工前由设计师回到原始 CAD 和现场复核尺寸、结构及专业条件。
"""


def material(name, color, roughness=0.82):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (*color, 1.0)
    nodes = mat.node_tree.nodes
    shader = next((node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    output = next((node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None)
    shader = shader or nodes.new("ShaderNodeBsdfPrincipled")
    output = output or nodes.new("ShaderNodeOutputMaterial")
    if not any(link.from_node == shader and link.to_node == output for link in mat.node_tree.links):
        mat.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Base Color"].default_value = (*color, 1.0)
    shader.inputs["Roughness"].default_value = roughness
    return mat


def box(name, center, dimensions, mat, angle=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = f"{name}_mesh"
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.rotation_euler.z = angle
    obj.data.materials.append(mat)
    return obj


def build_room(room, wall_height, floor_mat, wall_mat, edge_owners, opening_materials):
    scale = 0.001
    pts = [(p[0] * scale, p[1] * scale) for p in room["polygon"]]
    mesh = bpy.data.meshes.new(f"{room['id']}_floor_mesh")
    mesh.from_pydata([(x, y, 0) for x, y in pts], [], [list(range(len(pts)))])
    mesh.materials.append(floor_mat)
    floor = bpy.data.objects.new(f"{room['id']}_{room.get('name', 'room')}_floor", mesh)
    floor["model_role"] = "floor"
    floor["source_room_id"] = room["id"]
    bpy.context.collection.objects.link(floor)
    bpy.context.view_layer.objects.active = floor
    floor.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    floor.select_set(False)

    walls = room.get("walls", [{} for _ in pts])
    height = wall_height * scale
    thick = room.get("wall_thickness_mm", 120) * scale
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        if edge_owners[edge_key(room["polygon"][i], room["polygon"][(i + 1) % len(pts)])] != (room["id"], i):
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        openings = sorted(walls[i].get("openings", []), key=lambda o: o["start_mm"])
        cursor = 0.0
        segments = []
        for opening in openings:
            start = opening["start_mm"] * scale
            end = start + opening["width_mm"] * scale
            sill = opening.get("sill_mm", 0) * scale
            top = sill + opening["height_mm"] * scale
            if start > cursor:
                segments.append((cursor, start, 0.0, height))
            if sill > 0:
                segments.append((start, end, 0.0, sill))
            if top < height:
                segments.append((start, end, top, height))
            cursor = end
        if cursor < length:
            segments.append((cursor, length, 0.0, height))
        for j, (s0, s1, z0, z1) in enumerate(segments):
            if s1 <= s0 or z1 <= z0:
                continue
            mid = (s0 + s1) / 2
            cx = a[0] + math.cos(angle) * mid
            cy = a[1] + math.sin(angle) * mid
            wall_obj = box(f"{room['id']}_wall_{i + 1}_{j + 1}",
                           (cx, cy, (z0 + z1) / 2), (s1 - s0, thick, z1 - z0), wall_mat, angle)
            wall_obj["model_role"] = "wall"
            wall_obj["source_room_id"] = room["id"]
            wall_obj["source_edge_index"] = i + 1
            wall_obj["source_segment_index"] = j + 1
            wall_obj["source_edge_key"] = json.dumps(
                edge_key(room["polygon"][i], room["polygon"][(i + 1) % len(pts)])
            )

        for opening_index, opening in enumerate(openings, start=1):
            if opening.get("kind") in {"door", "window"}:
                build_opening_visuals(room, i, a, b, opening, opening_index, thick,
                                      opening_materials)


def build_opening_visuals(room, edge_index, a, b, opening, opening_index, wall_thickness, materials):
    """Add opening trim; draw a door leaf only when its swing is specified."""
    scale = 0.001
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    angle = math.atan2(dy, dx)
    start = float(opening["start_mm"]) * scale
    width = float(opening["width_mm"]) * scale
    sill = float(opening.get("sill_mm", 0)) * scale
    height = float(opening["height_mm"]) * scale
    frame = min(0.05, width * 0.12, height * 0.12)
    frame_depth = min(max(wall_thickness * 0.65, 0.035), 0.10)
    center_distance = start + width / 2
    opening_id = f"{room['id']}_E{edge_index + 1}_O{opening_index}"
    edge_source = json.dumps(edge_key(room["polygon"][edge_index],
                                      room["polygon"][(edge_index + 1) % len(room["polygon"])]))

    def part(name, distance, z, dimensions, mat):
        cx = a[0] + ux * distance + nx * 0.0
        cy = a[1] + uy * distance + ny * 0.0
        obj = box(f"{opening_id}_{name}", (cx, cy, z), dimensions, mat, angle)
        obj["model_role"] = "opening"
        obj["source_room_id"] = room["id"]
        obj["source_opening_id"] = opening_id
        obj["source_opening_kind"] = opening["kind"]
        obj["source_edge_index"] = edge_index + 1
        obj["source_edge_key"] = edge_source
        return obj

    if opening["kind"] == "door":
        for side, distance in (("jamb_start", start + frame / 2),
                               ("jamb_end", start + width - frame / 2)):
            part(side, distance, height / 2, (frame, frame_depth, height), materials["frame"])
        part("lintel", center_distance, height - frame / 2,
             (width, frame_depth, frame), materials["frame"])
        swing = opening.get("door_swing")
        if swing:
            leaf_center_mm, leaf_length_mm, leaf_angle = door_leaf_pose(
                a, b, opening["start_mm"], opening["width_mm"], frame / scale,
                swing["hinge"], swing["side"],
            )
            leaf_width = leaf_length_mm * scale
            leaf_depth = min(max(frame_depth * 0.4, 0.025), 0.045)
            leaf_center = (
                leaf_center_mm[0] * scale,
                leaf_center_mm[1] * scale,
                max(0.025, (height - frame / 2) / 2),
            )
            leaf = box(f"{opening_id}_door_leaf", leaf_center,
                       (leaf_width, leaf_depth, max(height - frame / 2, 0.05)),
                       materials["door"], leaf_angle)
            leaf["model_role"] = "opening"
            leaf["source_room_id"] = room["id"]
            leaf["source_opening_id"] = opening_id
            leaf["source_opening_kind"] = "door"
            leaf["source_edge_index"] = edge_index + 1
            leaf["source_edge_key"] = edge_source
            leaf["source_door_hinge"] = swing["hinge"]
            leaf["source_door_swing_side"] = swing["side"]
        return

    if opening["kind"] == "window":
        pane_height = max(height - 2 * frame, 0.01)
        pane_width = max(width - 2 * frame, 0.01)
        pane_depth = min(max(wall_thickness * 0.16, 0.012), 0.025)
        part("jamb_start", start + frame / 2, sill + height / 2,
             (frame, frame_depth, height), materials["frame"])
        part("jamb_end", start + width - frame / 2, sill + height / 2,
             (frame, frame_depth, height), materials["frame"])
        part("sill_frame", center_distance, sill + frame / 2,
             (width, frame_depth, frame), materials["frame"])
        part("head_frame", center_distance, sill + height - frame / 2,
             (width, frame_depth, frame), materials["frame"])
        part("tinted_glazing_proxy", center_distance, sill + height / 2,
             (pane_width, pane_depth, pane_height), materials["glass"])


def build_fixed_element(item, mat):
    scale = 0.001
    cx, cy = (value * scale for value in item["center_mm"])
    width, depth, height = (value * scale for value in item["size_mm"])
    base_z = item.get("base_z_mm", 0) * scale
    obj = box(f"{item['id']}_{item.get('name', item['type'])}",
              (cx, cy, base_z + height / 2), (width, depth, height), mat,
              math.radians(item.get("rotation_deg", 0)))
    obj["model_role"] = "fixed"
    obj["source_fixed_element_id"] = item["id"]
    obj["source_fixed_element_type"] = item["type"]
    obj["source_room_id"] = item["room_id"]
    return obj


def polygon_center(poly):
    area2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] -
                poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
    if abs(area2) < 1e-9:
        return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    cx = sum((poly[i][0] + poly[(i + 1) % len(poly)][0]) *
             (poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1])
             for i in range(len(poly))) / (3 * area2)
    cy = sum((poly[i][1] + poly[(i + 1) % len(poly)][1]) *
             (poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1])
             for i in range(len(poly))) / (3 * area2)
    return cx, cy


def build_furniture(item, materials):
    """Create a simple, dimension-faithful furniture proxy from its footprint."""
    scale = 0.001
    cx, cy = (v * scale for v in item["center_mm"])
    width, depth, height = (v * scale for v in item["size_mm"])
    angle = math.radians(item.get("rotation_deg", 0))
    kind = item.get("type", "box")
    base_name = item.get("id", item.get("name", "Furniture"))

    def part(suffix, offset, dims, mat):
        ox, oy, oz = offset
        rx = ox * math.cos(angle) - oy * math.sin(angle)
        ry = ox * math.sin(angle) + oy * math.cos(angle)
        obj = box(f"{base_name}_{suffix}", (cx + rx, cy + ry, oz), dims, mat, angle)
        bevel = obj.modifiers.new("Soft furniture edges", "BEVEL")
        bevel.width = min(0.012, min(dims) * 0.12)
        bevel.segments = 3
        bevel.limit_method = "ANGLE"
        bevel.angle_limit = math.radians(30)
        bevel.harden_normals = True
        normal = obj.modifiers.new("Weighted furniture normals", "WEIGHTED_NORMAL")
        normal.keep_sharp = True
        return obj

    def ellipsoid(suffix, offset, dims, mat):
        ox, oy, oz = offset
        rx = ox * math.cos(angle) - oy * math.sin(angle)
        ry = ox * math.sin(angle) + oy * math.cos(angle)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1,
                                             location=(cx + rx, cy + ry, oz))
        obj = bpy.context.object
        obj.name = f"{base_name}_{suffix}"
        obj.data.name = f"{obj.name}_mesh"
        obj.dimensions = dims
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        obj.rotation_euler.z = angle
        obj.data.materials.append(mat)
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        return obj

    def cylinder(suffix, offset, radius, depth, mat):
        ox, oy, oz = offset
        rx = ox * math.cos(angle) - oy * math.sin(angle)
        ry = ox * math.sin(angle) + oy * math.cos(angle)
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=radius, depth=depth,
                                            location=(cx + rx, cy + ry, oz))
        obj = bpy.context.object
        obj.name = f"{base_name}_{suffix}"
        obj.data.name = f"{obj.name}_mesh"
        obj.rotation_euler.z = angle
        obj.data.materials.append(mat)
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        return obj

    body = materials["wood"]
    if kind in {"box", "cabinet"}:
        return [part("body", (0, 0, height / 2), (width, depth, height), body)]

    if kind == "sink":
        # Visual proxy only: plumbing, faucet, bowl cutout and product details are omitted.
        cabinet_h = min(height * 0.78, max(height * 0.62, height - 0.18))
        rim_h = min(0.045, height * 0.06)
        basin_h = min(0.11, max(0.06, height * 0.16))
        rim_z = max(cabinet_h + rim_h / 2, height - rim_h / 2)
        basin_z = max(cabinet_h + 0.025, rim_z - basin_h / 2)
        return [
            part("cabinet", (0, 0, cabinet_h / 2), (width, depth, cabinet_h), body),
            part("counter_rim", (0, 0, rim_z), (width, depth, rim_h), materials["stone"]),
            part("basin_proxy", (0, 0, basin_z), (width * 0.62, depth * 0.58, basin_h), materials["dark"]),
        ]

    if kind == "toilet":
        # Rounded bowl faces local -Y; cistern is at the back (+Y).
        return [
            part("cistern", (0, depth * 0.34, height * 0.67),
                 (width * 0.82, depth * 0.25, height * 0.66), materials["ceramic"]),
            ellipsoid("pedestal", (0, -depth * 0.08, height * 0.20),
                      (width * 0.62, depth * 0.62, height * 0.38), materials["ceramic"]),
            ellipsoid("bowl", (0, -depth * 0.12, height * 0.39),
                      (width * 0.92, depth * 0.68, height * 0.23), materials["ceramic"]),
            ellipsoid("seat", (0, -depth * 0.12, height * 0.52),
                      (width * 0.88, depth * 0.65, max(height * 0.055, 0.025)), materials["ceramic"]),
        ]

    if kind == "refrigerator":
        door_depth = min(0.045, depth * 0.06)
        handle_depth = min(0.025, depth * 0.15)
        handle_h = height * 0.28
        return [
            part("cabinet", (0, 0, height / 2), (width, depth, height), materials["appliance"]),
            part("door_front", (0, -depth / 2 + door_depth / 2, height / 2),
                 (width * 0.96, door_depth, height * 0.98), materials["stone"]),
            part("handle", (width * 0.40, -depth / 2 + handle_depth / 2, height * 0.55),
                 (max(0.018, width * 0.025), handle_depth, handle_h), materials["dark"]),
        ]

    if kind == "cooktop":
        top_h = min(0.06, height * 0.35)
        made = [part("body", (0, 0, height - top_h / 2),
                     (width, depth, top_h), materials["appliance"])]
        burner_r = min(width, depth) * 0.105
        burner_z = max(top_h / 2 + 0.002, height - 0.006)
        for sx in (-1, 1):
            for sy in (-1, 1):
                made.append(cylinder(f"burner_{sx}_{sy}",
                                     (sx * width * 0.25, sy * depth * 0.25, burner_z),
                                     burner_r, 0.012, materials["dark"]))
        return made

    if kind in {"table", "mahjong_table"}:
        top_h = min(0.075, height * 0.12)
        leg_w = min(0.075, width * 0.09, depth * 0.09)
        top_mat = materials["green"] if kind == "mahjong_table" else body
        made = [part("top", (0, 0, height - top_h / 2), (width, depth, top_h), top_mat)]
        inset = leg_w * 0.9
        leg_h = max(height - top_h, 0.02)
        for sx in (-1, 1):
            for sy in (-1, 1):
                made.append(part(f"leg_{sx}_{sy}",
                                 (sx * (width / 2 - inset), sy * (depth / 2 - inset), leg_h / 2),
                                 (leg_w, leg_w, leg_h), materials["dark"]))
        if kind == "mahjong_table":
            made.append(part("playing_surface", (0, 0, height - top_h * 0.42),
                             (width * 0.78, depth * 0.78, top_h * 0.12), materials["green_light"]))
        return made

    if kind in {"chair", "armchair"}:
        seat_h = height * 0.46
        seat_t = max(0.045, height * 0.08)
        leg_w = min(0.055, width * 0.12, depth * 0.12)
        arm_w = min(0.07, width * 0.11) if kind == "armchair" else 0
        seat_width = width - 2 * arm_w
        made = [part("seat", (0, 0, seat_h), (seat_width, depth, seat_t), materials["upholstery"]),
                part("back", (0, depth * 0.42, seat_h + height * 0.25),
                     (seat_width, max(0.045, depth * 0.16), height * 0.48), materials["upholstery"])]
        leg_h = max(seat_h - seat_t / 2, 0.02)
        for sx in (-1, 1):
            for sy in (-1, 1):
                made.append(part(f"leg_{sx}_{sy}",
                                 (sx * (width / 2 - leg_w / 2), sy * (depth / 2 - leg_w / 2), leg_h / 2),
                                 (leg_w, leg_w, leg_h), materials["wood"]))
        if kind == "armchair":
            arm_h = height * 0.07
            arm_z = min(height - arm_h / 2,
                        seat_h + seat_t / 2 + height * 0.11 + arm_h / 2)
            arm_bottom = arm_z - arm_h / 2
            seat_top = seat_h + seat_t / 2
            support_h = max(arm_bottom - seat_top, 0.02)
            for sx in (-1, 1):
                made.append(part(f"arm_{sx}", (sx * (width / 2 - arm_w / 2), 0, arm_z),
                                 (arm_w, depth * 0.74, arm_h), materials["upholstery"]))
                for sy in (-1, 1):
                    made.append(part(f"arm_support_{sx}_{sy}",
                                     (sx * (width / 2 - arm_w / 2), sy * depth * 0.26,
                                      seat_top + support_h / 2),
                                     (arm_w * 0.72, max(depth * 0.10, 0.025), support_h), materials["wood"]))
        return made

    if kind == "sofa":
        seat_h = height * 0.34
        cushion_h = height * 0.16
        arm_w = min(width * 0.13, 0.22)
        usable_width = width - 2 * arm_w
        cushion_count = max(1, min(3, int(usable_width / 0.65 + 0.5)))
        cushion_gap = min(0.018, usable_width / (cushion_count * 4))
        cushion_width = (usable_width - cushion_gap * (cushion_count - 1)) / cushion_count
        made = [part("base", (0, 0, seat_h / 2), (width, depth, seat_h), materials["wood"])]
        for index in range(cushion_count):
            x = (index - (cushion_count - 1) / 2) * (cushion_width + cushion_gap)
            number = index + 1
            made.append(part(f"seat_cushion_{number}", (x, -depth * 0.04, seat_h + cushion_h / 2),
                             (cushion_width, depth * 0.82, cushion_h), materials["upholstery"]))
            made.append(part(f"back_cushion_{number}", (x, depth * 0.37, height * 0.68),
                             (cushion_width, depth * 0.22, height * 0.60), materials["upholstery"]))
        arm_h = height * 0.66
        for sx in (-1, 1):
            made.append(part(f"arm_{sx}", (sx * (width / 2 - arm_w / 2), 0, arm_h / 2),
                             (arm_w, depth, arm_h), materials["upholstery"]))
        return made

    if kind == "bed":
        frame_h = height * 0.22
        mattress_h = height * 0.30
        made = [part("frame", (0, 0, frame_h / 2), (width, depth, frame_h), materials["wood"]),
                part("mattress", (0, 0, frame_h + mattress_h / 2),
                     (width * 0.96, depth * 0.96, mattress_h), materials["upholstery"]),
                part("headboard", (0, depth * 0.48, height * 0.60),
                     (width, max(0.055, depth * 0.04), height * 0.48), materials["wood"])]
        return made

    raise ValueError(f"Unsupported furniture type: {kind}")


def organize_scene(data):
    """Create editable, per-role/per-room collection groups and source tags."""
    root = bpy.data.collections.new("室内方案")
    bpy.context.scene.collection.children.link(root)
    roles = {
        "floor": "01_地面",
        "wall": "02_墙体",
        "furniture": "03_家具占位",
        "fixed": "04_固定构件",
        "opening": "05_门窗构件",
    }
    groups = {}
    room_names = {room["id"]: room.get("name", "") for room in data["rooms"]}
    for role, group_name in roles.items():
        group = bpy.data.collections.new(group_name)
        root.children.link(group)
        for room_id, room_name in room_names.items():
            role_prefix = group_name.split("_", 1)[0]
            sub = bpy.data.collections.new(f"{role_prefix}_{room_id}_{room_name or 'room'}")
            group.children.link(sub)
            groups[(role, room_id)] = sub

    for obj in list(bpy.data.objects):
        role = obj.get("model_role")
        room_id = obj.get("source_room_id")
        if role == "furniture":
            role = "furniture"
        if (role, room_id) not in groups:
            raise ValueError(f"Cannot assign Blender object to editable collection: {obj.name}")
        target = groups[(role, room_id)]
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        target.objects.link(obj)


def main():
    args = parse_args()
    with open(args.input, "rb") as f:
        geometry_bytes = f.read()
    data = json.loads(geometry_bytes.decode("utf-8"))
    validate(data)
    if not args.execute:
        print(f"DRY_RUN rooms={len(data['rooms'])} furniture={len(data.get('furniture', []))} output_dir={args.output_dir} approval_required_for_execute=true")
        return
    if not args.approval:
        raise ValueError("--approval is required for writing Blender/GLB/viewer outputs")
    validate_receipt_snapshot(args.input, args.approval, geometry_bytes)
    note_name = "使用说明.md"
    names = [args.blend_name, args.glb_name, args.viewer_name, note_name, *VIEWER_FILES]
    output_dir = Path(args.output_dir).expanduser().resolve()
    preflight_output_bundle(output_dir, names, force=args.force)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}.staging-",
                                     dir=str(output_dir.parent)) as temporary:
        stage_dir = Path(temporary)
        staged_targets = [stage_dir / name for name in names[:3]]
        copy_viewer_assets(stage_dir)
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for block in bpy.data.collections:
            if block.users == 0:
                bpy.data.collections.remove(block)

        floor_mat = material("Floor - warm neutral", (0.72, 0.69, 0.62))
        wall_mat = material("Walls - soft white", (0.82, 0.84, 0.82))
        furniture_materials = {
            "wood": material("Furniture - warm wood", (0.38, 0.24, 0.14)),
            "dark": material("Furniture - dark frame", (0.16, 0.18, 0.17)),
            "upholstery": material("Furniture - muted upholstery", (0.46, 0.55, 0.49)),
            "green": material("Mahjong - green table", (0.07, 0.28, 0.20)),
            "green_light": material("Mahjong - playing surface", (0.16, 0.48, 0.33)),
            "stone": material("Counter - stone proxy", (0.58, 0.60, 0.59)),
        }
        fixed_mat = material("Fixed structure - concrete grey", (0.36, 0.39, 0.40))
        opening_materials = {
            "frame": material("Opening frame - neutral trim", (0.28, 0.31, 0.32)),
            "door": material("Door leaf - warm neutral", (0.42, 0.29, 0.18)),
            "glass": material("Window glazing proxy - blue grey", (0.28, 0.48, 0.57), roughness=0.24),
        }
        edge_owners = shared_edge_owners(data)
        for room in data["rooms"]:
            build_room(room, data.get("wall_height_mm", 2800), floor_mat, wall_mat,
                       edge_owners, opening_materials)
        for item in data.get("furniture", []):
            for part in build_furniture(item, furniture_materials):
                part["model_role"] = "furniture"
                part["source_furniture_id"] = item.get("id", item.get("name", "Furniture"))
                part["source_room_id"] = item["room_id"]
        for item in data.get("fixed_elements", []):
            build_fixed_element(item, fixed_mat)
        organize_scene(data)
        bpy.context.scene["interior_geometry_sha256"] = hashlib.sha256(geometry_bytes).hexdigest()

        bpy.ops.object.select_all(action="SELECT")
        bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
        bpy.ops.wm.save_as_mainfile(filepath=str(staged_targets[0]))
        bpy.ops.export_scene.gltf(filepath=str(staged_targets[1]), export_format="GLB", use_selection=False,
                                  export_extras=True)
        title = data.get("project", "室内方案")
        options = ['<option value="">选择房间…</option>']
        for room in data["rooms"]:
            poly = room["polygon"]
            cx, cy = polygon_center(poly)
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            radius = max(2.0, max(max(xs) - min(xs), max(ys) - min(ys)) * 0.0026)
            target = f"{cx * 0.001:.3f}m 0.000m {-cy * 0.001:.3f}m"
            option_id = html.escape(room["id"], quote=True)
            label = html.escape(f"{room['id']} · {room.get('name', '')}")
            options.append(f'<option value="{option_id}" data-target="{target}" data-radius="{radius:.3f}m">{label}</option>')
        viewer = render_viewer_page(title, args.glb_name, options, wall_mat.name)
        staged_targets[2].write_text(viewer, encoding="utf-8")
        (stage_dir / note_name).write_text(
            build_delivery_note(data, hashlib.sha256(geometry_bytes).hexdigest(),
                                args.blend_name, args.glb_name, args.viewer_name),
            encoding="utf-8",
        )
        commit_output_bundle(stage_dir, output_dir, names, force=args.force)
    print(f"INTERIOR_BUILD_OK rooms={len(data['rooms'])} furniture={len(data.get('furniture', []))} viewer={output_dir / args.viewer_name} runtime=local-v4.3.1")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INTERIOR_BUILD_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
