# Design Doc 116: Agent Code Refresh and Branch Control

**Status:** Draft — awaiting review
**Depends on:** 008 (Containerized Agent Runtime), 113 (Clone-Only Workspaces)
**Driver:** Let the user pull `/bond` updates and switch the agent's branch from the conversation header without recreating the container, and make the displayed branch a reliable mirror of what the worker is actually running.

---

## 1. The Problem

After a `git pull` lands on `dev`, the running agent containers are still on the old code. There is no in-product way to refresh them; the workaround is to recreate the container, which is heavy and disruptive (especially across many agents).

The conversation header already shows the agent's branch alongside worker status and container name, but there is no operation attached to that branch indicator. There are also reasons to suspect the displayed branch is not always accurate — it appears to be sourced from somewhere other than the live container state, so it can drift from what the worker is actually importing.

We want three things, simultaneously:

1. A one-click "pull" that brings the agent's `/bond` to the latest of its current branch and reloads the worker.
2. A way to switch the agent to a different branch (without an implicit pull).
3. A branch indicator that always reflects what the worker is currently running.

---

## 2. Design Principles

1. **One source of truth: the worker.** The branch the UI shows is whatever the worker reports it is. Anything else drifts.
2. **Primitives, not combos.** Expose `pull` and `checkout_branch` as separate operations. Compose them later if measurement shows users routinely want both at once.
3. **No implicit network.** Switching branches does not fetch. The user pulls explicitly when they want the latest.
4. **`/bond` only, in this doc.** Refresh and branch operations target the bond library inside the agent (the code the worker imports). Agent-owned workspaces under `/workspace/*` are out of scope here — they have their own credentials and lifecycle (per 113).
5. **Restart the worker, not the container.** A container restart re-runs the entire entrypoint and kills sidecars (execd, jupyter) for no benefit. Refreshing `/bond` only requires a worker process restart so Python re-imports.

---

## 3. Proposed Architecture

### 3.1 Three primitive operations

All three target a single agent (identified by `agent_id`); the host-side adapter dispatches the work into the right container.

| op | what it does | side effects |
|----|--------------|--------------|
| `pull(agent_id)` | `git -C /bond fetch && git -C /bond reset --hard origin/<current-branch>`, then restart the worker | Worker process replaced; in-flight turn aborted (see §3.5) |
| `checkout_branch(agent_id, branch)` | `git -C /bond checkout <branch>`, then restart the worker | Local checkout only; lands on whatever sha origin had at last fetch |
| `restart_worker(agent_id)` | Signal the worker to exit; supervisor brings it back | Used internally by the two above; also useful as a standalone debug tool |

Note that we are **not** introducing a combined "switch-and-pull" operation. If telemetry later shows users frequently fire `checkout_branch` followed immediately by `pull`, we will revisit; until then, two clicks is acceptable.

### 3.2 Branch source-of-truth

The worker emits its branch and HEAD sha as part of its existing status heartbeat. Specifically, on every status push (and on startup) it includes:

```json
{
  "bond_branch": "dev",
  "bond_head": "e406e14a..."
}
```

obtained from `git -C /bond rev-parse --abbrev-ref HEAD` and `git -C /bond rev-parse HEAD`. The UI displays this verbatim. Any other path (querying the host, caching from the container config, inferring from the start-time clone) is removed.

This makes the indicator self-correcting: if a `checkout_branch` succeeds and the worker comes back on the new branch, the next heartbeat reflects it. If the worker fails to come back, the heartbeat stops, and the existing "worker status" indicator surfaces the failure.

### 3.3 Branch persistence across container restart

`scripts/agent-entrypoint.sh` today does `git reset --hard origin/$CURRENT_BRANCH` where `$CURRENT_BRANCH` is whatever the local clone happens to be on. If the user uses `checkout_branch` to switch to `feature/x`, a subsequent `docker restart` will re-clone (or reset) onto `feature/x` only because that is what `/bond` is currently pointing to. That is correct *as long as* the local checkout state survives the restart — which it does for an existing clone, but not for the fresh-clone path (line 25-28).

Concretely:
- Existing clone, container restart → `git reset --hard origin/$CURRENT_BRANCH` honors the user's chosen branch. ✓
- Fresh clone (volume wiped or first start) → `git clone <url> /bond` lands on the remote default branch, ignoring the user's chosen branch. ✗

To make the chosen branch durable, write it to `/data/bond-branch` whenever `checkout_branch` succeeds, and have the entrypoint read it on the fresh-clone path:

```bash
if [ ! -d "/bond/.git" ]; then
    git clone "${BOND_REPO_URL:-...}" /bond
    if [ -f /data/bond-branch ]; then
        BRANCH=$(cat /data/bond-branch)
        git -C /bond checkout "$BRANCH" 2>/dev/null || true
    fi
fi
```

`/data` is already a per-agent volume that survives container recreation (per 008), so this is the right place.

### 3.4 UI surfaces

The conversation header currently shows: agent name, branch, worker status, container name. We add two interactions, no new layout:

- **Pull button.** Small icon (e.g., download-arrow) immediately to the right of the branch. Clicking calls `pull(agent_id)`. Disabled when the worker is busy (see §3.5).
- **Branch click → dropdown.** Clicking the branch name itself opens a dropdown listing branches the local clone knows about (`git -C /bond for-each-ref --format='%(refname:short)' refs/remotes/origin`). Selecting one calls `checkout_branch(agent_id, branch)`. Also disabled when the worker is busy.

Branches created on origin since the last fetch are not in the dropdown. This is by design — the user wanting "the truly latest set of branches" hits Pull first, which fetches.

### 3.5 In-flight turn handling

Both `pull` and `checkout_branch` end with a worker restart. If the worker is mid-turn, restarting kills the user's request.

Initial behavior: **disable the controls when the worker is busy.** Tooltip explains: "Agent is working — wait for the current turn to finish, or stop it manually." This avoids a confirm modal while still preventing accidental kills.

If users report this as frustrating ("I want to pull *now* and the agent always seems busy"), revisit with either an explicit interrupt-and-pull action or a drain wait. Don't pre-build either.

### 3.6 Worker restart mechanism

Confirmed state today:

- The worker is started as PID 1 via `exec gosu bond-agent python -m backend.app.worker` at the end of `scripts/agent-entrypoint.sh`.
- Agent containers are spawned by `backend/app/sandbox/bond_host_daemon.py` with no `--restart` flag — `docker inspect` reports `RestartPolicy = "no"`.
- No `tini`, `s6-overlay`, `dumb-init`, or other supervisor is present in any agent Dockerfile.

**There is no supervisor. If the worker exits, the container dies and stays dead.** That rules out approaches that depend on a respawn loop — we cannot have the worker exit and expect it to come back.

The right mechanism is **`os.execv` self-replacement**: the worker traps `SIGUSR1`, performs any cleanup it needs (close DB connections, flush state), then calls `os.execv(sys.executable, [sys.executable, "-m", "backend.app.worker", *sys.argv[1:]])`. `execv` replaces the process image *in place* — same PID, same parent, same file descriptors get re-inherited as the new process opens them — so PID 1 stays stable and the container does not exit. Python's import cache is wiped because it's a new process, which is the entire point.

`restart_worker` sends SIGUSR1 and waits for the next heartbeat as confirmation. No supervisor work is needed; no entrypoint change is needed for restart support.

### 3.7 Backend wiring

New host-side adapter methods (callable via the existing agent-control RPC channel):

- `agent.pull(agent_id) -> {branch, head_sha, restarted: bool}`
- `agent.checkout_branch(agent_id, branch) -> {branch, head_sha, restarted: bool}`
- `agent.restart_worker(agent_id) -> {restarted: bool}`

Each method:
1. Validates the agent exists and the container is up.
2. Refuses if the worker is currently busy (status != `idle`); returns a structured error the UI can show.
3. Runs the git operation via `docker exec` with a short timeout (network ops capped at e.g. 30 s).
4. Sends SIGUSR1 to the worker.
5. Waits for the next heartbeat (timeout, e.g. 10 s) and returns the new `branch`/`head_sha`.

Errors from any step propagate; the UI shows them inline near the control that triggered them.

---

## 4. Implementation Plan

1. **Backend / adapter:** add the three methods, with `docker exec` plumbing and the SIGUSR1 wait.
2. **Worker:** add SIGUSR1 → `os.execv(sys.executable, [...])` handler; ensure the heartbeat already includes `bond_branch` and `bond_head` (add if missing).
3. **Entrypoint:** read `/data/bond-branch` on fresh-clone path (one block, ~5 lines).
4. **UI:** add the pull icon and branch-dropdown wiring; replace any other branch source with the heartbeat field.
5. **Telemetry:** count `pull`, `checkout_branch`, and "back-to-back checkout-then-pull" events. The combo decision in §3.1 depends on this.

Steps 1, 2, and 4 can land independently behind the existing worker-status gating. Step 3 is a one-line change to the entrypoint and ships with the same PR as step 1.

---

## 5. Out of Scope

- **Combined "switch and pull"** — re-evaluate after telemetry from §4.
- **Refreshing `/workspace/*` repos.** Those are agent-owned; their refresh story belongs with 113.
- **Cross-agent batch operations** ("pull all running agents"). Likely a follow-up; the per-agent primitives compose into it cleanly.
- **Rebuilding the container image.** A code pull only catches Python source changes inside `/bond`. Dockerfile, base image, and system-package changes still require a rebuild; that has its own UX (not designed here).

---

## 6. Open Questions

1. **Branch-list freshness.** §3.4 lists branches from `refs/remotes/origin/*` without a fetch. Acceptable, but we should confirm with users that "missing newly created branches" is not surprising in practice. A "refresh branch list" affordance (fetch-only, no checkout, no restart) is cheap to add if it is.
2. **Multi-conversation agents.** If an agent serves multiple conversations, a pull from one conversation's header affects all of them. Confirm this is the intended UX; if not, the controls move to a per-agent (not per-conversation) settings panel.
