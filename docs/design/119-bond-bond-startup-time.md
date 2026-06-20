# Design Doc 119: Cut bond-bond cold-start and deploy time

**Status:** Implemented + deployed on `feat/startup-time`. Stack startup (§3): §3.1, §3.2.2, §3.2.3, §3.3 done; deferred §3.2.1/§6, §3.4 (gated on Doc 118), §3.5. **Agent cold start (§7)**: deadlock + timeout fixes done and live — agent worker cold start cut from ~10.6s to ~6s, and the post-restart first message from ~78s by removing a 60s stale-health timeout.
**Driver:** A `docker compose up -d --build` redeploy of the `bond` stack takes minutes (image rebuild) followed by a **~19-second** cold start before the backend reports `Application startup complete`. The frontend (473 ms) and gateway (~1 s) are already fast; nearly all the slow time is in the backend boot, and most of it is avoidable work we do on every single container start. This doc enumerates the costs, measured against the 2026-06-19 redeploy of `bond-bond-1`, and proposes targeted fixes.

---

## 1. Why we need this

- **Every restart pays the same fixed tax.** `docker compose restart bond`, `make` rebuilds, host reboot, and the redeploy flow all go through the same ~19 s backend boot. Anyone iterating on bond-bond eats it repeatedly.
- **Most of that time is redundant work, not real startup.** The single biggest chunk is `uv run uvicorn` re-resolving and re-installing the `bond` package on every boot — an environment the Dockerfile already built (`uv sync --frozen --no-dev`, `Dockerfile:34`). We rebuild it again at runtime for nothing.
- **Boot also kicks off external network work that frequently fails.** The job scheduler eagerly runs `sync_models` (and two other jobs) the instant it starts, hammering Anthropic/OpenAI `/models` endpoints. On the live box those keys are currently invalid, so every boot logs `HTTP 401` from both providers. This doesn't block readiness (it runs in a background task — see §3.2), but it's wasted work, log noise, and a latent foot-gun.
- **A full SpacetimeDB backup runs on every startup.** `run_on_startup` (`gateway/src/backups/scheduler.ts:251`) spins up a temp STDB instance and dumps the module on every boot (~4 s on the measured run). Backgrounded, so not on the critical path, but it loads the box at the worst moment and is pure waste when the previous container backed up minutes ago.
- **Backend-only changes still trigger a full image rebuild.** `COPY . .` (`Dockerfile:50`) invalidates the frontend build layer (`Dockerfile:60`, `pnpm build`) on *any* source change, so a one-line backend edit re-runs the most expensive build step. A hot-reload overlay already exists but is opt-in and carries a state-loss caveat (§3.4).

---

## 2. Measured baseline (2026-06-19 redeploy)

Timeline from `docker logs bond-bond-1 --timestamps`, container `Started` at 23:38:04 UTC:

| Time (UTC) | Event | Δ from boot |
|---|---|---|
| 23:38:04 | container started | 0 s |
| 23:38:15 | first app output — `Building bond @ file:///app` (uv sync) | ~11 s |
| 23:38:15.7 | frontend `Ready in 473ms` | 11 s |
| 23:38:16.6 | gateway listening on `:18789` | 12 s |
| 23:38:22.5 | backend `Started server process` | 18 s |
| 23:38:23.1 | backend **`Application startup complete`** | **~19 s** |
| 23:38:26–30 | startup hourly backup (background, ~4 s) | — |

Critical-path attribution to the 19 s "ready" mark:

| Cost | Approx | On critical path? | Fixable by |
|---|---|---|---|
| Container init + interpreter/shell cold start | ~11 s | yes | partial (§3.5) |
| `uv run` re-sync of the `bond` package | ~5–7 s | **yes** | §3.1 (easy) |
| Python import of `backend.app.main` + deps | ~6 s | yes | §3.5 (harder) |
| FastAPI `lifespan` (init_db, seed, MCP connect) | ~0.6 s | yes | §3.3 partial |
| `sync_models` / skill jobs (eager run) | n/a | **no** (background task) | §3.2 (noise/waste) |
| Startup hourly backup | ~4 s | no (background) | §3.3 |

The two numbers worth chasing are the **uv re-sync (§3.1)** and the **interpreter + import cost (§3.5)**. The rest is cleanup that reduces boot load and noise without moving the "ready" mark much.

---

## 3. Proposed changes

### 3.1 Stop re-syncing the uv environment on every boot — *highest leverage, lowest risk*

**Problem.** The container command (`docker-compose.yml:29`, `Dockerfile` `CMD`, and `docker-compose.hot-reload.yml`) launches the backend with `uv run uvicorn …`. `uv run` first reconciles the project environment against `pyproject.toml`/`uv.lock` — the logs show `Building bond @ file:///app` → `Uninstalled 1 package` → `Installed 1 package` on every boot. The Dockerfile already did this at build time (`Dockerfile:34`), so the runtime re-sync is pure overhead on the critical path.

**Fix.** Skip the sync at launch. Two equivalent options:

```sh
# Option A — keep uv run, skip the sync step
uv run --no-sync uvicorn backend.app.main:app --host 0.0.0.0 --port 18790

# Option B — call the already-installed console script directly
/app/.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 18790
```

Both verified against the running container: `/app/.venv/bin/uvicorn` exists and `uv run --no-sync python -c "import backend.app.main"` runs clean (no rebuild output). Prefer **Option A** — it keeps the `uv run` wrapper (env-var handling, `PYTHONPATH`) while dropping only the redundant resolve.

**Apply in three places:** `docker-compose.yml` command, `Dockerfile` `CMD`, and `docker-compose.hot-reload.yml` command (the `--reload` variant benefits identically on every reload).

**Trade-off.** With `--no-sync`, a change to `pyproject.toml`/`uv.lock` no longer self-heals at runtime — you must rebuild the image (which runs `uv sync`). That's correct behavior for a baked image; dependency changes already require a rebuild to land in the frontend/gateway layers anyway.

### 3.2 Make the eager startup jobs cheap and quiet

**Problem.** `JobScheduler._loop()` runs **all** registered jobs immediately on start (`backend/app/jobs/scheduler.py:84`, "Run all jobs immediately on startup"). At boot that means `sync_models`, `recalculate_skill_scores`, and `sync_skills` (`backend/app/main.py:63–65`) all fire at once. `sync_models` reaches out to every provider's `/models` endpoint; with the current dead keys it logs `HTTP 401` from Anthropic and OpenAI on every boot.

**Clarification (corrects an earlier assumption):** `scheduler.start()` does `asyncio.create_task(self._loop())` and returns (`scheduler.py:67`), so these jobs run **concurrently in a background task** — they do *not* block `Application startup complete`. The cost is wasted CPU/network during the boot window, recurring failed calls, and log noise, not added readiness latency.

**Fixes (pick per appetite):**
1. **Fix the credentials** so `sync_models` stops 401ing — see §6 for where keys live. This is the immediate, no-code win.
2. **Add a startup grace delay** so the first run happens N seconds after boot instead of competing with it. A `first_run_delay_seconds` (default ~30 s) on `_JobEntry`, honored before the initial run in `_loop`, keeps the boot window clear while preserving the 6-hour cadence.
3. **Make a provider failure a debug-level, non-retrying skip** rather than a `WARNING`/`ERROR` per provider, so a single bad key doesn't dominate the startup log.

Option 2 is the structural fix; options 1 and 3 are independently worth doing.

### 3.3 Gate the startup backup

**Problem.** `gateway/src/backups/scheduler.ts:251` unconditionally runs an hourly backup on every startup when `hourly.run_on_startup` is set. It spins a temp SpacetimeDB instance (`[backups] Starting temp STDB …`) and dumps the module — ~4 s and real I/O — even when the container it replaced backed up minutes ago.

**Fix.** Honor the existing "missed backup" logic before running the startup backup: if the most recent hourly backup is younger than the hourly interval, skip the startup run (it isn't missed). `checkMissedBackups` (`scheduler.ts:221`) already computes exactly this; the `run_on_startup` block should defer to it rather than firing unconditionally. Keep `run_on_startup` as the behavior for a genuinely stale/missed window only.

### 3.4 Fast iteration path: prefer hot-reload over rebuild

For the *deploy/iteration* cost (not cold start), the rebuild is the expensive part, and `docker-compose.hot-reload.yml` already turns a ~20 s+ rebuild into a ~1 s in-place uvicorn reload for backend-only edits:

```sh
docker compose -f docker-compose.yml -f docker-compose.hot-reload.yml up -d
```

It is correctly off by default: uvicorn `--reload` wipes `SandboxManager._containers` in-memory state, which races with live agent calls. **Design Doc 118** (persist `SandboxManager` state across restarts) removes exactly that caveat — once 118 lands, hot-reload becomes safe enough to be the default dev path. This doc should be read alongside 118; together they make backend iteration both fast (118 unblocks reload) and clean (this doc trims the cold start for the cases that still rebuild).

No change proposed here beyond documenting the workflow and the 118 dependency. We deliberately do **not** make hot-reload the default until 118 ships.

### 3.5 (Stretch) Trim interpreter + import cost

The ~6 s between the uv launch and `Started server process` is Python importing `backend.app.main` and its transitive deps (sqlalchemy, the MCP stack, sandbox adapters, etc.). Realistic levers, in rough order of value:
- **Lazy-import heavy, rarely-first-touched modules** behind function-level imports (several already follow this pattern inside `lifespan`, e.g. `faucet_manager`, `get_sandbox_manager`). Audit module-top imports in `main.py` and the routers for candidates.
- **Defer MCP server connection.** `mcp_manager.ensure_servers_loaded()` is `await`ed in `lifespan` (`main.py:~80`) and connects solidtime before readiness; moving it to a background task (with requests that need MCP awaiting readiness) would shave the connect time off the critical path.

This is a larger, measure-first effort and is explicitly lower priority than §3.1. Profile with `python -X importtime` before committing to it.

---

## 4. Non-goals / rejected alternatives

- **Switching the gateway off `tsx` to compiled JS at runtime.** The gateway is already ready in ~1 s; `tsx`/watch is not a measurable cold-start cost. Not worth the build-step change.
- **A full process supervisor (supervisord/s6) replacing the `sh -c "… & … & wait"` CMD.** Tempting for log separation, but it doesn't reduce startup time and adds an image dependency. Out of scope.
- **Baking secrets/keys into the image to avoid sync failures.** Wrong direction — keys belong in the vault / env, not the image. §3.2/§6 fix the symptom correctly.
- **Making hot-reload the default now.** Unsafe until Doc 118 persists `SandboxManager` state. Deferred, not rejected.

---

## 5. Rollout & verification

**Order:** §3.1 first (biggest win, lowest risk), then §3.3 and §3.2.3 (pure cleanup), then §3.2.2 (scheduler grace delay) if desired, then §3.5 only after profiling.

**Verification.** Capture the same timeline after each change:

```sh
docker compose up -d --build
docker logs bond-bond-1 --timestamps | grep -E "Building bond|Started server process|Application startup complete"
```

Target: `Application startup complete` within **~12 s** of container start after §3.1 (drop the ~5–7 s uv re-sync), and zero `HTTP 401` provider lines in the boot log after §3.2/§6. Confirm the app still serves real routes (`GET /api/v1/health` → 200) and agent containers still reconcile (`Reconciled N agent containers into port map`).

---

## 6. Open items

- **Where the dead provider keys live.** `sync_models` reads provider credentials from the vault/settings path used by `sync_models_stdb` (`backend/app/jobs/sync_models_stdb.py`). The 401s indicate the stored Anthropic and OpenAI keys are stale; locate and rotate them (or clear them so the provider is skipped rather than failing). Tracked separately from the code changes above.
- **The ~11 s container-init gap before first log.** Partly Docker recreate overhead, partly shell + uv cold start. Worth a focused measurement (`docker events` + in-container `date` at CMD entry) to confirm how much §3.1 actually reclaims versus irreducible runtime overhead.

---

## 7. Agent worker cold start (the bigger user-facing win)

The §1–§6 work above is about the **bond-bond stack** booting. Separately, the
latency a user actually feels is an **agent worker** cold-starting to handle a
message — `SandboxManager.ensure_running` creating/recovering the per-agent
container and waiting for its `/health`. A real "what time is it?" turn was
measured at **78.5 s** (gateway's own `Turn complete elapsed=78.5s`). That broke
down as ~60 s wasted + ~11 s real boot + ~7 s LLM turn.

### 7.1 The two costs

**(a) 60 s stale-container health timeout.** After a bond-bond restart its
in-memory container map is wiped, so the next message goes through
`_recover_existing_container` (`backend/app/sandbox/manager.py`). It found the
old agent container (running, but its worker process was dead) and called
`_wait_for_health(timeout=60.0)`. The fast-fail only covers a container that has
*exited*; a running-but-dead-worker burned the full 60 s before recreate.

**(b) ~5 s worker↔gateway startup deadlock.** During its lifespan boot — before
uvicorn accepts — the worker calls gateway `GET /api/v1/container/branch` to
learn its preferred branch (`worker.py:_checkout_preferred_branch`, httpx
`timeout=5.0`). That handler calls `resolveWorkerUrl()` → backend
`/api/v1/agent/resolve` → `ensure_running()`, which re-enters the **same agent
lock** the in-flight create already holds while waiting for *this* worker's
health. Circular; it broke only at the worker's 5 s httpx timeout. Worsened by a
3 s `getWorkerStatus` callback that also re-probes the not-yet-ready worker.

### 7.2 Fixes (all shipped)

| Fix | File | Win |
|---|---|---|
| Recovery + in-memory health re-checks use `_RECOVERED_HEALTH_TIMEOUT=5s`, not 60s; fresh creates keep 90s | `backend/app/sandbox/manager.py` | 60s → 5s on a dead recovered worker |
| `resolveWorkerUrl()` fetch gets a 700ms timeout → returns branch preference without live worker status (correct: worker isn't accepting yet) | `gateway/src/server.ts` | ~5s → ~0.7s, breaks the deadlock |
| `getWorkerStatus` callback 3s → 1s (defense in depth) | `gateway/src/branches/manager.ts` | — |
| Probe github SSH auth once and reuse, not twice per boot | `scripts/agent-entrypoint.sh` | ~0.3s |
| `LITELLM_LOCAL_MODEL_COST_MAP=True` — use bundled cost map, skip GitHub fetch on `import litellm` | `Dockerfile.agent` | ~0.3s |

### 7.3 Result & remaining floor

Fresh agent cold start (container → worker healthy, measured via
`scripts/measure-coldstart.sh`): **10.6 s → ~6.1 s** (stable across runs). The
post-restart first message no longer eats 60 s.

The remaining ~6 s is mostly *necessary* work, confirmed by `python -X
importtime` and a timestamped boot trace:

| Phase | ~cost | Removable? |
|---|---|---|
| container init + github SSH probe | ~1.0 s | mostly no |
| `git fetch`/reset `/bond` + agent_repos fetch | ~1.5 s | partly (defer agent_repos to first use) |
| `import litellm` (76 % of a 1.87 s worker import) | ~1.3 s | no — needed for the turn regardless |
| privilege drop + worker init | ~0.5 s | no |
| branch reconcile (the 700ms gateway timeout) | ~0.7 s | yes — pass preferred branch via env so the worker skips the gateway call entirely (needs adapter + worker change) |

**Next, if we want sub-5s:** (1) have the host adapter pass `BOND_GIT_BRANCH`
into the agent container and have the worker trust it, eliminating the gateway
branch round-trip and the dev→main re-checkout (~0.7s + the git ops); (2) defer
the agent_repos (`/workspace/*`) clone/fetch to a background task so it doesn't
block worker health (~1s) — the worker doesn't import from it. Both are
behavioral changes worth their own review.
