# Design Doc 099: Visual UI Testing Skill

**Status:** Proposal
**Author:** Bond AI
**Date:** 2026-04-04
**Depends on:** Design Doc 037 (Coding Agent Skill), Design Doc 047 (Skills Federation), Design Doc 098 (File Reading Tools Redesign)

---

## 1. Problem Statement

Bond's coding agents make UI/CSS/layout changes **blind** — they modify frontend code, commit, and push without ever seeing the rendered result. This has caused repeated, frustrating failures:

### 1.1 Blind CSS Changes (Critical — P0)

When a coding agent is tasked with fixing a frontend layout issue (e.g., terminal panel overflow, scroll behavior, panel sizing), it:

1. Reads the component source code
2. Reasons about what CSS/props to change
3. Makes the edit
4. Commits and reports "fixed"

There is **no verification step**. The agent has no way to render the page and check whether the fix actually works. This is the equivalent of a developer writing CSS with their monitor turned off.

**Observed failures:**
- Terminal panel scroll fix attempted 3 times — each commit claimed success, none actually worked
- Overflow issues "fixed" by adding `overflow: auto` without checking whether the parent container had a constrained height (it didn't)
- Layout changes that looked correct in code but caused visual regressions elsewhere on the page

**Root cause:** The agent sandbox (`Dockerfile.agent`) has no browser, no Playwright, and no mechanism to start the frontend dev server. The agent literally cannot see what it's building.

### 1.2 No Feedback Loop (High — P1)

Even when an agent suspects its fix might not work, it has no way to iterate. Human developers use a tight loop: change → save → check browser → adjust. Coding agents have: change → commit → hope. The absence of visual feedback makes UI work fundamentally different from backend work, where the agent can at least run tests or curl endpoints.

### 1.3 No Enforcement (Medium — P2)

The prompt system (`prompts/manifest.yaml`) has no guidance requiring visual verification for UI changes. Even if the tooling existed, agents wouldn't know to use it without explicit instructions.

---

## 2. Design Principles

1. **See what you ship.** No UI change should be committed without a screenshot proving it works.
2. **Isolated test data.** The dev environment uses a separate SpacetimeDB instance (port 18797) so tests never touch production data (port 18787).
3. **Minimal sandbox changes.** Add only what's necessary to `Dockerfile.agent` — Playwright + Chromium, nothing more.
4. **Self-contained skill.** The skill must handle the full lifecycle: start services → navigate → screenshot → analyze → iterate.
5. **Progressive disclosure.** The SKILL.md stays concise; scripts handle the complexity.

---

## 3. Architecture

### 3.1 High-Level Flow

```
┌─────────────────────────────────────────────────────────┐
│  HOST MACHINE                                           │
│                                                         │
│  ┌──────────────────────────────┐                       │
│  │ SpacetimeDB (test)           │                       │
│  │ bond-test-spacetimedb :18797 │                       │
│  │ Data: ~/.bond/spacetimedb-test│                      │
│  └──────────────┬───────────────┘                       │
│                 │ (port 18797)                           │
├─────────────────┼───────────────────────────────────────┤
│  Agent Container│(Dockerfile.agent)                     │
│                 │                                        │
│  ┌──────────┐  │ ┌──────────────┐    ┌──────────────┐  │
│  │ Coding   │──┼▶│ visual-ui-   │───▶│ Playwright   │  │
│  │ Agent    │  │ │ test skill   │    │ (Chromium)   │  │
│  │ (Claude) │◀─┼─│              │◀───│              │  │
│  └──────────┘  │ └──────┬───────┘    └──────┬───────┘  │
│                │        │                    │          │
│  ┌─────────────┴────────┴────────────────────┘          │
│  │                                                      │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐       │
│  │  │ Frontend  │  │ Backend   │  │ Gateway   │       │
│  │  │ :18788    │  │ :18790    │  │ :18789    │       │
│  │  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘       │
│  │        └───────────────┴──────────────┘              │
│  │              All connect to SpacetimeDB              │
│  │              via BOND_TEST_STDB_HOST:18797           │
│  └──────────────────────────────────────────────────────│
└─────────────────────────────────────────────────────────┘
```

**Key decision: SpacetimeDB runs on the host, services run in the container.** The agent container cannot run Docker-in-Docker, so the test SpacetimeDB instance must be started on the host machine before the container runs. The container's services connect to it via `BOND_TEST_STDB_HOST` (defaults to `host.docker.internal`). This avoids:
- Port conflicts with the production SpacetimeDB on port 18787
- Docker-in-Docker complexity inside the agent sandbox
- The container needing to know the host's actual hostname

### 3.2 Component Responsibilities

| Component | Role | Location |
|-----------|------|----------|
| `SKILL.md` | Skill instructions for the agent | `skills/visual-ui-test/SKILL.md` |
| `start-dev-env.sh` | Start frontend + backend + gateway, connect to test SpacetimeDB | `skills/visual-ui-test/scripts/start-dev-env.sh` |
| `take-screenshot.py` | Playwright script: navigate + screenshot | `skills/visual-ui-test/scripts/take-screenshot.py` |
| `stop-dev-env.sh` | Teardown background services (not SpacetimeDB) | `skills/visual-ui-test/scripts/stop-dev-env.sh` |
| `setup-test-spacetimedb.sh` | **Host-side**: start test SpacetimeDB, publish module, seed data | `skills/visual-ui-test/scripts/setup-test-spacetimedb.sh` |
| `teardown-test-spacetimedb.sh` | **Host-side**: stop and remove test SpacetimeDB container | `skills/visual-ui-test/scripts/teardown-test-spacetimedb.sh` |
| Prompt fragment | "MUST verify UI changes visually" | `prompts/frontend/visual-verification.md` |

### 3.3 Screenshot Workflow

```
Agent makes CSS change
        │
        ▼
Skill: start-dev-env.sh (if not already running)
        │
        ▼
Skill: take-screenshot.py --url /containers --output before.png
        │
        ▼
Agent analyzes screenshot (multimodal — reads the PNG)
        │
        ▼
Agent makes fix
        │
        ▼
Skill: take-screenshot.py --url /containers --output after.png
        │
        ▼
Agent compares before/after, confirms fix works
        │
        ▼
Agent commits with confidence
```

---

## 4. Skill Specification

### 4.1 SKILL.md Content

```yaml
---
name: visual-ui-test
description: >
  Visually verify UI/CSS/layout changes using Playwright screenshots. Use when making
  any frontend change that affects layout, styling, scrolling, overflow, or visual
  appearance. Triggers on UI fixes, CSS changes, component layout work, "verify the
  UI", "take a screenshot", "check how it looks". Required before committing any
  visual change.
---
```

**Body (imperative instructions):**

```markdown
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

## Important Rules

1. **NEVER commit a UI change without taking a screenshot first.** This is the whole point.
2. Always take both before AND after screenshots for comparison.
3. If the screenshot shows unexpected results, investigate — don't just retry the same fix.
4. Screenshots are saved to `/tmp/screenshots/`. Read them using the file read tool.
5. The dev environment uses a test SpacetimeDB instance (port 18797) — separate from production (port 18787).
```

### 4.2 Script Specifications

#### `take-screenshot.py`

```python
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--wait-for", help="CSS selector to wait for before screenshot")
    parser.add_argument("--delay", type=int, default=1000, help="Extra delay in ms")
    args = parser.parse_args()
    asyncio.run(take_screenshot(args.url, args.output, args.width, args.height,
                                args.wait_for, args.delay))

if __name__ == "__main__":
    main()
```

#### `start-dev-env.sh` (container-side)

Accepts `BOND_TEST_STDB_HOST` (default: `host.docker.internal`) and `BOND_TEST_STDB_PORT` (default: `18797`). Health-checks SpacetimeDB, then starts backend, gateway, and frontend with SpacetimeDB env vars configured. Sets `BOND_SPACETIMEDB_URL`, `NEXT_PUBLIC_STDB_HOST`, `NEXT_PUBLIC_STDB_PORT` for each service. See `skills/visual-ui-test/scripts/start-dev-env.sh` for full implementation.

#### `setup-test-spacetimedb.sh` (host-side)

Starts a `bond-test-spacetimedb` Docker container on port 18797, publishes the `bond-core-v2` module, and seeds test data. Idempotent — safe to run multiple times. Uses `~/.bond/spacetimedb-test` for data (isolated from production).

#### `teardown-test-spacetimedb.sh` (host-side)

Stops and removes the `bond-test-spacetimedb` container. Preserves the data volume.

#### `stop-dev-env.sh` (container-side)

Stops frontend, gateway, and backend processes. Does **not** stop the SpacetimeDB container (that's managed by the host).

---

## 5. Test Database Strategy (SpacetimeDB)

Bond's primary database is **SpacetimeDB** (not SQLite). The frontend connects to SpacetimeDB via WebSocket, the gateway uses the SpacetimeDB SDK, and the backend has a SpacetimeDB HTTP client. The SQLite database (`knowledge.db`) is only used for embeddings and is not needed for visual UI testing.

### 5.1 Test Instance Setup

The test SpacetimeDB instance runs as a Docker container on the **host machine** (port 18797), separate from the production instance (port 18787).

```bash
# One-time setup on the host
bash skills/visual-ui-test/scripts/setup-test-spacetimedb.sh
```

This script:
1. Starts a `bond-test-spacetimedb` Docker container on port 18797
2. Publishes the `bond-core-v2` module from `spacetimedb/spacetimedb/`
3. Seeds test data via `scripts/seed-spacetimedb.sh`

### 5.2 Environment Variables

| Variable | Where | Default | Purpose |
|----------|-------|---------|---------|
| `BOND_TEST_STDB_HOST` | Container env | `host.docker.internal` | Hostname to reach host's test SpacetimeDB |
| `BOND_TEST_STDB_PORT` | Container env | `18797` | Port for test SpacetimeDB |
| `BOND_SPACETIMEDB_URL` | Set by `start-dev-env.sh` | — | Full URL passed to backend/gateway |
| `NEXT_PUBLIC_STDB_HOST` | Set by `start-dev-env.sh` | — | SpacetimeDB host for frontend WebSocket |
| `NEXT_PUBLIC_STDB_PORT` | Set by `start-dev-env.sh` | — | SpacetimeDB port for frontend WebSocket |

### 5.3 Data Isolation

- Production SpacetimeDB: port 18787, data in `~/.bond/spacetimedb/`
- Test SpacetimeDB: port 18797, data in `~/.bond/spacetimedb-test/`
- The test container is named `bond-test-spacetimedb` (vs production `bond-spacetimedb`)

### 5.4 Teardown

```bash
# Remove the test instance (preserves data volume)
bash skills/visual-ui-test/scripts/teardown-test-spacetimedb.sh

# To also delete test data:
rm -rf ~/.bond/spacetimedb-test
```

---

## 6. System Prompt Changes

### 6.1 New Prompt Fragment

**File:** `prompts/frontend/visual-verification.md`

```markdown
## Visual Verification Requirement

When making **any** change that affects the visual appearance of the Bond frontend — including
CSS, layout, styling, component structure, overflow, scrolling, sizing, spacing, or responsive
behavior — you **MUST** visually verify the change before committing:

1. Use the `visual-ui-test` skill to start the dev environment
2. Take a "before" screenshot of the affected page
3. Make your change
4. Take an "after" screenshot
5. Read both screenshots and confirm the change works as intended
6. If it doesn't look right, iterate until it does

**Do not** commit UI changes based solely on reading the code. Code that looks correct can
produce incorrect visual results due to CSS specificity, inherited styles, flex/grid layout
interactions, and overflow cascading.

**Do not** report a UI fix as complete without a screenshot proving it works.
```

### 6.2 Manifest Entry

Add to `prompts/manifest.yaml` under Tier 2 (implementing phase):

```yaml
frontend/visual-verification.md:
  tier: 2
  phase: implementing
```

This ensures the visual verification guidance is injected whenever the agent enters the implementing phase. Since it's Tier 2, it only adds tokens when the agent is actively coding — not during planning or reviewing.

**Alternative:** Make it Tier 3 with utterance matching on frontend/CSS/UI terms. However, Tier 2 is safer — it guarantees the guidance is present for all implementation work, avoiding the risk of the semantic router missing a UI task.

---

## 7. Dockerfile Changes

### 7.1 Add Playwright to `Dockerfile.agent`

Add after the existing Node.js tool installations:

```dockerfile
# Playwright for visual UI testing
RUN pip install playwright==1.49.0 && \
    playwright install chromium && \
    playwright install-deps chromium
```

**Size impact:** ~300-400MB for Chromium + dependencies. This is significant but necessary. Chromium is the only browser needed.

### 7.2 Add pnpm

The frontend and gateway require pnpm. Add if not already present:

```dockerfile
RUN npm install -g pnpm
```

### 7.3 Frontend Dependencies

The container needs the frontend/gateway `node_modules` pre-installed or installable. Two options:

**Option A (Recommended): Install at dev-env start time.**
The `start-dev-env.sh` script runs `pnpm install` in frontend/ and gateway/ before starting services. Slower first start (~30s) but avoids bloating the image.

**Option B: Pre-install in Dockerfile.**
```dockerfile
COPY frontend/package.json frontend/pnpm-lock.yaml /workspace/bond/frontend/
RUN cd /workspace/bond/frontend && pnpm install --frozen-lockfile
```
Faster start but larger image and stale if dependencies change.

---

## 8. Implementation Phases

### Phase 1: Playwright in the Sandbox (Week 1)

1. Add `playwright` + Chromium to `Dockerfile.agent`
2. Add `pnpm` to the image
3. Build and verify: `docker build -f Dockerfile.agent -t bond-agent:test .`
4. Smoke test: run `playwright install chromium && python -c "from playwright.sync_api import sync_playwright"` inside the container

### Phase 2: Screenshot Script (Week 1)

1. Create `skills/visual-ui-test/scripts/take-screenshot.py`
2. Test standalone: start the dev environment on the host, run the script inside the container pointing at `host.docker.internal:18788`
3. Validate screenshot output is readable by the agent's file read tool

### Phase 3: Test SpacetimeDB Infrastructure (Week 2)

1. Write `setup-test-spacetimedb.sh` (host-side): start SpacetimeDB container on port 18797, publish module, seed data
2. Write `teardown-test-spacetimedb.sh` (host-side): stop and remove test container
3. Verify: host runs setup script, container can reach SpacetimeDB at `$BOND_TEST_STDB_HOST:18797`

### Phase 4: Dev Environment Scripts (Week 2)

1. Write `start-dev-env.sh` — accepts `BOND_TEST_STDB_HOST` env var, configures all services to use test SpacetimeDB on port 18797 (`BOND_SPACETIMEDB_URL`, `NEXT_PUBLIC_STDB_HOST`, `NEXT_PUBLIC_STDB_PORT`)
2. Write `stop-dev-env.sh` — stops local services only (SpacetimeDB is host-managed)
3. Health-check SpacetimeDB before starting services
4. Test inside the container: can all three services start and connect to test SpacetimeDB?
5. Verify Playwright can connect to the in-container frontend

### Phase 5: Skill + Prompt Integration (Week 3)

1. Write `skills/visual-ui-test/SKILL.md`
2. Write `prompts/frontend/visual-verification.md`
3. Add manifest entry to `prompts/manifest.yaml`
4. Test end-to-end: give a coding agent a UI fix task and verify it uses the skill

### Phase 6: Iteration & Hardening (Week 3-4)

1. Test with real UI bug fixes (terminal scroll, overflow, panel sizing)
2. Tune screenshot timing (delay, wait-for selectors)
3. Add common page paths to skill documentation (e.g., `/containers`, `/settings`)
4. Consider adding `--element` flag for targeted element screenshots

---

## 9. Open Questions

1. **Container resource limits.** Chromium is memory-hungry. Do agent containers have enough RAM? Current opensandbox config has `pids_limit: 512` but no explicit memory limit. Playwright + Chromium + Next.js + FastAPI may need 2-3GB.

2. **Hot reload reliability.** Next.js hot reload inside a container (without the host filesystem's inotify) may not work. May need `WATCHPACK_POLLING=true` or `CHOKIDAR_USEPOLLING=true`.

3. **Authentication.** If the frontend requires login, the screenshot script needs to handle auth. Options: bypass auth in test mode, inject a test session cookie, or add `--cookie` flag to the script.

4. **Image analysis quality.** Can Claude reliably analyze a 1280x720 screenshot to spot CSS issues? Initial testing suggests yes for obvious layout problems, but subtle issues (1px misalignment, slightly wrong color) may be missed.

5. **Startup time.** Starting 3 services + waiting for health could take 30-60 seconds. Is this acceptable per invocation? Should the skill keep services running across multiple screenshots?

6. **SpacetimeDB module changes.** When the SpacetimeDB module schema changes, the test instance needs to be torn down and re-created. Could automate: `teardown-test-spacetimedb.sh && setup-test-spacetimedb.sh`.

7. **Concurrent agents.** If multiple coding agents run visual tests simultaneously, port conflicts will occur. Options: randomize ports, use per-agent port offsets, or enforce single-agent-at-a-time for UI work.

---

## 10. References

- **Design Doc 037: Coding Agent Skill** — Agent sandbox architecture, `CodingAgentProcess`, container requirements
- **Design Doc 047: Skills Federation** — Skill discovery, tiered context loading (L0/L1/L2), SKILL.md format, manifest integration
- **Design Doc 098: File Reading Tools Redesign** — Agent tool capabilities, sandbox filesystem access patterns
- **`bond/skills/skill-creator/SKILL.md`** — Canonical SKILL.md format and bundled resource conventions
- **`bond/skills/appdeploy/SKILL.md`** — Example of a workflow-oriented skill with scripts and references
- **`bond/Dockerfile.agent`** — Current agent sandbox image (Python 3.12 slim, Node.js, Claude Code CLI)
- **`bond/docker-compose.dev.yml`** — Dev environment service definitions (frontend :18788, backend :18790, gateway :18789)
- **`paperclip/tests/e2e/playwright.config.ts`** — Existing Playwright configuration pattern in sibling project
- **`bond/prompts/manifest.yaml`** — Prompt fragment manifest with tier/phase system
- **`bond/prompts/frontend/frontend.md`** — Existing frontend prompt (Next.js App Router, component architecture)
