# Design Doc 022 — Deployment Checklist: Agent Repo Autonomy

**Status:** Reference  
**Date:** 2026-03-04  
**Related:** 020-agent-repo-autonomy.md, 021-prompt-hierarchy.md

---

## Overview

Design docs 020 and 021 introduce agent repo autonomy and the prompt hierarchy. Three human actions are required to fully operationalize them. This doc explains what each action does, whether it is strictly necessary, and a simpler alternative where one exists.

---

## Action 1: Branch Protection on `main`

**What:** In GitHub repo settings → Branches → Add protection rule for `main`:
- ✅ Require pull request before merging
- ✅ Require at least 1 approving review
- ✅ Require status checks to pass (CI: `npm run build`, `uv run pytest`)
- ❌ Allow direct pushes — disabled

**Why it matters:** This is the only hard guarantee that no agent can silently deploy untested code to the version of Bond that runs your other agents. The `repo_pr` tool always creates a feature branch — a well-behaved agent won't push directly to `main`. But branch protection ensures that even a buggy or poorly-prompted agent physically cannot. Without it, the entire PR review workflow is advisory, not enforced. One bad push and Bond breaks for everyone.

**Is there a simpler alternative?** No. Branch protection is a 2-minute setting in GitHub and is the actual safety net the entire design relies on.

**Do this first, before anything else.**

---

## Action 2: GitHub PAT → Vault (`github.token`)

**What:** Create a GitHub Personal Access Token (or GitHub App) with `contents: write` and `pull-requests: write` scopes. Store it in Bond's vault:

```bash
# Via Bond settings UI → API Keys → add key: github.token
# Or directly via vault CLI if available
```

The `repo_pr` tool reads this from `GITHUB_TOKEN` env var, which `sandbox/manager.py` injects from the vault at container launch.

**Why it matters:** Allows agents to automatically open GitHub PRs after pushing a branch. Without it, agents push branches but cannot create PRs — you would need to open them manually from GitHub.

**Is there a simpler alternative?** Yes. Skip this entirely at first. When an agent calls `repo_pr`, it will push the branch and return:
```
Branch 'feat/add-weather-tool' pushed. Set GITHUB_TOKEN to auto-create PRs.
```
You go to GitHub, see the branch, click "Compare & pull request". This costs 30 seconds and removes a dependency on credential management. Automate it when agent-created PRs become frequent enough to feel like friction.

---

## Action 3: GitHub Webhook (`GITHUB_WEBHOOK_SECRET`)

**What:** Two parts:
1. In GitHub repo settings → Webhooks → Add webhook:
   - Payload URL: `https://your-gateway-host/webhooks/github`
   - Content type: `application/json`
   - Secret: a random string (e.g. `openssl rand -hex 32`)
   - Events: "Just the push event"

2. Set `GITHUB_WEBHOOK_SECRET=<same random string>` in the Gateway environment.

**Why it matters:** When a PR merges to `main`, running agent containers automatically receive a reload signal via `POST /reload`. The worker pulls latest main and regenerates the prompt manifest — new tools and updated fragments are available to the agent without restarting the container.

**Is there a simpler alternative?** Yes. When you merge a PR, restart the affected agent container:
```bash
docker restart bond-<agent-id>
```
The entrypoint script always pulls latest `main` on startup, so the agent gets fresh content immediately. This costs one CLI command per merge and avoids webhook secret management entirely.

Set up the webhook when you have several long-running agents and want merged changes to propagate without interrupting their sessions.

---

## Summary

| Action | Necessary? | When to do it | Simpler alternative |
|--------|-----------|---------------|---------------------|
| Branch protection on `main` | **Yes — do first** | Before any agent has write access | None — this is the safety net |
| GitHub PAT → vault | No | When manual PR creation feels like friction | Open PRs manually after branch push |
| Webhook + secret | No | When container restarts after merge feel disruptive | `docker restart bond-<agent-id>` after merge |

---

## Quick Start

Minimum viable setup to use agent repo autonomy safely:

1. Enable branch protection on `main` in GitHub settings
2. Merge PR `feat/020-agent-repo-autonomy` and `feat/021-prompt-hierarchy`
3. Rebuild the agent Docker image: `docker build -f Dockerfile.agent -t bond-agent-worker .`
4. Restart running agent containers — they will clone the repo on first start
5. Test: ask an agent to use `repo_pr` to add a simple tool — verify a branch appears in GitHub, verify it cannot push directly to `main`
