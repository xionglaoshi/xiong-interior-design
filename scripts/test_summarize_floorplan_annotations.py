#!/usr/bin/env python3
"""Regression tests for the provenance-only annotation indexer."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from audit_geometry_sources import audit

SCRIPT = Path(__file__).with_name("summarize_floorplan_annotations.py")


class AnnotationIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="annotation-index-test-")
        self.root = Path(self.temp.name)
        self.source = self.root / "marks.json"
        self.output = self.root / "index.json"
        self.payload = {
            "schema_version": 1, "project_id": "demo-v1", "project": "演示",
            "canvas_px": [800, 600], "existing_hash": "a"*64, "layout_hash": "b"*64,
            "existing_state": {"sourceSha256": "c"*64}, "exported_at": "2026-09-24T00:00:00Z",
            "views": {
                "existing": [{"kind": "rect", "x": 20, "y": 30, "w": 100, "h": 80},
                             {"kind": "text", "x": 24, "y": 36, "text": "这里作为大厅"}],
                "layout": [{"kind": "line", "x1": 200, "y1": 210, "x2": 260, "y2": 210},
                           {"kind": "circle", "x": 300, "y": 300, "w": 40, "h": 40}],
            },
        }
        self.write_payload()

    def tearDown(self):
        self.temp.cleanup()

    def write_payload(self):
        self.source.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, execute=False):
        args = [sys.executable, "-B", str(SCRIPT), "--input", str(self.source), "--output", str(self.output)]
        if execute:
            args.append("--execute")
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def test_dry_run_is_read_only(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY_RUN marks=4", result.stdout)
        self.assertFalse(self.output.exists())

    def test_output_preserves_provenance_without_semantic_inference(self):
        result = self.run_cli(execute=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["interpretation_status"], "needs_interpretation")
        self.assertIn("不判断标注语义", report["notice"])
        self.assertEqual(report["marks"][0]["source_id"], "annotation:demo-v1:existing:001")
        self.assertEqual(report["marks"][1]["text_exact"], "这里作为大厅")
        self.assertEqual(report["marks"][2]["bounds_px"], [200, 210, 260, 210])
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.assertEqual(report["annotation_sha256"], digest)
        self.assertTrue(all(row["sha256"] == digest for row in report["source_ledger"]))
        self.assertEqual([r["id"] for r in report["source_ledger"]], [m["source_id"] for m in report["marks"]])

    def test_annotation_ledger_is_accepted_by_geometry_audit_and_detects_tampering(self):
        self.assertEqual(self.run_cli(execute=True).returncode, 0)
        index = json.loads(self.output.read_text(encoding="utf-8"))
        source = index["source_ledger"][0]
        geometry_source = {"id": "cad-basis", "kind": "cad", "locator": "合成测试：CAD尺寸依据占位"}
        geometry = {
            "source_ledger": [source, geometry_source],
            "rooms": [{"id": "R01", "source_refs": [source["id"], geometry_source["id"]], "walls": []}],
            "furniture": [], "fixed_elements": [],
        }
        geometry_path = self.root / "geometry.json"
        self.assertEqual(audit(geometry, geometry_path)["status"], "complete")
        geometry["rooms"][0]["source_refs"] = [source["id"]]
        report = audit(geometry, geometry_path)
        self.assertEqual(report["status"], "needs_sources")
        self.assertTrue(any("do not by themselves establish CAD dimensions/geometry" in item for item in report["warnings"]))
        geometry["rooms"][0]["source_refs"] = [source["id"], geometry_source["id"]]
        self.source.write_text(self.source.read_text(encoding="utf-8") + " ", encoding="utf-8")
        report = audit(geometry, geometry_path)
        self.assertEqual(report["status"], "errors")
        self.assertTrue(any("hash mismatch" in error for error in report["errors"]))

    def test_rejects_out_of_canvas_mark(self):
        self.payload["views"]["existing"][0]["x"] = 750
        self.write_payload()
        result = self.run_cli(execute=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_bad_canvas_hash(self):
        self.payload["layout_hash"] = "not-a-hash"
        self.write_payload()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)

    def test_refuses_overwrite(self):
        self.output.write_text("sentinel", encoding="utf-8")
        result = self.run_cli(execute=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "sentinel")


if __name__ == "__main__":
    unittest.main()
