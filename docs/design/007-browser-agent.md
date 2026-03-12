# Design Doc 007: Browser Agent (Playwright + browser-use in Docker Sandbox)

**Status**: Draft — awaiting review
**Author**: Developer Agent
**Date**: 2026-02-25
**Depends on**: 003 (Agent Tools & Sandbox), 005 (Message Queue & Interrupts), 006 (Web Search)

---

## Problem

Bond has `web_search` (find things) and `web_read` (extract static HTML) from design doc 006. But modern web pages are JavaScript-heavy SPAs, behind logins, or require multi-step interaction — click cookie banners, fill forms, paginate through results, interact with dashboards. Static HTML extraction can't handle any of this.

The agent needs a **full browser** — one that can navigate, click, fill forms, take screenshots, and extract content from dynamic pages. This is the difference between `curl` and a human sitting at a computer.

### What agent-zero does

agent-zero uses `browser-use` (v0.5.11) — a Python library that wraps Playwright and gives an LLM autonomous control of a browser. The LLM sees the page state (DOM snapshot + optional screenshots), decides what action to take (click, type, scroll, navigate), and browser-use executes it. This loops until the task is complete or max_steps is reached.

Key patterns from agent-zero's implementation:
- **Headless Chromium** runs inside the Docker container (not on host)
- **browser-use** manages the browser session, action loop, and page state extraction
- **Secrets management** — credentials passed as `sensitive_data` so browser-use can fill login forms without exposing passwords in LLM prompts
- **Interruptible** — hooks on `on_step_start` / `on_step_end` check for cancellation
- **Screenshots** saved per-step for debugging and progress reporting
- **Configurable LLM** — browser tasks can use a different (often cheaper/faster) model than the main chat

---

## Goals

- Browser agent runs **entirely inside the Docker sandbox container** — no browser on host
- Accepts natural language tasks ("log into GitHub and star the bond repo")
- Uses browser-use + Playwright for autonomous multi-step web interaction
- Returns extracted content + final screenshot
- Configurable max_steps to prevent runaway loops
- Interruptible via Bond's interrupt system (design doc 005)
- Configurable LLM model for browser tasks (can differ from chat model)
- Secrets (passwords, API keys) are available to the browser agent without appearing in LLM prompts
- New Docker image `bond-sandbox-browser` with all browser dependencies pre-installed

## Non-Goals

- Running the browser on the host machine
- Browser extensions or profiles that persist across sessions
- Recording/replaying browser macros
- Visual regression testing
- PDF generation or printing
- Real-time screen sharing of the browser session

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Bond Backend (host)                                     │
│                                                         │
│  Agent Loop                                             │
│    │                                                    │
│    ├─ LLM returns tool_call: browser_agent              │
│    │   { "task": "Go to X and extract Y" }              │
│    │                                                    │
│    ├─ SandboxManager.execute()                          │
│    │   (existing sandbox infrastructure from doc 003)   │
│    │                                                    │
│    ▼                                                    │
│  ┌────────────────────────────────────────────────────┐ │
│  │ docker exec into bond-sandbox-browser container    │ │
│  │                                                    │ │
│  │  python /opt/bond/browser_runner.py \              │ │
│  │    --task "Go to X and extract Y" \                │ │
│  │    --max-steps 30 \                                │ │
│  │    --model openai/gpt-4o-mini \                    │ │
│  │    --screenshot-dir /workspace/.bond/screenshots   │ │
│  │                                                    │ │
│  │  ┌──────────────────────────────────────────────┐  │ │
│  │  │ browser_runner.py                            │  │ │
│  │  │                                              │  │ │
│  │  │  browser-use Agent                           │  │ │
│  │  │    ├─ LLM (via API)                          │  │ │
│  │  │    ├─ Playwright BrowserSession              │  │ │
│  │  │    │    └─ Chromium headless shell            │  │ │
│  │  │    ├─ Controller (actions + done handler)     │  │ │
│  │  │    └─ Secrets (env vars → sensitive_data)     │  │ │
│  │  │                                              │  │ │
│  │  │  Loop:                                       │  │ │
│  │  │    1. Get page state (DOM + optional vision)  │  │ │
│  │  │    2. LLM decides action                     │  │ │
│  │  │    3. Execute action (click/type/navigate)   │  │ │
│  │  │    4. Screenshot → /workspace/.bond/shots/   │  │ │
│  │  │    5. Check interrupt flag file              │  │ │
│  │  │    6. Repeat until done or max_steps         │  │ │
│  │  │                                              │  │ │
│  │  │  Output: JSON to stdout                      │  │ │
│  │  │    { result, screenshot_paths, steps_used }  │  │ │
│  │  └──────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│    ├─ Parse JSON output                                 │
│    ├─ Read screenshots from workspace mount             │
│    └─ Return result to agent loop                       │
└─────────────────────────────────────────────────────────┘
```

### Communication Model

The browser agent runs as a **subprocess** inside the existing sandbox container — same pattern as `code_execute`, but instead of running arbitrary Python/JS, it runs `browser_runner.py` with structured arguments and returns structured JSON on stdout.

This keeps the architecture simple: no new services, no new ports, no new protocols. The `SandboxManager` already handles container lifecycle, workspace mounts, and timeout enforcement.

### Interrupt Mechanism

The backend writes a sentinel file (`/workspace/.bond/interrupt`) before the container checks it. The browser runner's `on_step_start` hook checks for this file and raises an exception to abort the loop cleanly. This is simpler than trying to signal a subprocess inside Docker and works across the existing workspace mount.

```
Backend sets interrupt (doc 005)
    │
    ▼
Write /workspace/.bond/interrupt file
    │
    ▼
browser_runner.py on_step_start hook
    ├─ Check for /workspace/.bond/interrupt
    ├─ If exists: delete file, raise InterruptError
    └─ browser-use stops, returns partial results
```

---

## Docker Image Specification

### `docker/sandboxes/browser/Dockerfile`

```dockerfile
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

# ── System dependencies ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Python
    python3 python3-pip python3-venv \
    # Playwright/Chromium dependencies
    fonts-unifont fonts-liberation fonts-noto-color-emoji \
    libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libcups2 libdrm2 libxkbcommon0 libpango-1.0-0 \
    libasound2t64 \
    # Networking
    ca-certificates curl wget \
    # Utilities
    && rm -rf /var/lib/apt/lists/*

# ── Python packages ──────────────────────────────────────────────
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

COPY requirements-browser.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-browser.txt

# ── Playwright Chromium headless shell ───────────────────────────
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install chromium --only-shell

# ── Browser runner script ────────────────────────────────────────
COPY browser_runner.py /opt/bond/browser_runner.py

# ── Environment ──────────────────────────────────────────────────
ENV ANONYMIZED_TELEMETRY=false
ENV BROWSER_USE_LOGGING_LEVEL=info

# ── Non-root user ────────────────────────────────────────────────
RUN useradd -m -s /bin/bash sandbox
USER sandbox

# ── Keep container alive for docker exec ─────────────────────────
CMD ["sleep", "infinity"]
```

### `docker/sandboxes/browser/requirements-browser.txt`

```
browser-use>=0.5.11,<0.6
playwright>=1.52.0,<2
openai>=1.0
anthropic>=0.30
litellm>=1.40
pydantic>=2.0
```

### Build

```makefile
# Added to existing Makefile sandbox-build target
sandbox-build-browser:
	docker build -t bond-sandbox-browser docker/sandboxes/browser/
```

### Image Size Estimate

| Component | Size |
|-----------|------|
| Ubuntu 24.04 base | ~77 MB |
| System packages (fonts, libs) | ~120 MB |
| Python + venv | ~50 MB |
| browser-use + deps | ~80 MB |
| Chromium headless shell | ~150 MB |
| **Total** | **~480 MB** |

---

## Tool Definition

```json
{
  "name": "browser_agent",
  "description": "Control a browser to complete tasks on web pages. The browser agent can navigate, click, fill forms, read content, and take screenshots. Use for: interacting with web apps, filling out forms, logging into sites, extracting data from dynamic/JS-heavy pages, or any task that requires a real browser. For simple page reads, prefer web_read instead.",
  "parameters": {
    "type": "object",
    "required": ["task"],
    "properties": {
      "task": {
        "type": "string",
        "description": "Natural language description of what to do in the browser. Be specific about what page to visit, what to interact with, and what information to extract or action to complete."
      },
      "start_url": {
        "type": "string",
        "description": "Optional URL to navigate to before starting the task. If omitted, the browser starts on a blank page."
      },
      "max_steps": {
        "type": "integer",
        "default": 30,
        "description": "Maximum number of browser actions before stopping. Each action is one click, type, scroll, or navigation. Default 30, max 100."
      },
      "include_screenshot": {
        "type": "boolean",
        "default": true,
        "description": "Whether to capture and return a screenshot of the final page state."
      },
      "use_vision": {
        "type": "boolean",
        "default": false,
        "description": "Whether to send screenshots to the LLM for visual understanding. Uses more tokens but helps with visually complex pages. Requires a vision-capable model."
      }
    }
  }
}
```

**Output format:**

```json
{
  "success": true,
  "result": "The repository has 1,234 stars. The README describes Bond as a local-first AI assistant...",
  "steps_used": 7,
  "final_url": "https://github.com/user/bond",
  "screenshot_path": "/workspace/.bond/screenshots/browser_abc123.png",
  "error": null
}
```

---

## Implementation Design

### 1. Browser Runner (`docker/sandboxes/browser/browser_runner.py`)

A self-contained Python script that runs inside the container. It receives arguments, runs browser-use, and outputs JSON to stdout.

```python
#!/usr/bin/env python3
"""Bond browser agent runner — executes inside sandbox container."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel


class TaskResult(BaseModel):
    title: str
    response: str
    page_summary: str


async def run_browser_task(
    task: str,
    start_url: str | None,
    max_steps: int,
    use_vision: bool,
    screenshot_dir: str,
    model_name: str,
    interrupt_file: str,
):
    import browser_use
    from browser_use import Agent, BrowserSession, BrowserProfile, Controller, ActionResult

    # ── Find Chromium headless shell ──────────────────────────────
    pw_cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/playwright"))
    binary = next(pw_cache.glob("chromium_headless_shell-*/chrome-*/headless_shell"), None)
    if not binary:
        raise RuntimeError("Chromium headless shell not found")

    # ── Browser session ───────────────────────────────────────────
    session = BrowserSession(
        browser_profile=BrowserProfile(
            headless=True,
            disable_security=True,
            chromium_sandbox=False,
            executable_path=str(binary),
            keep_alive=False,
            minimum_wait_page_load_time=1.0,
            wait_for_network_idle_page_load_time=2.0,
            maximum_wait_page_load_time=10.0,
            window_size={"width": 1280, "height": 1024},
            viewport={"width": 1280, "height": 1024},
            no_viewport=False,
            args=["--headless=new", "--no-sandbox", "--disable-gpu"],
        )
    )

    # ── Controller with done action ───────────────────────────────
    controller = Controller(output_model=TaskResult)

    @controller.registry.action("Complete task", param_model=TaskResult)
    async def complete_task(params: TaskResult):
        return ActionResult(
            is_done=True,
            success=True,
            extracted_content=params.model_dump_json(),
        )

    # ── LLM model ────────────────────────────────────────────────
    # browser-use uses litellm under the hood, so model_name
    # follows litellm format: "openai/gpt-4o-mini", "anthropic/claude-sonnet-4-20250514", etc.
    from langchain_openai import ChatOpenAI
    # Use litellm-compatible model initialization
    model = ChatOpenAI(model=model_name, timeout=120)

    # ── Secrets from environment ──────────────────────────────────
    secrets = {}
    for key, value in os.environ.items():
        if key.startswith("BOND_SECRET_"):
            secret_name = key[len("BOND_SECRET_"):].lower()
            secrets[secret_name] = value

    # ── Navigate to start URL if provided ─────────────────────────
    if start_url:
        task = f"First navigate to {start_url}. Then: {task}"

    # ── Interrupt hook ────────────────────────────────────────────
    interrupt_path = Path(interrupt_file)

    async def check_interrupt(agent):
        if interrupt_path.exists():
            interrupt_path.unlink(missing_ok=True)
            raise InterruptedError("Task interrupted by user")

    # ── Run ───────────────────────────────────────────────────────
    await session.start()

    agent = Agent(
        task=task,
        browser_session=session,
        llm=model,
        use_vision=use_vision,
        controller=controller,
        enable_memory=False,
        sensitive_data=secrets if secrets else None,
    )

    screenshot_path = None
    try:
        result = await agent.run(
            max_steps=max_steps,
            on_step_start=check_interrupt,
            on_step_end=check_interrupt,
        )

        # Take final screenshot
        os.makedirs(screenshot_dir, exist_ok=True)
        page = await session.get_current_page()
        if page:
            import uuid
            shot_name = f"browser_{uuid.uuid4().hex[:8]}.png"
            screenshot_path = os.path.join(screenshot_dir, shot_name)
            await page.screenshot(path=screenshot_path, full_page=False)

        # Parse result
        if result and result.is_done():
            final = result.final_result()
            output = {
                "success": True,
                "result": final or "Task completed",
                "steps_used": len(result.action_results()) if result.action_results() else 0,
                "final_url": (result.urls() or [""])[-1] if result.urls() else "",
                "screenshot_path": screenshot_path,
                "error": None,
            }
        else:
            urls = result.urls() if result else []
            output = {
                "success": False,
                "result": f"Task did not complete within {max_steps} steps.",
                "steps_used": max_steps,
                "final_url": urls[-1] if urls else "",
                "screenshot_path": screenshot_path,
                "error": "max_steps_reached",
            }
    except InterruptedError:
        output = {
            "success": False,
            "result": "Task interrupted by user.",
            "steps_used": -1,
            "final_url": "",
            "screenshot_path": screenshot_path,
            "error": "interrupted",
        }
    except Exception as e:
        output = {
            "success": False,
            "result": str(e),
            "steps_used": -1,
            "final_url": "",
            "screenshot_path": None,
            "error": "exception",
        }
    finally:
        await session.close()

    return output


def main():
    parser = argparse.ArgumentParser(description="Bond browser agent runner")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--start-url", default=None, help="Starting URL")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--use-vision", action="store_true")
    parser.add_argument("--screenshot-dir", default="/workspace/.bond/screenshots")
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--interrupt-file", default="/workspace/.bond/interrupt")
    args = parser.parse_args()

    result = asyncio.run(run_browser_task(
        task=args.task,
        start_url=args.start_url,
        max_steps=args.max_steps,
        use_vision=args.use_vision,
        screenshot_dir=args.screenshot_dir,
        model_name=args.model,
        interrupt_file=args.interrupt_file,
    ))

    # Output ONLY JSON to stdout (browser-use logs go to stderr)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
```

### 2. Backend Tool Handler (`backend/app/tools/browser_agent.py`)

```python
# backend/app/tools/browser_agent.py

async def execute_browser_agent(
    sandbox: ResolvedSandbox,
    sandbox_manager: SandboxManager,
    task: str,
    start_url: str | None = None,
    max_steps: int = 30,
    include_screenshot: bool = True,
    use_vision: bool = False,
    browser_model: str | None = None,
    secrets: dict[str, str] | None = None,
) -> dict:
    """Execute browser agent task inside the sandbox container."""

    max_steps = min(max_steps, 100)  # Hard cap

    # Build the command
    cmd_parts = [
        "python3", "/opt/bond/browser_runner.py",
        "--task", task,
        "--max-steps", str(max_steps),
        "--screenshot-dir", "/workspace/.bond/screenshots",
        "--interrupt-file", "/workspace/.bond/interrupt",
    ]

    if start_url:
        cmd_parts.extend(["--start-url", start_url])
    if use_vision:
        cmd_parts.append("--use-vision")
    if browser_model:
        cmd_parts.extend(["--model", browser_model])

    # Build environment with secrets (prefixed BOND_SECRET_)
    env = {}
    if secrets:
        for key, value in secrets.items():
            env[f"BOND_SECRET_{key.upper()}"] = value

    # LLM API keys need to be forwarded
    # (these come from Bond's settings, not the user's secrets)
    env["OPENAI_API_KEY"] = await get_setting("llm.api_key.openai")
    env["ANTHROPIC_API_KEY"] = await get_setting("llm.api_key.anthropic")

    # Execute inside the sandbox container
    result = await sandbox_manager.execute(
        sandbox=sandbox,
        language="bash",
        code=shlex.join(cmd_parts),
        timeout=max_steps * 15,  # ~15 seconds per step budget
        env=env,
    )

    if result.exit_code != 0:
        return {
            "success": False,
            "result": f"Browser agent failed: {result.stderr}",
            "steps_used": -1,
            "final_url": "",
            "screenshot_path": None,
            "error": "execution_error",
        }

    # Parse JSON from stdout
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "result": f"Failed to parse browser agent output: {result.stdout[:500]}",
            "steps_used": -1,
            "final_url": "",
            "screenshot_path": None,
            "error": "parse_error",
        }

    return output
```

### 3. Secrets Management

Secrets flow through environment variables, never through LLM prompts:

```
User stores credentials in Bond settings:
  Settings → Secrets → { "github_password": "***", "gmail_password": "***" }
    │
    ▼
Backend loads secrets for the agent's session:
  secrets = await secrets_store.get_agent_secrets(agent_id)
    │
    ▼
Passed as env vars to sandbox:
  BOND_SECRET_GITHUB_PASSWORD=***
  BOND_SECRET_GMAIL_PASSWORD=***
    │
    ▼
browser_runner.py reads env vars → builds sensitive_data dict:
  { "github_password": "***", "gmail_password": "***" }
    │
    ▼
Passed to browser-use Agent(sensitive_data=...)
  → browser-use uses placeholder tokens in LLM prompts
  → replaces with real values only when filling form fields
```

The LLM never sees the actual credential values. browser-use's `sensitive_data` mechanism handles substitution at the action level.

### 4. Model Configuration

The browser agent's LLM model is configured independently from the chat model:

```python
# Agent settings (in agents table)
{
    "model": "anthropic/claude-sonnet-4-20250514",      # Main chat model
    "browser_model": "openai/gpt-4o-mini",       # Browser agent model (cheaper/faster)
    "browser_use_vision": false                    # Whether to use vision mode
}
```

**Why a separate model?** Browser tasks involve many LLM calls (one per step, potentially 30+). Using a cheaper/faster model like GPT-4o-mini for browser navigation keeps costs manageable while the main chat model stays high-quality.

### 5. Screenshot Access

Screenshots are saved to `/workspace/.bond/screenshots/` inside the container. Because `/workspace/` is a mount from the host, the backend can read screenshots directly from the host filesystem after the tool call completes.

```
Container: /workspace/.bond/screenshots/browser_abc123.png
    ↕  (Docker bind mount)
Host: ~/.bond/data/screenshots/browser_abc123.png
```

Screenshots can be:
1. Returned as base64 in the tool result (for the agent to reference)
2. Served via `GET /api/v1/screenshots/{filename}` for the frontend to display
3. Referenced in agent responses so the user sees what the browser saw

### 6. Integration with Existing Tools

The `browser_agent` tool complements the existing tools from doc 006:

| Scenario | Tool |
|----------|------|
| Find information on the web | `web_search` |
| Read a static page | `web_read` |
| Interact with a page (click, fill, login) | `browser_agent` |
| Scrape a JS-rendered SPA | `browser_agent` |

The agent decides which tool to use based on the task. The system prompt should guide:
> Use `web_read` for simple page content extraction. Use `browser_agent` when you need to interact with the page — click buttons, fill forms, navigate multi-page flows, or when the page requires JavaScript to render content.

---

## Database Changes

### Agent settings extension

```sql
-- Migration: add browser model columns to agents table
ALTER TABLE agents ADD COLUMN browser_model TEXT;
ALTER TABLE agents ADD COLUMN browser_use_vision BOOLEAN NOT NULL DEFAULT false;
```

### Secrets store

```sql
-- Migration: secrets table for sensitive credentials
CREATE TABLE agent_secrets (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    key TEXT NOT NULL,           -- e.g., "github_password"
    value_encrypted BLOB NOT NULL,  -- encrypted with Bond's instance key
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_id, key)
);
```

Encryption uses Bond's instance key (generated on first run, stored in `~/.bond/instance.key`). The secrets are only decrypted in memory when needed and passed as env vars to the sandbox.

---

## Settings UI

Add to agent configuration page:

**Browser Agent section**:
- **Browser Model** — dropdown: "Same as chat model" / "gpt-4o-mini" / "gpt-4o" / "claude-sonnet" / custom
- **Use Vision** — toggle (default off, increases token usage ~3x)
- **Max Steps** — slider (5–100, default 30)

**Secrets section** (separate tab):
- Key-value pairs for credentials
- Values masked in UI after save
- "Add Secret" button
- Each secret shows: name, date added, delete button

---

## Stories

### Story B1: Docker Image — bond-sandbox-browser
- Create `docker/sandboxes/browser/Dockerfile`
- Create `docker/sandboxes/browser/requirements-browser.txt`
- Verify Chromium headless shell installs and runs
- Add `sandbox-build-browser` to Makefile
- Test: container starts, `playwright install chromium --only-shell` succeeds
- Test: headless Chromium launches and navigates to a page

### Story B2: Browser Runner Script
- Create `docker/sandboxes/browser/browser_runner.py`
- Implement CLI argument parsing
- Implement browser-use Agent setup (session, controller, done handler)
- Implement interrupt file checking in step hooks
- Implement screenshot capture
- JSON output to stdout, logs to stderr
- Test: run inside container with a simple task ("go to example.com and extract the title")

### Story B3: Backend Tool Handler
- Create `backend/app/tools/browser_agent.py`
- Add `browser_agent` tool definition to tool registry
- Implement `execute_browser_agent` — builds command, calls SandboxManager
- Parse JSON output, handle errors
- Forward LLM API keys as env vars
- Test: mock sandbox execution, verify command construction and output parsing

### Story B4: Interrupt Integration
- Backend writes `/workspace/.bond/interrupt` file on interrupt signal
- browser_runner checks file in `on_step_start` hook
- Partial results returned on interrupt
- Clean up interrupt file after handling
- Test: start browser task, send interrupt, verify clean stop

### Story B5: Secrets Management
- Migration: `agent_secrets` table
- Backend: `SecretsStore` — CRUD for encrypted secrets
- Backend: decrypt and pass as `BOND_SECRET_*` env vars to sandbox
- browser_runner reads env vars into `sensitive_data` dict
- Settings UI: secrets management tab
- Test: store secret, execute browser task with login, verify credential not in LLM logs

### Story B6: Model Configuration
- Migration: add `browser_model`, `browser_use_vision` to agents
- Settings UI: browser model dropdown and vision toggle
- Backend: resolve browser model from agent config
- Forward model name to browser_runner `--model` flag
- Test: configure different browser model, verify it's used

### Story B7: Screenshot Serving
- Backend: `GET /api/v1/screenshots/{filename}` endpoint
- Serve screenshots from workspace mount directory
- Frontend: render screenshots inline in tool results
- Test: browser task produces screenshot, frontend displays it

---

## Rollout

**Phase 1 (Stories B1–B3)**: Core functionality. Build the Docker image, browser runner, and backend handler. The browser agent works for public pages with no login required. Model defaults to whatever the agent's chat model is.

**Phase 2 (Stories B4–B5)**: Production readiness. Interrupt support and secrets management. The browser agent can now log into sites and be cancelled mid-task.

**Phase 3 (Stories B6–B7)**: Polish. Separate browser model config and screenshot serving in the UI.

---

## Open Questions

1. **Container lifecycle**: Should the browser container stay alive between `browser_agent` calls (like `code_execute` containers in doc 003), or spin up fresh each time? Recommendation: stay alive — Chromium startup takes 2-3 seconds, and browser-use can reuse the session. Kill after idle timeout (5 minutes).

2. **Concurrent browser tasks**: Can one agent run multiple browser tasks in parallel? Recommendation: no — one browser session per container. If the agent calls `browser_agent` while one is running, queue or reject. Simplifies state management.

3. **Network access**: Browser containers need `web` network mode (outbound HTTP/S). Should we allow `full` network mode for intranet access? Recommendation: default to `web`, allow `full` as an agent-level override for power users.

4. **Token cost**: Vision mode sends screenshots to the LLM, which can be expensive (each screenshot ~800 tokens). Should we default vision on or off? Recommendation: off by default. DOM-only mode works well for most tasks. Vision is opt-in for visually complex pages.

5. **browser-use version pinning**: browser-use is evolving rapidly (0.5.x → 0.6.x breaking changes likely). Should we pin to exact version or allow patch updates? Recommendation: pin to `>=0.5.11,<0.6` — accept patches but not minor version bumps.

6. **Downloads**: browser-use supports file downloads. Should we expose downloaded files to the agent? Recommendation: yes — save to `/workspace/.bond/downloads/`, accessible via workspace mount. But add a configurable size limit (default 50MB).

7. **Session persistence**: Should the browser session (cookies, localStorage) persist across `browser_agent` calls within the same conversation? Recommendation: yes within a conversation, no across conversations. Use a per-conversation user data directory like agent-zero does.

8. **Fallback when Docker unavailable**: What happens if Docker isn't running? Recommendation: return a clear error — "Browser agent requires Docker. Install Docker or use web_read for static page content." Same pattern as doc 003's graceful degradation.
