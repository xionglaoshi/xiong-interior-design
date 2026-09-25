#!/usr/bin/env python3
"""Run the reusable viewer template against a synthetic two-room GLB in Chrome.

All fixture HTML/runtime files live in TemporaryDirectory and are removed when
the test exits. The supplied source directory is read-only.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from viewer_assets import copy_viewer_assets, render_viewer_page


def main():
    parser = argparse.ArgumentParser(description="在无头Chrome中测试空白3D查看器模板与墙体开关。")
    parser.add_argument("--source-directory", required=True, type=Path,
                        help="只读合成输入目录，需包含 interior-scene.glb")
    parser.add_argument("--chrome", help="可选Chrome/Chromium可执行路径")
    args = parser.parse_args()
    source = args.source_directory.expanduser().resolve()
    source_glb = source / "interior-scene.glb"
    if not source_glb.is_file():
        raise FileNotFoundError(source_glb)

    options = [
        '<option value="">选择房间…</option>',
        '<option value="R01" data-target="2.000m 0.000m -1.500m" data-radius="7.800m">R01 · synthetic A</option>',
        '<option value="R02" data-target="5.500m 0.000m -1.500m" data-radius="7.800m">R02 · synthetic B</option>',
    ]
    with tempfile.TemporaryDirectory(prefix="interior-viewer-wall-toggle-") as temp_dir:
        output = Path(temp_dir)
        shutil.copyfile(source_glb, output / "interior-scene.glb")
        copy_viewer_assets(output)
        page = render_viewer_page("Synthetic wall toggle test", "interior-scene.glb",
                                  options, "Walls - soft white")
        (output / "index.html").write_text(page, encoding="utf-8")
        command = [sys.executable, str(SCRIPT_DIR / "test_model_viewer_browser.py"),
                   "--directory", str(output), "--assert-wall-toggle"]
        if args.chrome:
            command.extend(("--chrome", args.chrome))
        subprocess.run(command, check=True)
    print("MODEL_VIEWER_TEMPLATE_WALL_TOGGLE_OK source_read_only=true fixture_removed=true")


if __name__ == "__main__":
    main()
