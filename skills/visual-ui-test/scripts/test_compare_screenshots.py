"""Tests for compare-screenshots.py."""
import os
import sys
import tempfile
import unittest

from PIL import Image

# Import the module under test (hyphenated filename requires importlib)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "compare_screenshots",
    os.path.join(os.path.dirname(__file__), "compare-screenshots.py"),
)
compare_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare_mod)

compare = compare_mod.compare


class TestCompareScreenshots(unittest.TestCase):

    def _make_image(self, color, size=(100, 100)):
        img = Image.new("RGB", size, color)
        path = tempfile.mktemp(suffix=".png")
        img.save(path)
        self.addCleanup(lambda p=path: os.unlink(p) if os.path.exists(p) else None)
        return path

    def test_identical_images(self):
        a = self._make_image((255, 0, 0))
        b = self._make_image((255, 0, 0))
        identical, pct = compare(a, b, None, threshold=10)
        self.assertTrue(identical)
        self.assertEqual(pct, 0.0)

    def test_different_images(self):
        a = self._make_image((255, 0, 0))
        b = self._make_image((0, 255, 0))
        identical, pct = compare(a, b, None, threshold=10)
        self.assertFalse(identical)
        self.assertEqual(pct, 100.0)

    def test_dimension_mismatch(self):
        a = self._make_image((255, 0, 0), size=(100, 100))
        b = self._make_image((255, 0, 0), size=(200, 200))
        # Should not raise — resizes and compares
        identical, pct = compare(a, b, None, threshold=10)
        self.assertTrue(identical)

    def test_threshold_behavior(self):
        """Small difference below threshold → identical."""
        a = self._make_image((100, 100, 100))
        b = self._make_image((105, 100, 100))  # diff of 5
        identical_low, _ = compare(a, b, None, threshold=10)
        self.assertTrue(identical_low)
        identical_strict, _ = compare(a, b, None, threshold=2)
        self.assertFalse(identical_strict)

    def test_diff_image_output(self):
        a = self._make_image((255, 0, 0))
        b = self._make_image((0, 255, 0))
        diff_path = tempfile.mktemp(suffix=".png")
        self.addCleanup(lambda: os.unlink(diff_path) if os.path.exists(diff_path) else None)
        compare(a, b, diff_path, threshold=10)
        self.assertTrue(os.path.exists(diff_path))
        diff_img = Image.open(diff_path)
        self.assertEqual(diff_img.size, (100, 100))


if __name__ == "__main__":
    unittest.main()
