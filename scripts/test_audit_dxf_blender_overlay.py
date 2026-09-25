#!/usr/bin/env python3
"""Safety tests for the DXF/Blender overlay audit command."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf
from audit_dxf_blender_overlay import verify_geometry_provenance

SCRIPT = Path(__file__).with_name("audit_dxf_blender_overlay.py")


class OverlayAuditCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="overlay-audit-test-")
        self.root = Path(self.temp.name)
        self.dxf = self.root / "input.dxf"
        self.blend = self.root / "input.blend"
        self.blender = self.root / "blender"
        self.output = self.root / "audit.png"
        for path in (self.dxf, self.blend, self.blender):
            path.write_text("fixture", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *extra):
        return subprocess.run([
            sys.executable, str(SCRIPT), "--dxf", str(self.dxf),
            "--blend", str(self.blend), "--blender", str(self.blender),
            "--output", str(self.output), *extra,
        ], capture_output=True, text=True)

    def test_default_is_preview_and_does_not_write(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY_RUN", result.stdout)
        self.assertFalse(self.output.exists())

    def test_refuses_to_overwrite_existing_output_before_running_blender(self):
        self.output.write_bytes(b"keep-existing")
        result = self.invoke("--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        self.assertEqual(self.output.read_bytes(), b"keep-existing")


class OverlayProvenanceTests(unittest.TestCase):
    def make_doc(self, digest="a" * 64):
        doc = ezdxf.new("R2010")
        doc.appids.new("XIONG_META")
        doc.layers.new("A-NOTE")
        note = doc.modelspace().add_text("non-construction reference", dxfattribs={"layer": "A-NOTE"})
        note.set_xdata("XIONG_META", [(1000, f"geometry_sha256:{digest}"), (1070, 1)])
        return doc

    def test_matching_geometry_hash_is_accepted(self):
        self.assertEqual(verify_geometry_provenance(self.make_doc(), "a" * 64), "a" * 64)

    def test_mismatched_geometry_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "different geometry JSON versions"):
            verify_geometry_provenance(self.make_doc(), "b" * 64)

    def test_missing_dxf_provenance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing its geometry provenance"):
            verify_geometry_provenance(ezdxf.new("R2010"), "a" * 64)

    def test_missing_blender_provenance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Blender scene is missing"):
            verify_geometry_provenance(self.make_doc(), None)


if __name__ == "__main__":
    unittest.main()
