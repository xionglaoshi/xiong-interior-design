#!/usr/bin/env python3
"""Unit tests for common-wall reconciliation in geometry JSON v1."""

import math
import unittest

from geometry_utils import camera_profile_parameters, door_leaf_pose, shared_edge_owners, validate_geometry


def adjacent_rooms(shared_opening_right=None, left_thickness=120, right_thickness=120):
    left_shared = {"openings": []}
    right_shared = {"openings": []}
    if shared_opening_right is not None:
        left_shared["openings"].append({
            "kind": "door", "start_mm": 600, "width_mm": 900,
            "sill_mm": 0, "height_mm": 2100,
        })
        right_shared["openings"].append({
            "kind": "door", "start_mm": shared_opening_right, "width_mm": 900,
            "sill_mm": 0, "height_mm": 2100,
        })
    return {
        "schema_version": 1,
        "units": "mm",
        "rooms": [
            {"id": "R01", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
             "wall_thickness_mm": left_thickness,
             "walls": [{}, left_shared, {}, {}]},
            {"id": "R02", "polygon": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
             "wall_thickness_mm": right_thickness,
             "walls": [{}, {}, {}, right_shared]},
        ]
    }


class SharedWallTests(unittest.TestCase):
    def test_deduplicates_shared_edge_reversed(self):
        owners = shared_edge_owners(adjacent_rooms())
        self.assertEqual(len(owners), 7)
        self.assertEqual(owners[((4000.0, 0.0), (4000.0, 3000.0))], ("R01", 1))


class CameraProfileTests(unittest.TestCase):
    def test_interior_profile_reproduces_visible_low_wide_trial_for_sample_room(self):
        camera = camera_profile_parameters(4.0, 2.8, "interior")
        self.assertAlmostEqual(camera["offset_m"], 1.0)
        self.assertAlmostEqual(camera["height_m"], 3.3)
        self.assertEqual(camera["lens_mm"], 35.0)

    def test_elevated_profile_preserves_legacy_wide_overview(self):
        camera = camera_profile_parameters(4.0, 2.8, "elevated")
        self.assertAlmostEqual(camera["offset_m"], 2.08)
        self.assertAlmostEqual(camera["height_m"], 6.44)
        self.assertEqual(camera["lens_mm"], 40.0)

    def test_rejects_invalid_dimensions_and_profile(self):
        for args in ((0, 2.8, "interior"), (4, float("nan"), "interior"),
                     (4, 2.8, "unknown")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                camera_profile_parameters(*args)

    def test_normalizes_opening_offset_on_reversed_edge(self):
        owners = shared_edge_owners(adjacent_rooms(shared_opening_right=1500))
        self.assertEqual(len(owners), 7)

    def test_rejects_conflicting_openings(self):
        with self.assertRaisesRegex(ValueError, "Shared wall conflict"):
            shared_edge_owners(adjacent_rooms(shared_opening_right=1000))

    def test_rejects_conflicting_thickness(self):
        with self.assertRaisesRegex(ValueError, "Shared wall conflict"):
            shared_edge_owners(adjacent_rooms(right_thickness=180))

    def test_reversed_shared_wall_normalizes_same_door_swing(self):
        data = adjacent_rooms(shared_opening_right=1500)
        left_opening = data["rooms"][0]["walls"][1]["openings"][0]
        right_opening = data["rooms"][1]["walls"][3]["openings"][0]
        left_opening["door_swing"] = {"hinge": "start", "side": "left"}
        right_opening["door_swing"] = {"hinge": "end", "side": "right"}
        self.assertEqual(len(shared_edge_owners(data)), 7)
        right_opening["door_swing"]["side"] = "left"
        with self.assertRaisesRegex(ValueError, "Shared wall conflict"):
            shared_edge_owners(data)

    def test_rejects_swing_defined_on_only_one_shared_wall_copy(self):
        data = adjacent_rooms(shared_opening_right=1500)
        data["rooms"][0]["walls"][1]["openings"][0]["door_swing"] = {"hinge": "start", "side": "left"}
        with self.assertRaisesRegex(ValueError, "Shared wall conflict"):
            shared_edge_owners(data)


class GeometryValidationTests(unittest.TestCase):
    def test_accepts_valid_adjacent_rooms(self):
        validate_geometry(adjacent_rooms(shared_opening_right=1500))

    def test_accepts_common_appliance_proxy_types(self):
        for furniture_type in ("toilet", "refrigerator", "cooktop", "armchair"):
            data = adjacent_rooms()
            data["furniture"] = [{
                "id": "AP01", "room_id": "R01", "type": furniture_type,
                "center_mm": [2000, 1500], "size_mm": [800, 800, 2000],
            }]
            with self.subTest(furniture_type=furniture_type):
                validate_geometry(data)

    def test_door_swing_requires_confirmed_door_hinge_and_side(self):
        data = adjacent_rooms()
        data["rooms"][0]["walls"][0] = {"openings": [{
            "kind": "door", "start_mm": 600, "width_mm": 900,
            "sill_mm": 0, "height_mm": 2100,
            "door_swing": {"hinge": "start", "side": "left"},
        }]}
        validate_geometry(data)
        data["rooms"][0]["walls"][0]["openings"][0]["door_swing"]["hinge"] = "unknown"
        with self.assertRaisesRegex(ValueError, "door_swing requires"):
            validate_geometry(data)

    def test_rejects_door_swing_on_window(self):
        data = adjacent_rooms()
        data["rooms"][0]["walls"][0] = {"openings": [{
            "kind": "window", "start_mm": 600, "width_mm": 900,
            "sill_mm": 900, "height_mm": 1200,
            "door_swing": {"hinge": "start", "side": "left"},
        }]}
        with self.assertRaisesRegex(ValueError, "only valid.*door"):
            validate_geometry(data)

    def test_door_leaf_pose_covers_hinge_and_swing_directions(self):
        start_left, width_a, angle_a = door_leaf_pose((0, 0), (4000, 0), 500, 900, 50, "start", "left")
        start_right, _, angle_b = door_leaf_pose((0, 0), (4000, 0), 500, 900, 50, "start", "right")
        end_left, _, angle_c = door_leaf_pose((0, 0), (4000, 0), 500, 900, 50, "end", "left")
        end_right, _, angle_d = door_leaf_pose((0, 0), (4000, 0), 500, 900, 50, "end", "right")
        self.assertAlmostEqual(width_a, 850)
        self.assertGreater(start_left[1], 0)
        self.assertLess(start_right[1], 0)
        self.assertGreater(end_left[1], 0)
        self.assertLess(end_right[1], 0)
        self.assertAlmostEqual(math.sin(angle_a), math.sin(angle_c))
        self.assertAlmostEqual(math.cos(angle_a), math.cos(angle_c))
        self.assertAlmostEqual(math.sin(angle_b), math.sin(angle_d))
        self.assertAlmostEqual(math.cos(angle_b), math.cos(angle_d))
        diagonal, _, _ = door_leaf_pose((0, 0), (0, 4000), 500, 900, 50, "start", "left")
        self.assertLess(diagonal[0], 0)

    def test_rejects_non_finite_coordinate(self):
        data = adjacent_rooms()
        data["rooms"][0]["polygon"][1][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            validate_geometry(data)

    def test_rejects_self_intersecting_polygon(self):
        data = adjacent_rooms()
        data["rooms"][0]["polygon"] = [[0, 0], [4000, 0], [0, 3000], [4000, 3000], [2000, 1000]]
        data["rooms"][0]["walls"] = [{} for _ in range(5)]
        with self.assertRaises(ValueError):
            validate_geometry(data)

    def test_rejects_polygon_edge_that_doubles_back(self):
        data = adjacent_rooms()
        data["rooms"][0]["polygon"] = [[0, 0], [4000, 0], [2000, 0], [4000, 3000], [0, 3000]]
        data["rooms"][0]["walls"] = [{} for _ in range(5)]
        with self.assertRaisesRegex(ValueError, "doubles back"):
            validate_geometry(data)

    def test_rejects_overlapping_room_interiors(self):
        data = adjacent_rooms()
        data["rooms"][1]["polygon"] = [[3500, 0], [7000, 0], [7000, 3000], [3500, 3000]]
        with self.assertRaisesRegex(ValueError, "overlap|cross"):
            validate_geometry(data)

    def test_rejects_opening_outside_wall(self):
        data = adjacent_rooms()
        data["rooms"][0]["walls"][0] = {
            "openings": [{"kind": "door", "start_mm": -1, "width_mm": 800, "height_mm": 2100}]
        }
        with self.assertRaisesRegex(ValueError, r"rooms\[0\]\.walls\[0\]\.openings\[0\].*outside wall"):
            validate_geometry(data)

    def test_accepts_door_and_window_openings(self):
        data = adjacent_rooms()
        data["rooms"][0]["walls"][0] = {"openings": [
            {"kind": "door", "start_mm": 300, "width_mm": 900, "sill_mm": 0, "height_mm": 2100},
            {"kind": "window", "start_mm": 1600, "width_mm": 1200, "sill_mm": 900, "height_mm": 1200},
        ]}
        validate_geometry(data)

    def test_rejects_overlapping_door_and_window_openings(self):
        data = adjacent_rooms()
        data["rooms"][0]["walls"][0] = {"openings": [
            {"kind": "door", "start_mm": 300, "width_mm": 1000, "sill_mm": 0, "height_mm": 2100},
            {"kind": "window", "start_mm": 1200, "width_mm": 1200, "sill_mm": 900, "height_mm": 1200},
        ]}
        with self.assertRaisesRegex(ValueError, "openings overlap"):
            validate_geometry(data)

    def test_rejects_invalid_furniture_extent(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "F01", "room_id": "R01", "center_mm": [10, 20],
                              "size_mm": [100, 100, float("inf")]}]
        with self.assertRaisesRegex(ValueError, "finite positive numbers"):
            validate_geometry(data)

    def test_rejects_furniture_outside_room(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "F01", "room_id": "R01", "center_mm": [3900, 1500],
                              "size_mm": [400, 400, 750]}]
        with self.assertRaisesRegex(ValueError, r"furniture\[0\].*falls outside room"):
            validate_geometry(data)

    def test_accepts_furniture_touching_room_boundary(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "F01", "room_id": "R01", "center_mm": [100, 1500],
                              "size_mm": [200, 400, 750]}]
        validate_geometry(data)

    def test_rejects_overlapping_furniture(self):
        data = adjacent_rooms()
        data["furniture"] = [
            {"id": "T01", "room_id": "R01", "center_mm": [1500, 1500], "size_mm": [1000, 800, 750]},
            {"id": "S01", "room_id": "R01", "center_mm": [1900, 1500], "size_mm": [800, 600, 450]},
        ]
        with self.assertRaisesRegex(ValueError, r"furniture\[0\] \+ furniture\[1\].*overlap: T01 and S01"):
            validate_geometry(data)

    def test_accepts_furniture_touching_without_overlap(self):
        data = adjacent_rooms()
        data["furniture"] = [
            {"id": "T01", "room_id": "R01", "center_mm": [1500, 1500], "size_mm": [1000, 800, 750]},
            {"id": "S01", "room_id": "R01", "center_mm": [2400, 1500], "size_mm": [800, 600, 450]},
        ]
        validate_geometry(data)

    def test_accepts_sink_proxy_type(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "SINK01", "room_id": "R01", "type": "sink",
                              "center_mm": [1200, 1000], "size_mm": [900, 600, 900]}]
        validate_geometry(data)

    def test_accepts_cad_confirmed_fixed_column(self):
        data = adjacent_rooms()
        data["fixed_elements"] = [{"id": "C01", "room_id": "R01", "type": "column",
                                   "center_mm": [2000, 1500], "size_mm": [400, 400, 2800]}]
        validate_geometry(data)

    def test_rejects_furniture_intersecting_fixed_column(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "T01", "room_id": "R01", "center_mm": [2000, 1500],
                              "size_mm": [1200, 800, 750]}]
        data["fixed_elements"] = [{"id": "C01", "room_id": "R01", "type": "column",
                                   "center_mm": [2000, 1500], "size_mm": [400, 400, 2800]}]
        with self.assertRaisesRegex(ValueError, r"furniture\[0\] \+ fixed_elements\[0\].*T01 collides with fixed element C01"):
            validate_geometry(data)

    def test_accepts_furniture_below_overhead_beam(self):
        data = adjacent_rooms()
        data["furniture"] = [{"id": "CAB01", "room_id": "R01", "center_mm": [2000, 1500],
                              "size_mm": [1200, 600, 2100]}]
        data["fixed_elements"] = [{"id": "B01", "room_id": "R01", "type": "beam",
                                   "center_mm": [2000, 1500], "size_mm": [1400, 300, 400],
                                   "base_z_mm": 2400}]
        validate_geometry(data)

    def test_rejects_fixed_element_outside_room(self):
        data = adjacent_rooms()
        data["fixed_elements"] = [{"id": "C01", "room_id": "R01", "type": "column",
                                   "center_mm": [3900, 1500], "size_mm": [400, 400, 2800]}]
        with self.assertRaisesRegex(ValueError, "Fixed element C01 footprint falls outside room"):
            validate_geometry(data)

    def test_rejects_fixed_element_above_wall_height(self):
        data = adjacent_rooms()
        data["fixed_elements"] = [{"id": "B01", "room_id": "R01", "type": "beam",
                                   "center_mm": [2000, 1500], "size_mm": [1200, 200, 500],
                                   "base_z_mm": 2500}]
        with self.assertRaisesRegex(ValueError, "top exceeds wall_height"):
            validate_geometry(data)


if __name__ == "__main__":
    unittest.main()
