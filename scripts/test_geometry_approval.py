#!/usr/bin/env python3
"""Tests for exact-version geometry approval receipts."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry_approval import create_receipt, validate_receipt, validate_receipt_snapshot


class GeometryApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.geometry = self.root / "geometry.json"
        self.geometry.write_text(json.dumps({
            "schema_version": 1, "units": "mm",
            "source_ledger": [{"id": "synthetic", "kind": "synthetic_fixture", "locator": "unit test fixture only"}],
            "rooms": [{"id": "R01", "source_refs": ["synthetic"],
                "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "walls": [{"source_refs": ["synthetic"]} for _ in range(4)],
            }],
        }), encoding="utf-8")
        self.approval = self.root / "approval.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_receipt(self, confirmation="用户确认该版房间几何"):
        receipt = create_receipt(self.geometry, confirmation)
        self.approval.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt

    def test_receipt_matches_exact_geometry_bytes(self):
        receipt = self.write_receipt()
        checked = validate_receipt(self.geometry, self.approval)
        self.assertEqual(checked["geometry_sha256"], receipt["geometry_sha256"])
        self.assertEqual(checked["status"], "user_confirmed")

    def test_approval_must_match_the_already_loaded_geometry_snapshot(self):
        self.write_receipt()
        loaded_snapshot = self.geometry.read_bytes()
        self.assertEqual(validate_receipt_snapshot(self.geometry, self.approval, loaded_snapshot)["status"],
                         "user_confirmed")
        with self.assertRaisesRegex(ValueError, "changed after it was loaded"):
            validate_receipt_snapshot(self.geometry, self.approval, loaded_snapshot + b" ")

    def test_geometry_edit_invalidates_receipt(self):
        self.write_receipt()
        self.geometry.write_text(json.dumps({
            "schema_version": 1, "units": "mm",
            "source_ledger": [{"id": "synthetic", "kind": "synthetic_fixture", "locator": "unit test fixture only"}],
            "rooms": [{"id": "R01", "source_refs": ["synthetic"],
                "polygon": [[0, 0], [4100, 0], [4100, 3000], [0, 3000]],
                "walls": [{"source_refs": ["synthetic"]} for _ in range(4)],
            }],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after user confirmation"):
            validate_receipt(self.geometry, self.approval)

    def test_receipt_is_bound_to_geometry_file_path_not_only_content_hash(self):
        receipt = self.write_receipt()
        copied_geometry = self.root / "copied-geometry.json"
        copied_geometry.write_bytes(self.geometry.read_bytes())
        self.assertEqual(receipt["geometry_sha256"], hashlib.sha256(copied_geometry.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "different geometry file path"):
            validate_receipt(copied_geometry, self.approval)

    def test_empty_confirmation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit confirmation"):
            create_receipt(self.geometry, "  ")

    def test_source_traceability_is_required_before_confirmation(self):
        data = json.loads(self.geometry.read_text(encoding="utf-8"))
        data["rooms"][0]["walls"][0].pop("source_refs")
        self.geometry.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source audit is needs_sources"):
            create_receipt(self.geometry, "用户确认该版房间几何")

    def test_receipt_validation_rechecks_source_traceability(self):
        receipt = self.write_receipt()
        data = json.loads(self.geometry.read_text(encoding="utf-8"))
        data["rooms"][0]["walls"][0].pop("source_refs")
        self.geometry.write_text(json.dumps(data), encoding="utf-8")
        receipt["geometry_sha256"] = hashlib.sha256(self.geometry.read_bytes()).hexdigest()
        self.approval.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source audit is needs_sources"):
            validate_receipt(self.geometry, self.approval)

    def test_unsupported_or_unconfirmed_receipt_is_rejected(self):
        self.approval.write_text(json.dumps({"schema_version": 1, "status": "draft"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a supported user-confirmation"):
            validate_receipt(self.geometry, self.approval)

    def test_boolean_schema_version_is_not_accepted_as_integer_version_one(self):
        receipt = self.write_receipt()
        receipt["schema_version"] = True
        self.approval.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a supported user-confirmation"):
            validate_receipt(self.geometry, self.approval)


if __name__ == "__main__":
    unittest.main()
