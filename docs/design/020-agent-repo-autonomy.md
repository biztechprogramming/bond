# Design Doc 020 — Agent Repo Autonomy

**Status:** Draft  
**Date:** 2026-03-04  
**Author:** Developer Agent

---

## Overview

Bond agents should be able to improve their own codebase — adding tools, refining prompts, fixing bugs — without human intervention beyond a PR review. This doc describes how to give each agent a writable clone of the Bond repo, enforce that all changes flow through pull requests, and propagate merged changes back to running agents automatically.

The result is a self-improving system where humans remain the review gate but are not a bottleneck.

---

## Goals

- Agents can create tools, update prompts, and fix bugs autonomously
- All changes require a PR — no direct pushes to `main`
- Full git history provides an audit trail of every agent-made change
- Merged changes propagate back to running agents automatically
- No agent can unilaterally break production Bond

---

## Architecture

### Current State

```
Host: ~/bond (source)
  └── Read-only bind mount → /bond inside each container
```

Agents can read Bond source but cannot write to it.

### Proposed State

```
GitHub: bond repo (main = protected)
  │
  ├── Host: ~/bond (production clone, tracks main)
  │     └── Read-only bind mount → /bond inside Gateway (for serving)
  │
  └── Container: /bond (agent's own clone, read-write)
        └── Agent commits to feature branches, pushes to remote, opens PRs
```

Each agent container gets its **own read-write git clone** of the bond repo, not a bind mount. The agent works on feature branches, pushes to GitHub, and opens a PR. Merging to `main` is the only way to get changes into production.

---

## Branch Protection

Configure GitHub branch protection on `main`:

- ✅ Require pull request before merging
- ✅ Require at least 1 approving review (human or designated reviewer agent)
- ✅ Dismiss stale reviews on new commits
- ✅ Require status checks to pass (CI: `npm run build`, `uv run pytest`)
- ❌ Allow force pushes — disabled
- ❌ Allow direct pushes — disabled

A server-side git hook (`pre-receive`) can serve as a secondary enforcement layer if self-hosting the remote:

```bash
#!/bin/bash
# hooks/pre-receive — reject pushes to main
while read old_sha new_sha ref; do
  if [ "$ref" = "refs/heads/main" ]; then
    echo "Direct pushes to main are not allowed. Open a PR."
    exit 1
  fi
done
```

---

## Container Setup

At container startup, instead of bind-mounting `~/bond`, the entrypoint clones the repo:

```bash
# entrypoint.sh
if [ ! -d "/bond/.git" ]; then
  git clone "$BOND_REPO_URL" /bond
fi
cd /bond
git fetch origin
git checkout main
git pull origin main

# Configure agent identity for commits
git config user.name "$AGENT_NAME"
git config user.email "$AGENT_EMAIL"
```

**Environment variables per container:**

```
BOND_REPO_URL=git@github.com:biztechprogramming/bond.git
AGENT_NAME=bond-agent-<id>
AGENT_EMAIL=agent-<id>@bond.internal
GITHUB_TOKEN=<scoped token for this agent>
```

Each agent gets a **scoped GitHub token** with permissions:
- `contents: write` (push branches)
- `pull-requests: write` (open PRs)
- No `admin` access — cannot modify branch protection rules

---

## Agent Workflow

### Making a Change

```
1. Identify improvement (new tool, prompt fix, bug)
2. git checkout -b feat/tool-get-weather
3. Write the change (e.g., backend/app/agent/tools/dynamic/get_weather.py)
4. git add + git commit -m "feat: add get_weather tool"
5. git push origin feat/tool-get-weather
6. Open PR via GitHub API → notify human reviewer
```

Bond provides a native `repo_pr` tool the agent can call:

```python
# Tool: repo_pr
# Creates a branch, commits staged changes, pushes, opens PR
{
  "branch": "feat/tool-get-weather",
  "title": "feat: add get_weather tool",
  "body": "Adds a dynamic tool to fetch weather by location using wttr.in. No API key required.",
  "files": {
    "backend/app/agent/tools/dynamic/get_weather.py": "<code>"
  }
}
```

### What agents can change

| Path | Purpose | Notes |
|------|----------|-------|
| `backend/app/agent/tools/dynamic/` | Dynamic tools | Primary use case |
| `backend/app/agent/prompts/` | System prompts, fragments | Replaces DB-stored prompts |
| `backend/app/agent/tools/definitions.py` | Tool schemas | Schema changes only, not logic |
| `docs/` | Documentation | Low risk |

### What agents must NOT change

| Path | Reason |
|------|--------|
| `backend/app/agent/tools/native.py` | Core tool logic — high blast radius |
| `backend/app/worker.py` | Agent loop — breaking change risk |
| `gateway/src/server.ts` | Gateway entrypoint |
| `gateway/src/persistence.ts` | Persistence routing |
| `migrations/` | DB migrations — irreversible |
| `backend/app/core/` | Auth, crypto, vault |

These restrictions are documented in `AGENTS.md` and enforced by PR review, not technically (agents could still attempt changes — humans reject the PR).

---

## Prompt Versioning (Bonus: Replaces DB Prompts)

This architecture naturally solves the prompt versioning problem. Prompts move out of the database and into the repo:

```
backend/app/agent/prompts/
  ├── system/
  │   ├── default.md
  │   └── coding.md
  ├── fragments/
  │   ├── memory.md
  │   ├── tools.md
  │   └── context.md
  └── templates/
      └── task.md
```

Workers load prompts from the filesystem at startup. Changing a prompt = opening a PR. Git history = full audit trail. No migration required.

---

## Merge → Live Propagation

When a PR merges to `main`, running agents need to pick up the changes.

**Mechanism: GitHub Webhook → Gateway → Agent Reload**

```
GitHub merge event
  → POST /webhooks/github (Gateway)
  → Gateway verifies signature
  → Gateway broadcasts "repo_updated" event to connected workers
  → Worker runs: git pull origin main (in /bond)
  → Worker reloads dynamic tools + prompts
```

For tools that require a full restart (e.g., new dependencies), the Gateway can signal the container orchestrator to restart the container, which will re-clone/pull on startup.

**Selective reload** (no restart required):
- New/updated files in `tools/dynamic/` → hot-reload via `importlib.reload()`
- Updated prompts in `prompts/` → reload from filesystem
- Changes to `worker.py` or core files → require restart

---

## Security Considerations

| Risk | Mitigation |
|------|-----------|
| Agent pushes malicious code | PR review required before merge; CI must pass |
| Agent modifies branch protection | Token has no admin scope |
| Agent opens too many PRs | Rate limit via Gateway |
| Merge breaks production Bond | CI catches build/test failures; easy `git revert` |
| Agent leaks secrets in code | Pre-commit hook scans for API key patterns; PR review |
| Clone gets out of sync | `git pull` on every container start; webhook-triggered pull |

---

## Dynamic Tools Directory

Tools created by agents land in a dedicated directory, isolated from core tools:

```
backend/app/agent/tools/
  ├── native.py          # core tools (human-written, high trust)
  ├── definitions.py     # tool schemas
  ├── files.py           # file tools
  └── dynamic/           # agent-created tools (PR-reviewed)
      ├── __init__.py
      ├── get_weather.py
      └── summarize_url.py
```

The dynamic loader scans this directory at startup and on webhook-triggered reload:

```python
# backend/app/agent/tools/dynamic_loader.py

import importlib
import importlib.util
from pathlib import Path

DYNAMIC_DIR = Path(__file__).parent / "dynamic"

def load_dynamic_tools() -> dict:
    tools = {}
    for path in DYNAMIC_DIR.glob("*.py"):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "execute") and hasattr(mod, "SCHEMA"):
            tools[path.stem] = {
                "fn": mod.execute,
                "schema": mod.SCHEMA,
            }
    return tools
```

Each dynamic tool file follows a simple contract:

```python
# backend/app/agent/tools/dynamic/get_weather.py

SCHEMA = {
    "name": "get_weather",
    "description": "Get current weather for a location.",
    "parameters": {
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City or region name"}
        },
        "required": ["location"]
    }
}

def execute(location: str) -> dict:
    import requests
    r = requests.get(f"https://wttr.in/{location}?format=j1", timeout=10)
    r.raise_for_status()
    return r.json()["current_condition"][0]
```

---

## Migration Plan

| Step | What |
|------|------|
| 1 | Add branch protection to `main` on GitHub |
| 2 | Create `backend/app/agent/tools/dynamic/` directory with `__init__.py` |
| 3 | Create `backend/app/agent/prompts/` directory, migrate DB prompts to files |
| 4 | Build `dynamic_loader.py` + integrate into worker startup |
| 5 | Build `repo_pr` native tool (branch + commit + push + PR) |
| 6 | Update container entrypoint to clone instead of bind-mount |
| 7 | Add GitHub webhook handler to Gateway |
| 8 | Add hot-reload on webhook event |
| 9 | Update `AGENTS.md` with what agents can/cannot change |

---

## Open Questions

- **Reviewer agent:** Should there be a designated "reviewer agent" that automatically approves low-risk PRs (docs, prompts) and flags high-risk ones (core files) for human review?
- **Per-agent tokens:** GitHub personal access tokens vs. GitHub App (App is better for production — scoped per-installation, no personal account dependency)
- **Clone freshness:** Should clones be ephemeral (re-clone on every container start) or persistent volumes that `git pull`?
- **Conflict resolution:** If two agents modify the same file, standard git conflict — the second PR needs to rebase. Acceptable?
