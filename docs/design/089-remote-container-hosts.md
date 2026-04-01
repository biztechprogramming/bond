# Design Doc 089: Remote Container Hosts

**Status:** Draft — awaiting review  
**Depends on:** 008 (Containerized Agent Runtime), 037 (Coding Agent Skill), 035 (Secure Agent Execution Architecture)  
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

This is similar to how Kubernetes has a kubelet on each node, but much simpler.

#### Decision 3: SSH as the Default Transport

For the initial implementation, communication between Bond host and remote machines uses **SSH tunnels**:

- No firewall changes needed — SSH is almost always open
- Authentication uses existing SSH keys (already managed in Bond for git)
- Port forwarding gives us secure access to the worker's SSE port
- The `bond-host-daemon` listens only on `localhost` — SSH tunnel provides access

Future iterations can add WireGuard mesh networking or Tailscale for lower latency.

#### Decision 4: Placement Strategy

A simple placement strategy decides where to run each container:

```
1. If agent config specifies a host → use that host
2. If all hosts are at capacity → queue the request
3. Otherwise → pick the host with the most available resources
```

No complex scheduling — this isn't Kubernetes. The user can also manually assign agents to hosts via the UI.

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
      "ssh_key": "~/.ssh/id_ed25519",
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
      "ssh_key": "~/.ssh/id_ed25519",
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

**Python model:**

```python
@dataclass
class RemoteHost:
    id: str
    name: str
    host: str
    port: int = 22
    user: str = "bond"
    ssh_key: str = "~/.ssh/id_ed25519"
    daemon_port: int = 18795
    max_agents: int = 4
    labels: list[str] = field(default_factory=list)
    enabled: bool = True

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
    
    def __init__(self, host: RemoteHost):
        self._host = host
        self._tunnel: SSHTunnel | None = None
    
    async def _ensure_tunnel(self):
        """Establish SSH tunnel to the remote daemon."""
        if not self._tunnel or not self._tunnel.is_alive:
            self._tunnel = await SSHTunnel.connect(
                host=self._host.host,
                port=self._host.port,
                user=self._host.user,
                ssh_key=self._host.ssh_key,
                remote_port=self._host.daemon_port,
            )
    
    async def create_container(self, agent, key, config):
        await self._ensure_tunnel()
        
        # Send container spec to remote daemon
        resp = await self._client.post(
            f"{self._tunnel.local_url}/containers",
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
        resp = await self._client.get(
            f"{self._tunnel.local_url}/containers/{key}/url"
        )
        return resp.json()["url"]
```

### 4.5 Bond Host Daemon

A lightweight service that runs on each remote machine:

```python
# bond-host-daemon — runs on remote worker machines
# Install: pip install bond-host-daemon (or just copy the script)

app = FastAPI()

@app.post("/containers")
async def create_container(spec: ContainerSpec):
    """Create an agent container on this machine."""
    
    # 1. Ensure image is available
    await pull_or_build_image(spec.image)
    
    # 2. Prepare workspace via git clone (no bind mounts from Bond host)
    workspace_dir = f"/var/bond/workspaces/{spec.key}"
    if spec.repo_url:
        await git_clone(spec.repo_url, spec.repo_branch, workspace_dir)
    
    # 3. Write agent config to local temp file
    config_path = write_agent_config(spec.key, spec.agent_config)
    
    # 4. Write SSH keys to local temp
    ssh_dir = setup_ssh_keys(spec.key, spec.ssh_private_key)
    
    # 5. docker run with local paths
    container_id = await docker_run(
        image=spec.image,
        name=spec.key,
        workspace=workspace_dir,
        config=config_path,
        ssh=ssh_dir,
        env=spec.env_vars,
        resources=spec.resource_limits,
        # Worker calls back to Bond host, not localhost
        bond_api_url=spec.bond_api_url,
    )
    
    # 6. Allocate port and return
    return {"container_id": container_id, "worker_url": f"http://localhost:{port}"}

@app.delete("/containers/{key}")
async def destroy_container(key: str):
    """Stop and remove a container."""
    ...

@app.get("/containers/{key}/health")
async def container_health(key: str):
    """Health check a specific container."""
    ...

@app.get("/containers/{key}/logs")
async def container_logs(key: str, tail: int = 50):
    """Get container logs."""
    ...

@app.get("/health")
async def host_health():
    """Report this machine's resource availability."""
    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_available_mb": psutil.virtual_memory().available // 1024 // 1024,
        "disk_available_gb": psutil.disk_usage("/").free // 1024**3,
        "running_containers": len(await list_bond_containers()),
    }

@app.post("/containers/{key}/sync-back")
async def sync_results(key: str):
    """Push workspace changes back to git remote."""
    ...
```

### 4.6 SSE Proxying

The critical path is getting the worker's SSE stream back to the Bond gateway. Two approaches:

**Option A: SSH Port Forward (Recommended for v1)**
```
Bond Gateway ──SSH tunnel──► Remote Machine ──localhost──► Worker :18791
```

The `RemoteContainerAdapter` establishes an SSH port forward for each worker container. The gateway connects to a local port that tunnels to the remote worker. **Zero code changes to the SSE handling.**

**Option B: Daemon-Mediated Proxy (Future)**
```
Worker :18791 ──► bond-host-daemon ──WebSocket──► Bond Gateway
```

The daemon proxies the SSE stream. More complex but allows multiplexing and better error handling.

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

### 5.2 Rsync-Based Workspace (Fallback)

For local-only repos or repos with uncommitted changes:
1. Bond host rsyncs the workspace to the remote machine over SSH
2. On completion, rsync the changes back
3. Slower but handles edge cases

```python
async def sync_workspace_to_remote(host: RemoteHost, local_path: str, remote_path: str):
    """Rsync workspace to remote host."""
    cmd = [
        "rsync", "-az", "--delete",
        "-e", f"ssh -i {host.ssh_key} -p {host.port}",
        f"{local_path}/",
        f"{host.user}@{host.host}:{remote_path}/",
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.communicate()
```

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
| SSH key theft | Keys are short-lived or use SSH agent forwarding |
| Container escape on remote | Same Docker isolation as local; resource limits enforced |

### 6.2 Authentication Flow

```
1. Bond host connects to remote via SSH (key-based auth)
2. SSH tunnel established to bond-host-daemon port
3. Daemon requires a shared secret token (generated during setup)
4. All API calls include the token in Authorization header
5. Secrets (SSH keys, API keys) are transmitted through the tunnel
   and written to tmpfs on the remote machine
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

### 7.2 Backward Compatibility

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

## 9. Implementation Plan

### Phase 1: Adapter Abstraction (No Remote Yet)
1. Extract `ContainerHostAdapter` protocol from `SandboxManager`
2. Create `LocalContainerAdapter` wrapping existing `docker run` logic
3. Create `AgentContainerConfig` dataclass decoupling config from host paths
4. Refactor `SandboxManager.ensure_running()` to use the adapter
5. **All existing tests must pass — zero behavior change**

### Phase 2: Remote Host Daemon
1. Build `bond-host-daemon` as a standalone FastAPI service
2. Package as a simple install script (`curl | bash` or pip install)
3. Implement container lifecycle endpoints (create, destroy, health, logs)
4. Implement git-clone-based workspace setup
5. Test with manual SSH tunnel

### Phase 3: Remote Container Adapter
1. Implement `RemoteContainerAdapter` with SSH tunnel management
2. Implement `HostRegistry` with config loading from `bond.json`
3. Implement basic placement strategy (manual assignment + least-loaded)
4. Wire into `SandboxManager`

### Phase 4: SSE Proxying & Connectivity
1. Implement SSH port forwarding for worker SSE streams
2. Ensure gateway can connect to remote workers transparently
3. Handle tunnel reconnection on network interruption
4. Test end-to-end: issue → remote container → agent works → results pushed

### Phase 5: UI & UX
1. Add "Remote Hosts" section to settings UI
2. Show host status (online/offline, resource usage, running agents)
3. Allow manual agent-to-host assignment
4. Show which host each agent is running on in the agent list

### Phase 6: Hardening
1. Rsync fallback for non-git workspaces
2. Automatic image distribution (push to registry, remote pulls)
3. Credential rotation and tmpfs cleanup
4. Monitoring and alerting for remote host connectivity
5. Graceful handling of remote host going offline mid-task

---

## 10. API Changes

### New REST Endpoints

```
GET    /api/hosts              — List configured remote hosts + status
POST   /api/hosts              — Add a remote host
PUT    /api/hosts/{id}         — Update remote host config
DELETE /api/hosts/{id}         — Remove a remote host
GET    /api/hosts/{id}/health  — Detailed health check for a host
POST   /api/hosts/{id}/test    — Test SSH connectivity to a host

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

## 11. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---|---|---|
| Remote host goes offline | SSH tunnel drops; health check fails | Mark host unavailable; existing agents on that host are marked failed; retry on another host |
| SSH tunnel drops mid-task | SSE stream disconnects | Auto-reconnect tunnel; worker keeps running; gateway reconnects to SSE |
| Remote container OOM-killed | Docker event or health check | Restart container on same host or migrate to host with more RAM |
| Network partition | Health check timeout | Agent continues working (it's autonomous); results available when connectivity returns |
| Workspace git clone fails | Daemon returns error | Fall back to rsync; if that fails, report error to user |
| Image not available on remote | Daemon pull fails | Trigger image build/push from Bond host; retry |

---

## 12. What This Does NOT Cover

- **Multi-tenant isolation** — this is for a single Bond instance distributing work across machines it controls.
- **Cloud auto-scaling** — no automatic VM provisioning. Machines must be pre-configured.
- **Kubernetes integration** — the daemon pattern is intentionally simpler. K8s support could be a separate adapter in the future.
- **Live migration** — containers can't move between hosts mid-task.
- **Shared filesystem** — we don't require NFS/CIFS. Git is the synchronization mechanism.

---

## 13. Open Questions

1. **Image registry**: Should Bond host a private registry, or require users to push to Docker Hub/GHCR?
2. **Data persistence**: Agent data (`/data`) currently persists on the host. On remote machines, should we sync it back to the Bond host after task completion?
3. **Multi-repo workspaces**: Some agents work across multiple repos. How do we handle workspace mounts for multiple repos on remote machines?
4. **Daemon auto-update**: How does the `bond-host-daemon` get updated when Bond updates?
5. **Windows/macOS remote hosts**: The daemon assumes Linux. Should we support Docker Desktop on other OSes?

---

## 14. Alternatives Considered

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

## 15. Success Criteria

1. An agent container can be created on a remote machine with a single config change
2. The user experience (SSE streaming, tool execution, results) is identical to local
3. No regression in local-only mode (zero behavior change when no remote hosts configured)
4. Agent startup on remote host takes <60s (including git clone of a typical repo)
5. Remote host going offline doesn't crash the Bond backend
6. Credentials never persist on remote host disk (tmpfs only)
