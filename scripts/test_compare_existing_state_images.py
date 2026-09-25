#!/usr/bin/env python3
"""Tests for non-destructive comparison of a reference and cleaned CAD image."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).with_name("compare_existing_state_images.py")


class CompareExistingStateImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="state-image-compare-test-")
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.png"
        self.cleaned = self.root / "cleaned.png"
        Image.new("RGB", (12, 8), "white").save(self.reference)
        cleaned_image = Image.new("RGB", (12, 8), "white")
        for x in range(3, 9):
            cleaned_image.putpixel((x, 4), (0, 0, 0))
        cleaned_image.save(self.cleaned)
        self.original_bytes = (self.reference.read_bytes(), self.cleaned.read_bytes())
        self.output = self.root / "review.png"
        self.report = self.root / "review.json"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            capture_output=True,
            text=True,
        )

    def base_args(self):
        return ["--reference", self.reference, "--cleaned", self.cleaned,
                "--output", self.output, "--report", self.report]

    def test_dry_run_does_not_write_and_reports_same_canvas_and_manual_gate(self):
        result = self.run_cli(*self.base_args())
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["canvas_match"])
        self.assertFalse(report["automatic_geometry_verification"])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())
        self.assertEqual((self.reference.read_bytes(), self.cleaned.read_bytes()), self.original_bytes)

    def test_execute_writes_triptych_and_hash_bound_report(self):
        result = self.run_cli(*self.base_args(), "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        with Image.open(self.output) as review:
            self.assertEqual(review.size, tuple(report["review_sheet_size_px"]))
            self.assertEqual(review.size, (12 * 3 + 16 * 4, 8 + 44 + 16 * 2))
            self.assertEqual(review.getpixel((16 + 3, 16 + 44 + 4)), (255, 255, 255))
            self.assertEqual(review.getpixel((16 + 12 + 16 + 3, 16 + 44 + 4)), (0, 0, 0))
            self.assertEqual(review.getpixel((16 + 2 * (12 + 16) + 3, 16 + 44 + 4)), (127, 127, 127))
        self.assertEqual(report["reference_sha256"], hashlib.sha256(self.reference.read_bytes()).hexdigest())
        self.assertEqual(report["cleaned_sha256"], hashlib.sha256(self.cleaned.read_bytes()).hexdigest())
        self.assertEqual(report["review_sheet_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual((self.reference.read_bytes(), self.cleaned.read_bytes()), self.original_bytes)

    def test_refuses_canvas_mismatch_without_rescaling(self):
        Image.new("RGB", (11, 8), "white").save(self.cleaned)
        result = self.run_cli(*self.base_args(), "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止自动缩放或配准", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())

    def test_explicit_uniform_scale_normalizes_same_aspect_image_and_records_transform(self):
        raw = self.root / "larger-cleaned.png"
        normalized = self.root / "normalized.png"
        review = self.root / "scaled-review.png"
        report_path = self.root / "scaled-review.json"
        Image.new("RGB", (24, 16), "white").save(raw)
        result = self.run_cli("--reference", self.reference, "--cleaned", raw,
                              "--normalized-cleaned-output", normalized,
                              "--normalize-uniform-scale", "--output", review,
                              "--report", report_path, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["cleaned_candidate"], str(normalized.resolve()))
        self.assertEqual(report["cleaned_size_px"], [12, 8])
        self.assertEqual(report["normalization"]["scale_xy"], [0.5, 0.5])
        self.assertEqual(report["normalization"]["resampling"], "Lanczos")
        with Image.open(normalized) as image:
            self.assertEqual(image.size, (12, 8))

    def test_refuses_nonmatching_aspect_ratio_even_if_normalization_requested(self):
        raw = self.root / "wrong-aspect.png"
        Image.new("RGB", (24, 15), "white").save(raw)
        result = self.run_cli("--reference", self.reference, "--cleaned", raw,
                              "--normalized-cleaned-output", self.root / "normalized.png",
                              "--normalize-uniform-scale", "--output", self.output,
                              "--report", self.report, "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("拒绝裁切、补边或非等比拉伸", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())

    def test_refuses_overwrite_of_either_output(self):
        self.output.write_text("keep-image")
        result = self.run_cli(*self.base_args(), "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), "keep-image")
        self.assertFalse(self.report.exists())


if __name__ == "__main__":
    unittest.main()
