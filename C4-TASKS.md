# C4: Update SandboxManager — Run Worker in Container

**Reference:** Design Doc 008, §4 (Container Layout), §5 (Worker API), §12 (Migration Path), §14–15

**Goal:** Transform the SandboxManager from running `sleep infinity` containers (with `docker exec` for tool calls) to running the agent worker process inside the container. The container becomes a self-contained agent runtime.

---

## Task 1: Worker Entrypoint Instead of `sleep infinity`

**File:** `backend/app/sandbox/manager.py`

Currently:
```python
cmd.extend([sandbox_image, "sleep", "infinity"])
```

For containerized agents (where we run the worker), change to:
```python
cmd.extend([
    "-e", f"PYTHONPATH=/bond",
    sandbox_image,
    "python", "-m", "backend.app.worker",
    "--port", str(port),
    "--data-dir", "/data",
    "--config", "/config/agent.json",
])
```

Key details:
- `PYTHONPATH=/bond` so the worker can import `backend.app.worker`
- Port allocated from range (see Task 5)
- Store `worker_url` and `worker_port` in container tracking dict
- Host-mode agents (`sandbox_image` set but no worker) still use `sleep infinity` — see Task 7

---

## Task 2: Mount Strategy (All Required Volumes)

**File:** `backend/app/sandbox/manager.py`

Per design doc §4, containerized worker containers need these mounts:

| Mount | Source | Target | Mode |
|-------|--------|--------|------|
| Bond library | Project root (parent of `backend/`) | `/bond` | `ro` |
| Workspace(s) | Per agent config | `/workspace/{name}` | `rw` |
| Agent data | Docker volume `bond-agent-{id}` | `/data` | `rw` |
| Shared memory | `{project_root}/data/shared/` | `/data/shared` | `ro` |
| SSH keys | `~/.ssh` | `/tmp/.ssh` | `ro` |
| Agent config | Generated JSON file | `/config/agent.json` | `ro` |

Implementation details:
- **Bond library:** resolve project root from `Path(__file__).resolve().parents[3]` (sandbox/manager.py → backend/app/sandbox → backend/app → backend → project root). Validate the path contains `backend/app/worker.py` before mounting.
- **Agent data:** use named Docker volume `bond-agent-{agent_id}` — Docker creates automatically on first use, persists across restarts.
- **Shared memory:** create `data/shared/` dir on host if it doesn't exist (`os.makedirs(..., exist_ok=True)`).
- **Agent config:** see Task 3. Mount the specific file, not the directory.
- **SSH keys:** only mount if `~/.ssh` exists on host. Skip silently if not.
- **Port exposure:** `-p {host_port}:18791` — worker always listens on 18791 inside container, mapped to allocated host port.

---

## Task 3: Agent Config Generation

**File:** `backend/app/sandbox/manager.py` (new method `_write_agent_config`)

Generate the config file the worker reads on startup:

```json
{
  "agent_id": "agent-abc123",
  "model": "claude-sonnet-4-20250514",
  "system_prompt": "You are a helpful assistant...",
  "tools": ["respond", "search_memory", "memory_save", ...],
  "api_keys": {
    "anthropic": "sk-ant-...",
    "openai": "sk-..."
  }
}
```

Security requirements:
- Write to `{data_dir}/agent-configs/` (not `/tmp` — too exposed)
- File permissions: `0o600` (owner read/write only)
- Directory permissions: `0o700`
- Use `os.open()` with explicit mode flags, not `open()` + `chmod` (avoids race window)
- Clean up config file in `destroy_agent_container()` AND in a `finally` block if container creation fails
- Track config file path in container tracking dict for cleanup

Config path: `{project_root}/data/agent-configs/{agent_id}.json`

---

## Task 4: Health Wait on Startup

**File:** `backend/app/sandbox/manager.py` (new method `_wait_for_health`)

After starting the container, poll `/health` until the worker is ready:

```python
async def _wait_for_health(
    self,
    worker_url: str,
    agent_id: str,
    container_id: str,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> None:
    """Poll worker /health until it responds with correct agent_id, or raise."""
```

Requirements:
- Poll every `interval` seconds (default 0.5s)
- Timeout after 30s (configurable)
- **Validate response:** check `status == "ok"` AND `agent_id` matches expected
- On timeout: capture `docker logs {container_id} --tail 50` and include in the RuntimeError message (critical for debugging startup failures)
- On connection refused: expected during startup, just retry
- On unexpected HTTP status: log warning, retry
- Log successful startup with elapsed time: `"Worker healthy for agent %s in %.1fs"`
- Use `httpx.AsyncClient` with short per-request timeout (2s) to avoid blocking on hung workers

---

## Task 5: Port Allocation

**File:** `backend/app/sandbox/manager.py` (new methods)

Port range: 18791–18890 (100 ports, matching design doc §15.1)

```python
_PORT_RANGE_START = 18791
_PORT_RANGE_END = 18890

def _allocate_port(self, agent_key: str) -> int:
    """Allocate an unused port. Raises RuntimeError if range exhausted."""

def _release_port(self, agent_key: str) -> None:
    """Release a port back to the pool."""
```

Requirements:
- Track in `self._port_map: dict[str, int]` (agent_key → port)
- **Verify port is actually free** before allocating — `socket.socket().connect_ex(("localhost", port))` to detect ports held by non-Bond processes
- On range exhaustion: raise `RuntimeError("No available ports in range {start}–{end}. {n} agents running.")`
- Thread/async safe: use a simple set for tracking (single-threaded asyncio, no lock needed, but add a comment explaining why)
- On container reuse (existing container already has a port): recover port from stored state, don't re-allocate

---

## Task 6: `ensure_running()` Method + Per-Agent Lock

**File:** `backend/app/sandbox/manager.py`

New high-level method (per design doc §12):

```python
async def ensure_running(self, agent: dict) -> dict[str, Any]:
    """Ensure agent's containerized worker is running.
    
    Returns {"worker_url": "http://localhost:{port}", "container_id": "abc123"}.
    Raises RuntimeError if container fails to start or health check times out.
    """
```

**Per-agent lock** to prevent concurrent `ensure_running()` calls from racing:

```python
self._agent_locks: dict[str, asyncio.Lock] = {}

def _get_agent_lock(self, agent_key: str) -> asyncio.Lock:
    if agent_key not in self._agent_locks:
        self._agent_locks[agent_key] = asyncio.Lock()
    return self._agent_locks[agent_key]
```

Flow:
1. Acquire per-agent lock
2. Check if container already running + healthy → return immediately
3. If not: create container with worker entrypoint + all mounts
4. Wait for health check
5. Return worker URL + container ID
6. Release lock (via `async with`)

On failure (container create or health timeout):
- Attempt to capture `docker logs` for diagnostics
- Clean up partial state (remove tracking, release port, remove config file)
- Re-raise with context

---

## Task 7: Backward Compatibility — Host Mode

**File:** `backend/app/sandbox/manager.py`

The existing `get_or_create_container()` method must still work for host-mode execution:

- If called directly (not through `ensure_running()`), behavior is unchanged: `sleep infinity` entrypoint, `docker exec` for code execution
- `execute()` method is unchanged — still does `docker exec` for host-mode containers
- `ensure_running()` is the new path for containerized worker agents

**Decision logic** (in gateway, not in SandboxManager itself):
```python
if agent.get("sandbox_image"):
    result = await sandbox_manager.ensure_running(agent)
    worker_url = result["worker_url"]
    # Route turn to worker_url
else:
    # Host mode: run agent loop in-process
```

SandboxManager doesn't decide the mode — it provides both APIs. The caller decides.

---

## Task 8: Container Failure & Restart

**File:** `backend/app/sandbox/manager.py`

In `ensure_running()`, handle the case where a tracked container has died:

```python
if key in self._containers:
    cid = self._containers[key]["container_id"]
    if await self._is_running(cid):
        # Verify health too — container running doesn't mean worker is healthy
        try:
            await self._wait_for_health(worker_url, agent_id, cid, timeout=5.0)
            return {"worker_url": worker_url, "container_id": cid}
        except RuntimeError:
            logger.warning("Worker unhealthy in running container %s, destroying", cid)
            await self.destroy_agent_container(agent_id)
    else:
        logger.warning("Container %s for agent %s died, recreating", cid, agent_id)
        await self.destroy_agent_container(agent_id)
    # Fall through to create new container
```

- Don't just check if container is running — verify the worker is healthy
- If worker is unhealthy in a running container: destroy and recreate (don't try to repair)
- Log the event clearly for debugging

---

## Task 9: Cleanup Lifecycle

**File:** `backend/app/sandbox/manager.py`

Update `destroy_agent_container()`:
- Release allocated port via `_release_port()`
- Delete config file at `{project_root}/data/agent-configs/{agent_id}.json`
- Remove from `_agent_locks` dict
- Docker volume **persists** (by design — agent data survives restarts)

Update `cleanup_idle()`:
- Release ports for cleaned-up containers
- Delete config files for cleaned-up containers

New method `destroy_agent_data(agent_id: str)`:
- Called when an agent is **deleted** (not just stopped)
- Removes the Docker volume: `docker volume rm bond-agent-{agent_id}`
- Removes the config file
- This is the nuclear option — data is gone

---

## Task 10: Observability

**File:** `backend/app/sandbox/manager.py`

Structured logging for all lifecycle events:

```
INFO  Created worker container %s for agent %s (port=%d, image=%s)
INFO  Worker healthy for agent %s in %.1fs (container=%s)
WARN  Worker unhealthy in container %s for agent %s, destroying
WARN  Container %s for agent %s died, recreating
INFO  Destroyed container for agent %s (port %d released)
INFO  Cleaned up %d idle containers (ports released: %s)
ERROR Failed to create container for agent %s: %s
ERROR Health check timeout for agent %s after %.1fs — container logs:\n%s
```

Include port numbers in all log messages (critical for debugging port conflicts).

---

## Task 11: Tests

**File:** `backend/tests/test_sandbox_manager.py` (update existing + add new)

All tests mock Docker commands (`asyncio.create_subprocess_exec`). No real Docker needed.

**Port allocation:**
- test_port_allocation_returns_port_in_range
- test_port_allocation_sequential_no_duplicates
- test_port_release_on_destroy
- test_port_reuse_after_release
- test_port_exhaustion_raises_error
- test_port_conflict_detection_skips_busy_port

**Container creation (worker mode):**
- test_create_worker_container_command (verify entrypoint is `python -m backend.app.worker`, not `sleep infinity`)
- test_create_worker_container_mounts (verify all 6 mount types present)
- test_create_worker_container_sets_pythonpath
- test_create_worker_container_exposes_port

**Config generation:**
- test_config_file_created_with_correct_content
- test_config_file_permissions_0600
- test_config_file_cleaned_up_on_destroy
- test_config_file_cleaned_up_on_create_failure

**Health wait:**
- test_health_wait_success_fast
- test_health_wait_retries_connection_refused
- test_health_wait_timeout_includes_docker_logs
- test_health_wait_validates_agent_id

**ensure_running:**
- test_ensure_running_creates_and_returns_url
- test_ensure_running_reuses_healthy_container
- test_ensure_running_recreates_dead_container
- test_ensure_running_recreates_unhealthy_container
- test_ensure_running_concurrent_calls_serialized (verify lock prevents race)
- test_ensure_running_cleans_up_on_failure

**Backward compatibility:**
- test_get_or_create_still_uses_sleep_infinity
- test_execute_still_works (docker exec path unchanged)

**Cleanup:**
- test_destroy_releases_port_and_config
- test_destroy_agent_data_removes_volume
- test_cleanup_idle_releases_ports

---

## Definition of Done

- [ ] All 11 tasks implemented
- [ ] All existing tests still pass
- [ ] All new tests pass (target: 25+ new tests)
- [ ] Container starts with worker process, not `sleep infinity`
- [ ] Health check confirms worker is responding with correct agent_id before returning
- [ ] Port allocation is clean — no leaks, detects conflicts
- [ ] Config files have restricted permissions (0600)
- [ ] Config files cleaned up on all exit paths (success, failure, destroy)
- [ ] Per-agent locking prevents race conditions
- [ ] Dead/unhealthy containers are detected and recreated
- [ ] Host mode is completely unaffected
- [ ] All lifecycle events logged with port numbers and timing
- [ ] Committed: `feat: C4 — SandboxManager runs worker in container`
