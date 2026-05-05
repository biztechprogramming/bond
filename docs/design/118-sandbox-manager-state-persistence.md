# Design Doc 118: Persist `SandboxManager._containers` across bond-bond restarts

**Status:** Draft — not implemented
**Driver:** `SandboxManager` keeps the entire mapping of agent → running worker container in a process-local dict (`self._containers` in `backend/app/sandbox/manager.py:53`). Every time the bond-bond Python process restarts — uvicorn `--reload` (when hot-reload is on), `docker compose up`, OOM kill, host reboot — that dict is wiped. The recovery path (`_recover_existing_container`, `manager.py:168`) re-discovers the container by name from Docker, but only after a hard health check; concurrent callers that arrive during the recovery window can race and `docker rm -f` containers that were healthy. The 60-second timeout fix in PR #337 makes the loss survivable, but the lost state is still load-bearing on every restart and we'd like to remove the recovery dance entirely.

---

## 1. Why we need this

- **Recovery is the unhappy path, not the happy path.** Today every bond-bond restart sends every active agent through `_recover_existing_container`. That code branch races with normal `ensure_running` calls, has a separate (longer-than-it-should-be) health timeout, and on failure does `docker rm -f` on the live container. The healthy path should be "load state, keep going."
- **Hot-reload exposed it; other restart causes mask it.** PR #337 moved hot-reload behind an opt-in to stop the loop, but the same window opens for every `docker compose restart bond`, every `make rebuild-bond`, and every host reboot. Anyone iterating on bond-bond hits it; we just stopped iterating during the regression because the symptom was loud.
- **State loss has a real cost.** `last_used` resets on every restart, which means the idle-cleanup timer (`cleanup_idle`, `manager.py:628`) starts over and can't reap genuinely idle containers across a restart. `deps_installed` resets, which causes the dep-install script to re-run unnecessarily (the script is idempotent today, but that's not guaranteed forever). `clone_info` and `dep_install_script` aren't recoverable from Docker at all — they're set during creation and used later in the same process.
- **No good place to put new per-container state.** Anything we'd want to track (e.g., per-agent worker version, last branch synced, last health-check timestamp) currently has nowhere to live across a restart.

---

## 2. Approach: snapshot-on-mutation to a JSON file

A JSON file at `~/.bond/data/sandbox-state.json`, written atomically (`tmp + os.replace`) after every mutation of `self._containers`. Loaded once at `SandboxManager.__init__` and validated against Docker reality before being trusted.

**Why JSON, not sqlite or STDB:**

- The state is a single dict with ~10 string-keyed entries and JSON-native values. SQL is overkill.
- STDB is the canonical store for *user-facing* entities. `SandboxManager._containers` is process-local infrastructure state, not domain data; the rest of bond doesn't read it. Putting it in STDB would blur a useful boundary.
- `~/.bond/` is already bind-mounted into bond-bond (`docker-compose.yml:18`) and survives container recreation. No new volume needed.
- Atomic JSON writes are well-understood and don't add a runtime dependency.

**Why snapshot-on-mutation, not periodic flush:**

- Mutations are infrequent (one per agent start/stop/idle-sweep, not per request). The write cost is negligible.
- Periodic flush opens a window where a crash drops the most recent change. Snapshot-on-mutation is durable up to fsync.
- The mutation sites are already enumerable (see §3) — we don't need a "dirty" tracker.

---

## 3. Mutation sites in `manager.py`

The dict is mutated at the following lines (counted against `manager.py` at HEAD of `feat/backend-hot-reload`):

| Line | Operation | Context |
|---|---|---|
| 53  | init `{}` | `__init__` — replaced by `_load()` |
| 190 | full-row write | `_recover_existing_container` success path |
| 266 | `last_used` update | `ensure_running` cache hit |
| 285–286 | `mounts` / `config_fingerprint` update | `_recover_existing_container` post-hoc tagging |
| 319 | full-row write | `ensure_running` create-local path |
| 340 | full-row write | `ensure_running` create-remote path |
| 370 | `del` | `ensure_running` create-failure cleanup |
| 445 | `deps_installed = True` | post-dep-install marker |
| 501 | `last_used` update | `_get_or_create_container_inner` cache hit |
| 510 | `del` | `_get_or_create_container_inner` mounts-changed eviction |
| 516 | full-row write | `_get_or_create_container_inner` create path |
| 598 | `del` | `destroy_agent_container` |
| 660 | `del` | `cleanup_idle` |

13 call sites. Each gets a `self._persist()` call on the next line. No site sits inside a tight loop, so the write overhead is unconditional and fine.

**Two non-options for cleaning this up:**

- *Wrapping `self._containers` in a `PersistentDict` subclass that auto-persists on `__setitem__` / `__delitem__`.* Tempting, but doesn't catch nested mutations (e.g., `self._containers[key]["last_used"] = …` is a mutation of the inner dict, not the outer dict). We'd still have to call `_persist()` manually for those, and now there are two patterns. Reject.
- *Wrapping every mutation site in a helper method (`_set_container`, `_update_field`, `_delete_container`).* Cleaner but a much bigger refactor. Defer; do it only if §3 grows past 20 sites.

---

## 4. File format

```json
{
  "version": 1,
  "containers": {
    "bond-decor-dev-01KJDT339G7GEA1V90Q2YV2NVX": {
      "container_id": "d64fc584f0f0…",
      "worker_url": "http://bond-decor-dev-01KJDT339G7GEA1V90Q2YV2NVX:18791",
      "worker_port": 18792,
      "host_id": "local",
      "last_used": 1746458400.123,
      "mounts": ["{'host_path': …}", "…"],
      "config_fingerprint": "claude-3-7|claude-3-5|abc123…",
      "clone_info": [],
      "dep_install_script": null,
      "deps_installed": true
    }
  }
}
```

**`version: 1`** so we have a forward-compat hook. On read, an unrecognized version → log warning, ignore the file, fall back to live recovery (the existing path).

**Fields not persisted:**

- `self._agent_locks` — `asyncio.Lock` is bound to an event loop; can't survive process restart and doesn't need to. Re-formed on demand by `_get_agent_lock`.
- `self._remote_adapters` — recreated lazily by `_get_adapter`.
- `self._registry` running counts — already separately tracked; out of scope here.

---

## 5. Load-time validation (the hard part)

Persisted state can lie. Between bond-bond going down and coming back up:

- The container may have been killed (`docker rm -f`) by a human, by `cleanup_idle` from another process, or by Docker itself.
- The container may have crashed and exited.
- Port allocations may have shifted (a different process could have grabbed `18792`).

We can't trust the file blindly. On load:

1. Read the file. Empty / missing / corrupt → return `{}` and continue (no regression vs today).
2. For each entry, call `docker inspect <container_id>` (already abstracted as `_is_running` in the local adapter):
   - Container missing → drop the entry.
   - Container exists but not running → drop the entry.
   - Container running but `agent_id` env mismatch → drop the entry (the name was reused for a different agent).
3. For surviving entries, do *not* re-run `_wait_for_health`. The whole point is to skip that. If a request comes in for an agent and the worker is genuinely unresponsive, the existing line-265 path will catch it (now with a 60s timeout) and recreate.
4. After validation, write the cleaned state back (so the file converges toward truth).

**Validation cost:** O(N) `docker inspect` calls at startup, where N = number of running agents. Each is fast (~50ms). For the realistic ceiling of ~20 concurrent agents, that's ~1s of extra startup. Acceptable.

---

## 6. Concurrency

- **Within a process:** `self._persist()` is called from inside the per-agent lock at every mutation site, so writes from a single process are already serialized per agent. Different agents could mutate concurrently; the JSON file write needs its own lock. Use `asyncio.Lock` around the read-modify-write of the file.
- **Across processes:** uvicorn `--reload` shuts the old worker down before starting the new one. `docker compose restart` does the same. So in normal operation there is no overlap. As cheap insurance, take a `fcntl.flock` on the file during write — costs nothing if uncontested, prevents corruption if two processes ever briefly overlap.
- **Atomic on-disk update:** write to `sandbox-state.json.tmp`, fsync, `os.replace` to final name. Standard pattern.

---

## 7. Failure modes and fallbacks

| Failure | Behavior | Why this is OK |
|---|---|---|
| File doesn't exist (first boot) | Load returns `{}`; system behaves exactly as today | No regression |
| File corrupt (truncated, invalid JSON) | Log warning, return `{}`, fall back to live recovery | Worst case: same as today |
| Disk full on persist | Log warning, keep in-memory state, continue | In-memory is authoritative; we'll retry on next mutation |
| `docker inspect` hangs at startup | 5s timeout per call; entries that time out are dropped | Bounded startup cost |
| Persisted entry references a port now used by something else | `_is_running` returns false (different container has the name), drop entry | Same as today's recovery — name uniqueness saves us |
| Concurrent bond-bond processes briefly overlap | `flock` serializes writes; loser blocks ~ms | No corruption |

**Critical invariant:** persistence failure must never break a request. Every `_persist()` is wrapped in `try/except Exception` with a warning log. The in-memory dict is the source of truth; the file is an opportunistic snapshot.

---

## 8. Tests

`backend/tests/test_sandbox_manager.py` (new or extended):

1. **Round-trip.** Create a manager with synthetic state, mutate it through a representative sequence, instantiate a fresh manager pointed at the same file, assert the surviving state matches.
2. **Corrupt file.** Write `"{not valid json"` to the path; assert load returns `{}` and logs a warning.
3. **Stale entry.** Persist an entry referencing a container_id that doesn't exist; assert the entry is dropped on load and the cleaned file is rewritten.
4. **Stale entry — wrong container.** Persist an entry, then start an unrelated container with the same name; load should drop the entry (validation by container_id, not name).
5. **Disk full simulation.** Patch `os.replace` to raise; assert the manager continues to function and logs a warning.
6. **Version mismatch.** Persist a `{"version": 999, ...}` file; assert it's ignored.

No integration test for the cross-process flock case; that's a guard rail, not a behavior.

---

## 9. Migration / rollout

- **No data migration.** The file doesn't exist today; first boot after deploy creates it.
- **No flag flip.** The persistence path is the only path; the existing recovery code becomes the fallback for "file missing or stale."
- **Backwards compatibility.** Older bond-bond builds ignore the file (it's at a path they don't read). Newer builds reading an older state would only hit version=1 since this is the first version. No coordination needed.

---

## 10. Risks

- **The file becomes a "second source of truth" that drifts from Docker.** Mitigated by load-time validation; in steady state, the file is rewritten on every mutation, so drift is bounded by the time between mutations.
- **The `clone_info` and `dep_install_script` fields are opaque blobs we now need to keep stable.** Today they're internal to a single `ensure_running` call. Persisting them means a refactor of either field needs to consider on-disk compatibility. Mitigation: bump `version` on any change; document the schema in a docstring on `_load`.
- **`docker inspect` at startup blocks the event loop if it stalls.** Use `asyncio.create_subprocess_exec` (already the pattern in this file) with a per-call timeout.
- **Test pollution.** Tests that instantiate `SandboxManager` will start writing to `~/.bond/data/sandbox-state.json` unless redirected. Make the path injectable: `SandboxManager(state_path: Path | None = None)`, defaulting to the `~/.bond` location when not provided. Update existing tests to pass `tmp_path`.

---

## 11. Out of scope

- **Persisting `_agent_locks`.** Process-local; doesn't survive restart by design.
- **Persisting `HostRegistry` running counts.** Tracked separately; same restart problem but different data, different doc.
- **STDB-backed sandbox state.** Considered above and rejected — this is process infrastructure, not domain state.
- **Live host migration.** If we ever support migrating a running container between hosts, the state file would need to be replicated; out of scope for now.
- **Removing the `_recover_existing_container` path entirely.** It's the fallback when the file is missing or stale; keep it.

---

## 12. Effort estimate

- Code: ~60–80 LOC in `manager.py` plus ~120 LOC of tests. Net 1–2 hours for the implementation, plus 1–2 hours for tests, plus review.
- No infra changes. No DB changes. No new dependencies.
- One PR, no staged rollout.
