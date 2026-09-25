#!/usr/bin/env python3
"""Regression tests for CAD-anchored image-to-mm coordinate calibration."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from calibrate_drawing_coordinates import calibrate, fit_affine


def sample_data():
    # Screen Y grows downward; CAD Y is configured to grow upward.
    return {
        "schema_version": 1,
        "source_image": "synthetic-only.png",
        "source_image_sha256": "0" * 64,
        "source_image_px": [600, 500],
        "anchors": [
            {"pixel": [100, 100], "cad_mm": [0, 3000]},
            {"pixel": [500, 100], "cad_mm": [4000, 3000]},
            {"pixel": [100, 400], "cad_mm": [0, 0]},
            {"pixel": [500, 400], "cad_mm": [4000, 0]},
        ],
        "verification_points": [
            {"id": "independent-top-midpoint", "pixel": [300, 100], "cad_mm": [2000, 3000]},
        ],
        "points": [{"id": "corner", "pixel": [300, 250]}],
    }


class CalibrationTests(unittest.TestCase):
    def test_maps_pixel_y_down_to_cad_mm(self):
        result = calibrate(sample_data(), 1)
        self.assertEqual(result["points"][0]["point_mm"], [2000.0, 1500.0])
        self.assertLess(result["calibration"]["max_residual_mm"], 1e-6)
        self.assertEqual(result["calibration"]["independent_verification_count"], 1)
        self.assertEqual(result["calibration"]["max_independent_error_mm"], 0)
        self.assertGreaterEqual(result["calibration"]["anchor_spread_ratio"], 1e-4)

    def test_rejects_fewer_than_four_anchors(self):
        with self.assertRaisesRegex(ValueError, "At least 4"):
            fit_affine(sample_data()["anchors"][:3])

    def test_rejects_collinear_anchors(self):
        anchors = [{"pixel": [i * 100, i * 100], "cad_mm": [i * 1000, i * 1000]} for i in range(4)]
        with self.assertRaisesRegex(ValueError, "collinear|unstable"):
            fit_affine(anchors)

    def test_rejects_nearly_collinear_anchors_even_with_zero_fit_error(self):
        anchors = [
            {"pixel": [100, 100], "cad_mm": [0, 3000]},
            {"pixel": [500, 100], "cad_mm": [4000, 3000]},
            {"pixel": [100, 100.1], "cad_mm": [0, 0]},
            {"pixel": [500, 100.1], "cad_mm": [4000, 0]},
        ]
        with self.assertRaisesRegex(ValueError, "nearly collinear"):
            fit_affine(anchors)

    def test_rejects_inconsistent_anchor_when_residual_too_large(self):
        data = sample_data()
        data["anchors"][3]["cad_mm"] = [4200, 0]
        with self.assertRaisesRegex(ValueError, "exceeds limit"):
            calibrate(data, 2)

    def test_rejects_independent_check_mismatch_even_when_fit_residual_is_zero(self):
        data = sample_data()
        data["verification_points"][0]["cad_mm"] = [2100, 3000]
        with self.assertRaisesRegex(ValueError, "Independent CAD check.*exceeds limit"):
            calibrate(data, 5)

    def test_requires_independent_cad_check_point(self):
        data = sample_data()
        data["verification_points"] = []
        with self.assertRaisesRegex(ValueError, "independent verification_point"):
            calibrate(data, 5)

    def test_rejects_independent_check_outside_image_canvas(self):
        data = sample_data()
        data["verification_points"][0]["pixel"] = [601, 100]
        with self.assertRaisesRegex(ValueError, "outside source image canvas"):
            calibrate(data, 5)

    def test_rejects_verification_point_reusing_fit_anchor_pixel(self):
        data = sample_data()
        data["verification_points"][0]["pixel"] = [100, 100]
        with self.assertRaisesRegex(ValueError, "reuses a fitted anchor pixel"):
            calibrate(data, 5)

    def test_rejects_verification_point_reusing_fit_anchor_cad_coordinate(self):
        data = sample_data()
        data["verification_points"][0] = {
            "id": "independent-top-midpoint", "pixel": [200, 100], "cad_mm": [0, 3000],
        }
        with self.assertRaisesRegex(ValueError, "reuses a fitted anchor CAD coordinate"):
            calibrate(data, 5)

    def test_duplicate_point_ids_are_rejected(self):
        data = sample_data()
        data["points"].append({"id": "corner", "pixel": [200, 200]})
        with self.assertRaisesRegex(ValueError, "unique"):
            calibrate(data, 1)

    def test_non_finite_coordinates_are_rejected(self):
        data = sample_data()
        data["points"][0]["pixel"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            calibrate(data, 1)

    def test_rejects_pixel_outside_declared_image_canvas(self):
        data = sample_data()
        data["points"][0]["pixel"] = [601, 250]
        with self.assertRaisesRegex(ValueError, "outside source image canvas"):
            calibrate(data, 1)

    def test_requires_image_fingerprint_and_canvas_dimensions(self):
        data = sample_data()
        del data["source_image_sha256"]
        with self.assertRaisesRegex(ValueError, "source_image_sha256"):
            calibrate(data, 1)

    def run_cli(self, input_path, output_path, *extra):
        script = Path(__file__).with_name("calibrate_drawing_coordinates.py")
        return subprocess.run([sys.executable, "-B", str(script), "--input", str(input_path),
                               "--output", str(output_path), "--max-residual-mm", "1", *extra],
                              check=False, capture_output=True, text=True)

    def test_cli_accepts_exact_source_hash_and_rejects_replaced_image(self):
        with tempfile.TemporaryDirectory(prefix="coordinate-calibration-") as temp:
            root = Path(temp)
            image_path = root / "source.png"
            image = Image.new("RGB", (600, 500), "white")
            ImageDraw.Draw(image).rectangle((100, 100, 500, 400), outline="black")
            image.save(image_path)
            data = sample_data()
            image_bytes = image_path.read_bytes()
            data["source_image"] = str(image_path)
            data["source_image_sha256"] = hashlib.sha256(image_bytes).hexdigest()
            input_path = root / "calibration.json"
            input_path.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_cli(input_path, root / "result.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"source_image_px": [\n    600,\n    500\n  ]', result.stdout)
            self.assertFalse((root / "result.json").exists())

            executed = self.run_cli(input_path, root / "result-written.json", "--execute")
            self.assertEqual(executed.returncode, 0, executed.stderr)
            written = json.loads((root / "result-written.json").read_text(encoding="utf-8"))
            self.assertEqual(written["source_image_sha256"], data["source_image_sha256"])
            self.assertEqual(written["source_image_px"], [600, 500])

            data["source_image_px"] = [601, 500]
            input_path.write_text(json.dumps(data), encoding="utf-8")
            wrong_size = self.run_cli(input_path, root / "wrong-size-result.json")
            self.assertNotEqual(wrong_size.returncode, 0)
            self.assertIn("dimensions do not match", wrong_size.stderr)

            ImageDraw.Draw(image).point((20, 20), fill="red")
            image.save(image_path)
            data["source_image_px"] = [600, 500]
            input_path.write_text(json.dumps(data), encoding="utf-8")
            stale = self.run_cli(input_path, root / "stale-result.json")
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("SHA-256 does not match", stale.stderr)


if __name__ == "__main__":
    unittest.main()
