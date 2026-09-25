"""Shared geometry validation and wall helpers for geometry JSON v1 tools."""

from __future__ import annotations

import math

FURNITURE_TYPES = {"box", "cabinet", "table", "mahjong_table", "chair", "armchair", "sofa", "bed", "sink", "toilet", "refrigerator", "cooktop"}
FURNITURE_LABELS = {
    "box": "通用体块家具", "cabinet": "柜体", "table": "桌", "mahjong_table": "麻将桌",
    "chair": "无扶手椅", "armchair": "带扶手椅", "sofa": "沙发", "bed": "床",
    "sink": "水槽", "toilet": "坐便器", "refrigerator": "冰箱", "cooktop": "灶台",
}
FIXED_ELEMENT_TYPES = {"column", "shaft", "beam"}


def furniture_type_label(kind):
    return FURNITURE_LABELS.get(kind, str(kind))


def camera_profile_parameters(max_span_m, wall_height_m, profile):
    """Return repeatable room-camera placement values without changing geometry."""
    if not (_finite_number(max_span_m) and _finite_number(wall_height_m) and
            max_span_m > 0 and wall_height_m > 0):
        raise ValueError("Camera span and wall height must be finite positive numbers")
    profiles = {
        "interior": {
            "offset_m": max(max_span_m * 0.25, wall_height_m * 0.20),
            "height_m": max(wall_height_m * 1.17, wall_height_m + max_span_m * 0.125),
            "lens_mm": 35.0,
        },
        "elevated": {
            "offset_m": max_span_m * 0.52,
            "height_m": max(wall_height_m * 2.30, wall_height_m + max_span_m * 0.80),
            "lens_mm": 40.0,
        },
    }
    if profile not in profiles:
        raise ValueError(f"Unsupported camera profile: {profile}")
    return profiles[profile].copy()


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p, eps=1e-8):
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps and
            min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def _segments_intersect(a, b, c, d):
    eps = 1e-8
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > eps and ab_d < -eps or ab_c < -eps and ab_d > eps) and
            (cd_a > eps and cd_b < -eps or cd_a < -eps and cd_b > eps)):
        return True
    return ((abs(ab_c) <= eps and _on_segment(a, b, c)) or
            (abs(ab_d) <= eps and _on_segment(a, b, d)) or
            (abs(cd_a) <= eps and _on_segment(c, d, a)) or
            (abs(cd_b) <= eps and _on_segment(c, d, b)))


def _segments_properly_intersect(a, b, c, d):
    eps = 1e-8
    values = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    return ((values[0] > eps and values[1] < -eps or values[0] < -eps and values[1] > eps) and
            (values[2] > eps and values[3] < -eps or values[2] < -eps and values[3] > eps))


def _point_strictly_inside(point, polygon):
    inside = False
    x, y = point
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if abs(_cross(a, b, point)) <= 1e-8 and _on_segment(a, b, point):
            return False
        if (a[1] > y) != (b[1] > y):
            cross_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < cross_x:
                inside = not inside
    return inside


def _point_inside_or_on_boundary(point, polygon):
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        if abs(_cross(a, b, point)) <= 1e-8 and _on_segment(a, b, point):
            return True
    return _point_strictly_inside(point, polygon)


def _polygon_samples(polygon):
    samples = list(polygon)
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        samples.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    samples.append((sum(p[0] for p in polygon) / len(polygon),
                    sum(p[1] for p in polygon) / len(polygon)))
    return samples


def rectangle_footprint(center, size, rotation_deg=0):
    """Return the four XY corners for a centered, rotated rectangular footprint."""
    cx, cy = (float(value) for value in center)
    width, depth = (float(value) for value in size[:2])
    angle = math.radians(float(rotation_deg))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)
            for x, y in ((-width / 2, -depth / 2), (width / 2, -depth / 2),
                         (width / 2, depth / 2), (-width / 2, depth / 2))]


def _footprints_overlap(a, b):
    """Return true only when two convex footprints overlap by positive area."""
    for polygon in (a, b):
        for index, point in enumerate(polygon):
            nxt = polygon[(index + 1) % len(polygon)]
            axis = (-(nxt[1] - point[1]), nxt[0] - point[0])
            projections_a = [p[0] * axis[0] + p[1] * axis[1] for p in a]
            projections_b = [p[0] * axis[0] + p[1] * axis[1] for p in b]
            if min(max(projections_a), max(projections_b)) - max(min(projections_a), min(projections_b)) <= 1e-6:
                return False
    return True


def _validate_room_footprint(label, corners, polygon):
    samples = list(corners) + [((corners[i][0] + corners[(i + 1) % 4][0]) / 2,
                                (corners[i][1] + corners[(i + 1) % 4][1]) / 2) for i in range(4)]
    if not all(_point_inside_or_on_boundary(point, polygon) for point in samples) or any(
            _segments_properly_intersect(corners[i], corners[(i + 1) % 4],
                                         polygon[j], polygon[(j + 1) % len(polygon)])
            for i in range(4) for j in range(len(polygon))):
        raise ValueError(label)


def _input_error(path, message):
    """Return a ValueError that points to the offending geometry JSON field."""
    return ValueError(f"{path}: {message}")


def _polygon_area_signed(points):
    return sum(points[i][0] * points[(i + 1) % len(points)][1] -
               points[(i + 1) % len(points)][0] * points[i][1]
               for i in range(len(points))) / 2


def validate_geometry(data):
    """Validate shared room/furniture geometry before any exporter writes files."""
    if (not isinstance(data, dict) or isinstance(data.get("schema_version"), bool) or
            data.get("schema_version") != 1 or data.get("units") != "mm"):
        raise ValueError("Geometry input must use schema_version=1 and units='mm'")
    wall_height = data.get("wall_height_mm", 2800)
    if not _finite_number(wall_height) or wall_height <= 0:
        raise ValueError("wall_height_mm must be a finite positive number")
    rooms = data.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise ValueError("Geometry input must contain at least one room")

    room_ids = set()
    room_by_id = {}
    room_polygons = []
    for room_index, room in enumerate(rooms):
        room_path = f"rooms[{room_index}]"
        if not isinstance(room, dict):
            raise _input_error(room_path, "room must be an object")
        rid = room.get("id")
        if not isinstance(rid, str) or not rid.strip() or rid in room_ids:
            raise _input_error(f"{room_path}.id", f"missing, invalid or duplicated: {rid!r}")
        room_ids.add(rid)
        polygon = room.get("polygon")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise _input_error(f"{room_path}.polygon", f"Room {rid} needs at least 3 polygon points")
        points = []
        for index, point in enumerate(polygon):
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                    not all(_finite_number(value) for value in point)):
                raise _input_error(f"{room_path}.polygon[{index}]", f"Room {rid} point {index + 1} must contain two finite numbers")
            points.append((float(point[0]), float(point[1])))
        for index, point in enumerate(points):
            nxt = points[(index + 1) % len(points)]
            if math.dist(point, nxt) <= 1e-6:
                raise _input_error(f"{room_path}.polygon[{index}]", f"Room {rid} has a zero-length edge at {index + 1}")
            prev = points[index - 1]
            incoming = (point[0] - prev[0], point[1] - prev[1])
            outgoing = (nxt[0] - point[0], nxt[1] - point[1])
            if abs(incoming[0] * outgoing[1] - incoming[1] * outgoing[0]) <= 1e-8 and (
                    incoming[0] * outgoing[0] + incoming[1] * outgoing[1] < -1e-8):
                raise _input_error(f"{room_path}.polygon[{index}]", f"Room {rid} boundary doubles back at point {index + 1}")
        area = _polygon_area_signed(points)
        if abs(area) <= 1e-3:
            raise _input_error(f"{room_path}.polygon", f"Room {rid} polygon area is zero or too small")
        count = len(points)
        for i in range(count):
            a, b = points[i], points[(i + 1) % count]
            for j in range(i + 1, count):
                if j == i or j == i + 1 or (i == 0 and j == count - 1):
                    continue
                c, d = points[j], points[(j + 1) % count]
                if _segments_intersect(a, b, c, d):
                    raise _input_error(f"{room_path}.polygon", f"Room {rid} polygon self-intersects at edges {i + 1} and {j + 1}")
        room_polygons.append((rid, points))
        room_by_id[rid] = points

        walls = room.get("walls", [{} for _ in points])
        if not isinstance(walls, list) or len(walls) != len(points):
            raise _input_error(f"{room_path}.walls", f"Room {rid}: walls must match polygon edge count")
        thickness = room.get("wall_thickness_mm", 120)
        if not _finite_number(thickness) or thickness <= 0:
            raise _input_error(f"{room_path}.wall_thickness_mm", f"Room {rid}: wall thickness must be a finite positive number")
        for index, wall in enumerate(walls):
            wall_path = f"{room_path}.walls[{index}]"
            if not isinstance(wall, dict):
                raise _input_error(wall_path, f"Room {rid} edge {index + 1}: wall definition must be an object")
            openings = wall.get("openings", [])
            if not isinstance(openings, list):
                raise _input_error(f"{wall_path}.openings", f"Room {rid} edge {index + 1}: openings must be a list")
            length = math.dist(points[index], points[(index + 1) % count])
            parsed = []
            for opening_index, opening in enumerate(openings):
                opening_path = f"{wall_path}.openings[{opening_index}]"
                if not isinstance(opening, dict):
                    raise _input_error(opening_path, f"Room {rid} edge {index + 1}: each opening must be an object")
                kind = opening.get("kind", "opening")
                if not isinstance(kind, str) or kind not in {"door", "window", "opening"}:
                    raise _input_error(f"{opening_path}.kind", f"Room {rid} edge {index + 1}: unsupported opening kind {kind!r}")
                start, width = opening.get("start_mm"), opening.get("width_mm")
                sill, height = opening.get("sill_mm", 0), opening.get("height_mm")
                if not all(_finite_number(v) for v in (start, width, sill, height)):
                    raise _input_error(opening_path, f"Room {rid} edge {index + 1}: opening dimensions must be finite numbers")
                if start < 0 or width <= 0 or start + width > length + 1e-6:
                    raise _input_error(opening_path, f"Room {rid} edge {index + 1}: opening falls outside wall")
                if sill < 0 or height <= 0 or sill + height > wall_height + 1e-6:
                    raise _input_error(opening_path, f"Room {rid} edge {index + 1}: opening height exceeds wall")
                swing = opening.get("door_swing")
                if swing is not None:
                    if kind != "door" or not isinstance(swing, dict):
                        raise _input_error(f"{opening_path}.door_swing", f"Room {rid} edge {index + 1}: door_swing is only valid as an object on a door")
                    if set(swing) != {"hinge", "side"} or swing.get("hinge") not in {"start", "end"} or swing.get("side") not in {"left", "right"}:
                        raise _input_error(f"{opening_path}.door_swing", f"Room {rid} edge {index + 1}: door_swing requires hinge start/end and side left/right")
                parsed.append((float(start), float(start + width)))
            parsed.sort()
            if any(parsed[i][0] < parsed[i - 1][1] - 1e-6 for i in range(1, len(parsed))):
                raise _input_error(f"{wall_path}.openings", f"Room {rid} edge {index + 1}: openings overlap")

    for index, (room_a, poly_a) in enumerate(room_polygons):
        key_a = { _point_key(point) for point in poly_a }
        for room_b, poly_b in room_polygons[index + 1:]:
            key_b = { _point_key(point) for point in poly_b }
            if key_a == key_b or any(_point_strictly_inside(p, poly_b) for p in _polygon_samples(poly_a)) or any(
                    _point_strictly_inside(p, poly_a) for p in _polygon_samples(poly_b)):
                raise ValueError(f"Room interiors overlap: {room_a} and {room_b}")
            if any(_segments_properly_intersect(poly_a[i], poly_a[(i + 1) % len(poly_a)],
                                                poly_b[j], poly_b[(j + 1) % len(poly_b)])
                   for i in range(len(poly_a)) for j in range(len(poly_b))):
                raise ValueError(f"Room boundaries cross: {room_a} and {room_b}")

    furniture = data.get("furniture", [])
    if not isinstance(furniture, list):
        raise ValueError("furniture must be a list")
    furniture_ids = set()
    furniture_boxes = []
    for index, item in enumerate(furniture):
        item_path = f"furniture[{index}]"
        if not isinstance(item, dict):
            raise _input_error(item_path, f"Furniture item {index + 1} must be an object")
        item_id = item.get("id")
        if item_id is not None:
            if not isinstance(item_id, str) or not item_id.strip() or item_id in furniture_ids:
                raise _input_error(f"{item_path}.id", f"Furniture id missing, invalid or duplicated: {item_id!r}")
            furniture_ids.add(item_id)
        room_id = item.get("room_id")
        if not isinstance(room_id, str) or room_id not in room_ids:
            raise _input_error(f"{item_path}.room_id", f"Furniture {item_id or index + 1} references unknown room")
        center, size = item.get("center_mm"), item.get("size_mm")
        if (not isinstance(center, list) or len(center) != 2 or
                not all(_finite_number(v) for v in center)):
            raise _input_error(f"{item_path}.center_mm", f"Furniture {item_id or index + 1} center_mm must contain two finite numbers")
        if (not isinstance(size, list) or len(size) != 3 or
                not all(_finite_number(v) and v > 0 for v in size)):
            raise _input_error(f"{item_path}.size_mm", f"Furniture {item_id or index + 1} size_mm must contain three finite positive numbers")
        rotation = item.get("rotation_deg", 0)
        if not _finite_number(rotation):
            raise _input_error(f"{item_path}.rotation_deg", f"Furniture {item_id or index + 1} rotation_deg must be finite")
        kind = item.get("type", "box")
        if not isinstance(kind, str) or kind not in FURNITURE_TYPES:
            raise _input_error(f"{item_path}.type", f"Furniture {item_id or index + 1} has unsupported type: {kind!r}")
        corners = rectangle_footprint(center, size, rotation)
        room_polygon = room_by_id[room_id]
        try:
            _validate_room_footprint(f"Furniture {item_id or index + 1} footprint falls outside room {room_id}",
                                     corners, room_polygon)
        except ValueError as exc:
            raise _input_error(item_path, str(exc)) from exc
        furniture_boxes.append({"id": item_id or str(index + 1), "room_id": room_id,
                                "path": item_path, "footprint": corners, "height": float(size[2])})

    fixed_elements = data.get("fixed_elements", [])
    if not isinstance(fixed_elements, list):
        raise ValueError("fixed_elements must be a list")
    fixed_ids = set()
    fixed_boxes = []
    for index, item in enumerate(fixed_elements):
        item_path = f"fixed_elements[{index}]"
        if not isinstance(item, dict):
            raise _input_error(item_path, f"Fixed element {index + 1} must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip() or item_id in fixed_ids:
            raise _input_error(f"{item_path}.id", f"Fixed element id missing, invalid or duplicated: {item_id!r}")
        fixed_ids.add(item_id)
        kind = item.get("type")
        if not isinstance(kind, str) or kind not in FIXED_ELEMENT_TYPES:
            raise _input_error(f"{item_path}.type", f"Fixed element {item_id} has unsupported type: {kind!r}")
        room_id = item.get("room_id")
        if not isinstance(room_id, str) or room_id not in room_ids:
            raise _input_error(f"{item_path}.room_id", f"Fixed element {item_id} references unknown room")
        center, size = item.get("center_mm"), item.get("size_mm")
        if (not isinstance(center, list) or len(center) != 2 or
                not all(_finite_number(value) for value in center)):
            raise _input_error(f"{item_path}.center_mm", f"Fixed element {item_id} center_mm must contain two finite numbers")
        if (not isinstance(size, list) or len(size) != 3 or
                not all(_finite_number(value) and value > 0 for value in size)):
            raise _input_error(f"{item_path}.size_mm", f"Fixed element {item_id} size_mm must contain three finite positive numbers")
        rotation = item.get("rotation_deg", 0)
        base_z = item.get("base_z_mm", 0)
        if not _finite_number(rotation) or not _finite_number(base_z) or base_z < 0:
            raise _input_error(item_path, f"Fixed element {item_id} rotation_deg/base_z_mm must be finite; base_z_mm cannot be negative")
        if base_z + float(size[2]) > wall_height + 1e-6:
            raise _input_error(f"{item_path}.base_z_mm/size_mm[2]", f"Fixed element {item_id} top exceeds wall_height_mm")
        corners = rectangle_footprint(center, size, rotation)
        try:
            _validate_room_footprint(f"Fixed element {item_id} footprint falls outside room {room_id}",
                                     corners, room_by_id[room_id])
        except ValueError as exc:
            raise _input_error(item_path, str(exc)) from exc
        fixed_boxes.append({"id": item_id, "room_id": room_id, "footprint": corners,
                            "path": item_path, "base_z": float(base_z), "top_z": float(base_z) + float(size[2])})

    for index, first in enumerate(furniture_boxes):
        for second in furniture_boxes[index + 1:]:
            if first["room_id"] == second["room_id"] and _footprints_overlap(
                    first["footprint"], second["footprint"]):
                raise _input_error(f"{first['path']} + {second['path']}", f"Furniture footprints overlap: {first['id']} and {second['id']}")
        for fixed in fixed_boxes:
            if (first["room_id"] == fixed["room_id"] and first["height"] > fixed["base_z"] + 1e-6 and
                    _footprints_overlap(first["footprint"], fixed["footprint"])):
                raise _input_error(f"{first['path']} + {fixed['path']}", f"Furniture {first['id']} collides with fixed element {fixed['id']}")

    shared_edge_owners(data)

def _point_key(point):
    return tuple(round(float(value), 3) for value in point)


def edge_key(a, b):
    pa, pb = _point_key(a), _point_key(b)
    return (pa, pb) if pa <= pb else (pb, pa)


def door_leaf_pose(a, b, start_mm, width_mm, frame_mm, hinge, side):
    """Return a confirmed door leaf center (mm), length (mm), and yaw (rad)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    wall_angle = math.atan2(dy, dx)
    leaf_length = max(float(width_mm) - float(frame_mm), 50.0)
    hinge_distance = float(start_mm) + (
        float(frame_mm) / 2 if hinge == "start" else float(width_mm) - float(frame_mm) / 2
    )
    hinge_x, hinge_y = a[0] + ux * hinge_distance, a[1] + uy * hinge_distance
    side_sign = 1 if side == "left" else -1
    turn = side_sign * 90 if hinge == "start" else 180 - side_sign * 90
    yaw = wall_angle + math.radians(turn)
    center = (hinge_x + math.cos(yaw) * leaf_length / 2,
              hinge_y + math.sin(yaw) * leaf_length / 2)
    return center, leaf_length, yaw


def _wall_signature(a, b, wall, thickness):
    length = math.dist(a, b)
    forward = _point_key(a) <= _point_key(b)
    openings = []
    for opening in wall.get("openings", []):
        start = float(opening["start_mm"])
        width = float(opening["width_mm"])
        canonical_start = start if forward else length - start - width
        swing = opening.get("door_swing")
        if swing is not None and not forward:
            swing = {
                "hinge": "end" if swing["hinge"] == "start" else "start",
                "side": "right" if swing["side"] == "left" else "left",
            }
        openings.append((
            round(canonical_start, 3), round(width, 3),
            round(float(opening.get("sill_mm", 0)), 3),
            round(float(opening["height_mm"]), 3),
            str(opening.get("kind", "opening")),
            (swing["hinge"], swing["side"]) if swing is not None else ("", ""),
        ))
    return round(float(thickness), 3), tuple(sorted(openings))


def shared_edge_owners(data):
    """Return first owner of identical shared edges; reject conflicting definitions.

    Two room boundaries share a wall only when their centerline endpoints match.
    If the same edge is described from the reverse direction, opening offsets are
    normalized before comparison. Ambiguous thickness/opening definitions fail
    closed instead of silently selecting one room's interpretation.
    """
    owners = {}
    signatures = {}
    for room in data["rooms"]:
        polygon = room["polygon"]
        walls = room.get("walls", [{} for _ in polygon])
        thickness = room.get("wall_thickness_mm", 120)
        for index, a in enumerate(polygon):
            b = polygon[(index + 1) % len(polygon)]
            key = edge_key(a, b)
            signature = _wall_signature(a, b, walls[index], thickness)
            previous = signatures.get(key)
            if previous is not None and previous[0] != signature:
                prior_room, prior_index = previous[1]
                raise ValueError(
                    f"Shared wall conflict: {prior_room} edge {prior_index + 1} and "
                    f"{room['id']} edge {index + 1} differ in thickness or openings; "
                    "reconcile from the confirmed plan before exporting"
                )
            if previous is None:
                signatures[key] = (signature, (room["id"], index))
                owners[key] = (room["id"], index)
    return owners
