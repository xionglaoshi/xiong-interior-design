#!/usr/bin/env python3
"""Regression tests for the read-only geometry provenance audit."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from audit_geometry_sources import audit


def sample(source_refs: list[str] | None = None) -> dict:
    refs = source_refs or ["cad-floor"]
    return {
        "source_ledger": [{"id": "cad-floor", "kind": "cad", "locator": "A08层平面图，轴线交点1/A"}],
        "rooms": [{
            "id": "R01", "source_refs": refs,
            "walls": [{"source_refs": refs, "openings": [{"source_refs": refs}]}],
        }],
        "furniture": [{"id": "F01", "source_refs": refs}],
        "fixed_elements": [{"id": "C01", "source_refs": refs}],
    }


class GeometrySourceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.geometry_path = self.root / "geometry.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_complete_source_refs_cover_each_geometry_entity(self) -> None:
        report = audit(sample(), self.geometry_path)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["entities_checked"], 5)

    def test_missing_source_refs_are_warning_not_silently_complete(self) -> None:
        data = sample()
        data["rooms"][0]["walls"][0].pop("source_refs")
        report = audit(data, self.geometry_path)
        self.assertEqual(report["status"], "needs_sources")
        self.assertTrue(any("wall edge 1" in item for item in report["warnings"]))

    def test_annotation_only_marks_do_not_establish_geometry_basis(self) -> None:
        data = sample()
        data["source_ledger"].append({"id": "mark-1", "kind": "annotation", "locator": "layout rect pixels 100,100-300,300"})
        for entity in [data["rooms"][0], *data["rooms"][0]["walls"],
                       data["rooms"][0]["walls"][0]["openings"][0],
                       *data["furniture"], *data["fixed_elements"]]:
            entity["source_refs"] = ["mark-1"]
        report = audit(data, self.geometry_path)
        self.assertEqual(report["status"], "needs_sources")
        self.assertEqual(sum("do not by themselves establish CAD dimensions/geometry" in item for item in report["warnings"]), 5)

    def test_annotation_can_supplement_but_not_replace_cad_basis(self) -> None:
        data = sample(["cad-floor", "mark-1"])
        data["source_ledger"].append({"id": "mark-1", "kind": "annotation", "locator": "layout rect pixels 100,100-300,300"})
        self.assertEqual(audit(data, self.geometry_path)["status"], "complete")

    def test_unknown_source_id_is_error(self) -> None:
        report = audit(sample(["not-in-ledger"]), self.geometry_path)
        self.assertEqual(report["status"], "errors")
        self.assertTrue(any("unknown source id" in item for item in report["errors"]))

    def test_artifact_hash_is_checked_against_actual_file(self) -> None:
        artifact = self.root / "cad.txt"
        artifact.write_text("CAD evidence", encoding="utf-8")
        data = sample()
        data["source_ledger"][0].update({
            "artifact": "cad.txt",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        })
        self.assertEqual(audit(data, self.geometry_path)["status"], "complete")
        artifact.write_text("changed evidence", encoding="utf-8")
        report = audit(data, self.geometry_path)
        self.assertEqual(report["status"], "errors")
        self.assertTrue(any("hash mismatch" in item for item in report["errors"]))

    def test_unused_ledger_entry_is_warning(self) -> None:
        data = sample()
        data["source_ledger"].append({"id": "unused", "kind": "annotation", "locator": "未引用批注"})
        report = audit(data, self.geometry_path)
        self.assertEqual(report["status"], "needs_sources")
        self.assertTrue(any("not referenced" in item for item in report["warnings"]))

    def test_non_hex_sha256_is_error(self) -> None:
        artifact = self.root / "cad.txt"
        artifact.write_text("evidence", encoding="utf-8")
        data = sample()
        data["source_ledger"][0].update({"artifact": "cad.txt", "sha256": "z" * 64})
        report = audit(data, self.geometry_path)
        self.assertEqual(report["status"], "errors")
        self.assertTrue(any("hexadecimal SHA-256" in item for item in report["errors"]))

    def test_cli_exit_codes_distinguish_incomplete_from_invalid(self) -> None:
        script = Path(__file__).with_name("audit_geometry_sources.py")
        self.geometry_path.write_text(json.dumps(sample()), encoding="utf-8")
        complete = subprocess.run(
            [sys.executable, str(script), "--geometry", str(self.geometry_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.geometry_path.write_text(json.dumps(sample(["missing"])), encoding="utf-8")
        invalid = subprocess.run(
            [sys.executable, str(script), "--geometry", str(self.geometry_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(invalid.returncode, 1, invalid.stderr)

        incomplete_data = sample()
        incomplete_data["rooms"][0].pop("source_refs")
        self.geometry_path.write_text(json.dumps(incomplete_data), encoding="utf-8")
        incomplete = subprocess.run(
            [sys.executable, str(script), "--geometry", str(self.geometry_path)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(incomplete.returncode, 2, incomplete.stderr)
        self.assertIn('"status": "needs_sources"', incomplete.stdout)


if __name__ == "__main__":
    unittest.main()
