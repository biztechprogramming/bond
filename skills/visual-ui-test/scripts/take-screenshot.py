#!/usr/bin/env python3
"""Take a screenshot of a Bond frontend page using Playwright."""
import argparse
import asyncio
import os
from playwright.async_api import async_playwright

async def take_screenshot(url: str, output: str, width: int, height: int,
                          wait_for: str | None = None, delay_ms: int = 1000):
    os.makedirs(os.path.dirname(output), exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.goto(url, wait_until="networkidle")
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=10000)
        await page.wait_for_timeout(delay_ms)  # let animations settle
        await page.screenshot(path=output, full_page=False)
        await browser.close()
    print(f"Screenshot saved to {output}")

def main():
    parser = argparse.ArgumentParser(description="Take a screenshot of a web page using Playwright")
    parser.add_argument("--url", required=True, help="URL to screenshot")
    parser.add_argument("--output", required=True, help="Output file path for the PNG")
    parser.add_argument("--width", type=int, default=1280, help="Viewport width (default: 1280)")
    parser.add_argument("--height", type=int, default=720, help="Viewport height (default: 720)")
    parser.add_argument("--wait-for", help="CSS selector to wait for before screenshot")
    parser.add_argument("--delay", type=int, default=1000, help="Extra delay in ms after page load (default: 1000)")
    args = parser.parse_args()
    asyncio.run(take_screenshot(args.url, args.output, args.width, args.height,
                                args.wait_for, args.delay))

if __name__ == "__main__":
    main()
