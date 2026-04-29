#!/usr/bin/env python3
"""Compare two screenshots and produce a visual diff.

Requires: pip install Pillow (already available in the agent container).
"""
import argparse
import sys

from PIL import Image, ImageChops


def compare(before_path: str, after_path: str, diff_path: str | None,
            threshold: int = 10) -> tuple[bool, float]:
    """Compare two images pixel-by-pixel.

    Returns (identical, pct_changed) where identical is True when the
    percentage of changed pixels is 0.
    """
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")

    if before.size != after.size:
        print(f"Dimension mismatch: before={before.size} after={after.size}")
        # Resize after to match before for comparison
        after = after.resize(before.size, Image.LANCZOS)

    diff = ImageChops.difference(before, after)
    pixels = list(diff.getdata())
    total = len(pixels)
    changed = sum(1 for r, g, b in pixels if max(r, g, b) > threshold)
    pct = (changed / total) * 100 if total else 0.0

    if diff_path:
        # Create a red-overlay diff image
        overlay = Image.new("RGB", before.size, (0, 0, 0))
        diff_pixels = overlay.load()
        before_pixels = before.load()
        w, h = before.size
        for y in range(h):
            for x in range(w):
                r, g, b = diff.getpixel((x, y))
                if max(r, g, b) > threshold:
                    diff_pixels[x, y] = (255, 0, 0)
                else:
                    br, bg, bb = before_pixels[x, y]
                    diff_pixels[x, y] = (br // 3, bg // 3, bb // 3)
        overlay.save(diff_path)
        print(f"Diff image saved to {diff_path}")

    identical = changed == 0
    print(f"Pixels changed: {changed}/{total} ({pct:.2f}%)")
    print("Result: IDENTICAL" if identical else "Result: DIFFERENT")
    return identical, pct


def main():
    parser = argparse.ArgumentParser(description="Compare two screenshots pixel-by-pixel")
    parser.add_argument("--before", required=True, help="Path to the before image")
    parser.add_argument("--after", required=True, help="Path to the after image")
    parser.add_argument("--diff", help="Output path for the diff image")
    parser.add_argument("--threshold", type=int, default=10,
                        help="Pixel difference threshold 0-255 (default: 10)")
    args = parser.parse_args()
    identical, _ = compare(args.before, args.after, args.diff, args.threshold)
    sys.exit(0 if identical else 1)


if __name__ == "__main__":
    main()
