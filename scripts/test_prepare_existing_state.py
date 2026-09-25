#!/usr/bin/env python3
"""Regression tests for reversible CAD screenshot preparation."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageOps


SCRIPT = Path(__file__).with_name("prepare_existing_state.py")


def pixels(image):
    """Return pixel data across supported Pillow releases."""
    return list(image.getdata())


class PrepareExistingStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="existing-state-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        image = Image.new("RGB", (8, 6))
        for y in range(6):
            for x in range(8):
                image.putpixel((x, y), (x * 20, y * 30, (x + y) * 10))
        image.save(self.source)
        self.original = self.source.read_bytes()
        self.target = self.root / "output.png"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(["python3", str(SCRIPT), *map(str, args)], capture_output=True, text=True)

    def test_dry_run_does_not_write_and_reports_crop_mapping(self):
        result = self.run_cli("--input", self.source, "--crop", "2,1,7,5", "--output", self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["crop_px"], [2, 1, 7, 5])
        self.assertEqual(report["output_size_px"], [5, 4])
        self.assertFalse(report["geometry_pixels_rescaled"])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_execute_preserves_exact_crop_pixels_and_source(self):
        result = self.run_cli("--input", self.source, "--crop", "2,1,7,5", "--output", self.target, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(self.source) as source, Image.open(self.target) as output:
            self.assertEqual(output.size, (5, 4))
            self.assertEqual(pixels(output), pixels(source.crop((2, 1, 7, 5))))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_cad_dark_is_exact_grayscale_inversion_of_crop(self):
        result = self.run_cli("--input", self.source, "--crop", "1,1,6,5", "--tone", "cad-dark", "--output", self.target, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(self.source) as source, Image.open(self.target) as output:
            expected = ImageOps.invert(ImageOps.grayscale(source.crop((1, 1, 6, 5))))
            self.assertEqual(pixels(output.convert("L")), pixels(expected))

    def test_crop_uses_exif_display_orientation_and_records_both_size_frames(self):
        source = self.root / "exif-rotated.png"
        oriented_output = self.root / "exif-crop.png"
        report_path = self.root / "exif-crop.json"
        image = Image.new("RGB", (6, 8))
        for y in range(8):
            for x in range(6):
                image.putpixel((x, y), (x * 30, y * 20, (x + y) * 10))
        exif = Image.Exif()
        exif[274] = 6
        image.save(source, exif=exif)
        with Image.open(source) as opened:
            self.assertEqual(opened.size, (6, 8))
            self.assertEqual(opened.getexif().get(274), 6)
            expected = ImageOps.exif_transpose(opened).crop((2, 1, 7, 5))

        result = self.run_cli("--input", source, "--crop", "2,1,7,5", "--output", oriented_output,
                              "--report", report_path, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["source_size_px"], [6, 8])
        self.assertEqual(report["source_display_size_px"], [8, 6])
        self.assertEqual(report["exif_orientation"], 6)
        self.assertEqual(report["crop_px"], [2, 1, 7, 5])
        with Image.open(oriented_output) as output:
            self.assertEqual(output.size, (5, 4))
            self.assertEqual(pixels(output), pixels(expected))
            self.assertIsNone(output.getexif().get(274))

    def test_execute_writes_optional_reproducibility_record_bound_to_both_images(self):
        report_path = self.root / "output.json"
        result = self.run_cli("--input", self.source, "--crop", "2,1,7,5", "--output", self.target,
                              "--report", report_path, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["crop_px"], [2, 1, 7, 5])
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(report["output_sha256"], hashlib.sha256(self.target.read_bytes()).hexdigest())
        self.assertEqual(report["output_size_px"], [5, 4])
        self.assertFalse(report["geometry_pixels_rescaled"])

    def test_refuses_overwrite_source_and_out_of_bounds_crop(self):
        overwrite = self.run_cli("--input", self.source, "--output", self.source, "--execute")
        self.assertNotEqual(overwrite.returncode, 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.target.write_text("sentinel")
        existing = self.run_cli("--input", self.source, "--output", self.target, "--execute")
        self.assertNotEqual(existing.returncode, 0)
        self.assertEqual(self.target.read_text(), "sentinel")
        invalid = self.run_cli("--input", self.source, "--crop", "0,0,9,6", "--output", self.root / "bad.png")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("超出按 EXIF 方向显示的原图", invalid.stderr)

    def test_refuses_existing_reproducibility_record(self):
        report_path = self.root / "output.json"
        report_path.write_text("keep-existing")
        result = self.run_cli("--input", self.source, "--output", self.target, "--report", report_path, "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report_path.read_text(), "keep-existing")
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
