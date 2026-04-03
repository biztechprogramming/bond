# Review: Design Doc 089 — Remote Container Hosts

**Reviewer:** Bond AI Assistant  
**Date:** 2026-04-06  
**Status of doc:** Draft — awaiting review  

---

## Executive Summary

This is a well-structured design doc that tackles a real scaling limitation. The SSH-tunnel-first approach is pragmatic, the adapter pattern is clean, and backward compatibility is well-considered. Below are specific areas for improvement organized by category.

---

## 1. Strengths

- **Clear problem statement (§1):** The five pain points (resource ceiling, no horizontal scaling, laptop sleep, heterogeneous workloads, team usage) are concrete and well-motivated.
- **Adapter pattern (§4.2–4.4):** The `ContainerHostAdapter` abstraction with `LocalContainerAdapter` and `RemoteContainerAdapter` is the right design. It keeps the `SandboxManager` host-agnostic.
- **Workspace decision matrix (§5.3):** Excellent table covering git clone, rsync, shallow clone, sparse checkout, and empty workspace scenarios.
- **Security-by-default (§6):** Daemon binding to localhost behind SSH tunnels is a solid baseline. Credential handling via tmpfs (`/dev/shm`) is the right call.
- **Backward compatibility (§7.2):** Zero-config local-only mode preserved. This is critical for adoption.
- **SSE proxying options (§4.6):** Presenting Option A (SSH port forward) for v1 and Option B (daemon-mediated proxy) for the future is pragmatic.

---

## 2. Architecture Concerns

### 2.1 SSH Tunnel Lifecycle Management

The doc shows tunnel creation in `_ensure_tunnel()` but doesn't address:

- **Tunnel health monitoring:** What happens when an SSH tunnel silently dies mid-task? The agent container is still running on the remote but the gateway can't reach it.
- **Tunnel reconnection:** There's no retry/reconnect logic described. SSH connections drop due to network blips, especially over WAN.
- **Tunnel cleanup on gateway restart:** If the Bond gateway crashes and restarts, orphaned tunnels and remote containers need to be discovered and reclaimed.

**Suggestion:** Add a `TunnelManager` that:
```python
class TunnelManager:
    async def ensure_tunnel(self, host: RemoteHost) -> SSHTunnel:
        """Get or create a tunnel, with health checking."""
        ...
    
    async def health_check_loop(self):
        """Periodic check that all active tunnels are alive.
        Re-establish dead tunnels and mark containers as unreachable."""
        ...
    
    async def recover_after_restart(self):
        """On gateway startup, query all known remote daemons
        for running containers and re-establish tunnels."""
        ...
```

### 2.2 Single Point of Failure: The Gateway

The design assumes one Bond gateway orchestrating all remote hosts. If the gateway goes down:
- All SSH tunnels die
- No new agents can be placed
- Running agents lose their callback URL (`bond_api_url`)

**Suggestion:** Add a section on **gateway failure recovery**:
- Remote daemon should have a configurable timeout: if it loses contact with the gateway for N minutes, gracefully stop agents (with git push first).
- On gateway restart, enumerate running containers across all registered hosts.
- Consider a heartbeat protocol between daemon and gateway (not just gateway→daemon health checks).

### 2.3 Placement Strategy Needs More Detail

§4.1 mentions `"strategy": "least-loaded"` and `"prefer_local"` but the actual placement algorithm isn't specified.

**Questions to answer:**
- How is "load" measured? CPU? Memory? Running container count vs `max_agents`?
- Is placement decided at request time or queued?
- What happens when ALL hosts are at capacity? Queue? Reject? Wait?
- Is there affinity? If an agent was previously on host X with a cached repo, should it prefer host X?

**Suggestion:** Define the placement algorithm explicitly:
```python
async def get_placement(self, agent: dict) -> Host:
    candidates = [h for h in self._hosts.values() 
                  if h.enabled and h.running_count < h.max_agents]
    
    if not candidates:
        raise NoCapacityError("All hosts at max_agents capacity")
    
    # Label filtering
    required_label = agent.get("require_label")
    if required_label:
        candidates = [h for h in candidates if required_label in h.labels]
    
    # Strategy
    if self._strategy == "least-loaded":
        return min(candidates, key=lambda h: h.running_count / h.max_agents)
    elif self._strategy == "round-robin":
        return self._next_round_robin(candidates)
```

### 2.4 Port Allocation Collisions

The daemon allocates ports for worker containers but the doc doesn't specify the port range or collision avoidance strategy. If multiple containers are created rapidly, there's a race condition.

**Suggestion:** Use a port range (e.g., 19000–19999) with a file lock or atomic counter in the daemon. Or better yet, let Docker assign random ports and read them back:
```python
container = docker_client.containers.run(..., ports={"18791/tcp": None})
allocated_port = container.ports["18791/tcp"][0]["HostPort"]
```

---

## 3. Security Concerns

### 3.1 Shared Secret Token is Weak (§6.2)

The doc says: *"Daemon requires a shared secret token (generated during setup)"*. This is a static credential that:
- Must be manually rotated
- Could be leaked via logs, config files, or process listing
- Provides no identity — any bearer of the token has full daemon access

**Suggestion:** Use mutual TLS over the SSH tunnel, or better yet, since the tunnel already provides authentication (SSH key), consider the tunnel itself as the auth boundary and skip the token. If you keep it, use HMAC-based rotating tokens:
```python
# Generate a time-based token
import hmac, time
def generate_token(secret: str, window: int = 300) -> str:
    timestamp = str(int(time.time()) // window)
    return hmac.new(secret.encode(), timestamp.encode(), "sha256").hexdigest()
```

### 3.2 SSH Key Scope is Too Broad

The config shows a single SSH key (`~/.ssh/id_ed25519`) used for all remote hosts. If one remote host is compromised, the attacker has the SSH key that can access all other hosts.

**Suggestion:** Support per-host SSH keys in the config and recommend it as the default:
```json
{
  "hosts": [
    {
      "id": "worker1",
      "ssh_key": "~/.ssh/bond_worker1_ed25519"
    },
    {
      "id": "worker2", 
      "ssh_key": "~/.ssh/bond_worker2_ed25519"
    }
  ]
}
```

### 3.3 Credential Cleanup Guarantees

§6.3 writes credentials to `/dev/shm/bond-creds-{key}` but doesn't specify:
- What happens if the daemon crashes before cleanup?
- Is there a periodic sweep for orphaned credential dirs?
- Are the credentials encrypted at rest in tmpfs (they're in memory, but still readable by root)?

**Suggestion:** Add a cleanup-on-boot step to the daemon and a periodic reaper:
```python
@app.on_event("startup")
async def cleanup_stale_credentials():
    """Remove credential dirs for containers that no longer exist."""
    for entry in os.listdir("/dev/shm"):
        if entry.startswith("bond-creds-"):
            key = entry.replace("bond-creds-", "")
            if not await container_exists(key):
                shutil.rmtree(f"/dev/shm/{entry}")
```

### 3.4 No Rate Limiting on Daemon API

The daemon API has no rate limiting or request size limits. A compromised tunnel could flood the daemon with container creation requests.

**Suggestion:** Add `max_agents` enforcement in the daemon itself (not just the gateway), plus request rate limiting.

---

## 4. Operational Concerns

### 4.1 No Observability Story

The doc has a `/health` endpoint on the daemon but doesn't address:
- **Centralized logging:** How does an operator view logs from agents running across 5 remote hosts?
- **Metrics:** No mention of Prometheus/StatsD metrics from the daemon.
- **Alerting:** When a remote host goes unhealthy, who gets notified?
- **Tracing:** No correlation IDs between gateway requests and daemon operations.

**Suggestion:** Add a section on observability:
- Daemon should forward structured logs (JSON) to the gateway or a central log sink.
- Expose a `/metrics` endpoint on the daemon.
- Gateway should surface remote host status in the UI (relates to design doc 009).
- Include `trace_id` in all daemon API requests.

### 4.2 No Daemon Upgrade/Versioning Strategy

The daemon is a separate deployable (`pip install bond-host-daemon`). The doc doesn't cover:
- Version compatibility between gateway and daemon
- How to upgrade daemons across a fleet
- API versioning

**Suggestion:** Add an API version header and a version check on tunnel establishment:
```python
@app.get("/health")
async def host_health():
    return {
        "daemon_version": "0.1.0",
        "api_version": "v1",
        "min_gateway_version": "0.90.0",
        # ...existing fields...
    }
```

### 4.3 No Container Migration or Draining

If you need to take a remote host offline for maintenance, there's no way to:
- Drain running agents gracefully
- Prevent new placements on a host being decommissioned
- Migrate running work to another host

**Suggestion:** Add a `draining` state to hosts:
```python
@dataclass
class RemoteHost:
    # ...existing fields...
    status: Literal["active", "draining", "offline"] = "active"
```
When `draining`: no new placements, but existing containers run to completion.

### 4.4 Rsync Fallback Needs More Thought (§5.2)

The rsync-based workspace sync has several gaps:
- **Large workspaces:** No size limit or warning. Rsyncing a 10GB monorepo over a slow link could take forever.
- **Bidirectional conflicts:** If the agent modifies files that were also modified locally during the task, rsync-back will silently overwrite.
- **`.gitignore` respect:** Should rsync honor `.gitignore`? `node_modules` and build artifacts could bloat the transfer.

**Suggestion:** Add `--max-size`, `--filter=':- .gitignore'`, and a pre-sync size check:
```python
async def sync_workspace_to_remote(host, local_path, remote_path):
    # Check size first
    size = await get_dir_size(local_path)
    if size > MAX_SYNC_SIZE_MB:
        raise WorkspaceTooLargeError(f"Workspace is {size}MB, max is {MAX_SYNC_SIZE_MB}MB")
    
    cmd = [
        "rsync", "-az", "--delete",
        "--filter=:- .gitignore",  # Respect .gitignore
        "--max-size=100m",          # Skip huge files
        "-e", f"ssh -i {host.ssh_key} -p {host.port}",
        f"{local_path}/",
        f"{host.user}@{host.host}:{remote_path}/",
    ]
```

---

## 5. Missing Details

### 5.1 No Setup/Onboarding Flow

How does a user add a remote host? The doc shows the JSON config but doesn't describe:
- A CLI command (`bond remote add worker1 --host=... --user=...`)
- SSH key distribution / `ssh-copy-id` automation
- Daemon installation on the remote (ansible? script? manual?)
- Connectivity verification (`bond remote test worker1`)

This is critical for adoption. Without a smooth onboarding experience, nobody will configure remote hosts.

### 5.2 No Cost/Resource Tracking

For team usage, there's no way to:
- Track which user's agents are consuming which hosts
- Set per-user quotas
- Report on resource utilization over time

### 5.3 No Windows/macOS Remote Host Support

The doc implicitly assumes Linux remote hosts (SSH, Docker daemon). Should explicitly state this limitation and note any plans for Windows/macOS remotes.

### 5.4 No Failure Scenario Walkthroughs

The doc would benefit from explicit walkthroughs of failure scenarios:
1. Remote host goes offline mid-task
2. SSH tunnel drops during SSE streaming
3. Git clone fails on remote (auth issues, network)
4. Disk full on remote host
5. Docker daemon unresponsive on remote

For each: what does the user see? What recovery is automatic vs. manual?

---

## 6. Consistency with Related Design Docs

### 6.1 vs. Doc 008 (Containerized Agent Runtime)

Doc 008 defines the local container lifecycle. Doc 089 should explicitly reference which 008 interfaces it extends vs. replaces. The `ContainerHostAdapter` seems to wrap 008's `SandboxManager` — confirm this is the intended layering, not a parallel hierarchy.

### 6.2 vs. Doc 044 (Remote Discovery and Deployment Monitoring)

Doc 044 covers remote discovery patterns. The remote host health checking in 089 could reuse 044's monitoring infrastructure rather than building a separate health check system. Consider whether the daemon's `/health` endpoint should conform to 044's monitoring schema.

### 6.3 vs. Doc 009 (Container Configuration UI)

Doc 009 defines the container config UI. Remote host management (adding hosts, viewing status, placement preferences) needs UI support. This should be called out as a follow-up to 009.

---

## 7. Summary of Recommended Changes

| Priority | Recommendation | Section |
|----------|---------------|---------|
| **P0** | Define tunnel health monitoring and reconnection | §4.4 |
| **P0** | Define gateway failure recovery (orphan container handling) | §7.1 |
| **P0** | Add daemon-side `max_agents` enforcement | §6 |
| **P1** | Specify placement algorithm fully (capacity exhaustion, queueing) | §4.1 |
| **P1** | Add observability section (logging, metrics, tracing) | New §8 |
| **P1** | Define setup/onboarding CLI flow | New §9 |
| **P1** | Add failure scenario walkthroughs | New §10 |
| **P1** | Support per-host SSH keys | §3.1 config |
| **P2** | Add host draining for maintenance | §4.1 |
| **P2** | Improve rsync with .gitignore filtering and size limits | §5.2 |
| **P2** | Add daemon versioning and compatibility checks | §4.5 |
| **P2** | Add credential cleanup-on-boot | §6.3 |
| **P3** | Consider rotating auth tokens instead of static shared secret | §6.2 |
| **P3** | Add cost/resource tracking for team usage | New |
| **P3** | Document Windows/macOS remote host limitations | New |

---

*Overall: This is a solid design that's heading in the right direction. The SSH-tunnel-first approach is the right call for v1. The main gaps are around failure handling, observability, and the operational experience of managing a fleet of remote hosts. Addressing the P0 and P1 items above would make this production-ready.*
