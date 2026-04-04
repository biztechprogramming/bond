"""Tests for take-screenshot.py — runs without Chromium by mocking Playwright."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Import the module under test (hyphenated filename requires importlib)
import importlib.util, os, sys
_spec = importlib.util.spec_from_file_location(
    "take_screenshot",
    os.path.join(os.path.dirname(__file__), "take-screenshot.py"),
)
take_screenshot_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(take_screenshot_mod)

take_screenshot = take_screenshot_mod.take_screenshot
main = take_screenshot_mod.main


class TestCLIArguments(unittest.TestCase):
    """Verify argparse defaults."""

    def _parse(self, extra=None):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--url", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--width", type=int, default=1280)
        parser.add_argument("--height", type=int, default=720)
        parser.add_argument("--wait-for")
        parser.add_argument("--delay", type=int, default=1000)
        args = ["--url", "http://localhost", "--output", "/tmp/out.png"]
        if extra:
            args.extend(extra)
        return parser.parse_args(args)

    def test_defaults(self):
        args = self._parse()
        self.assertEqual(args.width, 1280)
        self.assertEqual(args.height, 720)
        self.assertEqual(args.delay, 1000)
        self.assertIsNone(args.wait_for)

    def test_custom_values(self):
        args = self._parse(["--width", "800", "--height", "600", "--delay", "500", "--wait-for", "#app"])
        self.assertEqual(args.width, 800)
        self.assertEqual(args.height, 600)
        self.assertEqual(args.delay, 500)
        self.assertEqual(args.wait_for, "#app")


class TestTakeScreenshot(unittest.TestCase):
    """Test the async take_screenshot function with mocked Playwright."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch.object(take_screenshot_mod, "os")
    @patch.object(take_screenshot_mod, "async_playwright")
    def test_basic_flow(self, mock_pw_ctx, mock_os):
        # Build mock chain: async_playwright() -> context manager -> .chromium.launch() -> page
        mock_page = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.new_page.return_value = mock_page

        mock_pw = AsyncMock()
        mock_pw.chromium.launch.return_value = mock_browser

        mock_pw_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_os.path.dirname.return_value = "/tmp/shots"
        self._run(take_screenshot("http://localhost:3000", "/tmp/shots/out.png", 1280, 720))

        mock_os.makedirs.assert_called_once_with("/tmp/shots", exist_ok=True)
        mock_pw.chromium.launch.assert_called_once_with(headless=True)
        mock_browser.new_page.assert_called_once_with(viewport={"width": 1280, "height": 720})
        mock_page.goto.assert_called_once_with("http://localhost:3000", wait_until="networkidle")
        mock_page.screenshot.assert_called_once_with(path="/tmp/shots/out.png", full_page=False)
        mock_browser.close.assert_called_once()

    @patch.object(take_screenshot_mod, "os")
    @patch.object(take_screenshot_mod, "async_playwright")
    def test_wait_for_selector(self, mock_pw_ctx, mock_os):
        mock_page = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw = AsyncMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_pw_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_pw)
        mock_pw_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        self._run(take_screenshot("http://x", "/tmp/o.png", 800, 600, wait_for="#app", delay_ms=0))

        mock_page.wait_for_selector.assert_called_once_with("#app", timeout=10000)


if __name__ == "__main__":
    unittest.main()
