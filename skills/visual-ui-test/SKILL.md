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

### Step 5: Iterate or commit

- If the change looks correct: proceed to commit
- If not: go back to Step 3, fix the issue, and take another screenshot

### Step 6: Teardown (optional)

Run `scripts/stop-dev-env.sh` to stop background services. This is optional — services
will be cleaned up when the container stops.

## Common Pages

| Path | What's there |
|------|-------------|
| `/` | Main dashboard / home |
| `/containers` | Container management panel |
| `/settings` | Settings page |

## Screenshot Options

| Flag | Default | Purpose |
|------|---------|---------|
| `--url` | (required) | Full URL to screenshot |
| `--output` | (required) | Output file path |
| `--width` | 1280 | Viewport width in pixels |
| `--height` | 720 | Viewport height in pixels |
| `--wait-for` | none | CSS selector to wait for before capturing |
| `--delay` | 1000 | Extra delay in ms (for animations) |

## Important Rules

1. **NEVER commit a UI change without taking a screenshot first.** This is the whole point.
2. Always take both before AND after screenshots for comparison.
3. If the screenshot shows unexpected results, investigate — don't just retry the same fix.
4. Screenshots are saved to `/tmp/screenshots/`. Read them using the file read tool.
5. The dev environment uses a test SpacetimeDB instance (port 18797) — separate from production (port 18787).
