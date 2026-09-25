#!/usr/bin/env python3
"""Run the complete skill test suite with Blender-only cases in Blender."""
from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


BLENDER_TESTS = ("test_blender_scene_geometry.py", "test_camera_framing.py",
                 "test_global_reference_camera.py")


def blender_process_failed(returncode: int, stdout: str, stderr: str) -> bool:
    """Blender can return zero after a Python exception; do not accept that as green."""
    output = f"{stdout}\n{stderr}"
    return returncode != 0 or "Traceback (most recent call last):" in output


def main() -> int:
    # Keep repeatable test runs from polluting the reusable skill directory.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blender", type=Path,
        help="Blender executable; defaults to BLENDER_BIN or a blender found on PATH",
    )
    parser.add_argument("--skip-blender", action="store_true", help="run Python tests only")
    parser.add_argument("--validator", type=Path, help="optional Codex skill-creator quick_validate.py")
    args = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    blender = args.blender
    if blender is None and not args.skip_blender:
        blender = Path(os.environ["BLENDER_BIN"]) if os.environ.get("BLENDER_BIN") else None
        if blender is None:
            found = shutil.which("blender")
            if found:
                blender = Path(found)
        if blender is None:
            candidates = (
                Path("/Applications/Blender.app/Contents/MacOS/Blender"),
                Path("/usr/bin/blender"),
            )
            blender = next((path for path in candidates if path.is_file()), None)
    if not args.skip_blender and (blender is None or not blender.is_file()):
        parser.error("Blender executable not found; install Blender or pass --blender/BLENDER_BIN (or use --skip-blender)")

    sys.path.insert(0, str(script_dir))
    skipped = {Path(name).stem for name in BLENDER_TESTS}
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    modules = sorted(
        module.name for module in pkgutil.iter_modules([str(script_dir)])
        if module.name.startswith("test_") and module.name not in skipped
    )
    for name in modules:
        suite.addTests(loader.loadTestsFromModule(importlib.import_module(name)))

    blender_tests = () if args.skip_blender else BLENDER_TESTS
    total_groups = 2 + len(blender_tests)
    print(f"[1/{total_groups}] Python tests: {len(modules)} modules", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    failures = int(not result.wasSuccessful())

    for index, filename in enumerate(blender_tests, start=2):
        print(f"[{index}/{total_groups}] Blender test: {filename}", flush=True)
        completed = subprocess.run(
            [str(blender), "--background", "--factory-startup", "--python", str(script_dir / filename)],
            cwd=script_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        process_failed = blender_process_failed(completed.returncode, completed.stdout, completed.stderr)
        if not process_failed:
            summaries = [line for line in completed.stdout.splitlines() if line.startswith(("BLENDER_SCENE_GEOMETRY_OK", "CAMERA_FRAMING_OK", "GLOBAL_REFERENCE_CAMERA_OK", "GLOBAL_REFERENCE_RENDER_OK"))]
            print("\n".join(summaries) or "BLENDER_TEST_OK", flush=True)
        else:
            print(completed.stdout, file=sys.stdout)
            print(completed.stderr, file=sys.stderr)
        failures += int(process_failed)

    print(f"[{total_groups}/{total_groups}] Skill structure validation", flush=True)
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    validator = args.validator or codex_home / "skills/.system/skill-creator/scripts/quick_validate.py"
    if not validator.is_file():
        print(f"SKILL_VALIDATOR_SKIPPED_NOT_INSTALLED {validator}", flush=True)
    else:
        completed = subprocess.run(
            [sys.executable, str(validator), str(script_dir.parent)],
            cwd=script_dir,
            check=False,
        )
        failures += int(completed.returncode != 0)

    if failures:
        print(f"SKILL_TESTS_FAILED groups={failures}", file=sys.stderr)
        return 1
    print("SKILL_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
