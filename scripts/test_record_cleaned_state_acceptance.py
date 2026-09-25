#!/usr/bin/env python3
"""End-to-end tests for binding a reviewed AI-cleaned image to source provenance."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


SCRIPTS = Path(__file__).parent
PREPARE = SCRIPTS / "prepare_existing_state.py"
COMPARE = SCRIPTS / "compare_existing_state_images.py"
RECORD = SCRIPTS / "record_cleaned_state_acceptance.py"
BUILD = SCRIPTS / "build_floorplan_annotator.py"
PYTHON = sys.executable


class RecordCleanedStateAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cleaned-state-acceptance-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.reference = self.root / "reference.png"
        self.base_report = self.root / "reference.json"
        self.cleaned = self.root / "cleaned.png"
        self.review = self.root / "review.png"
        self.comparison_report = self.root / "comparison.json"
        self.accepted_report = self.root / "accepted.json"
        self.annotator = self.root / "annotator.html"
        source = Image.new("RGB", (30, 20), "black")
        for x in range(5, 25):
            source.putpixel((x, 10), (255, 255, 255))
        source.save(self.source)
        result = self.run_command(PREPARE, "--input", self.source, "--tone", "cad-dark",
                                  "--output", self.reference, "--report", self.base_report, "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Synthetic edit fixture: same pixel dimensions and geometry, with no real user image.
        with Image.open(self.reference) as image:
            cleaned = image.convert("RGB")
        cleaned.putpixel((1, 1), (240, 240, 240))
        cleaned.save(self.cleaned)
        compare_result = self.run_command(COMPARE, "--reference", self.reference, "--cleaned", self.cleaned,
                                          "--output", self.review, "--report", self.comparison_report, "--execute")
        self.assertEqual(compare_result.returncode, 0, compare_result.stderr)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def run_command(script, *args):
        return subprocess.run([PYTHON, str(script), *map(str, args)], capture_output=True, text=True)

    def record_args(self):
        return ["--reference-report", self.base_report, "--reference", self.reference,
                "--cleaned", self.cleaned, "--comparison-report", self.comparison_report,
                "--confirmation", "用户确认清洗现状图可用于本项目注释器", "--output", self.accepted_report]

    def test_dry_run_and_execution_bind_source_candidate_comparison_and_user_confirmation(self):
        preview = self.run_command(RECORD, *self.record_args())
        self.assertEqual(preview.returncode, 0, preview.stderr)
        preview_report = json.loads(preview.stdout)
        self.assertFalse(preview_report["execute"])
        self.assertEqual(preview_report["output"], str(self.cleaned.resolve()))
        self.assertEqual(preview_report["output_sha256"], json.loads(self.comparison_report.read_text())["cleaned_sha256"])
        self.assertFalse(self.accepted_report.exists())

        result = self.run_command(RECORD, *self.record_args(), "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        accepted = json.loads(self.accepted_report.read_text(encoding="utf-8"))
        self.assertTrue(accepted["execute"])
        self.assertEqual(accepted["output"], str(self.cleaned.resolve()))
        self.assertEqual(accepted["ai_cleanup_review"]["comparison_report"], str(self.comparison_report.resolve()))
        self.assertIn("用户确认", accepted["ai_cleanup_review"]["user_confirmation"])

    def test_accepted_report_is_consumable_by_annotator_and_keeps_provenance(self):
        result = self.run_command(RECORD, *self.record_args(), "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        build = self.run_command(BUILD, "--project", "Synthetic QA", "--existing", self.cleaned,
                                 "--existing-state-report", self.accepted_report,
                                 "--output", self.annotator, "--execute")
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertTrue(self.annotator.is_file())
        html = self.annotator.read_text(encoding="utf-8")
        config_match = re.search(r'<script id="drawing-config" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(config_match)
        config = json.loads(config_match.group(1))
        cleanup = config["existingState"]["aiCleanupReview"]
        self.assertEqual(cleanup["comparison_report"], str(self.comparison_report.resolve()))
        self.assertEqual(cleanup["reference_sha256"], json.loads(self.comparison_report.read_text())["reference_sha256"])

    def test_uniformly_normalized_candidate_keeps_transform_through_annotator(self):
        raw = self.root / "cleaned-large.png"
        normalized = self.root / "cleaned-normalized.png"
        review = self.root / "normalized-review.png"
        compare_report = self.root / "normalized-comparison.json"
        accepted_report = self.root / "normalized-accepted.json"
        annotator = self.root / "normalized-annotator.html"
        with Image.open(self.cleaned) as image:
            image.resize((60, 40), Image.Resampling.NEAREST).save(raw)
        compare = self.run_command(COMPARE, "--reference", self.reference, "--cleaned", raw,
                                   "--normalize-uniform-scale", "--normalized-cleaned-output", normalized,
                                   "--output", review, "--report", compare_report, "--execute")
        self.assertEqual(compare.returncode, 0, compare.stderr)
        record_args = ["--reference-report", self.base_report, "--reference", self.reference,
                       "--cleaned", normalized, "--comparison-report", compare_report,
                       "--confirmation", "用户确认已规范尺寸的清洗候选", "--output", accepted_report]
        record = self.run_command(RECORD, *record_args, "--execute")
        self.assertEqual(record.returncode, 0, record.stderr)
        build = self.run_command(BUILD, "--project", "Synthetic normalized QA", "--existing", normalized,
                                 "--existing-state-report", accepted_report,
                                 "--output", annotator, "--execute")
        self.assertEqual(build.returncode, 0, build.stderr)
        html = annotator.read_text(encoding="utf-8")
        config_match = re.search(r'<script id="drawing-config" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(config_match)
        cleanup = json.loads(config_match.group(1))["existingState"]["aiCleanupReview"]
        self.assertEqual(cleanup["normalization"]["scale_xy"], [0.5, 0.5])
        self.assertEqual(cleanup["raw_cleaned_candidate"], str(raw.resolve()))

    def test_rejects_changed_candidate_and_missing_comparison_or_confirmation(self):
        with Image.open(self.cleaned) as image:
            altered = image.convert("RGB")
        altered.putpixel((2, 2), (120, 120, 120))
        altered.save(self.cleaned)
        result = self.run_command(RECORD, *self.record_args(), "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("哈希", result.stderr)
        self.assertFalse(self.accepted_report.exists())

        args = self.record_args()
        args[args.index("--confirmation") + 1] = "x"
        result = self.run_command(RECORD, *args, "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("明确接受确认", result.stderr)
        self.assertFalse(self.accepted_report.exists())

    def test_refuses_existing_record(self):
        self.accepted_report.write_text("keep")
        result = self.run_command(RECORD, *self.record_args(), "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.accepted_report.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
