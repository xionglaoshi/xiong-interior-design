import unittest

from run_skill_tests import blender_process_failed


class BlenderProcessStatusTests(unittest.TestCase):
    def test_nonzero_exit_is_failure(self):
        self.assertTrue(blender_process_failed(1, "", "error"))

    def test_zero_exit_with_python_traceback_is_failure(self):
        self.assertTrue(blender_process_failed(
            0, "Traceback (most recent call last):\nIndentationError", ""))
        self.assertTrue(blender_process_failed(
            0, "", "Traceback (most recent call last):\nRuntimeError"))

    def test_clean_zero_exit_is_success(self):
        self.assertFalse(blender_process_failed(0, "BLENDER_TEST_OK", "warning only"))


if __name__ == "__main__":
    unittest.main()
