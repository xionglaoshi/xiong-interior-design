#!/usr/bin/env python3
"""Regression tests for the offline browser viewer runtime bundle."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from viewer_assets import ASSET_DIR, VIEWER_FILES, copy_viewer_assets, render_viewer_page

TEMPLATE = ASSET_DIR.parent / "model-viewer-template.html"


class ViewerAssetTests(unittest.TestCase):
    def test_copies_pinned_runtime_and_all_license_notices(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = copy_viewer_assets(temp_dir)
            self.assertEqual([path.name for path in outputs], list(VIEWER_FILES))
            for filename in VIEWER_FILES:
                source = ASSET_DIR / filename
                target = Path(temp_dir) / filename
                self.assertEqual(
                    hashlib.sha256(source.read_bytes()).digest(),
                    hashlib.sha256(target.read_bytes()).digest(),
                )
            self.assertIn("BSD 3-Clause", (Path(temp_dir) / VIEWER_FILES[2]).read_text())
            self.assertIn("MIT License", (Path(temp_dir) / VIEWER_FILES[3]).read_text())

    def test_refuses_to_overwrite_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / VIEWER_FILES[0]
            target.write_text("user data")
            with self.assertRaises(FileExistsError):
                copy_viewer_assets(temp_dir)
            self.assertEqual(target.read_text(), "user data")

    def test_force_replaces_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / VIEWER_FILES[0]
            target.write_text("stale runtime")
            copy_viewer_assets(temp_dir, force=True)
            self.assertEqual(target.read_bytes(), (ASSET_DIR / VIEWER_FILES[0]).read_bytes())

    def test_template_uses_local_runtime_and_explains_failures(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('src="model-viewer-4.3.1.min.js"', template)
        self.assertNotIn("ajax.googleapis.com", template)
        self.assertIn("三维查看器脚本加载失败", template)
        self.assertIn("浏览器 WebGL 支持", template)
        self.assertIn('id="toggle-walls"', template)
        self.assertIn("setBaseColorFactor", template)
        self.assertIn("仅改变浏览器中的显示，不修改或删除模型墙体", template)
        self.assertIn("{{WALL_MATERIAL_NAME}}", template)

    def test_template_rendering_escapes_user_strings_and_injects_material_name(self):
        page = render_viewer_page(
            'Project "A"', "scene file.glb",
            ['<option value="R01">R01 · room</option>'], 'Walls "special"',
        )
        self.assertIn("Project &quot;A&quot;", page)
        self.assertIn('src="scene file.glb"', page)
        self.assertIn('const wallMaterialName = "Walls \\\"special\\\"";', page)
        self.assertNotIn("{{", page)


if __name__ == "__main__":
    unittest.main()
