# Design Doc 112: Credential Passthrough for Containerized Bond

**Status:** Draft — awaiting review
**Depends on:** 003 (Agent Tools & Sandbox), 008 (Containerized Agent Runtime), 009 (Container Configuration UI)
**Related incident:** Default agent failed to clone the bond repo because no SSH key reached the sandbox container. Root cause traced to `Path.home()` resolving to the bond container's home, not the host's.

---

## 1. The Problem

When bond runs in a Docker container (`bond-bond-1`) and shells out to `docker run` to spawn agent sandbox containers, paths used as `-v` sources are resolved by the **docker daemon on the host**, not by bond's process. Bond's existing auto-mount code computes those paths as `Path.home() / ".ssh"`, `Path.home() / ".claude" / ".credentials.json"`, etc. — but `Path.home()` returns the *container's* `$HOME`, which is meaningless to the host daemon.

Two failure modes:

- **The path doesn't exist on the host.** The daemon either errors or silently creates an empty directory at the bind source.
- **Even when bond `exists()`-checks the path before mounting, the check runs against the container filesystem,** which doesn't have the credentials, so the mount is skipped entirely.

The current architecture quietly assumed bond runs natively on the host — a state where `Path.home()` and the daemon's view of the filesystem agree. That assumption broke when bond started running in a container.

### 1.1 Why simple fixes fall short

We considered three quick fixes during incident response:

1. **Per-agent explicit mounts.** Set `workspace_mounts` on the default agent so the SSH and Claude paths are pinned. Works, but every new agent needs the same boilerplate, and any user who hasn't read this doc will hit the same issue on day one.
2. **Match container `$HOME` to host `$HOME`.** Set `HOME=/home/andrew` and bind-mount `/home/andrew` over the container's home. Works, but ripples through the Dockerfile (`BOND_HOME=/home/bond/.bond`), the data layout, and every place that uses `/home/bond/...`. Big blast radius.
3. **Run bond natively (`make dev`).** Avoids the problem entirely but gives up the deploy-style container that the rest of the stack assumes.

Each is a partial fix. We need a model that handles native and containerized bond uniformly, with sensible defaults *and* per-agent override.

---

## 2. Design Principles

In priority order:

1. **Simple by default.** First-run on a typical workstation should "just work" without any agent configuration UI tinkering. SSH and Claude credentials should reach a default agent automatically.
2. **One source of truth for host paths.** The model has one knob (`BOND_HOST_HOME`) for "where is the human user's home directory on the host?" Everything else is derived.
3. **Native operation is the no-config case.** Setting nothing extra, running `make dev`, the design collapses to current behavior.
4. **Customizable per agent.** Power users override or extend the auto-mount set on a per-agent basis (already supported by `workspace_mounts`).
5. **No path translation logic.** Avoid maintaining a translation table between container paths and host paths. Use bind mounts to make the path *be the same* on both sides where it matters.

---

## 3. Proposed Architecture

### 3.1 The host-home invariant

Introduce a single environment variable, `BOND_HOST_HOME`, which is the absolute path of the human user's home directory **as the docker daemon sees it**.

```
Native bond:        BOND_HOST_HOME unset → defaults to Path.home()
Containerized bond: BOND_HOST_HOME=/home/andrew (set in compose)
```

Bond code never calls `Path.home()` for purposes that involve another container. It always goes through a small helper:

```python
# backend/app/sandbox/host_paths.py
import os
from pathlib import Path

def host_home() -> Path:
    """The host user's home directory, as seen by the docker daemon."""
    return Path(os.environ.get("BOND_HOST_HOME", str(Path.home())))

def host_path(*parts: str) -> Path:
    """Build a host-side path under the user's home."""
    return host_home().joinpath(*parts)
```

`Path.home()` continues to be valid for purely-internal uses (writing logs to bond's own state dir, reading bond's own config). The discipline is: **anything that becomes the source of a `-v` flag goes through `host_path()`.**

### 3.2 The "same path on both sides" rule

For containerized bond, `BOND_HOST_HOME` is **also** bind-mounted into the bond container at the same path. So `Path(BOND_HOST_HOME) / ".ssh"`:

- exists inside bond's container (read-only) → `exists()` checks pass, bond can read the keys if it ever needs to
- exists on the host at the same absolute path → docker daemon resolves it correctly when bond passes it as a `-v` source

```yaml
# compose snippet
services:
  bond:
    environment:
      - BOND_HOST_HOME=/home/andrew
    volumes:
      - /home/andrew/.ssh:/home/andrew/.ssh:ro
      - /home/andrew/.claude:/home/andrew/.claude:ro
      # (existing bond data mount unchanged)
      - ~/.bond:/home/bond/.bond
```

Note: we mount the *specific* credential subdirs, not the entire home. This keeps the surface area visible to the bond container scoped to what it actually needs.

### 3.3 Default credential set

A small, declarative list of standard credentials lives in `backend/app/sandbox/default_mounts.py`:

```python
DEFAULT_CREDENTIAL_MOUNTS = [
    # SSH keys for git operations
    CredentialMount(
        source=lambda: host_path(".ssh"),
        target="/tmp/.ssh",
        mode="ro",
        required_for=["git-over-ssh"],
        skip_if_target_overridden=True,
    ),
    # Claude API credentials
    CredentialMount(
        source=lambda: host_path(".claude", ".credentials.json"),
        target="/home/bond-agent/.claude/.credentials.json",
        mode="rw",
        required_for=["claude-tool-use"],
        skip_if_target_overridden=True,
    ),
    # Claude settings (read-only)
    CredentialMount(
        source=lambda: host_path(".claude", "settings.json"),
        target="/home/bond-agent/.claude/settings.json",
        mode="ro",
        required_for=["claude-tool-use"],
        skip_if_target_overridden=True,
    ),
]
```

The agent-spawn code iterates this list, applies each mount whose source exists, and skips any whose `target` is already declared in the agent's `workspace_mounts` (preserving the existing override semantics).

### 3.4 Per-agent overrides (option 1, expanded)

Existing `workspace_mounts` already supports `host_path`, `container_path`, `mode`. We promote three explicit override modes:

| Mode | UI label | Meaning |
| --- | --- | --- |
| `add` (default) | "Add mount" | Append to the auto-detected set |
| `replace` | "Replace default" | Mount path matches a default's target → silently overrides |
| `disable` | "Don't mount" | Mount entry with `host_path=null` → suppresses the default with the same target |

Power users disable the default SSH mount and provide a deploy-key path elsewhere; first-run users do nothing and get sensible defaults.

### 3.5 Sanity check at startup

When bond's backend starts, it runs a self-check:

```python
def check_credential_passthrough() -> list[str]:
    warnings = []
    if "BOND_HOST_HOME" in os.environ:
        # We're (probably) containerized.
        host_home_path = host_home()
        if not host_home_path.exists():
            warnings.append(
                f"BOND_HOST_HOME={host_home_path} is set but not visible inside the bond container. "
                f"Add a bind mount: '{host_home_path}:{host_home_path}:ro' or similar."
            )
        elif not (host_home_path / ".ssh").exists():
            warnings.append(
                f"{host_home_path}/.ssh not found. SSH-based git operations will fail in agent sandboxes."
            )
    return warnings
```

These surface in the gateway log and the first-run UI, so the failure mode is loud instead of "agent times out 90 seconds in".

---

## 4. Why This Works

### 4.1 Native bond
`BOND_HOST_HOME` unset → `host_home()` returns `Path.home()` → identical to today's behavior. Auto-mounts continue to work because there's only one filesystem in play.

### 4.2 Containerized bond
`BOND_HOST_HOME=/home/andrew` is set, and `/home/andrew/.ssh` is bind-mounted at the same path. Bond's `host_path(".ssh")` returns `/home/andrew/.ssh`. The path:
- exists inside bond's container (so `exists()` returns true, default mount fires)
- exists on the host at the same absolute path (so the docker daemon resolves the `-v` source correctly)
- can be passed to `docker run -v /home/andrew/.ssh:/tmp/.ssh:ro` and Just Works

### 4.3 No translation logic
We never compute "the host equivalent of /home/bond/.ssh". The path is the same on both sides because we made it so. The complexity that was hiding inside an implicit assumption is now visible: one env var, one bind mount, both pointing at the same path.

### 4.4 Customizable
Per-agent `workspace_mounts` is unchanged in shape — we just give it three explicit override modes (add/replace/disable) so users don't have to figure out priority by reading code.

---

## 5. Migration Plan

1. **Add `host_paths` helper module** with `host_home()` and `host_path()`. Default to `Path.home()` when env var unset. (No behavior change yet.)
2. **Refactor `adapters.py` auto-mount code** to use `host_path()` and to iterate `DEFAULT_CREDENTIAL_MOUNTS` instead of inlining each credential. Add the override-mode logic. (Still no behavior change for native bond.)
3. **Update deploy compose** (`/srv/environments/dev/bond/docker-compose.yml`) to set `BOND_HOST_HOME` and add the credential bind mounts.
4. **Add the startup sanity check** so misconfigurations surface immediately.
5. **Document in `CLAUDE.md`**: when running bond in a container, set `BOND_HOST_HOME` and mirror at least `~/.ssh` (and `~/.claude` if using Claude tools) into the container at the same absolute path.
6. **UI (later, optional):** expose the per-agent override-mode dropdown in the agent settings dialog. Until then, users can edit `workspace_mounts` directly.

Backwards-compatibility note: existing agents with explicit `workspace_mounts` of `/tmp/.ssh` etc. continue to work unchanged. The refactor preserves the "skip auto-mount if target already specified" behavior.

---

## 6. Tradeoffs and Open Questions

### 6.1 Bind-mounting credentials read-only into bond's container

The bond container can read the user's SSH private key and Claude credentials. This is already true in any architecture where bond brokers credentials to agents — the bond process is trusted with these secrets. The new design doesn't change the trust boundary, just makes the mount path explicit. We use `:ro` to prevent accidental writes from bond.

### 6.2 Why not a "credential vault" subdirectory?

An alternative is to copy credentials into a dedicated `~/.bond/credentials/` directory and only bind-mount that one path. Pros: tighter scope. Cons: requires copy-on-change logic, adds a new place credentials can drift, and copying private keys is a footgun (file permissions, encryption-at-rest, etc.). The bind-mount-the-original approach is simpler and more honest about where the secret of record lives.

### 6.3 WSL and Docker Desktop

On WSL2, docker daemon paths are typically `/mnt/c/Users/...` or the equivalent native path depending on whether Docker Desktop is configured to use the WSL backend. `BOND_HOST_HOME` lets the user set this explicitly per-environment. The same env var handles macOS Docker Desktop, Linux native, and Linux-in-VM equally well — it's the user's call what value to set.

### 6.4 Multi-user / multi-tenant deployments

This design assumes a single human user per bond instance. A shared deployment with multiple users would need a different model — likely per-conversation credential injection rather than agent-level mounts, with credentials stored in the bond database or a vault. Out of scope for this doc, but worth noting that `BOND_HOST_HOME` is fundamentally a single-user abstraction.

### 6.5 Auto-detection of containerization

Could bond detect that it's containerized and set `BOND_HOST_HOME` automatically (e.g. by reading `/proc/1/cgroup`)? Yes, but auto-detecting containerization doesn't tell us *which* host path to use — only the user/operator knows that. So an explicit env var is unavoidable. Auto-detect could at most enforce "you must set this when containerized" via a startup check (which §3.5 already does).

### 6.6 Should default mounts be data, not code?

The list in §3.3 is currently Python. Migrating it to YAML or JSON later (so users can edit it without a code change) is a small, isolated refactor. Worth doing once we have more than 2-3 standard credentials.

---

## 7. Summary

- **One env var** (`BOND_HOST_HOME`) for the host's user home.
- **One bind mount** (or a small number of subdirs) so that path resolves to the same content inside and outside bond's container.
- **One default-mount list** that drives auto-attachment of standard credentials.
- **Three override modes** (`add` / `replace` / `disable`) on per-agent mounts for explicit user control.
- **One startup check** to make misconfigurations loud.

Result: native bond behaves exactly like today. Containerized bond behaves identically to native bond as long as the operator sets `BOND_HOST_HOME` and mirrors the credential paths. Power users override per agent. No path-translation logic, no implicit assumptions about where home is.
