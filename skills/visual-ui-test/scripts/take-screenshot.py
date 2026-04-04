#!/usr/bin/env python3
"""Take a screenshot of a Bond frontend page using Playwright."""
import argparse
import asyncio
import json
import os
from playwright.async_api import async_playwright

PRESETS_PATH = os.path.join(os.path.dirname(__file__), "page-presets.json")


def load_preset(name: str) -> dict:
    """Load a named preset from page-presets.json."""
    with open(PRESETS_PATH) as f:
        presets = json.load(f)
    if name not in presets:
        available = ", ".join(sorted(presets.keys()))
        raise SystemExit(f"Unknown preset '{name}'. Available: {available}")
    return presets[name]


async def take_screenshot(url: str, output: str, width: int, height: int,
                          wait_for: str | None = None, delay_ms: int = 1000,
                          element: str | None = None, full_page: bool = False,
                          dark_mode: bool = False):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context_opts: dict = {"viewport": {"width": width, "height": height}}
        if dark_mode:
            context_opts["color_scheme"] = "dark"
        page = await browser.new_page(**context_opts)
        await page.goto(url, wait_until="networkidle")
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=10000)
        await page.wait_for_timeout(delay_ms)  # let animations settle
        if element:
            el = page.locator(element)
            await el.wait_for(state="visible", timeout=10000)
            await el.screenshot(path=output)
        else:
            await page.screenshot(path=output, full_page=full_page)
        await browser.close()
    print(f"Screenshot saved to {output}")


def main():
    parser = argparse.ArgumentParser(description="Take a screenshot of a web page using Playwright")
    parser.add_argument("--url", help="URL to screenshot (required unless --preset provides a path)")
    parser.add_argument("--output", required=True, help="Output file path for the PNG")
    parser.add_argument("--width", type=int, default=1280, help="Viewport width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Viewport height (default: 720)")
    parser.add_argument("--wait-for", help="CSS selector to wait for before screenshot")
    parser.add_argument("--delay", type=int, default=None, help="Extra delay in ms after page load (default: 1000)")
    parser.add_argument("--element", help="CSS selector — screenshot just this element")
    parser.add_argument("--full-page", action="store_true", help="Capture the full scrollable page")
    parser.add_argument("--dark-mode", action="store_true", help="Emulate prefers-color-scheme: dark")
    parser.add_argument("--preset", help="Load settings from page-presets.json by name")
    args = parser.parse_args()

    # Apply preset defaults, let explicit flags override
    preset_url_base = None
    if args.preset:
        preset = load_preset(args.preset)
        preset_url_base = preset.get("path")
        if args.wait_for is None:
            args.wait_for = preset.get("wait_for")
        if args.delay is None:
            args.delay = preset.get("delay", 1000)
        if args.element is None:
            args.element = preset.get("element")

    if args.delay is None:
        args.delay = 1000

    if not args.url:
        if preset_url_base:
            args.url = f"http://localhost:18788{preset_url_base}"
        else:
            parser.error("--url is required (or use --preset to derive it)")

    asyncio.run(take_screenshot(args.url, args.output, args.width, args.height,
                                args.wait_for, args.delay, args.element,
                                args.full_page, args.dark_mode))

if __name__ == "__main__":
    main()
