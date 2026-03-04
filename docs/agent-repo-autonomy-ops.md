# Agent Repo Autonomy — Operations Guide

**Date:** 2026-03-04  
**Related design docs:** design/020-agent-repo-autonomy.md, design/021-prompt-hierarchy.md

---

## Overview

Design docs 020 and 021 introduce agent repo autonomy and the prompt hierarchy. This guide covers what human actions are needed to operationalize them, which are strictly necessary vs. optional, and simpler alternatives where they exist.

---

## Action 1: Branch Protection on `main`

**What:** In GitHub repo settings → Branches → Add protection rule for `main`:
- ✅ Require pull request before merging
- ✅ Require at least 1 approving review
- ✅ Require status checks to pass (CI: `npm run build`, `uv run pytest`)
- ❌ Allow direct pushes — disabled

**Why it matters:** This is the only hard guarantee that no agent can silently deploy untested code to the version of Bond that runs your other agents. The `repo_pr` tool always creates a feature branch — a well-behaved agent won't push directly to `main`. But branch protection ensures that even a buggy or poorly-prompted agent physically cannot. Without it, the entire PR review workflow is advisory, not enforced. One bad push and Bond breaks for everyone.

**Simpler alternative?** No. Branch protection is a 2-minute setting in GitHub and is the actual safety net the entire design relies on.

**Do this first, before any agent has write access.**

---

## Action 2: GitHub PAT → Vault (`github.token`)

**What:** Create a GitHub Personal Access Token (or GitHub App) with `contents: write` and `pull-requests: write` scopes. Store it in Bond's vault:

```
Bond settings UI → API Keys → add key: github.token
```

The `repo_pr` tool reads this from the `GITHUB_TOKEN` env var, which `sandbox/manager.py` injects from the vault at container launch.

**Why it matters:** Allows agents to automatically open GitHub PRs after pushing a branch. Without it, agents push branches but can't create PRs — you open them manually.

**Simpler alternative?** Yes — skip it entirely at first. When an agent calls `repo_pr` without a token, it returns:
```
Branch 'feat/add-weather-tool' pushed. Set GITHUB_TOKEN to auto-create PRs.
```
Go to GitHub, see the branch, click "Compare & pull request". 30 seconds. No credential management. Add the token when agent-created PRs become frequent enough to feel like friction.

---

## Action 3: GitHub Webhook

**What:** Two parts:

1. GitHub repo settings → Webhooks → Add webhook:
   - Payload URL: `https://your-gateway-host/webhooks/github`
   - Content type: `application/json`
   - Secret: a random string (`openssl rand -hex 32`)
   - Events: push only

2. Set `GITHUB_WEBHOOK_SECRET=<same value>` in the Gateway environment.

**How it works:** GitHub sends a POST to the Gateway when anything is pushed. The Gateway verifies the HMAC signature, detects a push to `main`, and needs to notify all running agent workers to reload their prompt manifest.

**The fan-out gap:** The Gateway doesn't track which workers are running or on what ports — each agent worker runs on a dynamically assigned port managed by `sandbox/manager.py`. The correct flow is:

```
GitHub
  → POST /webhooks/github (Gateway)
  → POST /api/v1/internal/reload-all (Backend)
  → Backend iterates self._containers (has all worker_urls)
  → Backend calls POST /reload on each worker
  → Worker: git pull origin main + regenerate prompt manifest
```

The backend fan-out endpoint is not yet implemented (tracked as a follow-up to design doc 020). An alternative pattern: workers subscribe to a Gateway SSE broadcast channel at startup and receive reload events over their existing connection — cleaner but more infrastructure.

**Simpler alternative?** Yes — skip the webhook entirely for now. When you merge a PR, restart the affected containers:
```bash
docker restart bond-<agent-id>
```
The entrypoint always pulls latest `main` on startup, so the agent gets fresh content immediately. Add the webhook when restarting containers after merges becomes annoying.

---

## Summary

| Action | Necessary? | When to do it | Simpler alternative |
|--------|-----------|---------------|---------------------|
| Branch protection on `main` | **Yes — do first** | Before any agent has write access | None — this is the safety net |
| GitHub PAT → vault | No | When manual PR creation feels like friction | Open PRs manually after branch push |
| Webhook + secret | No | When restarting containers after merge feels disruptive | `docker restart bond-<agent-id>` after merge |

---

## Quick Start (minimum viable)

1. Enable branch protection on `main` in GitHub settings
2. Merge `feat/020-agent-repo-autonomy` and `feat/021-prompt-hierarchy`
3. Rebuild the agent image: `docker build -f Dockerfile.agent -t bond-agent-worker .`
4. Restart running agent containers — they clone the repo on first start
5. Test: ask an agent to call `repo_pr` to add a simple tool — verify a branch appears in GitHub, verify it cannot push directly to `main`
