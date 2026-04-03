# Improvements Review: Design Doc 089 — Remote Container Hosts

**Reviewer:** Claude
**Date:** 2026-04-03
**Inputs:** Doc 089 (main), 089-REVIEW.md (existing review), Doc 008, Doc 044

---

## 0. Relationship to Existing Review

The existing REVIEW.md is solid and covers the major gaps well. This document focuses on **additional improvements not covered there**, plus deeper dives on items the review only touched on briefly. Where there's overlap, I note it and add new specifics.

---

## 1. Architecture & Design

### 1.1 Worker-to-Gateway Callback URL is Under-specified

Doc 008 established that container workers make **zero callbacks** to the host — everything flows via the SSE stream initiated by the gateway. But Doc 089 §4.5 introduces `bond_api_url` as a parameter passed to remote containers, implying the worker *does* call back to the Bond host. This is a **contradiction with Doc 008's core design principle**.

**Impact:** If the worker needs to reach the Bond API (e.g., for shared memory sync, credential refresh), the remote container needs a routable path back to the Bond host — not just an SSH tunnel from Bond→remote.

**Suggestion:** Clarify whether remote workers need a callback path. If yes:
- Define a reverse SSH tunnel (remote→Bond) or use the daemon as a relay proxy.
- Update the security model to account for this bidirectional flow.
- If no, remove `bond_api_url` from the daemon's `docker_run` call in §4.5.

### 1.2 Shared Memory Snapshot Delivery to Remote Hosts

Doc 008 §3 defines shared memory delivery via bind-mounted `/data/shared/shared.db`. On remote hosts, this bind mount doesn't exist. The doc never addresses how shared memory snapshots reach remote containers.

**Suggestion:** Add a mechanism:
- Option A: Daemon pulls the snapshot from Bond host on container creation (via the SSH tunnel) and mounts it locally.
- Option B: Bake the snapshot into the container create payload (it's small — typically <10MB).
- Option C: Worker fetches it on startup via an endpoint on the daemon that proxies to Bond.

### 1.3 Agent Data Volume Persistence on Remote Hosts

Doc 008 §7.3 shows agent data persisting in Docker volumes (`bond-agent-{id}`). On remote hosts, these volumes are local to the remote machine. If an agent is placed on a different host next time (due to load balancing), it loses its `/data/agent.db`.

**Suggestion:** Either:
- Add host affinity: once an agent runs on host X, prefer host X for subsequent runs (the existing review mentions this briefly in §2.3 but doesn't connect it to data loss).
- Sync `/data` back to Bond host after task completion, and seed it on the next host.
- Document this as a known limitation: remote agents are stateless across runs.

### 1.4 The Daemon is a Second Control Plane

The `bond-host-daemon` manages container lifecycle, workspace setup, credential handling, port allocation, and health reporting. This is effectively a second control plane alongside the Bond backend. But the doc doesn't address:
- **State reconciliation:** If the daemon's view of running containers diverges from the Bond backend's view (e.g., after a network partition), who is authoritative?
- **Idempotency:** What happens if the Bond backend sends a duplicate `create_container` request (retry after timeout)?

**Suggestion:**
- Define the daemon as stateless — it queries Docker for ground truth on every request. The Bond backend is the sole source of intent; Docker on the remote host is the sole source of actual state.
- Make `create_container` idempotent: if a container with the given key already exists and is running, return its info rather than failing.

---

## 2. Security (Beyond Existing Review)

### 2.1 SSH Agent Forwarding Risk

§6.1 mentions "SSH agent forwarding" as a mitigation for key theft, but agent forwarding is itself a security risk — a compromised remote host can use the forwarded agent to authenticate to *any* server the user's SSH agent has keys for.

**Suggestion:** Do not use SSH agent forwarding. Instead, generate **purpose-scoped ephemeral keys** for each remote host:
- Bond generates a short-lived ED25519 key pair per session.
- The public key is added to the remote host's `authorized_keys` (via the setup flow).
- The private key is used only for that host and rotated regularly.
- This limits blast radius: compromise of one host doesn't give access to others or to git remotes.

### 2.2 Git Credential Exposure on Remote Host

When the daemon runs `git clone <repo_url>`, it needs git credentials. The doc says SSH keys are written to tmpfs, but doesn't address:
- HTTPS repos that use tokens in the URL or git credential helpers.
- The cloned `.git/config` may contain credential info that persists in the workspace directory (not tmpfs).

**Suggestion:**
- Use `GIT_ASKPASS` or `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*` environment variables to inject credentials without persisting them.
- After clone, strip any credential info from `.git/config`.

### 2.3 Daemon Binds to All of /dev/shm

Writing to `/dev/shm/bond-creds-{key}` shares the tmpfs namespace with all processes on the host. Any process running as root (or the same user) can read these credentials.

**Suggestion:** Use Docker secrets or a dedicated tmpfs mount within the container instead:
```python
# Mount a per-container tmpfs for credentials
docker_run(..., tmpfs={"/run/secrets": "size=10m,mode=0700"})
```

---

## 3. Reliability & Failure Modes (Beyond Existing Review)

### 3.1 Split-Brain After Network Partition

§11 says "Agent continues working (it's autonomous); results available when connectivity returns." But what if the Bond backend, believing the host is down, marks the agent as failed and starts a *new* agent on another host for the same task? Now two agents are working on the same issue, potentially creating conflicting git branches.

**Suggestion:**
- Add a **fencing mechanism**: before marking an agent as failed, attempt to send a stop signal via the daemon. Only if the daemon is unreachable for a configurable timeout (e.g., 5 minutes) should the agent be marked failed.
- When re-creating an agent for the same task, use a different branch name to avoid conflicts.
- Document the "two agents, one task" scenario and how it resolves (last push wins? user chooses?).

### 3.2 Partial Workspace Clone

If `git clone` succeeds but is interrupted (e.g., during checkout of a large repo), the workspace is in an inconsistent state. The daemon returns success (the container was created), but the agent will fail on missing files.

**Suggestion:** The daemon should verify clone integrity before reporting success — e.g., check that `HEAD` resolves and the working tree is clean. Wrap clone in a retry with cleanup:
```python
async def git_clone_with_verify(url, branch, dest):
    await run(["git", "clone", "--branch", branch, url, dest])
    result = await run(["git", "-C", dest, "rev-parse", "HEAD"])
    if result.returncode != 0:
        shutil.rmtree(dest)
        raise CloneVerificationError(...)
```

### 3.3 Daemon Crash Recovery

If the daemon crashes while containers are running, who manages those containers? The existing review mentions credential cleanup-on-boot, but doesn't cover:
- Re-registering running containers with the Bond backend.
- Re-establishing SSE tunnel forwarding for orphaned workers.

**Suggestion:** On daemon startup, enumerate all `bond-agent-*` containers via `docker ps`, and expose them via the `/containers` listing endpoint. The Bond backend should poll this on reconnect to reconcile state.

---

## 4. Performance

### 4.1 SSH Tunnel Per Worker is Expensive

§4.6 creates a separate SSH port forward for each worker container. With 8 agents on one remote host, that's 8 SSH tunnels plus 1 for the daemon = 9 SSH connections.

**Suggestion:** Use a **single SSH multiplexed connection** per remote host with `ControlMaster`:
```
ssh -o ControlMaster=auto -o ControlPath=/tmp/bond-ssh-%h -o ControlPersist=600
```
All port forwards share one TCP connection. This reduces connection overhead and simplifies tunnel management.

### 4.2 Git Clone Latency for Large Repos

§5.1 acknowledges `--depth 1` for large repos but doesn't address:
- Agents that need git history (e.g., `git log`, `git blame` for context).
- Repos with large binary assets (LFS).
- Private submodules.

**Suggestion:** Add configurable clone depth per agent/repo. Default to `--depth 1 --single-branch` for speed, but allow agents to request full history via config. For LFS, add `GIT_LFS_SKIP_SMUDGE=1` by default and let agents pull specific LFS files on demand.

### 4.3 No Connection Pooling for Daemon HTTP Calls

`RemoteContainerAdapter` creates HTTP requests to the daemon via the tunnel, but there's no mention of connection pooling or keep-alive.

**Suggestion:** Use a single `httpx.AsyncClient` per remote host with connection pooling enabled (this is httpx's default, just ensure the client is reused, not created per-request).

---

## 5. Scalability

### 5.1 Registry is In-Memory Only

`HostRegistry` loads hosts from `bond.json` config. There's no mention of:
- Dynamic host addition without restart (the REST API in §10 implies this, but the registry doesn't support it).
- Persisting runtime state (running container counts, last health check) across gateway restarts.

**Suggestion:** Back the registry with a database table:
```sql
CREATE TABLE remote_hosts (
    id TEXT PRIMARY KEY,
    name TEXT,
    host TEXT NOT NULL,
    port INTEGER DEFAULT 22,
    ... -- other config fields
    status TEXT DEFAULT 'active',
    last_health_check TEXT,
    running_agents INTEGER DEFAULT 0
);
```
Config file seeds the table; REST API mutates it; gateway reads from DB on startup.

### 5.2 No Queuing When All Hosts Are Full

§3.2 Decision 4 mentions "queue the request" when all hosts are at capacity, but there's no queue design:
- Where is the queue? In memory? Database?
- What's the ordering? FIFO? Priority?
- What's the timeout before giving up?
- Does the user see "queued" status in the UI?

**Suggestion:** Add a simple database-backed queue:
```sql
CREATE TABLE agent_placement_queue (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    required_labels TEXT,  -- JSON array
    status TEXT DEFAULT 'pending',  -- pending, placed, timeout, cancelled
    placed_host_id TEXT,
    placed_at TEXT
);
```
A background task polls every 5 seconds and places queued agents when capacity frees up. Timeout after a configurable period (default 5 minutes).

---

## 6. Operational Concerns (Beyond Existing Review)

### 6.1 No Setup Validation

The existing review mentions the missing onboarding flow. Beyond that, there's no **validation** that a remote host is correctly configured:
- Docker installed and accessible by the bond user?
- Sufficient disk space for image pulls and workspace clones?
- Required ports available?
- Git installed with correct version?

**Suggestion:** Add a `POST /api/hosts/{id}/validate` endpoint that runs a comprehensive check:
```python
checks = [
    ("docker", "docker info"),
    ("disk", "df -h /var/bond"),
    ("git", "git --version"),
    ("ports", f"ss -tlnp | grep {daemon_port}"),
]
```

### 6.2 No Log Aggregation Strategy

The existing review mentions centralized logging. More specifically: when debugging a failed agent on a remote host, the operator needs:
- Agent worker logs (inside the container)
- Daemon logs (on the remote host)
- SSH tunnel logs (on the Bond host)
- Docker daemon logs (on the remote host)

Currently these are in 4 different places on 2 different machines.

**Suggestion:** Have the daemon forward container logs to the Bond backend as part of the SSE proxy stream (or a separate log channel). At minimum, ensure `GET /api/agents/{id}/logs` works transparently regardless of whether the agent is local or remote.

### 6.3 Reuse Doc 044's Discovery Infrastructure

Doc 044 already has an SSH-based remote execution framework (broker + discovery scripts). The `bond-host-daemon` setup could reuse this:
- Use the broker to install the daemon on remote hosts.
- Use discovery scripts to validate remote host prerequisites.
- Conform the daemon's health reporting to Doc 044's monitoring schema for consistency.

This avoids building two separate SSH remote execution systems.

---

## 7. Edge Cases

### 7.1 Agent Needs Host-Specific Tools

Some agents may need tools only available on specific hosts (e.g., GPU for browser agent with Playwright, specific compiler versions). The label system handles placement, but doesn't handle:
- Validating that a host actually has the capabilities its labels claim.
- What happens if a labeled host goes offline and no other host has that label.

**Suggestion:** Add label verification to the health check: the daemon should validate labels against actual capabilities (e.g., label "gpu" requires `nvidia-smi` to succeed).

### 7.2 Clock Skew Between Hosts

Agent logs, git commits, and health check timestamps rely on system clocks. If remote hosts have significant clock skew, debugging becomes confusing and git operations may produce odd commit orderings.

**Suggestion:** Include system time in the daemon health response. The Bond backend should warn if skew exceeds 30 seconds.

### 7.3 Concurrent Agents on Same Repo/Branch

If two agents are placed on different remote hosts but work on the same repo and branch, they'll each `git clone` independently. When both try to push, one will fail with a non-fast-forward error.

**Suggestion:** The placement strategy should enforce that agents targeting the same repo+branch go to the same host (for workspace reuse) or use unique branch names per agent.

### 7.4 Remote Host Disk Exhaustion

Git clones and Docker images accumulate on remote hosts. Without cleanup, disk fills up.

**Suggestion:** Add to the daemon:
- Workspace cleanup after container removal (§4.5 mentions this but doesn't specify timing).
- Docker image pruning on a schedule (e.g., `docker image prune --filter "until=24h"`).
- Pre-flight disk space check before creating a container.

---

## 8. Clarity & Completeness

### 8.1 Missing: How Does the Worker Know It's Remote?

Does the agent worker behave differently when running on a remote host vs locally? The doc implies identical behavior, but:
- Locally, `/workspace` is a bind mount with live host filesystem sync.
- Remotely, `/workspace` is a git clone — changes aren't visible on the user's machine until pushed.

This is a **fundamental UX difference** that the doc should call out explicitly. The user should know: "Your agent is working on a remote copy. Results will be pushed to branch X when done."

### 8.2 Missing: Daemon Installation & Lifecycle

§4.5 says "pip install bond-host-daemon (or just copy the script)" but there's no:
- systemd unit file for running the daemon as a service.
- Auto-start on boot configuration.
- Log rotation for daemon logs.

**Suggestion:** Provide a complete setup script that handles all of these, similar to how doc 044 uses broker scripts.

### 8.3 Missing: Testing Strategy

No mention of how to test remote container functionality:
- Unit tests with mock SSH/Docker?
- Integration tests with a local "remote" (localhost SSH)?
- CI pipeline considerations?

**Suggestion:** Add a testing section. At minimum: integration tests using `ssh localhost` as a fake remote host, with the daemon running in a container itself.

---

## 9. Summary: Top Recommendations (Beyond Existing Review)

| Priority | Recommendation | Section |
|----------|---------------|---------|
| **P0** | Resolve contradiction with Doc 008 re: worker callbacks vs `bond_api_url` | §1.1 |
| **P0** | Define shared memory snapshot delivery to remote containers | §1.2 |
| **P0** | Address split-brain / duplicate agent scenario after network partition | §3.1 |
| **P1** | Use SSH multiplexing instead of per-worker tunnels | §4.1 |
| **P1** | Add agent data volume persistence strategy across hosts | §1.3 |
| **P1** | Define daemon as stateless with idempotent operations | §1.4 |
| **P1** | Make `create_container` idempotent | §1.4 |
| **P1** | Reuse Doc 044's broker infrastructure for daemon setup | §6.3 |
| **P2** | Avoid SSH agent forwarding; use ephemeral scoped keys | §2.1 |
| **P2** | Handle git credential exposure in cloned repos | §2.2 |
| **P2** | Add placement queue design | §5.2 |
| **P2** | Back registry with database for dynamic management | §5.1 |
| **P2** | Call out the UX difference (remote = git clone, not live sync) | §8.1 |
| **P3** | Add clock skew detection | §7.2 |
| **P3** | Add concurrent same-branch protection | §7.3 |
| **P3** | Add testing strategy section | §8.3 |

---

*This review complements the existing REVIEW.md. Together they provide comprehensive coverage. The most critical gaps are the Doc 008 callback contradiction (§1.1), shared memory delivery (§1.2), and split-brain handling (§3.1) — these affect the fundamental correctness of the design.*
