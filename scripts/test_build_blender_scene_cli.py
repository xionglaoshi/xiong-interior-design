#!/usr/bin/env python3
"""CLI safety tests for Blender scene generation without writing artifacts."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from geometry_approval import create_receipt

BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
SCRIPT = Path(__file__).with_name("build_blender_scene.py")


class BlenderSceneCliSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blender-scene-cli-")
        self.root = Path(self.temp.name)
        self.geometry = self.root / "geometry.json"
        self.output = self.root / "not-created" / "scene"
        self.approval = self.root / "approval.json"
        self.geometry.write_text(json.dumps({
            "schema_version": 1, "units": "mm", "wall_height_mm": 2800,
            "project": "合成CLI测试",
            "source_ledger": [{"id": "fixture", "kind": "synthetic_fixture", "locator": "仅用于CLI回归"}],
            "rooms": [{"id": "R01", "name": "测试房间", "source_refs": ["fixture"],
                       "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                       "wall_thickness_mm": 120,
                       "walls": [{"source_refs": ["fixture"]} for _ in range(4)]}],
            "furniture": [], "fixed_elements": [],
        }), encoding="utf-8")
        self.approval.write_text(json.dumps(create_receipt(self.geometry, "合成CLI测试凭据")), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *extra):
        return subprocess.run([
            str(BLENDER), "--background", "--factory-startup", "--python", str(SCRIPT), "--",
            "--input", str(self.geometry), "--output-dir", str(self.output), *extra,
        ], capture_output=True, text=True, timeout=90, check=False)

    def test_dry_run_does_not_create_output_tree(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY_RUN", result.stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.parent.exists())

    def test_execute_without_approval_refuses_before_creating_output_tree(self):
        result = self.invoke("--execute")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--approval is required", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.parent.exists())

    def test_successful_build_delivers_a_human_readable_manifest_with_the_model(self):
        result = self.invoke("--approval", str(self.approval), "--execute")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = {"interior-scene.blend", "interior-scene.glb", "index.html", "使用说明.md",
                    "model-viewer-4.3.1.min.js", "model-viewer-4.3.1-LICENSE.txt",
                    "lit-BSD-3-Clause-LICENSE.txt", "threejs-MIT-LICENSE.txt"}
        self.assertEqual({path.name for path in self.output.iterdir()}, expected)
        note = (self.output / "使用说明.md").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8890/index.html", note)
        self.assertIn("测试房间", note)
        self.assertIn("不是施工图", note)
        self.assertIn(create_receipt(self.geometry, "unused")["geometry_sha256"], note)


if __name__ == "__main__":
    unittest.main()
