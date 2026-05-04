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
2. **HTTP, not docker.** Operations on a *running* agent are HTTP calls to the worker's port, never `docker exec`. Local and remote agents use the identical code path. The host-daemon's job is container lifecycle; once spawned, the agent is reached by URL.
3. **Primitives, not combos.** Expose `pull` and `checkout` as separate operations. Compose them later if measurement shows users routinely want both at once.
4. **No implicit network.** Switching branches does not fetch. The user pulls explicitly when they want the latest.
5. **`/bond` only, in this doc.** Refresh and branch operations target the bond library inside the agent (the code the worker imports). Agent-owned workspaces under `/workspace/*` are out of scope here — they have their own credentials and lifecycle (per 113).
6. **Restart the worker, not the container.** A container restart re-runs the entire entrypoint and kills sidecars (execd, jupyter) for no benefit. Refreshing `/bond` only requires a worker process restart so Python re-imports.

---

## 3. Proposed Architecture

### 3.1 Four primitive operations

All four are HTTP endpoints on the worker (see §3.7 for the full table and §3.8 for transport/auth). Bond-bond calls the worker URL directly — same code path whether the worker is on the same host or a remote one.

| op | what it does | side effects |
|----|--------------|--------------|
| `pull` | `git -C /bond fetch && git -C /bond reset --hard origin/<current-branch>`, then `os.execv` self-restart | Worker process replaced; in-flight turn aborted (see §3.5) |
| `checkout` | `git -C /bond checkout <branch>`, then `os.execv` self-restart | Local checkout only; lands on whatever sha origin had at last fetch |
| `fetch` | `git -C /bond fetch --prune origin` | Refreshes `refs/remotes/origin/*` so newly pushed branches appear in the dropdown. No checkout, no reset, no worker restart. Safe to run while the worker is busy. |
| `reload` | Worker self-replaces via `os.execv` (see §3.6) | Used internally by `pull` and `checkout`; also useful as a standalone debug op. Behavior change to the existing `/reload` endpoint. |

Note that we are **not** introducing a combined "switch-and-pull" operation. If telemetry later shows users frequently fire `checkout` followed immediately by `pull`, we will revisit; until then, two clicks is acceptable.

### 3.2 Branch source-of-truth

The worker already exposes `GET /branch` (`backend/app/worker.py:670`), which returns `{branch, active_turns, pending_reload}` from a live `git rev-parse` against `/bond`. Bond-bond polls this endpoint (or includes it in the existing status pull) for the conversation header indicator and adds `head_sha` to the response (one-line addition: `git rev-parse HEAD`). The UI displays whatever the worker last reported.

This makes the indicator self-correcting: if a checkout succeeds and the worker comes back on the new branch, the next poll reflects it. If the worker is unreachable, the existing "worker status" indicator surfaces the failure independently. No host-side caching, no inference from start-time clone state.

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

The conversation header currently shows: agent name, branch, worker status, container name. We add three interactions, no new layout:

- **Pull button.** Small icon (e.g., download-arrow) immediately to the right of the branch. Clicking calls `pull(agent_id)`. Disabled when the worker is busy (see §3.5).
- **Fetch button.** A second icon next to the pull button (e.g., refresh-cycle). Clicking calls `fetch_branches(agent_id)` and refreshes the dropdown contents on success. **Always enabled** — fetch does not touch the worker or the working tree, so there is no busy state to worry about. This is the path for "a new branch was just pushed and I want to switch to it" without first interrupting the agent.
- **Branch click → dropdown.** Clicking the branch name opens a dropdown listing branches the local clone knows about (`git -C /bond for-each-ref --format='%(refname:short)' refs/remotes/origin`). Selecting one calls `checkout_branch(agent_id, branch)`. Disabled when the worker is busy.

The dropdown reflects only what the local clone has seen. Branches created on origin since the last fetch will not appear until the user hits the fetch button (or pulls, which also fetches).

**Scope: per-agent, not per-conversation.** Even though these controls appear in the conversation header, they target the underlying agent. An agent serving multiple conversations has one container, one worker, one `/bond` checkout — pulling or switching from any conversation's header restarts the worker for *all* conversations the agent is participating in. The header surfaces the controls because it is the most direct affordance, but the operation is always agent-scoped.

### 3.5 In-flight turn handling

Both `pull` and `checkout_branch` end with a worker restart. If the worker is mid-turn, restarting kills the user's request.

Initial behavior: **disable the controls when the worker is busy.** Tooltip explains: "Agent is working — wait for the current turn to finish, or stop it manually." This avoids a confirm modal while still preventing accidental kills.

If users report this as frustrating ("I want to pull *now* and the agent always seems busy"), revisit with either an explicit interrupt-and-pull action or a drain wait. Don't pre-build either.

### 3.6 Worker restart mechanism

Confirmed state today:

- The worker is started as PID 1 via `exec gosu bond-agent python -m backend.app.worker` at the end of `scripts/agent-entrypoint.sh`.
- Agent containers are spawned by `backend/app/sandbox/bond_host_daemon.py` with no `--restart` flag — `docker inspect` reports `RestartPolicy = "no"`.
- No `tini`, `s6-overlay`, `dumb-init`, or other supervisor is present in any agent Dockerfile.
- `POST /reload` already exists in the worker (`backend/app/worker.py:690`), with a deferred-shutdown-on-busy pattern. Today its `_shutdown_for_branch_change` handler **exits the process** — and because nothing supervises the container, this currently relies on bond-bond noticing and recreating the container. Slow and fragile.

**There is no supervisor.** That rules out approaches that depend on a respawn loop. The right mechanism is **`os.execv` self-replacement, called from inside the `/reload` handler itself.** The worker performs any cleanup it needs (close DB connections, flush state), returns 200 to the caller, then calls `os.execv(sys.executable, [sys.executable, "-m", "backend.app.worker", *sys.argv[1:]])`. `execv` replaces the process image *in place* — same PID, same parent, same file descriptors get re-inherited as the new process opens them — so PID 1 stays stable and the container does not exit. Python's import cache is wiped because it's a new process, which is the entire point.

This is a behavior change to the existing `/reload` endpoint, not a new endpoint or a new signal channel. No SIGUSR1, no supervisor, no entrypoint change. The deferred-on-busy logic already in `/reload` carries over unchanged.

### 3.7 Worker HTTP endpoints

All four primitives are **endpoints on the worker's own HTTP server (port 18791)**, called by bond-bond directly. No `docker exec`, no host-side adapter methods. The worker is the only process that needs to mutate `/bond` or restart itself, so it owns the implementations end-to-end.

| endpoint | request | response | busy behavior |
|---|---|---|---|
| `GET /branch` *(extend existing)* | — | `{branch, head_sha, active_turns, pending_reload}` | always responds |
| `POST /pull` | — | `{branch, head_sha, restarted: bool}` | refuses with 409 when `active_turns > 0` |
| `POST /checkout` | `{branch}` | `{branch, head_sha, restarted: bool}` | refuses with 409 when `active_turns > 0` |
| `POST /fetch` | — | `{branches: [{name, sha}]}` | always allowed; does not touch working tree or worker process |
| `POST /reload` *(behavior change)* | `{}` | `{ok, deferred, restarted}` | deferred when `active_turns > 0` (existing behavior); now performs `os.execv` instead of process exit |

`pull`, `checkout`, and `reload` are sequenced:
1. Refuse with 409 if `active_turns > 0` (keeps the existing `/reload` semantic — UI surfaces "agent is busy").
2. Run the git operation via `subprocess.run(["git", ...], cwd="/bond", timeout=30)`.
3. For `pull` and `checkout`, internally invoke the same `os.execv` path as `/reload` so they are functionally one operation.

`fetch` is independent:
1. `subprocess.run(["git", "fetch", "--prune", "origin"], cwd="/bond", timeout=30)`.
2. Read `refs/remotes/origin/*` and return the list with sha per branch.
3. Never touches `active_turns` or the process — safe to call any time.

**Bond-bond's role is just routing.** It looks up the agent's worker URL (which is `http://localhost:<assigned-port>` for local hosts and `http://localhost:<tunnel-forward-port>` for remote hosts via `TunnelManager.add_port_forward` from 089) and proxies the call. There are no bond-side git operations and no docker operations for any of these four primitives.

### 3.8 Auth and transport

The worker's HTTP port currently has no auth. That's tolerable today because it's reachable only from inside docker on the local host; it is not tolerable once we route privileged operations (pull, checkout, reload) through it from a remote bond-bond.

**Auth.** Add Bearer-token middleware to the worker, mirroring `bond_host_daemon.py:159-161`. The token is the existing `BOND_AGENT_TOKEN` env var (already set per agent and read at `worker.py:242` for the MCP proxy). Every privileged endpoint (`/pull`, `/checkout`, `/fetch`, `/reload`, plus existing `/turn`/`/interrupt`) requires `Authorization: Bearer $BOND_AGENT_TOKEN`. `/health` stays open. Bond-bond holds the token (it's the entity that generated it for the agent config) and includes it on every call.

**Transport — local host.** Bond-bond connects directly to `http://localhost:<assigned-port>` where `<assigned-port>` is what docker mapped 18791 to on the host (already tracked in the registry).

**Transport — remote host.** Bond-bond uses the existing `TunnelManager` (089). When an agent is placed on a remote host, the manager calls `tunnel.add_port_forward(container_key, 18791)` once at agent start, getting back a `localhost:<n>` URL that proxies through the SSH `ControlMaster` tunnel to the worker. That URL is what bond-bond uses for *all* worker calls — `/turn`, `/interrupt`, `/branch`, and the four new endpoints. The four primitives are not aware of "local vs remote"; they all hit a `localhost:<port>` URL pulled from the registry.

**What stays in the host-daemon.** Container lifecycle only — `docker run`, `docker stop`, `docker rm`, port assignment, ssh-key/config materialization. None of that is on the path of design 116 once spawning is complete. The host-daemon does **not** gain any new endpoints from this design.

**Registry update.** When `manager.py` provisions an agent, it stores `worker_url` (already does) and the auth token in the agent's record. Bond-bond's per-agent operations look both up and call the worker directly. This is also what makes "scale to N hosts" trivial: the registry is the only thing that needs to know which host owns which agent.

---

## 4. Implementation Plan

1. **Worker (most of the work lives here):**
   - Add Bearer-token middleware reading `BOND_AGENT_TOKEN` (mirror `bond_host_daemon.py:159-161`).
   - Extend `GET /branch` to include `head_sha`.
   - Add `POST /pull`, `POST /checkout`, `POST /fetch` per §3.7.
   - Change `/reload` so its handler runs `os.execv(sys.executable, [...])` in place of the current process-exit path, after returning 200.
2. **Bond-bond:**
   - Pass the worker URL + token through to the four new operations (URL is already in the registry; token needs to be persisted alongside it).
   - Replace any docker-exec or host-daemon paths that today implement equivalent logic with direct worker HTTP calls.
3. **Entrypoint:** read `/data/bond-branch` on the fresh-clone path so a chosen branch survives container recreation (one ~5-line block).
4. **UI:** pull icon, fetch icon, branch-dropdown wiring; replace any other branch source with the `/branch` response.
5. **Telemetry:** count `pull`, `checkout`, `fetch`, and back-to-back `checkout`-then-`pull` events. The combo decision in §3.1 depends on this.

Steps 1, 2, and 4 can land independently behind the existing worker-status gating. Step 3 is small and ships with whichever PR touches the entrypoint next.

---

## 5. Out of Scope

- **Combined "switch and pull"** — re-evaluate after telemetry from §4.
- **Refreshing `/workspace/*` repos.** Those are agent-owned; their refresh story belongs with 113.
- **Cross-agent batch operations** ("pull all running agents"). Likely a follow-up; the per-agent primitives compose into it cleanly.
- **Rebuilding the container image.** A code pull only catches Python source changes inside `/bond`. Dockerfile, base image, and system-package changes still require a rebuild; that has its own UX (not designed here).

---

## 6. Open Questions

None at time of writing.
