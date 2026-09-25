#!/usr/bin/env python3
"""Offline regression tests for the self-contained plan-annotator generator."""

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPT = Path(__file__).with_name("build_floorplan_annotator.py")
PREPARE_SCRIPT = Path(__file__).with_name("prepare_existing_state.py")


class AnnotatorGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="annotator-generator-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.png"
        self.existing = self.root / "existing.png"
        self.existing_report = self.root / "existing-report.json"
        self.layout = self.root / "layout.png"
        self.output = self.root / "project.html"
        for path, color in ((self.source, "white"), (self.layout, "#eeeeee")):
            image = Image.new("RGB", (48, 32), color)
            ImageDraw.Draw(image).rectangle((3, 4, 20, 25), outline="black", width=1)
            image.save(path)
        prepared = subprocess.run(
            [sys.executable, "-B", str(PREPARE_SCRIPT), "--input", str(self.source),
             "--output", str(self.existing), "--report", str(self.existing_report), "--execute"],
            capture_output=True, text=True, check=False,
        )
        if prepared.returncode != 0:
            raise AssertionError(prepared.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--project", "合成测试", "--project-id", "test-v1",
             "--existing", str(self.existing), "--existing-state-report", str(self.existing_report),
             "--output", str(self.output), *extra],
            capture_output=True, text=True, check=False,
        )

    def test_embeds_aligned_layers_as_self_contained_html(self):
        result = self.run_cli("--layout", str(self.layout), "--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        html = self.output.read_text(encoding="utf-8")
        config_text = html.split('<script id="drawing-config" type="application/json">', 1)[1].split("</script>", 1)[0]
        config = json.loads(config_text)
        self.assertEqual((config["width"], config["height"]), (48, 32))
        self.assertEqual(config["existingState"]["sourceSha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(config["existingState"]["outputSha256"], hashlib.sha256(self.existing.read_bytes()).hexdigest())
        self.assertEqual(config["existingState"]["cropPx"], [0, 0, 48, 32])
        self.assertEqual(config["existingState"]["sourceDisplaySizePx"], [48, 32])
        self.assertEqual(config["existingState"]["exifOrientation"], 1)
        self.assertNotEqual(config["existingHash"], config["layoutHash"])
        self.assertEqual(base64.b64decode(config["existingImage"].split(",", 1)[1])[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(base64.b64decode(config["layoutImage"].split(",", 1)[1])[:8], b"\x89PNG\r\n\x1a\n")
        self.assertNotIn("{{", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn("<link rel=\"stylesheet\" href=", html)

    def test_without_layout_uses_existing_image_as_initial_copy(self):
        result = self.run_cli("--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        config_text = self.output.read_text(encoding="utf-8").split(
            '<script id="drawing-config" type="application/json">', 1)[1].split("</script>", 1)[0]
        config = json.loads(config_text)
        self.assertEqual(config["existingHash"], config["layoutHash"])
        self.assertEqual(config["existingImage"], config["layoutImage"])

    def test_mismatched_canvas_dimensions_are_rejected_without_output(self):
        Image.new("RGB", (49, 32), "white").save(self.layout)
        result = self.run_cli("--layout", str(self.layout), "--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("必须完全相同", result.stderr)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.write_text("user sentinel", encoding="utf-8")
        result = self.run_cli("--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "user sentinel")

    def test_rejects_mismatched_or_tampered_existing_state_report(self):
        report = json.loads(self.existing_report.read_text(encoding="utf-8"))
        report["output_sha256"] = "0" * 64
        self.existing_report.write_text(json.dumps(report), encoding="utf-8")
        result = self.run_cli("--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 不一致", result.stderr)
        self.assertFalse(self.output.exists())

    def test_rejects_report_with_inconsistent_crop_mapping(self):
        report = json.loads(self.existing_report.read_text(encoding="utf-8"))
        report["crop_px"] = [0, 0, 47, 32]
        self.existing_report.write_text(json.dumps(report), encoding="utf-8")
        result = self.run_cli("--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("互相矛盾", result.stderr)
        self.assertFalse(self.output.exists())

    def test_exif_rotated_source_validates_crop_in_display_coordinate_frame(self):
        source = self.root / "exif-source.png"
        existing = self.root / "exif-existing.png"
        report_path = self.root / "exif-report.json"
        output = self.root / "exif-project.html"
        image = Image.new("RGB", (6, 8), "white")
        exif = Image.Exif()
        exif[274] = 6
        image.save(source, exif=exif)
        prepared = subprocess.run(
            [sys.executable, "-B", str(PREPARE_SCRIPT), "--input", str(source), "--crop", "2,1,7,5",
             "--output", str(existing), "--report", str(report_path), "--execute"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.existing, self.existing_report, self.output = existing, report_path, output
        result = self.run_cli("--execute")
        self.assertEqual(result.returncode, 0, result.stderr)
        html = output.read_text(encoding="utf-8")
        config_text = html.split('<script id="drawing-config" type="application/json">', 1)[1].split("</script>", 1)[0]
        existing_state = json.loads(config_text)["existingState"]
        self.assertEqual(existing_state["sourceSizePx"], [6, 8])
        self.assertEqual(existing_state["sourceDisplaySizePx"], [8, 6])
        self.assertEqual(existing_state["exifOrientation"], 6)
        self.assertEqual(existing_state["cropPx"], [2, 1, 7, 5])

    def test_layer_switch_preserves_registered_zoom_and_pan(self):
        script = (SCRIPT.parents[1] / "assets" / "drawing-viewer" / "blank-template.js").read_text(encoding="utf-8")
        switch = script.split("function switchView(name)", 1)[1].split("function draftItem", 1)[0]
        self.assertNotIn("fit()", switch)
        self.assertIn("switchView('existing'); fit();", script)


if __name__ == "__main__":
    unittest.main()
