# Design Doc 089: Remote Container Hosts

**Status:** Draft — revised after review
**Depends on:** 008 (Containerized Agent Runtime), 037 (Coding Agent Skill), 035 (Secure Agent Execution Architecture), 044 (Remote Discovery and Deployment Monitoring)
**Architecture refs:** [03 — Agent Runtime](../architecture/03-agent-runtime.html), [08 — Sandbox System](../architecture/08-sandbox.html)

---

## 1. The Problem

Today, Bond creates agent containers on the **local Docker daemon** — the same machine running the gateway and backend. This is fine for a single developer, but it creates hard limits:

- **Resource ceiling** — one machine can only run so many concurrent agents before CPU/RAM is exhausted. Heavy coding agents (Claude Code, Codex) can easily consume 2+ GB RAM each.
- **No horizontal scaling** — if you want 10 agents working in parallel on different issues, you need 10× the resources on one box.
- **Machine availability** — your laptop goes to sleep, your agents die. A beefy server sitting idle in the closet can't help.
- **Heterogeneous workloads** — some agents need GPUs (browser agent with Playwright), some need lots of RAM (large repo indexing), some are lightweight. One-size-fits-all doesn't work.
- **Team usage** — multiple team members can't share a pool of worker machines through a single Bond instance.

### What We Want

The ability to say: *"Run this agent's container on Machine B instead of locally"* — while the Bond UI, gateway, and backend stay on Machine A. The user experience should be identical regardless of where the container physically runs.

> **Important UX caveat:** The experience is *functionally* identical, but there is a fundamental difference the user must understand: locally, `/workspace` is a bind mount with live host filesystem sync; remotely, `/workspace` is a git clone. Changes made by a remote agent are **not visible on the user's machine until pushed to a branch**. The UI should clearly indicate when an agent is running remotely, e.g.: *"Your agent is working on a remote copy. Results will be pushed to branch `agent/issue-42` when done."* <!-- P2: UX difference documentation (IMPROVEMENTS §8.1) -->

---

## 2. Current Architecture (Local Only)

```
┌─────────────────────────────────────────────────────────────┐
│  MACHINE A (Bond Host)                                      │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐      │
│  │ Frontend │◄──►│ Gateway  │◄──►│ Backend          │      │
│  │ :18788   │    │ :18789   │    │ :18790           │      │
│  └──────────┘    └──────────┘    │                  │      │
│                                   │  SandboxManager  │      │
│                                   │  ┌────────────┐  │      │
│                                   │  │docker run  │  │      │
│                                   │  │(local CLI) │  │      │
│                                   │  └─────┬──────┘  │      │
│                                   └────────┼─────────┘      │
│                                            │                │
│  ┌─────────────────────────────────────────┼───────────┐    │
│  │  Local Docker Daemon                    ▼           │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  bond-agent-XXXX (container)                 │   │    │
│  │  │  Worker :18791 ──SSE──► Gateway :18789       │   │    │
│  │  │  /workspace ←bind── /host/path/to/project    │   │    │
│  │  │  /bond ←bind── /host/path/to/bond (ro)       │   │    │
│  │  │  /data ←bind── /host/data/agents/XXXX        │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**Key coupling points to the local machine:**

1. `SandboxManager._create_worker_container()` shells out to `docker run` via `asyncio.create_subprocess_exec("docker", ...)`.
2. Bind mounts assume shared filesystem: `/bond`, `/workspace`, `/data`, `~/.ssh`, `~/.claude`, vault data — all reference host paths.
3. Port allocation (`_allocate_port`) binds to `localhost` ports.
4. `host.docker.internal:host-gateway` is used for the container to reach the Bond API.
5. `--env-file .env` passes the host's `.env` file directly.
6. Container health checks hit `localhost:{port}`.

---

## 3. Proposed Architecture

### 3.1 Overview

Introduce a **Remote Host Registry** and a **Container Host Adapter** abstraction. The `SandboxManager` delegates container lifecycle operations to an adapter, which can target either the local Docker daemon or a remote machine.

```
┌─────────────────────────────────────────────────────────────┐
│  MACHINE A (Bond Host)                                      │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────┐      │
│  │ Frontend │◄──►│ Gateway  │◄──►│ Backend          │      │
│  │ :18788   │    │ :18789   │    │ :18790           │      │
│  └──────────┘    └──────────┘    │                  │      │
│                                   │  SandboxManager  │      │
│                                   │  ┌────────────┐  │      │
│                                   │  │HostRouter  │  │      │
│                                   │  └──┬─────┬───┘  │      │
│                                   └─────┼─────┼──────┘      │
│                                         │     │             │
│                          ┌──────────────┘     └──────────┐  │
│                          ▼                               ▼  │
│                   ┌─────────────┐               ┌───────────┤
│                   │LocalAdapter │               │RemoteAdapt│
│                   │(docker CLI) │               │er (SSH/API)│
│                   └──────┬──────┘               └─────┬─────┤
│                          │                            │     │
│                          ▼                            │     │
│                   Local Docker                        │     │
│                   Daemon                              │     │
└───────────────────────────────────────────────────────┼─────┘
                                                        │
                              SSH tunnel / WireGuard     │
                                                        │
┌───────────────────────────────────────────────────────┼─────┐
│  MACHINE B (Remote Worker Host)                       ▼     │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  bond-host-daemon (lightweight agent)               │    │
│  │  :18795                                             │    │
│  │  • Receives container lifecycle commands             │    │
│  │  • Manages local Docker daemon                      │    │
│  │  • Syncs workspace via git clone (not bind mounts)  │    │
│  │  • Forwards worker SSE back to Bond host            │    │
│  └──────────┬──────────────────────────────────────────┘    │
│             │                                               │
│  ┌──────────▼──────────────────────────────────────────┐    │
│  │  Docker Daemon                                      │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  bond-agent-XXXX (container)                 │   │    │
│  │  │  Worker :18791                               │   │    │
│  │  │  /workspace ← git clone (not bind mount)     │   │    │
│  │  │  /bond ← git clone or image-baked            │   │    │
│  │  │  /data ← Docker volume (synced back)         │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Key Design Decisions

#### Decision 1: Git Clone Instead of Bind Mounts

The biggest challenge with remote containers is that **bind mounts don't work across machines**. The workspace at `/mnt/c/dev/myproject` on Machine A doesn't exist on Machine B.

**Solution:** Remote containers get their workspace via `git clone` instead of bind mounts.

- The agent already knows the repo URL and branch (from the agent config / issue context).
- The `workspace_cloner.py` module already handles git cloning for containers — we extend this pattern.
- After the agent finishes, results are `git push`ed to a branch — this already happens today.
- For repos without a remote (local-only), remote execution is not supported (clear error message).

#### Decision 2: Bond Host Daemon (Lightweight Agent on Remote Machine)

Rather than exposing the Docker API over the network (massive security risk), we run a small daemon on each remote machine:

- **`bond-host-daemon`** — a lightweight FastAPI service (~200 LOC) that:
  - Accepts authenticated container lifecycle commands (create, destroy, health, logs)
  - Manages the local Docker daemon on behalf of Bond
  - Caches the `bond-agent-worker` image (pulled from registry or built locally)
  - Provides a reverse proxy for worker SSE streams back to the Bond gateway
  - Reports machine resource usage (CPU, RAM, disk) for scheduling decisions
  - **Enforces `max_agents` locally** — rejects `create_container` when at capacity, regardless of what the gateway believes <!-- P0: Daemon-side max_agents enforcement (REVIEW §2.3) -->

This is similar to how Kubernetes has a kubelet on each node, but much simpler.

**Daemon as Stateless Process:** The daemon should be treated as stateless — it queries Docker for ground truth on every request. The Bond backend is the sole source of *intent*; Docker on the remote host is the sole source of *actual state*. This avoids state reconciliation problems between the daemon and the Bond backend after network partitions. <!-- P1: Daemon statelessness (IMPROVEMENTS §1.4) -->

**Idempotent Container Creation:** `create_container` must be idempotent. If the Bond backend retries a request after a timeout, and a container with the given key already exists and is running, the daemon returns its existing info rather than failing or creating a duplicate. <!-- P1: Idempotent create_container (IMPROVEMENTS §1.4) -->

```python
@app.post("/containers")
async def create_container(spec: ContainerSpec):
    # Idempotency: check if container already exists
    existing = await find_container(spec.key)
    if existing and existing.status == "running":
        return {"container_id": existing.id, "worker_url": f"http://localhost:{existing.port}"}

    # Enforce local capacity limit
    running = len(await list_bond_containers())
    if running >= MAX_AGENTS:
        raise HTTPException(429, f"Host at capacity ({running}/{MAX_AGENTS})")

    # ... proceed with creation
```

#### Decision 3: SSH as the Default Transport

For the initial implementation, communication between Bond host and remote machines uses **SSH tunnels**:

- No firewall changes needed — SSH is almost always open
- Authentication uses existing SSH keys (already managed in Bond for git)
- Port forwarding gives us secure access to the worker's SSE port
- The `bond-host-daemon` listens only on `localhost` — SSH tunnel provides access

**SSH Multiplexing:** Rather than establishing a separate SSH connection for each worker port forward, use `ControlMaster` to multiplex all tunnels over a single SSH connection per remote host. With 8 agents on one host, this avoids 9 separate TCP connections: <!-- P1: SSH multiplexing (IMPROVEMENTS §4.1) -->

```
ssh -o ControlMaster=auto \
    -o ControlPath=/tmp/bond-ssh-%h \
    -o ControlPersist=600 \
    -L local_port:localhost:remote_port ...
```

All port forwards share one TCP connection, reducing connection overhead and simplifying tunnel management. The `TunnelManager` (see §4.7) uses this automatically.

Future iterations can add WireGuard mesh networking or Tailscale for lower latency.

#### Decision 4: Placement Strategy

A simple placement strategy decides where to run each container:

```
1. If agent config specifies a host → use that host
2. If all hosts are at capacity → queue the request (see §4.8)
3. Otherwise → pick the host with the most available resources
```

No complex scheduling — this isn't Kubernetes. The user can also manually assign agents to hosts via the UI.

**Full Placement Algorithm:** <!-- P1: Full placement algorithm spec (REVIEW §2.3) -->

```python
async def get_placement(self, agent: dict) -> Host:
    # 1. Explicit host assignment
    if preferred := agent.get("preferred_host"):
        host = self._hosts.get(preferred)
        if host and host.enabled and host.status == "active":
            return host
        # Preferred host unavailable — fall through to auto-placement

    # 2. Build candidate list (exclude draining/offline hosts)
    candidates = [
        h for h in self._all_hosts()
        if h.enabled and h.status == "active" and h.running_count < h.max_agents
    ]

    # 3. Label filtering
    required_labels = agent.get("host_labels", [])
    if required_labels:
        candidates = [
            h for h in candidates
            if all(label in h.labels for label in required_labels)
        ]

    # 4. Host affinity — prefer the host where this agent last ran
    #    (preserves cached git repos and Docker volumes)
    last_host_id = agent.get("last_host_id")
    if last_host_id:
        affinity_match = [h for h in candidates if h.id == last_host_id]
        if affinity_match:
            return affinity_match[0]

    # 5. Apply strategy
    if not candidates:
        return await self._enqueue(agent)  # Queue when no capacity

    if self._prefer_local and self._local in candidates:
        return self._local

    if self._strategy == "least-loaded":
        return min(candidates, key=lambda h: h.running_count / h.max_agents)
    elif self._strategy == "round-robin":
        return self._next_round_robin(candidates)
```

When all hosts are at capacity, the request is placed in a queue (see §4.8 Placement Queue). The user sees a "queued" status in the UI. A background task polls every 5 seconds and places queued agents when capacity frees up. Requests time out after a configurable period (default 5 minutes), at which point the user is notified.

#### Decision 5: Worker-to-Gateway Communication Model

<!-- P0: Resolve bond_api_url contradiction with Doc 008 (IMPROVEMENTS §1.1) -->

Doc 008 established that container workers make **zero callbacks** to the Bond host — all communication flows via the SSE stream initiated *by the gateway toward the worker*. However, an earlier draft of this doc passed `bond_api_url` to remote containers, implying the worker calls back to the Bond API. **This contradicts Doc 008's core design.**

**Resolution:** Remote workers do **not** call back to the Bond host. The `bond_api_url` parameter is removed from the daemon's `docker_run` invocation. All data flows follow the same pattern as local containers:

- **Gateway → Worker:** HTTP requests to the worker's API (via SSH tunnel)
- **Worker → Gateway:** SSE event stream (initiated by the gateway connecting to the worker's SSE endpoint)
- **Shared memory:** Delivered at container creation time, not fetched by the worker (see §4.9)
- **Credential refresh:** Not supported mid-task; credentials are injected at container start

If a future requirement arises for worker→Bond communication, this will be implemented as a **reverse SSH tunnel** (remote→Bond) or a **daemon relay proxy**, with a corresponding update to the security model. This is explicitly out of scope for v1.

---

## 4. Component Design

### 4.1 Remote Host Registry

New configuration in `bond.json`:

```json
{
  "remote_hosts": [
    {
      "id": "server-closet",
      "name": "Home Server",
      "host": "192.168.1.100",
      "port": 22,
      "user": "bond",
      "ssh_key": "~/.ssh/bond_server_closet_ed25519",
      "daemon_port": 18795,
      "max_agents": 8,
      "labels": ["high-memory", "gpu"],
      "enabled": true
    },
    {
      "id": "cloud-worker-1",
      "name": "Cloud VM",
      "host": "worker1.example.com",
      "port": 22,
      "user": "bond",
      "ssh_key": "~/.ssh/bond_cloud_worker1_ed25519",
      "daemon_port": 18795,
      "max_agents": 4,
      "labels": ["cloud"],
      "enabled": true
    }
  ],
  "placement": {
    "strategy": "least-loaded",
    "prefer_local": true,
    "require_label": null
  }
}
```

> **Note:** Each host should use a **dedicated SSH key** (`ssh_key` per host entry). If one remote host is compromised, the attacker only has the key for that host, not all hosts. The setup CLI (§12) generates per-host keys automatically. <!-- P1: Per-host SSH keys (REVIEW §2.2) -->

**Python model:**

```python
@dataclass
class RemoteHost:
    id: str
    name: str
    host: str
    port: int = 22
    user: str = "bond"
    ssh_key: str = "~/.ssh/id_ed25519"  # Per-host key recommended
    daemon_port: int = 18795
    max_agents: int = 4
    labels: list[str] = field(default_factory=list)
    enabled: bool = True
    status: Literal["active", "draining", "offline"] = "active"  # P2: Host draining

class HostRegistry:
    """Manages the set of available container hosts."""

    def __init__(self, config: dict):
        self._hosts: dict[str, RemoteHost] = {}
        self._local = LocalHost()  # Always available
        self._load_from_config(config)

    async def get_placement(self, agent: dict) -> RemoteHost | LocalHost:
        """Decide where to place an agent container."""
        ...

    async def health_check_all(self) -> dict[str, HostStatus]:
        """Check connectivity and resource availability of all hosts."""
        ...
```

**Host Draining:** When a host's status is set to `"draining"`, no new agents are placed on it, but existing containers run to completion. This enables graceful maintenance windows. The draining state is set via `PATCH /api/hosts/{id}` with `{"status": "draining"}`. <!-- P2: Host draining (REVIEW §4.3) -->

**Database-Backed Registry:** For production deployments with dynamic host management, the registry should be backed by a database table rather than solely by the config file. The config file seeds the table on first run; the REST API (`POST /api/hosts`, etc.) mutates the database; the gateway reads from the database on startup. This allows adding/removing hosts without gateway restarts and persists runtime state (running container counts, last health check) across restarts. <!-- P2: Database-backed registry (IMPROVEMENTS §5.1) -->

```sql
CREATE TABLE remote_hosts (
    id TEXT PRIMARY KEY,
    name TEXT,
    host TEXT NOT NULL,
    port INTEGER DEFAULT 22,
    user TEXT DEFAULT 'bond',
    ssh_key TEXT,
    daemon_port INTEGER DEFAULT 18795,
    max_agents INTEGER DEFAULT 4,
    labels TEXT,  -- JSON array
    enabled BOOLEAN DEFAULT TRUE,
    status TEXT DEFAULT 'active',
    last_health_check TEXT,
    running_agents INTEGER DEFAULT 0
);
```

### 4.2 Container Host Adapter (Abstract Interface)

```python
class ContainerHostAdapter(Protocol):
    """Interface for creating/managing containers on any host."""

    async def create_container(
        self,
        agent: dict,
        key: str,
        config: AgentContainerConfig,
    ) -> ContainerInfo:
        """Create and start an agent worker container."""
        ...

    async def destroy_container(self, key: str) -> bool:
        """Stop and remove a container."""
        ...

    async def is_running(self, key: str) -> bool:
        """Check if a container is running."""
        ...

    async def get_logs(self, key: str, tail: int = 50) -> str:
        """Retrieve container logs."""
        ...

    async def get_worker_url(self, key: str) -> str:
        """Get the URL to reach the worker's HTTP/SSE endpoint."""
        ...

    async def health(self) -> HostStatus:
        """Report host resource usage and connectivity."""
        ...

@dataclass
class AgentContainerConfig:
    """Everything needed to create a container, decoupled from host paths."""
    agent_id: str
    sandbox_image: str
    repo_url: str | None
    repo_branch: str
    env_vars: dict[str, str]
    ssh_private_key: str  # Content, not path
    agent_config_json: str  # Serialized config content
    vault_data: bytes | None  # Encrypted credentials blob
    shared_memory_snapshot: bytes | None  # Shared memory DB content (see §4.9)
    resource_limits: ResourceLimits

@dataclass
class ResourceLimits:
    memory_mb: int = 2048
    cpus: float = 2.0

@dataclass
class ContainerInfo:
    container_id: str
    host_id: str  # "local" or remote host ID
    worker_url: str  # How the gateway can reach the worker
    created_at: datetime
```

### 4.3 LocalContainerAdapter

Wraps the existing `SandboxManager._create_worker_container()` logic — essentially a refactor of the current code behind the new interface:

```python
class LocalContainerAdapter(ContainerHostAdapter):
    """Creates containers on the local Docker daemon (current behavior)."""

    async def create_container(self, agent, key, config):
        # Existing docker run logic, but using config object
        # instead of reading host paths directly.
        # Bind mounts still work for local.
        ...

    async def get_worker_url(self, key):
        port = self._port_map[key]
        return f"http://localhost:{port}"
```

### 4.4 RemoteContainerAdapter

Communicates with the `bond-host-daemon` on a remote machine:

```python
class RemoteContainerAdapter(ContainerHostAdapter):
    """Creates containers on a remote machine via bond-host-daemon."""

    def __init__(self, host: RemoteHost, tunnel_manager: TunnelManager):
        self._host = host
        self._tunnel_manager = tunnel_manager
        self._client = httpx.AsyncClient()  # Reuse for connection pooling

    async def create_container(self, agent, key, config):
        tunnel = await self._tunnel_manager.ensure_tunnel(self._host)

        # Send container spec to remote daemon
        resp = await self._client.post(
            f"{tunnel.local_url}/containers",
            json={
                "key": key,
                "image": config.sandbox_image,
                "repo_url": config.repo_url,
                "repo_branch": config.repo_branch,
                "env_vars": config.env_vars,
                "agent_config": config.agent_config_json,
                "resource_limits": asdict(config.resource_limits),
            },
            # SSH key and vault data sent as separate encrypted payload
            # Shared memory snapshot included in payload (see §4.9)
        )
        result = resp.json()

        return ContainerInfo(
            container_id=result["container_id"],
            host_id=self._host.id,
            worker_url=result["worker_url"],  # Tunneled URL
        )

    async def get_worker_url(self, key):
        # The daemon sets up a port forward for the worker's SSE port
        # and returns a tunneled URL accessible from the Bond host
        tunnel = await self._tunnel_manager.ensure_tunnel(self._host)
        resp = await self._client.get(
            f"{tunnel.local_url}/containers/{key}/url"
        )
        return resp.json()["url"]
```

### 4.5 Bond Host Daemon

A lightweight service that runs on each remote machine:

```python
# bond-host-daemon — runs on remote worker machines
# Install: pip install bond-host-daemon (or just copy the script)

app = FastAPI()

# --- Startup: recover state and clean up stale resources ---

@app.on_event("startup")
async def startup_recovery():
    """On daemon start, reconcile with Docker and clean up stale resources."""
    # Re-discover any running bond-agent-* containers
    # (handles daemon crash/restart while containers are running)
    await reconcile_running_containers()  # P1: Daemon crash recovery (IMPROVEMENTS §3.3)

    # Clean up credential dirs for containers that no longer exist
    await cleanup_stale_credentials()  # P2: Credential cleanup-on-boot (REVIEW §3.3)

async def cleanup_stale_credentials():
    """Remove credential dirs for containers that no longer exist."""
    for entry in os.listdir("/dev/shm"):
        if entry.startswith("bond-creds-"):
            key = entry.replace("bond-creds-", "")
            if not await container_exists(key):
                shutil.rmtree(f"/dev/shm/{entry}")

async def reconcile_running_containers():
    """Enumerate running bond-agent-* containers and register them internally."""
    containers = await docker_ps_filtered("bond-agent-*")
    for c in containers:
        _register_existing_container(c)

# --- Container lifecycle ---

@app.post("/containers")
async def create_container(spec: ContainerSpec):
    """Create an agent container on this machine."""

    # Idempotency: return existing container if it matches
    existing = await find_container(spec.key)
    if existing and existing.status == "running":
        return {"container_id": existing.id, "worker_url": f"http://localhost:{existing.port}"}

    # Enforce local max_agents (daemon-side enforcement)
    running = len(await list_bond_containers())
    if running >= config.max_agents:
        raise HTTPException(429, f"Host at capacity ({running}/{config.max_agents})")

    # 1. Ensure image is available
    await pull_or_build_image(spec.image)

    # 2. Prepare workspace via git clone (no bind mounts from Bond host)
    workspace_dir = f"/var/bond/workspaces/{spec.key}"
    if spec.repo_url:
        await git_clone_with_verify(spec.repo_url, spec.repo_branch, workspace_dir)

    # 3. Write agent config to local temp file
    config_path = write_agent_config(spec.key, spec.agent_config)

    # 4. Write SSH keys to local temp
    ssh_dir = setup_ssh_keys(spec.key, spec.ssh_private_key)

    # 5. Write shared memory snapshot if provided
    if spec.shared_memory_snapshot:
        shared_dir = setup_shared_memory(spec.key, spec.shared_memory_snapshot)

    # 6. docker run with local paths
    #    NOTE: No bond_api_url — workers do not call back to Bond host (see §3.2 Decision 5)
    container_id = await docker_run(
        image=spec.image,
        name=spec.key,
        workspace=workspace_dir,
        config=config_path,
        ssh=ssh_dir,
        shared=shared_dir,
        env=spec.env_vars,
        resources=spec.resource_limits,
    )

    # 7. Let Docker assign the port to avoid collisions
    port = await get_container_port(container_id, "18791/tcp")

    return {"container_id": container_id, "worker_url": f"http://localhost:{port}"}

async def git_clone_with_verify(url: str, branch: str, dest: str):
    """Clone and verify integrity. Handles credential injection securely."""
    # Use GIT_ASKPASS to inject credentials without persisting them
    env = {
        "GIT_ASKPASS": "/var/bond/bin/git-askpass-helper",
        "GIT_LFS_SKIP_SMUDGE": "1",  # Skip LFS by default; agents pull on demand
    }
    await run(["git", "clone", "--branch", branch, url, dest], env=env)

    # Verify clone integrity
    result = await run(["git", "-C", dest, "rev-parse", "HEAD"])
    if result.returncode != 0:
        shutil.rmtree(dest)
        raise CloneVerificationError(f"Clone verification failed for {url}")

    # Strip any credential info from .git/config
    await run(["git", "-C", dest, "config", "--remove-section", "credential"], check=False)

@app.delete("/containers/{key}")
async def destroy_container(key: str):
    """Stop and remove a container, cleaning up workspace and credentials."""
    await docker_stop_and_remove(key)
    # Clean up workspace
    workspace_dir = f"/var/bond/workspaces/{key}"
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    # Clean up credentials from tmpfs
    creds_dir = f"/dev/shm/bond-creds-{key}"
    if os.path.exists(creds_dir):
        shutil.rmtree(creds_dir)

@app.get("/containers/{key}/health")
async def container_health(key: str):
    """Health check a specific container."""
    ...

@app.get("/containers/{key}/logs")
async def container_logs(key: str, tail: int = 50):
    """Get container logs."""
    ...

@app.get("/containers")
async def list_containers():
    """List all bond-agent-* containers (for gateway state reconciliation)."""
    containers = await list_bond_containers()
    return {"containers": [asdict(c) for c in containers]}

@app.get("/health")
async def host_health():
    """Report this machine's resource availability."""
    return {
        "daemon_version": __version__,
        "api_version": "v1",
        "min_gateway_version": "0.90.0",
        "system_time": datetime.utcnow().isoformat(),  # P3: Clock skew detection
        "cpu_percent": psutil.cpu_percent(),
        "memory_available_mb": psutil.virtual_memory().available // 1024 // 1024,
        "disk_available_gb": psutil.disk_usage("/").free // 1024**3,
        "running_containers": len(await list_bond_containers()),
        "max_agents": config.max_agents,
    }
```

<!-- P2: Daemon versioning & compatibility (REVIEW §4.2) -->
The health endpoint includes `daemon_version`, `api_version`, and `min_gateway_version`. The gateway checks version compatibility when establishing a tunnel. If the daemon's `api_version` is incompatible, the gateway logs a warning and marks the host as requiring a daemon upgrade.

### 4.6 SSE Proxying

The critical path is getting the worker's SSE stream back to the Bond gateway. Two approaches:

**Option A: SSH Port Forward (Recommended for v1)**
```
Bond Gateway ──SSH tunnel──► Remote Machine ──localhost──► Worker :18791
```

The `RemoteContainerAdapter` establishes an SSH port forward for each worker container (multiplexed over the host's `ControlMaster` connection). The gateway connects to a local port that tunnels to the remote worker. **Zero code changes to the SSE handling.**

**Option B: Daemon-Mediated Proxy (Future)**
```
Worker :18791 ──► bond-host-daemon ──WebSocket──► Bond Gateway
```

The daemon proxies the SSE stream. More complex but allows multiplexing and better error handling.

### 4.7 Tunnel Health Monitoring & Reconnection

<!-- P0: SSH tunnel health monitoring & reconnection (REVIEW §2.1) -->

SSH tunnels can silently die mid-task — the agent container continues running on the remote host, but the gateway can no longer reach it. A `TunnelManager` handles tunnel lifecycle, health monitoring, and reconnection:

```python
class TunnelManager:
    """Manages SSH tunnels to all remote hosts with health monitoring."""

    def __init__(self, registry: HostRegistry):
        self._tunnels: dict[str, SSHTunnel] = {}  # host_id → tunnel
        self._registry = registry

    async def ensure_tunnel(self, host: RemoteHost) -> SSHTunnel:
        """Get or create a tunnel to the given host, with health check."""
        tunnel = self._tunnels.get(host.id)
        if tunnel and tunnel.is_alive:
            return tunnel

        # Create new tunnel (uses ControlMaster for multiplexing)
        tunnel = await SSHTunnel.connect(
            host=host.host,
            port=host.port,
            user=host.user,
            ssh_key=host.ssh_key,
            remote_port=host.daemon_port,
            control_path=f"/tmp/bond-ssh-{host.id}",
        )
        self._tunnels[host.id] = tunnel
        return tunnel

    async def health_check_loop(self):
        """Periodic check (every 30s) that all active tunnels are alive.
        Re-establish dead tunnels. Mark hosts as unreachable if reconnect fails."""
        while True:
            for host_id, tunnel in list(self._tunnels.items()):
                if not tunnel.is_alive:
                    host = self._registry.get_host(host_id)
                    try:
                        new_tunnel = await SSHTunnel.connect(
                            host=host.host, port=host.port,
                            user=host.user, ssh_key=host.ssh_key,
                            remote_port=host.daemon_port,
                            control_path=f"/tmp/bond-ssh-{host.id}",
                        )
                        self._tunnels[host_id] = new_tunnel
                        logger.info(f"Reconnected tunnel to {host_id}")
                    except Exception:
                        logger.warning(f"Tunnel to {host_id} is dead, reconnect failed")
                        self._registry.mark_unreachable(host_id)
            await asyncio.sleep(30)

    async def recover_after_restart(self):
        """On gateway startup, reconnect to all known remote hosts and
        query their daemons for running containers to reconcile state."""
        for host in self._registry.get_all_remote_hosts():
            try:
                tunnel = await self.ensure_tunnel(host)
                # Query daemon for running containers
                resp = await httpx.AsyncClient().get(f"{tunnel.local_url}/containers")
                running = resp.json()["containers"]
                await self._registry.reconcile_containers(host.id, running)
                logger.info(f"Recovered {len(running)} containers from {host.id}")
            except Exception:
                logger.warning(f"Could not recover state from {host.id}")
```

The health check loop runs as a background task started during gateway initialization. If a tunnel cannot be re-established after 3 consecutive attempts (90 seconds), the host is marked as `"offline"` and the gateway begins the failure recovery process for any agents on that host.

### 4.8 Placement Queue

<!-- P2: Placement queue design (IMPROVEMENTS §5.2) -->

When all hosts are at capacity, placement requests are queued rather than rejected:

```sql
CREATE TABLE agent_placement_queue (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    required_labels TEXT,  -- JSON array
    status TEXT DEFAULT 'pending',  -- pending, placed, timeout, cancelled
    placed_host_id TEXT,
    placed_at TEXT,
    timeout_at TEXT NOT NULL  -- requested_at + configurable timeout (default 5min)
);
```

A background task polls every 5 seconds and places queued agents when capacity frees up. FIFO ordering, with the queue visible in the UI as a "queued" agent status. After the timeout, the request is marked `timeout` and the user is notified: *"No remote hosts had capacity within the timeout period."*

### 4.9 Shared Memory Snapshot Delivery

<!-- P0: Shared memory snapshot delivery (IMPROVEMENTS §1.2) -->

Doc 008 defines shared memory delivery via bind-mounted `/data/shared/shared.db`. On remote hosts, this bind mount doesn't exist. Remote containers receive the shared memory snapshot as part of the container creation payload:

- The Bond backend reads the current `shared.db` file and includes its contents (typically <10 MB) in the `AgentContainerConfig.shared_memory_snapshot` field.
- The daemon writes the snapshot to a local directory and bind-mounts it into the container at `/data/shared/shared.db`.
- This is a point-in-time snapshot — remote agents do not receive live updates to shared memory during execution. This is acceptable because agents are autonomous and short-lived.

```python
# In the daemon:
async def setup_shared_memory(key: str, snapshot: bytes) -> str:
    shared_dir = f"/var/bond/shared/{key}"
    os.makedirs(shared_dir, mode=0o700, exist_ok=True)
    with open(f"{shared_dir}/shared.db", "wb") as f:
        f.write(snapshot)
    return shared_dir  # Mount as /data/shared in the container
```

### 4.10 Agent Data Volume Persistence

<!-- P1: Agent data volume persistence across hosts (IMPROVEMENTS §1.3) -->

Doc 008 §7.3 shows agent data persisting in Docker volumes (`bond-agent-{id}`). On remote hosts, these volumes are local to the remote machine. If an agent is placed on a different host on its next run (due to load balancing), it loses its `/data/agent.db`.

**Strategy (v1):** Host affinity. The placement algorithm (§3.2 Decision 4) records `last_host_id` for each agent after its first run. Subsequent runs prefer the same host, preserving cached repos and data volumes. If the preferred host is unavailable, the agent runs on another host with a clean data volume — this is documented as a known limitation.

**Future enhancement:** Sync `/data` back to the Bond host after task completion, and seed it on the next host. This enables true host-agnostic agent data persistence but adds complexity and latency.

---

## 5. Workspace Strategy for Remote Hosts

This is the hardest problem. Local containers use bind mounts. Remote containers can't.

### 5.1 Git-Based Workspace (Primary)

For repos with a remote:
1. `bond-host-daemon` runs `git clone <repo_url> --branch <branch>` into a local directory
2. Mounts that directory into the container as `/workspace`
3. Agent works normally — all file operations are local to the remote machine
4. On completion, agent pushes to a branch (already the standard workflow)
5. `bond-host-daemon` cleans up the workspace

**Git credential security:** The daemon uses `GIT_ASKPASS` or `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*` environment variables to inject credentials at clone time without persisting them in `.git/config`. After cloning, any credential sections in `.git/config` are stripped. <!-- P2: Git credential exposure (IMPROVEMENTS §2.2) -->

**Concurrent branch protection:** The placement strategy should be aware of repo+branch combinations. If two agents target the same repo and branch on different hosts, they will each clone independently and one will fail with a non-fast-forward error on push. Mitigation: agents always push to unique branch names (e.g., `agent/{agent_id}/issue-42`), which is already the standard workflow. <!-- P3: Concurrent same-branch protection (IMPROVEMENTS §7.3) -->

### 5.2 Rsync-Based Workspace (Fallback)

For local-only repos or repos with uncommitted changes:
1. Bond host rsyncs the workspace to the remote machine over SSH
2. On completion, rsync the changes back
3. Slower but handles edge cases

```python
async def sync_workspace_to_remote(host: RemoteHost, local_path: str, remote_path: str):
    """Rsync workspace to remote host with safety guards."""
    # Pre-sync size check
    size_mb = await get_dir_size_mb(local_path)
    if size_mb > MAX_SYNC_SIZE_MB:  # Default: 2048 MB
        raise WorkspaceTooLargeError(
            f"Workspace is {size_mb}MB, max is {MAX_SYNC_SIZE_MB}MB. "
            "Consider using a git remote instead."
        )

    cmd = [
        "rsync", "-az", "--delete",
        "--filter=:- .gitignore",   # Respect .gitignore (skip node_modules, etc.)
        "--max-size=100m",           # Skip individual files >100MB
        "-e", f"ssh -i {host.ssh_key} -p {host.port}",
        f"{local_path}/",
        f"{host.user}@{host.host}:{remote_path}/",
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.communicate()
```

<!-- P2: Rsync .gitignore filtering and size limits (REVIEW §4.4) -->

### 5.3 Decision Matrix

| Scenario | Strategy | Notes |
|---|---|---|
| Repo with git remote | Git clone | Fast, clean, preferred |
| Local repo, no remote | Rsync | Slower, but works |
| Large repo (>1GB) | Git shallow clone | `--depth 1` to save time |
| Monorepo with sparse checkout | Git sparse checkout | Only clone needed paths |
| No repo (ad-hoc task) | Empty workspace | Agent creates files from scratch |

---

## 6. Security

### 6.1 Threat Model

| Threat | Mitigation |
|---|---|
| Unauthorized access to remote daemon | SSH tunnel only; daemon binds to localhost |
| Secrets in transit | SSH encryption; vault data encrypted at rest |
| Compromised remote host | Agent containers are sandboxed; no access to Bond DB |
| SSH key theft | Per-host scoped keys limit blast radius (see below) |
| Container escape on remote | Same Docker isolation as local; resource limits enforced |
| Git credential exposure | `GIT_ASKPASS` injection; `.git/config` stripped post-clone |
| SSH agent forwarding abuse | Agent forwarding is **not used** (see below) |

<!-- P2: Avoid SSH agent forwarding; use ephemeral scoped keys (IMPROVEMENTS §2.1) -->

**SSH Key Strategy:** Do **not** use SSH agent forwarding. A compromised remote host with a forwarded agent can authenticate to *any* server the user's SSH agent has keys for. Instead:

- Generate **per-host SSH key pairs** during the setup flow (`bond remote add`).
- Each key is scoped to a single remote host via `authorized_keys` restrictions.
- For enhanced security, generate **ephemeral session keys** that are rotated regularly (e.g., daily). The setup flow adds the public key to the remote host; the private key is used only for that host.
- This limits blast radius: compromise of one host does not give access to others or to git remotes.

### 6.2 Authentication Flow

```
1. Bond host connects to remote via SSH (per-host key-based auth)
2. SSH tunnel established to bond-host-daemon port (via ControlMaster)
3. Daemon requires a shared secret token (generated during setup)
4. All API calls include the token in Authorization header
5. Secrets (SSH keys, API keys) are transmitted through the tunnel
   and written to tmpfs on the remote machine
```

<!-- P3: Rotating auth tokens (REVIEW §3.1) -->
**Future enhancement:** Replace the static shared secret with HMAC-based rotating tokens. Since the SSH tunnel already provides authentication (SSH key), the token is a defense-in-depth measure. A time-windowed HMAC token would eliminate the need for manual rotation:

```python
import hmac, time
def generate_token(secret: str, window: int = 300) -> str:
    timestamp = str(int(time.time()) // window)
    return hmac.new(secret.encode(), timestamp.encode(), "sha256").hexdigest()
```

### 6.3 Credential Handling

Credentials must reach the remote container without persisting on the remote host's disk:

```python
# On the remote daemon:
async def setup_credentials(key: str, creds: CredentialPayload):
    """Write credentials to tmpfs, auto-cleaned on container removal."""
    tmpfs_dir = f"/dev/shm/bond-creds-{key}"
    os.makedirs(tmpfs_dir, mode=0o700)

    # SSH keys
    write_file(f"{tmpfs_dir}/id_ed25519", creds.ssh_private_key, mode=0o600)

    # Vault data
    if creds.vault_data:
        write_file(f"{tmpfs_dir}/credentials.enc", creds.vault_data)

    return tmpfs_dir  # Mount this into the container
```

**Credential cleanup-on-boot:** The daemon cleans up stale credential directories on startup (see §4.5 `cleanup_stale_credentials`). A periodic reaper (every 5 minutes) also sweeps for orphaned credential dirs whose containers no longer exist. <!-- P2: Credential cleanup-on-boot (REVIEW §3.3) -->

---

## 7. SandboxManager Integration

### 7.1 Modified `ensure_running()`

The existing `ensure_running()` method gains host-awareness:

```python
async def ensure_running(self, agent: dict) -> dict[str, Any]:
    key = f"bond-agent-{agent['id'][:8]}"

    async with self._get_agent_lock(key):
        # Check if already running (on any host)
        existing = self._containers.get(key)
        if existing and await self._adapter_for(existing).is_running(key):
            return existing

        # Determine placement
        host = await self._registry.get_placement(agent)
        adapter = self._get_adapter(host)

        # Build config (host-path-independent)
        config = self._build_container_config(agent)

        # Create container on target host
        info = await adapter.create_container(agent, key, config)

        # Wait for health
        await self._wait_for_health(info.worker_url, key)

        # Track
        self._containers[key] = {
            "container_id": info.container_id,
            "host_id": info.host_id,
            "worker_url": info.worker_url,
            "agent_id": agent["id"],
        }

        return self._containers[key]
```

### 7.2 Gateway Failure Recovery

<!-- P0: Gateway failure recovery (REVIEW §2.1) -->

If the Bond gateway crashes, orphaned containers and SSH tunnels on remote hosts are never reclaimed unless explicitly handled. On gateway startup:

1. **Tunnel recovery:** The `TunnelManager.recover_after_restart()` method (§4.7) reconnects to all registered remote hosts and queries each daemon's `/containers` endpoint to discover running containers.

2. **State reconciliation:** The gateway compares the daemon's list of running `bond-agent-*` containers against its own database of expected containers. Containers found running on the remote but not in the gateway's database are either re-adopted (if they match a known agent) or stopped.

3. **Daemon-side timeout:** The daemon implements a configurable **gateway heartbeat timeout** (default: 10 minutes). If the daemon receives no API calls from the gateway within this window, it assumes the gateway is down and begins graceful shutdown of running agents:
   - Each agent is given 60 seconds to push its current work to git.
   - After the grace period, containers are stopped and cleaned up.
   - When the gateway reconnects, the daemon reports which agents were shut down and why.

```python
# In the daemon:
class GatewayHeartbeatMonitor:
    def __init__(self, timeout_minutes: int = 10):
        self._last_contact = time.time()
        self._timeout = timeout_minutes * 60

    def touch(self):
        """Called on every API request from the gateway."""
        self._last_contact = time.time()

    async def monitor_loop(self):
        while True:
            if time.time() - self._last_contact > self._timeout:
                logger.warning("Gateway heartbeat timeout — initiating graceful shutdown")
                await graceful_shutdown_all_agents()
                self._last_contact = time.time()  # Reset after shutdown
            await asyncio.sleep(30)
```

### 7.3 Split-Brain Prevention

<!-- P0: Split-brain / duplicate agent after network partition (IMPROVEMENTS §3.1) -->

If the Bond gateway loses contact with a remote host, it might mark agents on that host as failed and start replacements on another host. When the original host comes back, two copies of the same agent are running — potentially creating conflicting git branches.

**Fencing protocol:**

1. Before marking an agent as failed, the gateway attempts to send a stop signal to the daemon. Only if the daemon is unreachable for a configurable timeout (default: 5 minutes) is the agent marked as failed.

2. When re-creating an agent for the same task, the gateway assigns a **different branch name** (e.g., `agent/{agent_id}-retry-1/issue-42`) to avoid git push conflicts.

3. When the partitioned host comes back online, the `TunnelManager.recover_after_restart()` discovers the orphaned agent. The gateway checks if a replacement agent was already created:
   - If the replacement has completed: stop the orphaned agent, discard its work.
   - If the replacement is still running: stop the orphaned agent (the replacement is newer).
   - If no replacement was created: re-adopt the orphaned agent.

4. The UI shows a notification when a split-brain situation is detected, letting the user choose which branch to keep if both produced results.

### 7.4 Backward Compatibility

- Default behavior unchanged: if no `remote_hosts` configured, everything runs locally.
- `LocalContainerAdapter` wraps the existing `docker run` logic identically.
- The `OpenSandboxAdapter` is unaffected — it already talks to an external API.
- Existing agent configs don't need changes.

---

## 8. Bond Library on Remote Machines

The current setup bind-mounts `/bond` (the Bond source) read-only into containers. On remote machines:

### Option A: Bake into Image (Recommended)
- Build `bond-agent-worker` image with Bond library included (not mounted)
- Push to a container registry (Docker Hub, GHCR, private)
- Remote machines pull the image
- Trade-off: image rebuilds on code changes, but agents are more self-contained

### Option B: Git Clone at Startup
- The entrypoint already supports this: if `/bond/.git` doesn't exist, it clones from `BOND_REPO_URL`
- Works today with no changes
- Slower startup (~10-30s for clone)

### Option C: Shared NFS Mount
- Mount Bond source via NFS on remote machines
- Fast, always up-to-date
- Requires NFS setup (more infrastructure)

**Recommendation:** Option B for v1 (already works), Option A for production.

---

## 9. Observability

<!-- P1: Observability section (REVIEW §3.1) -->

Remote container hosts introduce a distributed system where logs, metrics, and traces span multiple machines. Without an observability story, debugging remote agent failures is extremely painful.

### 9.1 Logging

When debugging a failed agent on a remote host, the operator needs logs from four sources across two machines:
- Agent worker logs (inside the container)
- Daemon logs (on the remote host)
- SSH tunnel logs (on the Bond host)
- Docker daemon logs (on the remote host)

**Strategy:**

- The daemon forwards container logs to the Bond backend via the `/containers/{key}/logs` endpoint. `GET /api/agents/{id}/logs` works transparently regardless of whether the agent is local or remote.
- The daemon itself emits structured JSON logs to stdout (captured by systemd journal or a log file with rotation).
- All daemon API requests include a `X-Trace-Id` header generated by the gateway, enabling cross-machine correlation.

### 9.2 Metrics

The daemon exposes a `/metrics` endpoint (Prometheus format) with:
- `bond_daemon_containers_running` — gauge of active containers
- `bond_daemon_containers_created_total` — counter of container creations
- `bond_daemon_host_cpu_percent` — CPU utilization
- `bond_daemon_host_memory_available_mb` — available memory
- `bond_daemon_host_disk_available_gb` — available disk

The gateway aggregates these and surfaces them in the UI (Remote Hosts dashboard) and optionally forwards to an external monitoring system.

### 9.3 Alerting

- **Host unreachable:** Gateway emits an alert (log + UI notification) when a tunnel health check fails 3 consecutive times.
- **Host at capacity:** When a host reaches `max_agents`, the gateway logs a warning and surfaces it in the UI.
- **Disk low:** When `disk_available_gb` drops below a threshold (default: 5 GB), the daemon returns a warning in its health response, surfaced in the gateway UI.
- **Clock skew:** If the daemon's `system_time` differs from the gateway's clock by more than 30 seconds, the gateway logs a warning. <!-- P3: Clock skew detection (IMPROVEMENTS §7.2) -->

### 9.4 Tracing

All daemon API requests carry a `X-Trace-Id` header. The daemon includes this trace ID in its own logs and passes it as an environment variable to the container, enabling end-to-end correlation from gateway request → daemon operation → agent execution.

---

## 10. Failure Scenarios

<!-- P1: Failure scenario walkthroughs (REVIEW §3.3) -->

Concrete walkthroughs of failure scenarios, describing what happens automatically and what the user sees.

### 10.1 Remote Host Goes Offline Mid-Task

**Sequence:**
1. SSH tunnel drops. `TunnelManager` health check detects failure within 30 seconds.
2. Gateway attempts tunnel reconnection — 3 retries over 90 seconds.
3. If reconnection fails, host is marked `"offline"`.
4. For each agent on the offline host:
   - Agent status changes to `"unreachable"` in the UI.
   - Gateway waits for the fencing timeout (5 minutes) before marking the agent as `"failed"`.
   - If the agent's task is retryable, a new agent is placed on another host with a different branch name (split-brain prevention, §7.3).
5. When the host comes back online, `recover_after_restart()` reconciles state.

**User sees:** Agent status transitions from "running" → "unreachable" → "failed". Notification explains the host went offline and whether a retry was initiated.

### 10.2 SSH Tunnel Drops During SSE Streaming

**Sequence:**
1. The gateway's SSE connection to the remote worker breaks.
2. `TunnelManager` detects the dead tunnel within 30 seconds.
3. Tunnel is re-established (the worker container is still running on the remote host).
4. Gateway reconnects to the worker's SSE endpoint via the new tunnel.
5. The worker's SSE stream resumes — no events are lost because the worker buffers recent events.

**User sees:** Brief interruption in real-time streaming (up to ~30 seconds), then it resumes. May see a "reconnecting..." indicator in the UI.

### 10.3 Git Clone Fails on Remote Host

**Sequence:**
1. Daemon's `git_clone_with_verify()` fails (auth issues, network, repo not found).
2. Daemon returns HTTP 422 with error details.
3. Gateway receives the error and marks container creation as failed.
4. If the error is auth-related, the gateway logs a credential issue and does not retry.
5. If the error is transient (network), the gateway retries once on the same host.

**User sees:** Agent status shows "failed to start" with the specific error: *"Git clone failed: authentication required for https://github.com/org/repo"*.

### 10.4 Disk Full on Remote Host

**Sequence:**
1. Daemon's pre-flight check (`disk_available_gb < 5`) rejects container creation with HTTP 507.
2. Gateway removes this host from the candidate list and retries placement on another host.
3. If no other host has capacity, the request is queued (§4.8).

**User sees:** If placement succeeds on another host, nothing unusual. If all hosts are full, agent enters "queued" status.

### 10.5 Docker Daemon Unresponsive on Remote Host

**Sequence:**
1. Daemon's `docker_run()` call hangs or times out (30-second timeout).
2. Daemon returns HTTP 504 to the gateway.
3. Gateway marks the host as `"offline"` and retries on another host.

**User sees:** Slightly delayed agent start (30s for timeout + retry time). No manual intervention needed.

### 10.6 Daemon Crashes While Containers Are Running

**Sequence:**
1. Containers continue running (Docker daemon is independent of the bond-host-daemon).
2. Gateway's next API call to the daemon fails.
3. Gateway marks the host as unreachable.
4. When the daemon restarts, `startup_recovery()` re-discovers running containers via `docker ps`.
5. Gateway's `recover_after_restart()` (or next health check) reconnects and reconciles state.

**User sees:** Temporary "unreachable" status, automatically resolved when daemon restarts.

---

## 11. Implementation Plan

### Phase 1: Adapter Abstraction (No Remote Yet)
1. Extract `ContainerHostAdapter` protocol from `SandboxManager`
2. Create `LocalContainerAdapter` wrapping existing `docker run` logic
3. Create `AgentContainerConfig` dataclass decoupling config from host paths
4. Refactor `SandboxManager.ensure_running()` to use the adapter
5. **All existing tests must pass — zero behavior change**

### Phase 2: Remote Host Daemon
1. Build `bond-host-daemon` as a standalone FastAPI service
2. **Reuse Doc 044's broker infrastructure** for SSH-based daemon installation and remote host validation, rather than building a separate remote execution system <!-- P1: Reuse Doc 044 broker (IMPROVEMENTS §6.3) -->
3. Implement container lifecycle endpoints (create, destroy, health, logs) with idempotent `create_container`
4. Implement git-clone-based workspace setup with verification and credential security
5. Implement daemon-side `max_agents` enforcement
6. Implement startup recovery (container reconciliation, credential cleanup)
7. Test with manual SSH tunnel

### Phase 2.5: Settings-Driven Configuration

**Goal:** Move all container host configuration from environment variables and `bond.json` into the database-backed settings UI, so users never touch config files or set env vars.

**Motivation:** Environment variables are hostile to non-technical users and invisible in the UI. A user should be able to add a remote server, configure container defaults, and adjust placement strategy entirely from the Bond settings page — no terminal, no JSON editing, no restarts.

#### 2.5.1 Database Schema

**New table: `container_hosts`**

```sql
CREATE TABLE container_hosts (
    id              TEXT PRIMARY KEY,           -- e.g. "server-closet", auto-generated UUID if not provided
    name            TEXT NOT NULL,              -- human-readable: "Home Server"
    host            TEXT NOT NULL,              -- hostname or IP: "192.168.1.100"
    port            INTEGER NOT NULL DEFAULT 22,
    user            TEXT NOT NULL DEFAULT 'bond',
    ssh_key         TEXT,                       -- encrypted at rest via SettingsService crypto
    daemon_port     INTEGER NOT NULL DEFAULT 18795,
    max_agents      INTEGER NOT NULL DEFAULT 4,
    memory_mb       INTEGER,                   -- total available memory (NULL = auto-detect via daemon)
    labels          TEXT NOT NULL DEFAULT '[]', -- JSON array: ["gpu", "high-memory"]
    enabled         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'active', -- active | draining | offline
    is_local        INTEGER NOT NULL DEFAULT 0,     -- 1 for the implicit local host
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed the local host row on first migration
INSERT OR IGNORE INTO container_hosts (id, name, host, port, user, max_agents, is_local)
VALUES ('local', 'This Machine', 'localhost', 0, '', 4, 1);
```

**New settings keys (in existing `settings` table):**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `container.default_image` | string | `bond-agent-worker:latest` | Default Docker image for agent containers |
| `container.memory_limit_mb` | integer | `4096` | Default per-container memory limit |
| `container.cpu_limit` | float | `2.0` | Default per-container CPU limit (cores) |
| `container.startup_commands` | JSON | `[]` | Commands to run after container health check |
| `container.extra_packages` | JSON | `[]` | apt packages to install on container start |
| `container.placement_strategy` | enum | `least-loaded` | Placement algorithm: `least-loaded`, `round-robin`, `manual` |
| `container.prefer_local` | boolean | `true` | Prefer local host when it has capacity |
| `container.queue_timeout_seconds` | integer | `300` | How long to wait when all hosts are full |
| `container.ssh_key_default` | string (encrypted) | `~/.ssh/id_ed25519` | Default SSH key for remote hosts |

#### 2.5.2 Backend API

**Extend `hosts.py` router** — the existing CRUD endpoints already exist but need to be wired to the database instead of `HostRegistry._load_from_config()`:

```python
# PATCH: hosts.py — replace in-memory HostRegistry with DB-backed queries

@router.get("/hosts")
async def list_hosts(db: AsyncSession = Depends(get_db)) -> list[HostResponse]:
    """List all container hosts from database."""
    rows = await db.execute(text("SELECT * FROM container_hosts ORDER BY is_local DESC, name"))
    hosts = rows.mappings().all()
    # Enrich with live status from running containers
    for h in hosts:
        h["running_agents"] = await _count_running(h["id"])
        h["memory_used_mb"] = await _memory_used(h["id"])
    return hosts

@router.post("/hosts")
async def add_host(body: HostCreate, db: AsyncSession = Depends(get_db)) -> HostResponse:
    """Add a new remote container host."""
    host_id = body.id or str(uuid4())
    encrypted_key = encrypt_value(body.ssh_key) if body.ssh_key else None
    await db.execute(text("""
        INSERT INTO container_hosts (id, name, host, port, user, ssh_key, daemon_port,
                                     max_agents, memory_mb, labels, enabled)
        VALUES (:id, :name, :host, :port, :user, :ssh_key, :daemon_port,
                :max_agents, :memory_mb, :labels, :enabled)
    """), {
        "id": host_id, "name": body.name, "host": body.host, "port": body.port,
        "user": body.user, "ssh_key": encrypted_key, "daemon_port": body.daemon_port,
        "max_agents": body.max_agents, "memory_mb": body.memory_mb,
        "labels": json.dumps(body.labels or []), "enabled": body.enabled,
    })
    await db.commit()
    return await get_host(host_id, db)

@router.post("/hosts/{host_id}/test")
async def test_host_connection(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Test SSH connectivity and daemon health for a host."""
    host = await _get_host_row(host_id, db)
    ssh_key = decrypt_value(host["ssh_key"]) if host["ssh_key"] else None
    results = {
        "ssh": await _test_ssh(host["host"], host["port"], host["user"], ssh_key),
        "daemon": await _test_daemon(host["host"], host["daemon_port"], ssh_key),
        "docker": await _test_docker_remote(host["host"], host["user"], ssh_key),
        "memory_available_mb": await _probe_memory(host["host"], host["user"], ssh_key),
    }
    return {"host_id": host_id, "results": results, "all_passed": all(r["ok"] for r in results.values())}
```

**New container settings endpoints** — extend `settings.py`:

```python
# Container-specific settings with validation

CONTAINER_SETTINGS_SCHEMA = {
    "container.default_image":        {"type": "string",  "default": "bond-agent-worker:latest"},
    "container.memory_limit_mb":      {"type": "integer", "default": 4096, "min": 512, "max": 65536},
    "container.cpu_limit":            {"type": "float",   "default": 2.0,  "min": 0.5, "max": 32.0},
    "container.startup_commands":     {"type": "json",    "default": "[]"},
    "container.extra_packages":       {"type": "json",    "default": "[]"},
    "container.placement_strategy":   {"type": "enum",    "default": "least-loaded",
                                       "values": ["least-loaded", "round-robin", "manual"]},
    "container.prefer_local":         {"type": "boolean", "default": "true"},
    "container.queue_timeout_seconds":{"type": "integer", "default": 300, "min": 30, "max": 3600},
    "container.ssh_key_default":      {"type": "string",  "default": "~/.ssh/id_ed25519", "encrypted": True},
}

@router.get("/settings/container")
async def get_container_settings(db: AsyncSession = Depends(get_db)) -> dict:
    """Get all container-related settings with defaults applied."""
    svc = _service()
    result = {}
    for key, schema in CONTAINER_SETTINGS_SCHEMA.items():
        stored = await svc.get(key)
        value = stored.get("value") if stored.get("value") else schema["default"]
        result[key.replace("container.", "")] = value
    return result

@router.put("/settings/container")
async def update_container_settings(body: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Bulk-update container settings with validation."""
    svc = _service()
    for short_key, value in body.items():
        full_key = f"container.{short_key}"
        if full_key not in CONTAINER_SETTINGS_SCHEMA:
            raise HTTPException(400, f"Unknown container setting: {short_key}")
        _validate_setting(full_key, value, CONTAINER_SETTINGS_SCHEMA[full_key])
        await svc.upsert(full_key, str(value))
    return await get_container_settings(db)
```

#### 2.5.3 HostRegistry Migration

Refactor `HostRegistry` to load from the database instead of `bond.json`:

```python
class HostRegistry:
    """Database-backed host registry with placement logic."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._cache: dict[str, RemoteHost] = {}
        self._cache_ttl = 30  # seconds
        self._last_refresh = 0

    async def refresh(self):
        """Reload hosts from database."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM container_hosts WHERE enabled = 1"
            )
            self._cache = {r["id"]: RemoteHost(**r) for r in rows}
            self._last_refresh = time.monotonic()

    async def get_placement(self, agent: dict) -> RemoteHost:
        """Select a host using the configured placement strategy."""
        if time.monotonic() - self._last_refresh > self._cache_ttl:
            await self.refresh()

        strategy = await self._get_setting("container.placement_strategy")
        prefer_local = await self._get_setting("container.prefer_local")

        candidates = [h for h in self._cache.values()
                      if h.enabled and h.status == "active"
                      and h.running_count < h.max_agents
                      and self._has_memory(h, agent)]

        if not candidates:
            return None  # Triggers placement queue

        if prefer_local == "true":
            local = next((h for h in candidates if h.is_local), None)
            if local:
                return local

        if strategy == "least-loaded":
            return min(candidates, key=lambda h: h.running_count / h.max_agents)
        elif strategy == "round-robin":
            return self._next_round_robin(candidates)
        else:  # manual — should not reach here
            return candidates[0]

    def _has_memory(self, host: RemoteHost, agent: dict) -> bool:
        """Check if host has enough memory for another container."""
        if not host.memory_mb:
            return True  # No memory tracking, trust max_agents
        memory_limit = int(self._get_setting_sync("container.memory_limit_mb") or 4096)
        used = host.running_count * memory_limit
        return (host.memory_mb - used) >= memory_limit
```

#### 2.5.4 Frontend: Container Hosts Settings Tab

Add a new **"Container Hosts"** section in the settings UI at `frontend/src/app/settings/containers/`:

**ContainerHostsTab.tsx** — Main tab with two sub-sections:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Container Hosts                                                     │
│                                                                      │
│  ┌─ Container Defaults ──────────────────────────────────────────┐  │
│  │  Docker Image:    [bond-agent-worker:latest    ▼]             │  │
│  │  Memory Limit:    [4096] MB                                   │  │
│  │  CPU Limit:       [2.0] cores                                 │  │
│  │  Placement:       [Least Loaded ▼]  ☑ Prefer local host      │  │
│  │  Queue Timeout:   [300] seconds                               │  │
│  │                                                               │  │
│  │  Startup Commands:                                            │  │
│  │  ┌──────────────────────────────────────────────────────┐     │  │
│  │  │ pip install numpy pandas                             │     │  │
│  │  │ apt-get install -y ffmpeg                            │     │  │
│  │  └──────────────────────────────────────────────────────┘     │  │
│  │                                           [Save Defaults]     │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─ Hosts ───────────────────────────────────────────────────────┐  │
│  │                                                               │  │
│  │  ● This Machine (local)                    4/4 agents  ██░░  │  │
│  │    Status: active | Memory: 16 GB | 2 agents running          │  │
│  │                                                    [Edit]     │  │
│  │                                                               │  │
│  │  ● Home Server (192.168.1.100)             8/8 agents  ████  │  │
│  │    Status: active | Memory: 64 GB | 5 agents running          │  │
│  │    Labels: high-memory, gpu                                   │  │
│  │                                      [Test] [Edit] [Remove]  │  │
│  │                                                               │  │
│  │  ○ Cloud VM (worker1.example.com)          4/4 agents  ░░░░  │  │
│  │    Status: offline | Last seen: 2 min ago                     │  │
│  │                                      [Test] [Edit] [Remove]  │  │
│  │                                                               │  │
│  │                                          [+ Add Remote Host]  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**AddHostModal.tsx** — Guided form for adding a remote host:

```
┌─────────────────────────────────────────────────────┐
│  Add Remote Host                                     │
│                                                      │
│  Name:          [________________________]           │
│  Hostname/IP:   [________________________]           │
│  SSH Port:      [22____]                             │
│  SSH User:      [bond__]                             │
│  SSH Key:       [~/.ssh/id_ed25519] [Browse]         │
│  Daemon Port:   [18795_]                             │
│  Max Agents:    [4_____]                             │
│  Memory (MB):   [______] (leave blank to auto-detect)│
│  Labels:        [gpu, high-memory___________]        │
│                                                      │
│  ┌─ Connection Test ──────────────────────────────┐  │
│  │  ✓ SSH connection          OK (240ms)          │  │
│  │  ✓ Daemon reachable        OK (180ms)          │  │
│  │  ✓ Docker available        OK                  │  │
│  │  ✓ Memory detected         64,512 MB           │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│                    [Cancel]  [Test Connection]  [Add] │
└─────────────────────────────────────────────────────┘
```

#### 2.5.5 Environment Variable Migration

On first startup after this phase, auto-import existing configuration:

```python
async def migrate_env_to_settings(db: AsyncSession):
    """One-time migration: import env vars and bond.json into database settings."""

    # Import container env vars
    ENV_TO_SETTINGS = {
        "BOND_SANDBOX_IMAGE":      "container.default_image",
        "BOND_MEMORY_LIMIT":       "container.memory_limit_mb",
        "BOND_CPU_LIMIT":          "container.cpu_limit",
        "BOND_PLACEMENT_STRATEGY": "container.placement_strategy",
    }
    svc = SettingsService()
    for env_key, settings_key in ENV_TO_SETTINGS.items():
        val = os.environ.get(env_key)
        if val:
            existing = await svc.get(settings_key)
            if not existing.get("value"):
                await svc.upsert(settings_key, val)
                logger.info(f"Migrated {env_key} → {settings_key}")

    # Import bond.json remote_hosts into container_hosts table
    bond_json = _load_bond_json()
    if bond_json and "remote_hosts" in bond_json:
        for host in bond_json["remote_hosts"]:
            existing = await db.execute(
                text("SELECT id FROM container_hosts WHERE id = :id"),
                {"id": host["id"]}
            )
            if not existing.fetchone():
                await db.execute(text("""
                    INSERT INTO container_hosts (id, name, host, port, user, ssh_key,
                        daemon_port, max_agents, labels, enabled)
                    VALUES (:id, :name, :host, :port, :user, :ssh_key,
                        :daemon_port, :max_agents, :labels, :enabled)
                """), {
                    **host,
                    "ssh_key": encrypt_value(host.get("ssh_key", "")),
                    "labels": json.dumps(host.get("labels", [])),
                    "enabled": 1 if host.get("enabled", True) else 0,
                })
                logger.info(f"Migrated bond.json host '{host['id']}' to database")
        await db.commit()

    # Mark migration as complete
    await svc.upsert("container.migrated_from_env", "true")
```

**Env var overrides still work** for CI/automation:
```python
def get_container_setting(key: str, db_value: str) -> str:
    """Env var takes precedence over DB value (for CI/Docker compose)."""
    env_key = "BOND_" + key.replace(".", "_").upper()
    return os.environ.get(env_key, db_value)
```

When an env var override is active, the UI shows a warning banner:
> ⚠️ Some container settings are overridden by environment variables. Changes in the UI will not take effect until the env vars are removed.

#### 2.5.6 Memory-Aware Placement

The placement algorithm now considers actual memory, not just agent count:

```python
async def get_placement(self, agent: dict) -> RemoteHost | None:
    """Select a host with enough memory for a new container."""
    memory_limit = int(await self._get_setting("container.memory_limit_mb") or 4096)

    candidates = []
    for host in self._cache.values():
        if not host.enabled or host.status != "active":
            continue
        if host.running_count >= host.max_agents:
            continue

        # Memory check: if host reports total memory, verify headroom
        if host.memory_mb:
            used_mb = host.running_count * memory_limit
            available_mb = host.memory_mb - used_mb
            if available_mb < memory_limit:
                continue  # Not enough memory for another container

        candidates.append(host)

    if not candidates:
        return None  # All hosts full → placement queue

    # Apply strategy
    strategy = await self._get_setting("container.placement_strategy")
    if strategy == "least-loaded":
        return min(candidates, key=lambda h: h.running_count / max(h.max_agents, 1))
    elif strategy == "round-robin":
        return self._next_round_robin(candidates)
    return candidates[0]
```

The daemon's `/health` endpoint reports real-time memory:
```json
{
  "status": "ok",
  "memory_total_mb": 65536,
  "memory_available_mb": 42000,
  "containers_running": 3,
  "max_agents": 8
}
```

The UI reflects this: each host card shows a memory bar and agent count.

#### 2.5.7 Implementation Tasks

| ID | Task | Size |
|----|------|------|
| S1 | Migration: `container_hosts` table + seed local host row | S |
| S2 | Migration: new `container.*` settings keys with defaults | S |
| S3 | Backend: DB-backed `HostRegistry` replacing `bond.json` loader | M |
| S4 | Backend: Container settings CRUD endpoints with validation | M |
| S5 | Backend: Extend hosts API to read/write `container_hosts` table | M |
| S6 | Backend: Env var → DB migration script (`migrate_env_to_settings`) | S |
| S7 | Backend: Env var override logic with precedence | S |
| S8 | Frontend: `ContainerHostsTab` with defaults form + hosts list | L |
| S9 | Frontend: `AddHostModal` with connection testing | M |
| S10 | Frontend: Env var override warning banner | S |
| S11 | Backend: Memory-aware placement in `HostRegistry.get_placement()` | M |
| S12 | Tests: DB-backed registry, settings CRUD, migration, placement | M |

#### 2.5.8 Acceptance Criteria

1. A user can add, edit, and remove remote hosts entirely from the settings UI
2. Container defaults (image, memory, CPU, placement strategy) are configurable in the UI
3. No environment variables or `bond.json` editing is required for any container configuration
4. Existing `bond.json` and env var configurations are auto-migrated on first boot
5. Env var overrides still work for CI/automation, with a visible warning in the UI
6. Placement algorithm considers both `max_agents` and available memory
7. The local host appears as an always-present, non-removable entry in the hosts list
8. SSH keys are encrypted at rest in the database
9. Connection test button validates SSH, daemon, and Docker availability before saving

### Phase 3: Remote Container Adapter
1. Implement `RemoteContainerAdapter` with SSH tunnel management via `TunnelManager`
2. Implement SSH multiplexing via `ControlMaster`
3. Implement `HostRegistry` with ~~config loading from `bond.json`~~ database-backed host loading (Phase 2.5)
4. Implement full placement algorithm (label filtering, host affinity, capacity exhaustion → queue)
5. Wire into `SandboxManager`
6. Show placement queue status for queued agents

### Phase 6: Hardening
1. Rsync fallback for non-git workspaces (with .gitignore filtering and size limits)
2. Automatic image distribution (push to registry, remote pulls)
3. Credential rotation and tmpfs cleanup (ephemeral keys, periodic reaper)
4. Observability: daemon metrics endpoint, structured logging, trace ID propagation
5. ~~Database-backed host registry for dynamic management~~ *(moved to Phase 2.5)*
6. Graceful host draining
7. Daemon version compatibility checks
8. Monitoring and alerting for remote host connectivity

---

## 12. Onboarding & Setup CLI Flow

<!-- P1: Setup/onboarding CLI flow (REVIEW §5.1) -->

A smooth onboarding experience is critical for adoption. The CLI provides a guided flow for adding remote hosts.

### 12.1 Adding a Remote Host

```bash
# Interactive guided setup
$ bond remote add
  ✓ Daemon reachable: OK (v0.1.0, API v1)
  ✓ Docker available: OK (Docker 24.0.7)
  ✓ Disk space: 142 GB available
  ✓ Git installed: OK (git 2.43.0)

? Max concurrent agents [4]: 8
? Labels (comma-separated) []: high-memory, gpu

✓ Remote host "server-closet" added and verified.
```

### 12.2 Managing Hosts

```bash
# List all hosts with status
$ bond remote list
ID              NAME          HOST              STATUS   AGENTS  MAX
local           Local         localhost          active   2/8
server-closet   Home Server   192.168.1.100     active   3/8
cloud-worker-1  Cloud VM      worker1.example.com  offline  0/4

# Test connectivity
$ bond remote test server-closet
  ✓ SSH connection: OK (latency: 12ms)
  ✓ Daemon: OK (v0.1.0, uptime: 3d 4h)
  ✓ Docker: OK (3 containers running)
  ✓ Resources: 24 GB RAM free, 142 GB disk

# Drain a host for maintenance
$ bond remote drain server-closet
  Host "server-closet" set to draining. No new agents will be placed.
  3 agents still running — will complete before host goes offline.

# Remove a host
$ bond remote remove cloud-worker-1
```

### 12.3 Remote Validation

The `POST /api/hosts/{id}/validate` endpoint (called by `bond remote test`) runs a comprehensive check on the remote host:

```python
checks = [
    ("ssh", "SSH connectivity"),
    ("daemon", "Daemon reachable and version-compatible"),
    ("docker", "docker info"),
    ("disk", f"df -h /var/bond — minimum {MIN_DISK_GB}GB free"),
    ("git", "git --version — minimum 2.20"),
    ("ports", f"Daemon port {daemon_port} listening"),
    ("labels", "Label capabilities validated (e.g., 'gpu' requires nvidia-smi)"),
]
```

---

## 13. API Changes

### New REST Endpoints

```
GET    /api/hosts              — List configured remote hosts + status
POST   /api/hosts              — Add a remote host
PUT    /api/hosts/{id}         — Update remote host config
DELETE /api/hosts/{id}         — Remove a remote host
GET    /api/hosts/{id}/health  — Detailed health check for a host
POST   /api/hosts/{id}/test    — Test SSH connectivity to a host
POST   /api/hosts/{id}/validate — Comprehensive remote host validation

PATCH  /api/agents/{id}/host   — Assign an agent to a specific host
```

### Agent Config Extension

```json
{
  "id": "agent-123",
  "sandbox_image": "bond-agent-worker",
  "preferred_host": "server-closet",
  "host_labels": ["high-memory"],
  "allow_remote": true
}
```

---

## 14. Failure Modes & Recovery (Summary)

| Failure | Detection | Recovery |
|---|---|---|
| Remote host goes offline | SSH tunnel drops; health check fails | Mark host unavailable; fencing timeout (5 min) before marking agents failed; retry on another host with different branch |
| SSH tunnel drops mid-task | SSE stream disconnects; TunnelManager health check | Auto-reconnect tunnel (ControlMaster); worker keeps running; gateway reconnects to SSE |
| Remote container OOM-killed | Docker event or health check | Restart container on same host or migrate to host with more RAM |
| Network partition | Health check timeout | Fencing protocol prevents split-brain; agent continues working; results available when connectivity returns |
| Workspace git clone fails | Daemon returns error (with verification) | Retry once if transient; fall back to rsync; if that fails, report error to user |
| Image not available on remote | Daemon pull fails | Trigger image build/push from Bond host; retry |
| Gateway crashes | Daemon heartbeat timeout (10 min) | Daemon gracefully stops agents (with git push); gateway reconciles on restart |
| Daemon crashes | Gateway API call fails | Containers keep running; daemon recovers state on restart; gateway reconciles |
| Split-brain (two agents, one task) | Detected on host reconnection | Newer agent wins; user notified if both produced results |
| Disk full on remote | Pre-flight check; health endpoint | Reject placement; retry on another host |

---

## 15. What This Does NOT Cover

- **Multi-tenant isolation** — this is for a single Bond instance distributing work across machines it controls.
- **Cloud auto-scaling** — no automatic VM provisioning. Machines must be pre-configured.
- **Kubernetes integration** — the daemon pattern is intentionally simpler. K8s support could be a separate adapter in the future.
- **Live migration** — containers can't move between hosts mid-task.
- **Shared filesystem** — we don't require NFS/CIFS. Git is the synchronization mechanism.
- **Windows/macOS remote hosts** — the daemon assumes Linux with a native Docker daemon. Docker Desktop on macOS/Windows is not supported for remote hosts due to differences in networking, filesystem semantics, and SSH setup. This may be revisited in the future. <!-- P3: Windows/macOS limitations (REVIEW §5.3) -->

---

## 16. Open Questions

1. **Image registry**: Should Bond host a private registry, or require users to push to Docker Hub/GHCR?
2. **Data persistence**: Agent data (`/data`) currently persists on the host. On remote machines, should we sync it back to the Bond host after task completion? (See §4.10 for the current strategy.)
3. **Multi-repo workspaces**: Some agents work across multiple repos. How do we handle workspace mounts for multiple repos on remote machines?
4. **Daemon auto-update**: How does the `bond-host-daemon` get updated when Bond updates? (See §4.5 versioning for compatibility checks.)
5. ~~**Windows/macOS remote hosts**~~: Answered — Linux only for v1 (see §15).

---

## 17. Alternatives Considered

### Docker Context (Remote Docker API over SSH)
Docker natively supports `docker context create` to run Docker commands against a remote daemon over SSH. This would let `SandboxManager` run `docker -H ssh://user@remote run ...` with minimal code changes.

**Rejected because:**
- Bind mounts still reference local paths (doesn't solve the filesystem problem)
- Exposes the full Docker API over SSH (security concern)
- No workspace management, credential handling, or health reporting
- No resource-aware placement

### Docker Swarm / Kubernetes
Full container orchestration platforms.

**Rejected because:**
- Massive operational overhead for what's fundamentally "run a container on another machine"
- Requires cluster setup, networking overlays, service meshes
- Bond agents are ephemeral and autonomous — they don't need service discovery or rolling updates
- Could be a future adapter behind the same `ContainerHostAdapter` interface

### Tailscale / ZeroTier Mesh Networking
VPN mesh that makes all machines appear on the same network.

**Rejected as primary (but great as enhancement):**
- Requires additional software on all machines
- Doesn't solve workspace/filesystem problem
- Great complement to SSH tunnels for lower latency — worth adding in Phase 6

---

## 18. Testing Strategy

<!-- P3: Testing strategy section (IMPROVEMENTS §8.3) -->

### 18.1 Unit Tests

- `ContainerHostAdapter` interface compliance tests (run against both `LocalContainerAdapter` and `RemoteContainerAdapter` with mocked SSH/Docker).
- `HostRegistry` placement algorithm tests (label filtering, affinity, capacity exhaustion, queueing).
- `TunnelManager` health check and reconnection logic (mocked SSH connections).
- Split-brain detection and fencing protocol.

### 18.2 Integration Tests

- Use `ssh localhost` as a fake remote host — the daemon runs locally but is accessed via SSH tunnel, exercising the full remote code path.
- Test the complete lifecycle: `bond remote add` → create agent → SSE streaming → results pushed → cleanup.
- Failure injection: kill the daemon mid-task, drop the SSH tunnel, fill the disk.

### 18.3 CI Pipeline

- Integration tests run in CI using a Docker-in-Docker setup: one container acts as the "Bond host", another as the "remote host" with the daemon installed.
- SSH is configured between the two containers with key-based auth.
- Tests cover: container creation, SSE proxying, git clone workspace, tunnel reconnection, daemon restart recovery.

---

## 19. Future Considerations

<!-- P3: Cost/resource tracking (REVIEW §5.2) -->
- **Cost/resource tracking:** For team usage, track which user's agents are consuming which hosts, set per-user quotas, and report on resource utilization over time. This becomes important as remote host pools grow beyond a few machines.

<!-- P3: Rotating auth tokens (REVIEW §3.1) — see also §6.2 -->

---

## 20. Success Criteria

1. An agent container can be created on a remote machine with a single config change
2. The user experience (SSE streaming, tool execution, results) is identical to local
3. No regression in local-only mode (zero behavior change when no remote hosts configured)
4. Agent startup on remote host takes <60s (including git clone of a typical repo)
5. Remote host going offline doesn't crash the Bond backend
6. Credentials never persist on remote host disk (tmpfs only)
7. SSH tunnel reconnects automatically within 60 seconds of a network interruption <!-- From REVIEW §2.1 -->
8. Gateway restart recovers all running remote agents without data loss <!-- From REVIEW §2.2 -->
9. Daemon enforces `max_agents` independently of the gateway <!-- From REVIEW §2.3 -->
10. Shared memory snapshots are delivered to remote containers at creation time <!-- From IMPROVEMENTS §1.2 -->
11. No split-brain: at most one active agent per task after network partition recovery <!-- From IMPROVEMENTS §3.1 -->
12. `bond remote add` onboarding flow completes in under 5 minutes for a prepared host <!-- From REVIEW §5.1 -->
13. Agent logs are accessible via `GET /api/agents/{id}/logs` regardless of host <!-- From REVIEW §4.1 -->
14. All container host configuration is manageable from the settings UI without env vars or config files <!-- From Phase 2.5 -->
15. Placement algorithm considers available memory per host, not just agent count <!-- From Phase 2.5 -->
16. Existing env var and `bond.json` configurations are auto-migrated to database on upgrade <!-- From Phase 2.5 -->