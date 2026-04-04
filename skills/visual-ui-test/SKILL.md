---
name: visual-ui-test
description: >
  Visually verify UI/CSS/layout changes using Playwright screenshots. Use when making
  any frontend change that affects layout, styling, scrolling, overflow, or visual
  appearance. Triggers on UI fixes, CSS changes, component layout work, "verify the
  UI", "take a screenshot", "check how it looks". Required before committing any
  visual change.
---

# Visual UI Test

Take screenshots of the Bond frontend to verify UI/CSS/layout changes work correctly.

## Workflow

### Step 1: Start the dev environment

Run `scripts/start-dev-env.sh` from the skill directory. This starts the frontend (port 18788),
backend (port 18790), and gateway (port 18789) in the background using the test database.

Wait for the script to report all services are healthy before proceeding.

If the dev environment is already running (check with `curl -s http://localhost:18788`), skip this step.

### Step 2: Take a "before" screenshot

Run the screenshot script:

```bash
python skills/visual-ui-test/scripts/take-screenshot.py \
  --url http://localhost:18788/<page-path> \
  --output /tmp/screenshots/before.png \
  --width 1280 --height 720
```

Or use a preset:

```bash
python skills/visual-ui-test/scripts/take-screenshot.py \
  --preset terminal \
  --output /tmp/screenshots/before.png
```

Replace `<page-path>` with the relevant page (e.g., `/containers`, `/settings`, `/`).

Read the screenshot file to see the current state.

### Step 3: Make your code changes

Edit the frontend code as needed.

### Step 4: Take an "after" screenshot

The dev server hot-reloads, so just wait 2-3 seconds after saving, then:

```bash
python skills/visual-ui-test/scripts/take-screenshot.py \
  --url http://localhost:18788/<page-path> \
  --output /tmp/screenshots/after.png \
  --width 1280 --height 720
```

Read the screenshot file and compare with the before screenshot.

### Step 5: Compare screenshots (optional)

Use the compare script for a pixel-level diff:

```bash
python skills/visual-ui-test/scripts/compare-screenshots.py \
  --before /tmp/screenshots/before.png \
  --after /tmp/screenshots/after.png \
  --diff /tmp/screenshots/diff.png
```

Exit code 0 = identical, 1 = different. Read the diff image to see highlighted changes.

### Step 6: Iterate or commit

- If the change looks correct: proceed to commit
- If not: go back to Step 3, fix the issue, and take another screenshot

### Step 7: Teardown (optional)

Run `scripts/stop-dev-env.sh` to stop background services. This is optional — services
will be cleaned up when the container stops.

## Common Pages

| Preset | Path | Selector | Delay | Description |
|--------|------|----------|-------|-------------|
| `home` | `/` | `[data-testid='dashboard']` | 1500ms | Main dashboard / home page |
| `containers` | `/containers` | `[data-testid='container-list']` | 2000ms | Container management panel |
| `settings` | `/settings` | `form` | 1000ms | Settings page |
| `settings-deployment` | `/settings/deployment` | `[data-testid='deployment-tab']` | 1000ms | Deployment settings with test SpacetimeDB controls |
| `terminal` | `/` | `[data-testid='terminal-panel']` | 2000ms | Terminal panel (element screenshot) |
| `chat` | `/` | `[data-testid='chat-panel']` | 1500ms | Chat/conversation panel |

## Screenshot Options

| Flag | Default | Purpose |
|------|---------|---------|
| `--url` | (required*) | Full URL to screenshot (*optional with `--preset`) |
| `--output` | (required) | Output file path |
| `--width` | 1280 | Viewport width in pixels |
| `--height` | 720 | Viewport height in pixels |
| `--wait-for` | none | CSS selector to wait for before capturing |
| `--delay` | 1000 | Extra delay in ms (for animations) |
| `--element` | none | CSS selector — screenshot just this element |
| `--full-page` | false | Capture the full scrollable page |
| `--dark-mode` | false | Emulate `prefers-color-scheme: dark` |
| `--preset` | none | Load settings from `page-presets.json` by name |

When using `--preset`, the preset provides default values for `--url` (from path), `--wait-for`, `--delay`, and `--element`. Explicit flags override preset values.

## Comparing Screenshots

The `compare-screenshots.py` script performs pixel-level comparison:

```bash
python skills/visual-ui-test/scripts/compare-screenshots.py \
  --before before.png --after after.png --diff diff.png --threshold 10
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--before` | (required) | Path to the before image |
| `--after` | (required) | Path to the after image |
| `--diff` | none | Output path for diff image (red = changed pixels) |
| `--threshold` | 10 | Pixel difference threshold 0-255 |

The diff image shows changed pixels in red and unchanged areas dimmed.

## Real-World Examples

### Terminal scroll fix

```bash
# 1. Screenshot the terminal panel before the fix
python scripts/take-screenshot.py --preset terminal --output /tmp/screenshots/before.png

# 2. Fix the overflow CSS in the terminal component
# ... edit code ...

# 3. Screenshot after
python scripts/take-screenshot.py --preset terminal --output /tmp/screenshots/after.png

# 4. Compare
python scripts/compare-screenshots.py \
  --before /tmp/screenshots/before.png --after /tmp/screenshots/after.png \
  --diff /tmp/screenshots/diff.png
```

### Dark mode verification

```bash
# Screenshot in dark mode to check contrast/readability
python scripts/take-screenshot.py \
  --preset home --dark-mode --output /tmp/screenshots/dark.png
```

### Layout regression check

```bash
# Full-page screenshots before and after a refactor
python scripts/take-screenshot.py \
  --url http://localhost:18788/ --full-page --output /tmp/screenshots/before-full.png

# ... make changes ...

python scripts/take-screenshot.py \
  --url http://localhost:18788/ --full-page --output /tmp/screenshots/after-full.png

python scripts/compare-screenshots.py \
  --before /tmp/screenshots/before-full.png --after /tmp/screenshots/after-full.png \
  --diff /tmp/screenshots/diff-full.png
```

## Important Rules

1. **NEVER commit a UI change without taking a screenshot first.** This is the whole point.
2. Always take both before AND after screenshots for comparison.
3. If the screenshot shows unexpected results, investigate — don't just retry the same fix.
4. Screenshots are saved to `/tmp/screenshots/`. Read them using the file read tool.
5. The dev environment uses a test SpacetimeDB instance (port 18797) — separate from production (port 18787).
