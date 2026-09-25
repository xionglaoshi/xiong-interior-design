#!/usr/bin/env python3
"""Browser-test the viewer's user-facing message for a corrupt GLB."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from viewer_assets import copy_viewer_assets, render_viewer_page


SCRIPT_DIR = Path(__file__).resolve().parent


class ModelViewerFailureStateTests(unittest.TestCase):
    def test_corrupt_glb_shows_clear_error_without_external_requests(self):
        with tempfile.TemporaryDirectory(prefix="viewer-corrupt-glb-") as temp:
            directory = Path(temp)
            copy_viewer_assets(directory)
            (directory / "interior-scene.glb").write_bytes(b"not a GLB file")
            options = ['<option value="">选择房间…</option>']
            page = render_viewer_page("Synthetic failure test", "interior-scene.glb", options,
                                      "Walls - soft white")
            (directory / "index.html").write_text(page, encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(SCRIPT_DIR / "test_model_viewer_browser.py"),
                "--directory", str(directory), "--expect-model-error", "--timeout", "15",
            ], capture_output=True, text=True, timeout=45, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("MODEL_VIEWER_FAILURE_STATE_OK", result.stdout)
            self.assertIn("模型加载失败", result.stdout)
            self.assertIn('"external_resources": []', result.stdout)


if __name__ == "__main__":
    unittest.main()
