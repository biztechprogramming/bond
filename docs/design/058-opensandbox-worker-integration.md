# 058 — OpenSandbox Worker Integration

**Status:** Draft  
**Created:** 2026-03-22  
**Author:** Bond Agent  

## Problem

The OpenSandbox adapter (`opensandbox_adapter.py`) was built for generic sandbox code execution (run code snippets, execute shell commands via the execd API). However, bond's agent architecture requires **long-lived worker containers** that expose an HTTP server on port 18791. The backend proxies SSE through this worker for conversation turns (`/turn`), interrupts (`/interrupt`), health checks (`/health`), and coding agent events.

When the sandbox backend was switched to `opensandbox` (commit `361267e`, 2026-03-11), the conversation and agent API paths were never migrated — they continued importing `get_sandbox_manager()` directly, bypassing the `get_executor()` routing entirely.

Now that the API layer has been updated to call `get_executor()` (2026-03-22), the opensandbox adapter fails because it was never designed for the worker contract:

1. **Wrong entrypoint** — Hardcoded `["/bin/bash"]` instead of using the image's `ENTRYPOINT + CMD`. The opensandbox bootstrap wraps this, so `/bin/bash` exits immediately with code 0. *(Fixed 2026-03-22.)*
2. **Wrong network** — Containers are created on the `bridge` network. They cannot reach `spacetimedb`, `host.docker.internal`, or other services on `bond-network`.
3. **Worker port not exposed** — OpenSandbox only maps its own ports (execd on 44772, HTTP on 8080). The agent worker listens on 18791 internally, which is never mapped to a host port.
4. **Wrong worker_url** — `ensure_running()` returns the execd URL as `worker_url`. The backend calls `worker_url/turn`, `worker_url/health`, etc. — these are agent worker endpoints, not execd endpoints.
5. **Missing environment variables** — The `SandboxManager` injects ~15 environment variables (API keys, PYTHONPATH, BOND_API_URL, SpacetimeDB URL, agent identity, broker token, Langfuse keys). The opensandbox adapter passes none of these.
6. **Missing infrastructure mounts** — Agent config (`/config/agent.json`), vault data (`/bond-home/data`), agent data dir (`/data`), shared data (`/data/shared`), skills DB (`/data/skills.db`), and credential mounts (SSH keys, Claude config) are not mounted.
7. **No workspace cloning** — The `SandboxManager` clones workspaces for concurrency (Design Doc 057). The opensandbox adapter mounts host paths directly, which causes file contention when multiple agents share a workspace.
8. **No health check after creation** — The `SandboxManager` polls `/health` on the worker until it responds with the correct `agent_id`. The opensandbox adapter only waits for the container state to become "Running" (which just means Docker started it, not that the worker is ready).
9. **Volume name sanitization** — OpenSandbox requires volume names matching `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`. Mount names from the database (empty strings, `.ssh`, `.claude`) fail this validation. *(Fixed 2026-03-22.)*
10. **Container naming** — The `SandboxManager` names containers `bond-{agent_name}-{agent_id}`. OpenSandbox names them `sandbox-{uuid}`. Other parts of the system (recovery, destroy, logs) expect the bond naming convention.

## Design

### Approach: Extend the OpenSandbox adapter

Rather than maintaining two parallel container lifecycle managers, extend the opensandbox adapter to support bond's worker contract. The opensandbox server manages container lifecycle (creation, pause, resume, expiration, cleanup, execd injection) — bond should leverage this rather than reimplementing it in `SandboxManager`.

The opensandbox server's `extensions` field (opaque `dict[str, str]` on `CreateSandboxRequest`) is the integration seam. Extensions allow bond-specific parameters to be passed through without modifying the opensandbox API schema.

### §1 — Network: Join `bond-network`

**Server-side change required.** The opensandbox server config (`docker-compose.spacetimedb.yml`) currently sets `network_mode = "bridge"`. This needs to change to use `bond-network`.

**Option A — Config-level:** Change the opensandbox config TOML:
```toml
[docker]
network_mode = "bond-network"
```
*Risk:* The opensandbox server may not support named networks in `network_mode` — it only validates `host` or `bridge`. This would require a server-side patch.

**Option B — Extensions:** Pass `{"docker.network": "bond-network"}` in the `extensions` field and patch the opensandbox server's `_base_host_config_kwargs` to read it.

**Option C — Post-creation connect:** After the sandbox container is created and started, use `docker network connect bond-network <container>` from the adapter. This requires no opensandbox server changes.

**Recommendation:** Option C for immediate fix (no server changes). File an upstream issue for Option A support.

Additionally, the container needs `--add-host host.docker.internal:host-gateway` for `BOND_API_URL`. This can be passed via extensions or post-creation `docker exec`.

### §2 — Port Mapping: Expose Worker Port 18791

The opensandbox server allocates random host ports for its known ports (44772, 8080). It does not know about 18791.

**Option A — Extensions:** Pass `{"docker.extra_ports": "18791"}` and patch the server to add these to `port_bindings` and `exposed_ports`.

**Option B — Adapter-side port allocation:** The adapter allocates a host port itself (reusing `SandboxManager._allocate_port` logic), then passes it via extensions: `{"docker.port_bindings": "18791:allocated_port"}`.

**Option C — Post-creation setup:** Not viable — port bindings cannot be added after container creation.

**Recommendation:** The adapter should allocate the host port and pass the binding via extensions. The opensandbox server needs a small patch to honor `extensions["docker.port_bindings"]` in `_provision_sandbox`.

**Interim workaround:** If the opensandbox server cannot be patched immediately, the adapter can use the opensandbox API to create the container but then call `docker` CLI directly to recreate it with correct ports. This is ugly but functional.

**Preferred interim approach:** Port-forward via `socat` inside the container or use the execd proxy (`/proxy/{port}`) to reach port 18791 through the already-mapped execd port (44772). The execd proxy is designed for exactly this — proxying arbitrary ports through the execd sidecar.

### §3 — Worker URL Resolution

Currently `ensure_running()` returns `execd_url` as `worker_url`. This must change:

```python
# After sandbox is running and port is mapped:
worker_url = f"http://localhost:{allocated_host_port}"
# OR if using execd proxy:
worker_url = f"http://localhost:{execd_host_port}/proxy/18791"
```

The return dict should include both:
```python
return {
    "worker_url": worker_url,       # For conversation turns
    "sandbox_id": sandbox_id,       # For opensandbox lifecycle ops
    "execd_url": execd_url,         # For code execution tools
    "container_id": container_id,   # For backward compat
}
```

### §4 — Environment Variables

The adapter's `_create_sandbox` must inject the same env vars as `SandboxManager._create_worker_container`:

| Variable | Source | Purpose |
|---|---|---|
| `PYTHONPATH` | `/bond` | Worker imports |
| `BOND_API_URL` | `http://host.docker.internal:18790` | Tools calling host API |
| `BOND_SPACETIMEDB_URL` | `http://spacetimedb:3000` | SpacetimeDB access (via bond-network) |
| `AGENT_NAME` | `bond-agent-{agent_id}` | Git identity |
| `AGENT_EMAIL` | `agent-{id}@bond.internal` | Git identity |
| `BOND_REPO_URL` | Configured repo URL | Git clone source |
| `{PROVIDER}_API_KEY` | `agent.api_keys` | LLM provider auth |
| `GITHUB_TOKEN` | Vault | GitHub operations |
| `BOND_AGENT_TOKEN` | Broker API | MCP proxy access |
| `LANGFUSE_*` | Settings | Observability |

Implementation: Build the env dict in the adapter, pass via `CreateSandboxRequest.env`.

### §5 — Infrastructure Volume Mounts

These must be added to the `volumes` list in addition to workspace mounts:

| Mount | Host Path | Container Path | Mode |
|---|---|---|---|
| Agent config | `data/agent-configs/{id}.json` | `/config/agent.json` | ro |
| Agent data | `data/agents/{id}/` | `/data` | rw |
| Shared data | `data/shared/` | `/data/shared` | ro |
| Skills DB | `data/skills.db` | `/data/skills.db` | rw |
| Vault | `~/.bond/data/` | `/bond-home/data` | rw |
| Claude config | `~/.claude.json` | `/home/bond-agent/.claude.json` | ro |
| Claude creds | `~/.claude/.credentials.json` | `/home/bond-agent/.claude/.credentials.json` | rw |
| Claude settings | `~/.claude/settings.json` | `/home/bond-agent/.claude/settings.json` | ro |
| SSH keys | `~/.ssh` | `/tmp/.ssh` | ro |

The adapter must call `_write_agent_config()` (extracted from or shared with `SandboxManager`) before creating the sandbox.

Volume names must be sanitized per §9.

### §6 — Workspace Cloning

Reuse the workspace cloning pipeline from `SandboxManager` (Design Doc 057):

1. Detect workspace types (`detect_workspace_type`)
2. Generate clone plans (`generate_clone_plan`)
3. Execute clones in parallel (`execute_clone_plan`)
4. Mount cloned paths instead of originals

The `workspace_cloner.py` module is already decoupled from `SandboxManager` — the adapter can call it directly.

### §7 — Health Check After Creation

After the opensandbox reports state "Running", the adapter must poll the agent worker:

```python
async def _wait_for_worker_health(self, worker_url, agent_id, timeout=30.0):
    """Poll worker /health until it responds with correct agent_id."""
    # Same logic as SandboxManager._wait_for_health
```

This is necessary because "Running" in opensandbox means Docker started the container, not that the worker process inside has finished initializing (cloning repos, loading models, etc.).

### §8 — Container Naming

Pass `{"docker.container_name": f"bond-{agent_name}-{agent_id}"}` via extensions, or via metadata. The opensandbox server currently names containers `sandbox-{uuid}` — this should be configurable.

Alternatively, store the sandbox_id → bond key mapping in the adapter's tracking dict and use it for recovery/destroy operations.

### §9 — Volume Name Sanitization

*(Already implemented 2026-03-22.)*

Strip leading dots/dashes, replace invalid characters with dashes, fall back to host path basename or index-based name.

### §10 — Config Fingerprinting and Container Recreation

Port the config fingerprint logic from `SandboxManager.ensure_running`:
- Hash API keys, model, utility_model
- Compare on subsequent calls
- Destroy and recreate if changed (preserving workspace clones)

### §11 — Broker Token Injection

Issue a broker token via `POST /api/v1/broker/token/issue` and inject as `BOND_AGENT_TOKEN` env var, same as `SandboxManager`.

### §12 — Lazy Dependency Installation

Port `ensure_deps_installed` from `SandboxManager` — after the first code execution tool call, run the generated dependency install script inside the container.

## Implementation Plan

### Phase 1 — Critical path (get conversations working)

1. **Environment variables** (§4) — Build complete env dict in `_create_sandbox`
2. **Infrastructure mounts** (§5) — Add agent config, vault, data dir mounts
3. **Entrypoint** (done) — Use image's actual ENTRYPOINT + CMD
4. **Volume name sanitization** (§9, done) — Sanitize mount names
5. **Network connectivity** (§1) — Post-creation `docker network connect`
6. **Port exposure** (§2) — Use execd proxy as interim, or server-side patch
7. **Worker URL** (§3) — Return correct URL for worker port
8. **Worker health check** (§7) — Poll /health after container starts

### Phase 2 — Feature parity

9. **Workspace cloning** (§6) — Integrate workspace_cloner
10. **Config fingerprinting** (§10) — Detect model/key changes
11. **Broker token** (§11) — Issue and inject
12. **Container naming** (§8) — Convention alignment
13. **Lazy deps** (§12) — Port ensure_deps_installed

### Phase 3 — Server-side improvements

14. **OpenSandbox extensions** — Patch server to support `docker.network`, `docker.port_bindings`, `docker.container_name` in extensions field
15. **OpenSandbox healthcheck fix** — Replace `curl` with `wget` or a built-in check in the compose healthcheck
16. **Deprecate SandboxManager** — Once opensandbox adapter has full parity, remove the legacy code path

## Open Questions

1. **Should the opensandbox server be forked or patched upstream?** The `extensions` approach keeps the API clean, but requires server-side changes for networking and port bindings. An alternative is to contribute these as first-class features upstream.

2. **execd proxy viability for worker traffic:** The execd proxy (`/proxy/{port}`) can forward HTTP to any port inside the container. However, SSE streaming through this proxy needs testing — conversation turns are long-lived SSE connections. If the proxy doesn't support streaming, direct port mapping is the only option.

3. **Recovery after backend restart:** `SandboxManager` recovers running containers by inspecting Docker directly. The opensandbox adapter should use the opensandbox `GET /v1/sandboxes` list API filtered by bond metadata to recover state.

## Files Changed

| File | Change |
|---|---|
| `backend/app/sandbox/opensandbox_adapter.py` | Extend `_create_sandbox` with env, mounts, networking, health check |
| `backend/app/sandbox/opensandbox_adapter.py` | Add `_wait_for_worker_health`, `_build_worker_env`, `_build_infrastructure_volumes` |
| `backend/app/sandbox/opensandbox_adapter.py` | Update `ensure_running` return value to include `worker_url` pointing to worker port |
| `backend/app/sandbox/__init__.py` | No changes needed (routing already works) |
| `backend/app/api/v1/conversations.py` | Already migrated to `get_executor()` |
| `backend/app/api/v1/agent.py` | Already migrated to `get_executor()` |
| `backend/app/agent/tools/code.py` | Already migrated to `get_executor()` |
| `backend/app/agent/tools/files.py` | Already migrated to `get_executor()` |
| `docker-compose.spacetimedb.yml` | Fix opensandbox healthcheck (`curl` → `wget` or remove) |
