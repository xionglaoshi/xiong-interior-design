#!/usr/bin/env python3
"""Round-trip tests for the simple designer-reference DXF exporter."""

import tempfile
import unittest
from pathlib import Path
import json
import hashlib
import subprocess
import sys

import ezdxf
from ezdxf import bbox

from export_reference_dxf import export
from geometry_utils import _point_strictly_inside, rectangle_footprint
from geometry_approval import create_receipt


def canonical_line(entity):
    """Order-independent 2D line key for exact DXF geometry assertions."""
    points = [
        (round(entity.dxf.start.x, 6), round(entity.dxf.start.y, 6)),
        (round(entity.dxf.end.x, 6), round(entity.dxf.end.y, 6)),
    ]
    return tuple(sorted(points))


def source_refs(entity):
    return [tag.value for tag in entity.get_xdata("XIONG_SOURCE") if tag.code == 1000]


class ReferenceDxfTests(unittest.TestCase):
    def test_l_shaped_room_label_is_inside_after_dxf_round_trip(self):
        polygon = [[0, 0], [4000, 0], [4000, 1500], [2500, 1500], [2500, 3000], [0, 3000]]
        data = {
            "schema_version": 1, "units": "mm", "rooms": [{"id": "L01", "name": "L shape",
                "polygon": polygon, "walls": [{} for _ in polygon], "wall_thickness_mm": 120}],
        }
        with tempfile.TemporaryDirectory(prefix="reference-dxf-test-") as temp:
            target = Path(temp) / "concave-room.dxf"
            export(data, target)
            doc = ezdxf.readfile(target)
            audit = doc.audit()
            self.assertEqual(audit.errors, [])
            labels = [entity for entity in doc.modelspace().query("TEXT")
                      if entity.dxf.layer == "A-ROOM-TEXT"]
            self.assertEqual(len(labels), 1)
            point = (labels[0].dxf.insert.x, labels[0].dxf.insert.y)
            self.assertTrue(_point_strictly_inside(point, polygon), point)
            self.assertEqual(doc.header["$INSUNITS"], 4)

    def test_room_label_uses_area_centroid_when_it_is_inside(self):
        from export_reference_dxf import room_label_point

        polygon = [[0, 0], [4000, 0], [4000, 1500], [2500, 1500], [2500, 3000], [0, 3000]]
        point = room_label_point(polygon)
        self.assertTrue(_point_strictly_inside(point, polygon), point)
        self.assertNotEqual(point, (sum(x for x, _ in polygon) / 6, sum(y for _, y in polygon) / 6))

    def test_falls_back_to_interior_scanline_when_area_centroid_is_outside(self):
        from export_reference_dxf import room_label_point

        polygon = [[0, 0], [5000, 0], [5000, 5000], [4000, 5000],
                   [4000, 1000], [1000, 1000], [1000, 5000], [0, 5000]]
        point = room_label_point(polygon)
        self.assertTrue(_point_strictly_inside(point, polygon), point)

    def test_round_trips_openings_furniture_and_fixed_elements_on_named_layers(self):
        polygon = [[0, 0], [5000, 0], [5000, 4000], [0, 4000]]
        data = {
            "schema_version": 1,
            "units": "mm",
            "rooms": [{"id": "R01", "name": "Synthetic", "polygon": polygon,
                       "source_refs": ["cad-room"],
                       "wall_thickness_mm": 120,
                       "walls": [{"source_refs": ["cad-wall-1"], "openings": [{"source_refs": ["cad-window"], "kind": "window", "start_mm": 1000,
                                                    "width_mm": 1200, "sill_mm": 900, "height_mm": 1200}]},
                                 {"source_refs": ["cad-wall-2"], "openings": [{"source_refs": ["user-door"], "kind": "door", "start_mm": 1500,
                                                "width_mm": 900, "sill_mm": 0, "height_mm": 2100}]}, {}, {}]}],
            "furniture": [{"id": "T01", "room_id": "R01", "name": "Table", "type": "table", "source_refs": ["user-table"],
                           "center_mm": [2800, 2500], "size_mm": [900, 700, 750], "rotation_deg": 30},
                          {"id": "AC01", "room_id": "R01", "name": "Armchair", "type": "armchair", "source_refs": ["user-chair"],
                           "center_mm": [1200, 2500], "size_mm": [600, 650, 900], "rotation_deg": 0}],
            "fixed_elements": [{"id": "C01", "room_id": "R01", "type": "column", "name": "Column", "source_refs": ["cad-column"],
                                "center_mm": [700, 700], "size_mm": [300, 300, 2800]}],
        }
        with tempfile.TemporaryDirectory(prefix="reference-dxf-test-") as temp:
            target = Path(temp) / "layers.dxf"
            export(data, target)
            doc = ezdxf.readfile(target)
            audit = doc.audit()
            self.assertEqual(audit.errors, [])
            self.assertEqual(doc.header["$INSUNITS"], 4)
            self.assertEqual(doc.header["$MEASUREMENT"], 1)
            self.assertEqual(doc.header["$LWDISPLAY"], 1)
            drawing_bounds = bbox.extents(doc.modelspace(), fast=True)
            self.assertEqual(tuple(doc.header["$EXTMIN"]), tuple(drawing_bounds.extmin))
            self.assertEqual(tuple(doc.header["$EXTMAX"]), tuple(drawing_bounds.extmax))
            self.assertLess(doc.header["$EXTMIN"][1], 0)
            msp = doc.modelspace()
            by_layer = {}
            for entity in msp:
                by_layer.setdefault(entity.dxf.layer, []).append(entity)
            self.assertEqual(len([e for e in by_layer["A-OPENING"] if e.dxftype() == "LINE"]), 4)
            self.assertEqual(len([e for e in by_layer["A-WINDOW"] if e.dxftype() == "LINE"]), 2)
            self.assertEqual(len([e for e in by_layer["A-FURNITURE"] if e.dxftype() == "LINE"]), 8)
            self.assertEqual(len([e for e in by_layer["A-FIXED"] if e.dxftype() == "LINE"]), 4)
            self.assertIn("A-NOTE", by_layer)
            furniture_labels = {entity.dxf.text: entity for entity in by_layer["A-FURNITURE"]
                                if entity.dxftype() == "TEXT"}
            self.assertIn("T01 Table 桌", furniture_labels)
            self.assertIn("AC01 Armchair 带扶手椅", furniture_labels)
            self.assertEqual(
                [tag.value for tag in furniture_labels["T01 Table 桌"].get_xdata("XIONG_SOURCE") if tag.code == 1000],
                ["user-table"],
            )
            furniture_line_refs = [
                [tag.value for tag in entity.get_xdata("XIONG_SOURCE") if tag.code == 1000]
                for entity in by_layer["A-FURNITURE"] if entity.dxftype() == "LINE"
            ]
            self.assertEqual(furniture_line_refs.count(["user-table"]), 4)
            self.assertEqual(furniture_line_refs.count(["user-chair"]), 4)
            # Verify the actual wall/opening coordinates, not just layer membership/counts.
            wall_lines = [e for e in by_layer["A-WALL"] if e.dxftype() == "LINE"]
            south_wall_bands = {canonical_line(e) for e in wall_lines
                                if abs(e.dxf.start.y - e.dxf.end.y) < 1e-6
                                and round(abs(e.dxf.start.y), 6) == 60}
            self.assertEqual(south_wall_bands, {
                ((0.0, -60.0), (1000.0, -60.0)),
                ((2200.0, -60.0), (5000.0, -60.0)),
                ((0.0, 60.0), (1000.0, 60.0)),
                ((2200.0, 60.0), (5000.0, 60.0)),
            })
            opening_lines = [e for e in by_layer["A-OPENING"] if e.dxftype() == "LINE"]
            self.assertEqual({canonical_line(e) for e in opening_lines}, {
                ((1000.0, -60.0), (1000.0, 60.0)),
                ((2200.0, -60.0), (2200.0, 60.0)),
                ((4940.0, 1500.0), (5060.0, 1500.0)),
                ((4940.0, 2400.0), (5060.0, 2400.0)),
            })
            window_lines = [e for e in by_layer["A-WINDOW"] if e.dxftype() == "LINE"]
            self.assertEqual({canonical_line(e) for e in window_lines}, {
                ((1000.0, -20.0), (2200.0, -20.0)),
                ((1000.0, 20.0), (2200.0, 20.0)),
            })

            # Verify each furniture footprint and fixed column against the source JSON.
            table_lines = [e for e in by_layer["A-FURNITURE"]
                           if e.dxftype() == "LINE" and source_refs(e) == ["user-table"]]
            expected_table = rectangle_footprint([2800, 2500], [900, 700], 30)
            self.assertEqual(len(table_lines), 4)
            self.assertEqual({canonical_line(e) for e in table_lines}, {
                tuple(sorted((tuple(round(v, 6) for v in expected_table[i]),
                              tuple(round(v, 6) for v in expected_table[(i + 1) % 4]))))
                for i in range(4)
            })
            fixed_lines = [entity for entity in by_layer["A-FIXED"] if entity.dxftype() == "LINE"]
            self.assertEqual(len(fixed_lines), 4)
            self.assertTrue(all(
                [tag.value for tag in entity.get_xdata("XIONG_SOURCE") if tag.code == 1000] == ["cad-column"]
                for entity in fixed_lines
            ))
            self.assertEqual({canonical_line(e) for e in fixed_lines}, {
                ((550.0, 550.0), (850.0, 550.0)),
                ((550.0, 550.0), (550.0, 850.0)),
                ((550.0, 850.0), (850.0, 850.0)),
                ((850.0, 550.0), (850.0, 850.0)),
            })
            room_label = next(entity for entity in by_layer["A-ROOM-TEXT"] if entity.dxftype() == "TEXT")
            self.assertEqual([tag.value for tag in room_label.get_xdata("XIONG_SOURCE")], ["cad-room"])
            opening_marks = [entity for entity in by_layer["A-OPENING"] if entity.dxftype() == "LINE"]
            self.assertEqual(
                {tag.value for entity in opening_marks for tag in entity.get_xdata("XIONG_SOURCE")},
                {"cad-room", "cad-wall-1", "cad-window", "cad-wall-2", "user-door"},
            )
            self.assertIn("XIONG_SOURCE", doc.appids)
            rotated_bounds = rectangle_footprint([2800, 2500], [900, 700], 30)
            table_label = furniture_labels["T01 Table 桌"]
            self.assertAlmostEqual(table_label.dxf.insert.x, min(point[0] for point in rotated_bounds))
            self.assertAlmostEqual(table_label.dxf.insert.y, min(point[1] for point in rotated_bounds) - 110)


class ReferenceDxfCliTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reference-dxf-cli-")
        self.root = Path(self.temp.name)
        self.geometry = self.root / "geometry.json"
        self.approval = self.root / "approval.json"
        data = {
            "schema_version": 1, "units": "mm",
            "source_ledger": [{"id": "synthetic", "kind": "synthetic_fixture", "locator": "CLI transaction test only"}],
            "rooms": [{"id": "R01", "name": "Synthetic", "source_refs": ["synthetic"],
                "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "walls": [{"source_refs": ["synthetic"]} for _ in range(4)],
            }],
        }
        self.geometry.write_text(json.dumps(data), encoding="utf-8")
        self.approval.write_text(json.dumps(create_receipt(self.geometry, "合成 CLI 回归测试")), encoding="utf-8")
        self.script = Path(__file__).with_name("export_reference_dxf.py")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, output, *extra):
        return subprocess.run([sys.executable, str(self.script), "--input", str(self.geometry),
            "--approval", str(self.approval), "--output", str(output), "--execute", *extra],
            capture_output=True, text=True, check=False)

    def test_cli_stages_audits_and_commits_dxf(self):
        output = self.root / "deliver" / "reference.dxf"
        result = self.run_cli(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = ezdxf.readfile(output)
        self.assertEqual(doc.audit().errors, [])
        self.assertEqual(doc.header["$INSUNITS"], 4)
        notes = list(doc.modelspace().query('TEXT[layer=="A-NOTE"]'))
        self.assertEqual(len(notes), 1)
        metadata = notes[0].get_xdata("XIONG_META")
        provenance = [tag.value for tag in metadata if tag.code == 1000]
        expected_hash = hashlib.sha256(self.geometry.read_bytes()).hexdigest()
        self.assertEqual(provenance, [f"geometry_sha256:{expected_hash}"])
        self.assertEqual([tag.value for tag in metadata if tag.code == 1070], [1])
        self.assertEqual(list(output.parent.glob(".*.staging-*")), [])

    def test_cli_refuses_replacement_before_touching_existing_output(self):
        output = self.root / "reference.dxf"
        output.write_text("keep-existing", encoding="utf-8")
        result = self.run_cli(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep-existing")
        self.assertEqual(list(self.root.glob(".*.staging-*")), [])


if __name__ == "__main__":
    unittest.main()
