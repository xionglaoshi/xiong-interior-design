#!/usr/bin/env python3
"""Tests for staged, rollback-safe output bundle commits."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import atomic_output_bundle as bundle


class AtomicOutputBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="atomic-output-test-")
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.output = self.root / "outputs"
        self.stage.mkdir()
        self.names = ["scene.blend", "scene.glb", "index.html"]
        for name in self.names:
            (self.stage / name).write_text(f"new:{name}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_commits_all_staged_files_and_preserves_unrelated_output(self):
        self.output.mkdir()
        unrelated = self.output / "notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        targets = bundle.commit_output_bundle(self.stage, self.output, self.names)
        self.assertEqual([p.read_text(encoding="utf-8") for p in targets],
                         [f"new:{name}" for name in self.names])
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
        self.assertTrue(all(not (self.stage / name).exists() for name in self.names))

    def test_refuses_existing_targets_without_force_before_changing_anything(self):
        self.output.mkdir()
        (self.output / self.names[0]).write_text("old", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            bundle.commit_output_bundle(self.stage, self.output, self.names)
        self.assertEqual((self.output / self.names[0]).read_text(encoding="utf-8"), "old")
        self.assertTrue(all((self.stage / name).is_file() for name in self.names))

    def test_force_replaces_only_named_targets(self):
        self.output.mkdir()
        for name in self.names:
            (self.output / name).write_text(f"old:{name}", encoding="utf-8")
        unrelated = self.output / "notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        bundle.commit_output_bundle(self.stage, self.output, self.names, force=True)
        self.assertEqual([(self.output / name).read_text(encoding="utf-8") for name in self.names],
                         [f"new:{name}" for name in self.names])
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_mid_commit_failure_restores_previous_bundle(self):
        self.output.mkdir()
        for name in self.names:
            (self.output / name).write_text(f"old:{name}", encoding="utf-8")
        original_replace = bundle.os.replace
        calls = 0

        def fail_during_second_install(source, target):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("synthetic commit interruption")
            return original_replace(source, target)

        with patch.object(bundle.os, "replace", side_effect=fail_during_second_install):
            with self.assertRaisesRegex(OSError, "synthetic commit interruption"):
                bundle.commit_output_bundle(self.stage, self.output, self.names, force=True)
        self.assertEqual([(self.output / name).read_text(encoding="utf-8") for name in self.names],
                         [f"old:{name}" for name in self.names])

    def test_missing_staged_member_leaves_destination_uncreated(self):
        (self.stage / self.names[-1]).unlink()
        with self.assertRaises(FileNotFoundError):
            bundle.commit_output_bundle(self.stage, self.output, self.names)
        self.assertFalse(self.output.exists())

    def test_rejects_path_traversal_names(self):
        with self.assertRaisesRegex(ValueError, "plain file names"):
            bundle.validate_bundle_names(["../outside.txt"])

    def test_broken_symlink_counts_as_existing_output(self):
        self.output.mkdir()
        link = self.output / self.names[0]
        link.symlink_to(self.root / "missing-target")
        with self.assertRaises(FileExistsError):
            bundle.commit_output_bundle(self.stage, self.output, self.names)
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
