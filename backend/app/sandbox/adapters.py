"""Container Host Adapter — abstract interface for container lifecycle operations.

Design Doc 089: Remote Container Hosts §4.2–4.4
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from backend.app.sandbox.workspace_cloner import (
    cleanup_workspace_clones,
    detect_workspace_type,
    execute_clone_plan,
    generate_clone_plan,
    generate_dep_install_script,
)

logger = logging.getLogger("bond.sandbox.adapters")

# Port range for containerized worker agents
_PORT_RANGE_START = 18791
_PORT_RANGE_END = 18890

# Internal port the worker always listens on inside the container
_WORKER_INTERNAL_PORT = 18791

# Project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


# Inside the agent images the worker runs as bond-agent (uid/gid 1000) after
# privilege drop. Bond-bond runs as root, so any file/dir it writes for an
# agent bind-mount is owned by uid 0 with mode 0600/0700 — unreadable by the
# agent. _agent_readable() chowns/chmods the path so uid 1000 can read.
_BOND_AGENT_UID = 1000
_BOND_AGENT_GID = 1000


def _agent_readable(path: Path, *, dir_mode: int = 0o755, file_mode: int = 0o644) -> Path:
    """Make ``path`` readable by the agent's bond-agent user (uid 1000).

    Best-effort: a chown failure (e.g. because the host filesystem doesn't
    support changing owner) is logged and ignored — the chmod alone is
    usually enough since 0o644/0o755 grant world-read.
    """
    try:
        if path.is_dir():
            os.chmod(path, dir_mode)
        else:
            os.chmod(path, file_mode)
    except OSError as e:
        logger.warning("chmod %s failed: %s", path, e)
    try:
        os.chown(path, _BOND_AGENT_UID, _BOND_AGENT_GID)
    except (OSError, PermissionError) as e:
        logger.debug("chown %s to %d:%d failed (ok if mode is world-readable): %s",
                     path, _BOND_AGENT_UID, _BOND_AGENT_GID, e)
    return path


def _agent_bind_data_root() -> Path:
    """Root for files & dirs that bond-bond bind-mounts into agent containers.

    The bind-mount source has to exist on the *host* docker daemon's filesystem,
    not just bond-bond's. When bond runs natively, ``_PROJECT_ROOT/data`` works
    on both. When bond runs containerized, ``_PROJECT_ROOT`` is ``/app`` — a
    path the host doesn't have, so docker silently materializes empty dirs and
    breaks the agent (Doc 112). Redirect to a path under ``BOND_HOST_HOME``,
    bind-mounted at the same path inside bond-bond by docker-compose.
    """
    override = os.environ.get("BOND_HOST_HOME")
    if override:
        root = Path(override) / ".bond" / "agent-data"
    else:
        root = _PROJECT_ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


_CLONE_URL_PREFIXES = ("git@", "http://", "https://", "ssh://", "git+", "git://")


def _looks_like_clone_url(value: str) -> bool:
    """True if the string looks like a git clone URL rather than a host path.

    Cheap heuristic — `git@host:owner/repo.git`, `https://...`, etc. would
    otherwise survive into a `docker -v` arg and break the colon-separated
    parse with "invalid mode" errors.
    """
    if not value:
        return False
    if value.startswith(_CLONE_URL_PREFIXES):
        return True
    # `git@github.com:owner/repo` form: `<no-slash>@<no-slash>:`
    head, sep, _ = value.partition(":")
    return bool(sep) and "@" in head and "/" not in head


# ---------------------------------------------------------------------------
# Data models (Design Doc 089 §4.2)
# ---------------------------------------------------------------------------


@dataclass
class ResourceLimits:
    memory_mb: int = 2048
    cpus: float = 2.0


@dataclass
class AgentContainerConfig:
    """Everything needed to create a container, decoupled from host paths."""

    agent_id: str
    sandbox_image: str
    repo_url: str | None = None
    repo_branch: str = "main"
    env_vars: dict[str, str] = field(default_factory=dict)
    ssh_private_key: str = ""  # Content, not path
    agent_config_json: str = ""  # Serialized config content
    vault_data: bytes | None = None
    shared_memory_snapshot: bytes | None = None
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)


@dataclass
class ContainerInfo:
    container_id: str
    host_id: str  # "local" or remote host ID
    worker_url: str  # How the gateway can reach the worker
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HostStatus:
    host_id: str
    online: bool
    cpu_percent: float = 0.0
    memory_available_mb: int = 0
    disk_available_gb: int = 0
    running_containers: int = 0
    max_agents: int = 4
    daemon_version: str = ""
    api_version: str = ""


# ---------------------------------------------------------------------------
# Protocol (Design Doc 089 §4.2)
# ---------------------------------------------------------------------------


@runtime_checkable
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


# ---------------------------------------------------------------------------
# LocalContainerAdapter (Design Doc 089 §4.3)
# ---------------------------------------------------------------------------


class LocalContainerAdapter:
    """Creates containers on the local Docker daemon (existing behavior)."""

    def __init__(self) -> None:
        self._port_map: dict[str, int] = {}

    # -- Port allocation --

    def _allocate_port(self, agent_key: str) -> int:
        if agent_key in self._port_map:
            return self._port_map[agent_key]
        used_ports = set(self._port_map.values())
        for port in range(_PORT_RANGE_START, _PORT_RANGE_END + 1):
            if port in used_ports:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("localhost", port)) != 0:
                    self._port_map[agent_key] = port
                    return port
        raise RuntimeError(
            f"No available ports in range {_PORT_RANGE_START}–{_PORT_RANGE_END}. "
            f"{len(self._port_map)} agents running."
        )

    def _release_port(self, agent_key: str) -> int | None:
        return self._port_map.pop(agent_key, None)

    async def reconcile_port_map(self) -> int:
        # Rebuild _port_map from running bond-agent-* containers. Without this,
        # after bond-bond restarts the in-memory map is empty and _allocate_port's
        # in-container localhost probe can't see host-side bindings, so it can
        # hand out a port already bound by another still-running agent.
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("reconcile_port_map: docker ps failed rc=%d", proc.returncode)
            return 0

        reconciled = 0
        for line in stdout.decode().splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, image, ports = parts[0], parts[1], parts[2]
            if not image.startswith("bond-agent-"):
                continue
            host_port: int | None = None
            for entry in ports.split(","):
                entry = entry.strip()
                if f"->{_WORKER_INTERNAL_PORT}/tcp" not in entry:
                    continue
                left = entry.split("->", 1)[0]
                if ":" not in left:
                    continue
                try:
                    host_port = int(left.rsplit(":", 1)[1])
                    break
                except ValueError:
                    continue
            if host_port is not None:
                self._port_map[name] = host_port
                reconciled += 1
                logger.info("reconcile_port_map: %s -> %d", name, host_port)
        return reconciled

    # -- Credential mounts (shared helper) --

    @staticmethod
    def _append_credential_mounts(
        cmd: list[str],
        workspace_mounts: list[dict] | None = None,
    ) -> None:
        """Append Claude Code credential and SSH mounts to a docker run command.

        Uses ``BOND_HOST_HOME`` (Doc 112) when set so that paths handed to the
        host docker daemon as bind-mount sources resolve on the host's
        filesystem, not on the container's. Falls back to ``Path.home()`` for
        native bond installs.
        """
        host_home = Path(os.environ.get("BOND_HOST_HOME") or str(Path.home()))

        claude_json = host_home / ".claude.json"
        if claude_json.exists():
            cmd.extend(["-v", f"{claude_json}:/home/bond-agent/.claude.json:ro"])

        claude_credentials = host_home / ".claude" / ".credentials.json"
        if claude_credentials.exists():
            cmd.extend(["-v", f"{claude_credentials}:/home/bond-agent/.claude/.credentials.json:rw"])

        claude_settings = host_home / ".claude" / "settings.json"
        if claude_settings.exists():
            cmd.extend(["-v", f"{claude_settings}:/home/bond-agent/.claude/settings.json:ro"])

        ssh_dir = host_home / ".ssh"
        workspace_targets = {m.get("container_path", "") for m in (workspace_mounts or [])}
        if ssh_dir.exists() and "/tmp/.ssh" not in workspace_targets:
            cmd.extend(["-v", f"{ssh_dir}:/tmp/.ssh:ro"])

    # -- Docker retry helper --

    async def _docker_run_with_conflict_retry(
        self,
        cmd: list[str],
        container_name: str,
        agent_id: str,
        max_retries: int = 5,
    ) -> bytes:
        last_err_msg = ""
        stdout = b""
        for attempt in range(max_retries + 1):
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout
            last_err_msg = stderr.decode()
            if "Conflict" in last_err_msg and attempt < max_retries:
                backoff = 2 ** attempt
                logger.warning(
                    "Container name conflict for agent %s (attempt %d/%d), "
                    "removing stale container %s and retrying in %ds",
                    agent_id, attempt + 1, max_retries, container_name, backoff,
                )
                rm_proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await rm_proc.communicate()
                await asyncio.sleep(backoff)
                continue
            break
        logger.error("Failed to create container for agent %s: %s", agent_id, last_err_msg)
        raise RuntimeError(f"Failed to create container for agent {agent_id}: {last_err_msg}")

    # -- ContainerHostAdapter implementation --

    async def create_container(
        self,
        agent: dict,
        key: str,
        config: AgentContainerConfig,
    ) -> ContainerInfo:
        """Create a Docker container running the agent worker (local)."""
        agent_id = agent["id"]
        sandbox_image = agent["sandbox_image"]
        port = self._allocate_port(key)

        # Write agent config
        config_path = self._write_agent_config(agent)

        # Remove stale container
        rm_proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await rm_proc.communicate()

        cmd = [
            "docker", "run", "-d",
            "--name", key,
            "--network", "bond-network",
            "--memory", f"{config.resource_limits.memory_mb}m",
            "--cpus", str(config.resource_limits.cpus),
        ]

        cmd.extend(["-p", f"{port}:{_WORKER_INTERNAL_PORT}"])
        cmd.extend(["-e", "PYTHONPATH=/bond"])
        cmd.extend(["-e", "BOND_API_URL=http://host.docker.internal:18790"])
        cmd.extend(["--add-host", "host.docker.internal:host-gateway"])

        # Forward .env file
        _env_file = _PROJECT_ROOT / ".env"
        if _env_file.is_file():
            cmd.extend(["--env-file", str(_env_file)])

        # SpacetimeDB URL
        stdb_url = os.environ.get("BOND_SPACETIMEDB_URL", "")
        if stdb_url:
            cmd.extend(["-e", f"BOND_SPACETIMEDB_URL={stdb_url}"])

        # Agent identity & repo env vars
        cmd.extend(["-e", f"AGENT_NAME=bond-agent-{agent_id}"])
        cmd.extend(["-e", f"AGENT_EMAIL=agent-{agent_id}@bond.internal"])
        cmd.extend(["-e", "BOND_REPO_URL=git@github.com:biztechprogramming/bond.git"])

        # API keys
        api_keys = agent.get("api_keys", {})
        for provider_id, key_value in api_keys.items():
            if key_value:
                env_var = f"{provider_id.upper()}_API_KEY"
                cmd.extend(["-e", f"{env_var}={key_value}"])

        # GitHub token from vault
        try:
            from backend.app.core.vault import get_vault
            vault = get_vault()
            github_token = vault.get("github.token")
            if github_token:
                cmd.extend(["-e", f"GITHUB_TOKEN={github_token}"])
        except Exception:
            pass

        # Broker token for MCP proxy access
        try:
            from backend.app.config import get_settings
            settings = get_settings()
            gateway_url = os.environ.get(
                "BOND_GATEWAY_URL",
                f"{settings.gateway_scheme}://{settings.gateway_host}:{settings.gateway_port}",
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{gateway_url}/api/v1/broker/token/issue",
                    json={"agent_id": agent_id, "ttl": 86400},
                )
                if resp.status_code == 200:
                    agent_token = resp.json().get("token", "")
                    if agent_token:
                        cmd.extend(["-e", f"BOND_AGENT_TOKEN={agent_token}"])
                        # Persist for bond-bond → worker callbacks (Design Doc 116 §3.8)
                        from backend.app.sandbox.agent_tokens import save_agent_token
                        save_agent_token(agent_id, agent_token)
                        logger.info("Injected broker token for agent %s", agent_id)
                else:
                    logger.warning("Failed to get broker token: %d %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Could not issue broker token for agent %s: %s", agent_id, e)

        # --- Mounts ---
        project_root = _PROJECT_ROOT
        agent_name = agent.get("name", "")
        is_deploy_agent = agent_name.startswith("deploy-")
        # Dev mode (BOND_DEV_MOUNT_SOURCE): bind-mount the host working tree at
        # /bond so local edits are live inside agents — no commit/push/re-clone.
        # Only meaningful when bond-bond runs natively (project_root is a real
        # host path); a containerized bond-bond would mount its own /app.
        dev_mount_source = bool(os.environ.get("BOND_DEV_MOUNT_SOURCE")) and "BOND_HOST_HOME" not in os.environ

        if is_deploy_agent:
            cmd.extend(["-v", f"{project_root}:/bond:ro"])
        elif dev_mount_source:
            # rw (not ro) so the worker can write __pycache__ etc.; the
            # entrypoint skips git fetch/reset (BOND_DEV_SKIP_GIT) so this
            # working tree is never reset --hard out from under the developer.
            cmd.extend(["-v", f"{project_root}:/bond:rw"])
            cmd.extend(["-e", "BOND_DEV_SKIP_GIT=1"])
            logger.info("Dev mode: mounting host source %s at /bond for agent %s", project_root, agent_id)
        else:
            bond_volume = f"bond-clone-{agent_id}"
            await asyncio.create_subprocess_exec(
                "docker", "volume", "create", bond_volume,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            cmd.extend(["-v", f"{bond_volume}:/bond:rw"])

        # Workspace mounts with concurrent cloning (Design Doc 057)
        workspace_mounts = agent.get("workspace_mounts", [])
        clone_info: list[dict] = []
        dep_install_script: str | None = None

        if workspace_mounts:
            mount_configs = []
            for mount in workspace_mounts:
                host_path = os.path.expanduser(mount.get("host_path", ""))
                mount_name = mount.get("mount_name", "workspace")
                container_path = mount.get("container_path") or f"/workspace/{mount_name}"
                readonly = mount.get("readonly", False)
                mount_configs.append((host_path, mount_name, container_path, readonly))

            # Phase 1: Detect workspace types in parallel
            rw_mounts = [(i, hp, mn) for i, (hp, mn, _cp, ro) in enumerate(mount_configs) if not ro]
            detections: dict[int, dict] = {}
            if rw_mounts:
                detect_results = await asyncio.gather(
                    *[detect_workspace_type(hp) for _, hp, _ in rw_mounts],
                    return_exceptions=True,
                )
                for (idx, _hp, _mn), result in zip(rw_mounts, detect_results):
                    if not isinstance(result, Exception):
                        detections[idx] = result

            # Phase 2: Generate clone plans
            plans: dict[int, object] = {}
            plan_coros = []
            plan_indices = []
            for idx, detection in detections.items():
                hp, mn, _cp, _ro = mount_configs[idx]
                plan_coros.append(generate_clone_plan(hp, agent_id, mn, detection))
                plan_indices.append(idx)
            if plan_coros:
                plan_results = await asyncio.gather(*plan_coros, return_exceptions=True)
                for idx, result in zip(plan_indices, plan_results):
                    if not isinstance(result, Exception):
                        plans[idx] = result

            # Phase 3: Execute clone plans in parallel
            exec_coros = []
            exec_indices = []
            for idx, plan in plans.items():
                if not plan.direct_mount:
                    exec_coros.append(execute_clone_plan(plan))
                    exec_indices.append(idx)
            if exec_coros:
                exec_results = await asyncio.gather(*exec_coros, return_exceptions=True)
                for idx, result in zip(exec_indices, exec_results):
                    if isinstance(result, Exception):
                        hp = mount_configs[idx][0]
                        raise RuntimeError(
                            f"Workspace clone failed for {hp} — refusing to start container "
                            f"with wrong mount. Original error: {result}"
                        )

            # Phase 4: Build mount commands
            for i, (host_path, mount_name, container_path, readonly) in enumerate(mount_configs):
                effective_host_path = host_path
                if i in plans and not plans[i].direct_mount:
                    plan = plans[i]
                    if plan.clone_base:
                        effective_host_path = plan.clone_base
                    clone_info.append({
                        "mount_name": mount_name,
                        "original_path": host_path,
                        "clone_path": effective_host_path,
                        "case": plan.case,
                    })
                    if dep_install_script is None:
                        dep_install_script = generate_dep_install_script(effective_host_path)

                # Reject anything that isn't a real filesystem path. A leftover
                # workspace_mounts row with host_path = "git@github.com:foo/bar"
                # would otherwise get passed to `docker -v` and split as
                # src=git@github.com / dst=foo/bar / mode=/workspace/... which
                # docker rejects with "invalid mode". Doc 113 migrates these to
                # agent_repos; this is the belt-and-suspenders.
                if not effective_host_path or _looks_like_clone_url(effective_host_path):
                    logger.warning(
                        "Skipping workspace_mount %r for agent %s: host_path %r "
                        "is not a filesystem path (likely a stale pre-doc-113 row)",
                        mount_name, agent_id, effective_host_path,
                    )
                    continue

                mount_str = f"{effective_host_path}:{container_path}"
                if readonly:
                    mount_str += ":ro"
                cmd.extend(["-v", mount_str])

        # Agent data directory. Use _agent_bind_data_root() so the source path
        # resolves on both bond-bond and the host docker daemon (Doc 112).
        data_root = _agent_bind_data_root()

        agent_data_dir = data_root / "agents" / agent_id
        os.makedirs(str(agent_data_dir), exist_ok=True)
        # bond-agent (uid 1000) writes the agent sqlite db here at startup —
        # chown so it can.
        _agent_readable(agent_data_dir)
        cmd.extend(["-v", f"{agent_data_dir}:/data:rw"])

        # Shared memory
        shared_dir = data_root / "shared"
        os.makedirs(str(shared_dir), exist_ok=True)
        _agent_readable(shared_dir)
        cmd.extend(["-v", f"{shared_dir}:/data/shared:ro"])

        # Skills DB
        skills_db = data_root / "skills.db"
        if skills_db.exists():
            _agent_readable(skills_db)
            cmd.extend(["-v", f"{skills_db}:/data/skills.db:rw"])

        # Credentials + SSH
        self._append_credential_mounts(cmd, workspace_mounts)

        # Agent repos: per-repo named volumes + /config/repos.json (Design Doc 113)
        await self._prepare_agent_repos(agent_id, cmd)

        # Agent config file
        cmd.extend(["-v", f"{config_path}:/config/agent.json:ro"])

        # Shared images directory — mount the bond-data volume's images dir
        # into the agent so generated images are accessible to the gateway.
        from backend.app.config import get_settings
        bond_home = Path(get_settings().bond_home)
        shared_images_dir = bond_home / "images"
        os.makedirs(str(shared_images_dir), exist_ok=True)
        cmd.extend(["-v", f"{shared_images_dir}:/data/images:rw"])

        # Vault data
        vault_data_dir = bond_home / "data"
        if vault_data_dir.exists():
            cmd.extend(["-v", f"{vault_data_dir}:/bond-home/data:rw"])

        # Entrypoint
        cmd.extend([
            sandbox_image,
            "--port", str(_WORKER_INTERNAL_PORT),
            "--data-dir", "/data",
            "--config", "/config/agent.json",
        ])

        stdout = await self._docker_run_with_conflict_retry(cmd, key, agent_id)
        container_id = stdout.decode().strip()[:12]
        logger.info(
            "Created worker container %s for agent %s (port=%d, image=%s)",
            container_id, agent_id, port, sandbox_image,
        )

        return ContainerInfo(
            container_id=container_id,
            host_id="local",
            worker_url=f"http://localhost:{port}",
        ), clone_info, dep_install_script

    async def destroy_container(self, key: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        self._release_port(key)
        return proc.returncode == 0

    async def is_running(self, key: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().lower() == "true"

    async def get_logs(self, key: str, tail: int = 50) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "logs", key, "--tail", str(tail),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return (stdout + stderr).decode(errors="replace")
        except Exception:
            return "<failed to capture logs>"

    async def get_worker_url(self, key: str) -> str:
        port = self._port_map.get(key)
        if port is None:
            raise RuntimeError(f"No port allocated for {key}")
        return f"http://localhost:{port}"

    async def health(self) -> HostStatus:
        return HostStatus(
            host_id="local",
            online=True,
            running_containers=len(self._port_map),
            max_agents=_PORT_RANGE_END - _PORT_RANGE_START + 1,
        )

    # -- Agent config helper --

    async def _prepare_agent_repos(self, agent_id: str, cmd: list[str]) -> None:
        """Prepare agent_repos clones (Design Doc 113).

        Fetches the agent's repo rows + resolved credentials, writes a config
        file at data/agent-configs/{agent_id}.repos.json, mounts it read-only
        at /config/repos.json, and creates one Docker named volume per repo
        mounted at /workspace/{name}.

        Robust to a not-yet-deployed schema: if the agent_repos table does
        not exist, logs a warning and returns without altering cmd.
        """
        from backend.app.core.spacetimedb import get_stdb
        from backend.app.core.vault import Vault
        from backend.app.sandbox.git_credentials import resolve_credential

        stdb = get_stdb()
        try:
            repo_rows = await stdb.query(
                f"SELECT * FROM agent_repos WHERE agent_id = '{agent_id}'"
            )
        except Exception as e:
            logger.warning(
                "Could not fetch agent_repos for %s (table may not be deployed yet): %s",
                agent_id, e,
            )
            return

        if not repo_rows:
            return

        try:
            cred_rows = await stdb.query("SELECT * FROM git_credentials")
        except Exception:
            cred_rows = []

        vault = Vault()
        repos_config: list[dict] = []

        for repo in repo_rows:
            cred = resolve_credential(
                repo["url"], repo.get("credential_id", ""), cred_rows
            )
            cred_block: dict | None = None
            if cred:
                secret = vault.get(cred["secret_ref"]) or ""
                if secret:
                    cred_block = {
                        "auth_type": cred["auth_type"],
                        "secret": secret,
                        "username": cred.get("username") or "",
                    }
                else:
                    logger.warning(
                        "Credential %s has no secret in vault; clone for %s will be unauthenticated",
                        cred["id"], repo["url"],
                    )

            repos_config.append({
                "id": repo["id"],
                "name": repo["name"],
                "url": repo["url"],
                "default_branch": repo["default_branch"] or "main",
                "active_branch": repo.get("active_branch") or "",
                "credential": cred_block,
            })

            # Create the named volume for this repo (idempotent)
            volume_name = f"bond-repo-{agent_id}-{repo['id']}"
            await asyncio.create_subprocess_exec(
                "docker", "volume", "create", volume_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            cmd.extend(["-v", f"{volume_name}:/workspace/{repo['name']}:rw"])

        # Write the repos config file
        config_dir = _agent_bind_data_root() / "agent-configs"
        os.makedirs(str(config_dir), mode=0o755, exist_ok=True)
        _agent_readable(config_dir)
        repos_path = config_dir / f"{agent_id}.repos.json"

        if repos_path.is_dir():
            shutil.rmtree(repos_path)

        fd = os.open(str(repos_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, json.dumps({"repos": repos_config}, indent=2).encode())
        finally:
            os.close(fd)
        _agent_readable(repos_path)

        cmd.extend(["-v", f"{repos_path}:/config/repos.json:ro"])
        logger.info(
            "Prepared %d agent_repos clone(s) for agent %s", len(repos_config), agent_id,
        )

    def _write_agent_config(self, agent: dict) -> Path:
        agent_id = agent["id"]
        config_dir = _agent_bind_data_root() / "agent-configs"
        os.makedirs(str(config_dir), mode=0o755, exist_ok=True)
        _agent_readable(config_dir)
        config_path = config_dir / f"{agent_id}.json"

        if config_path.is_dir():
            shutil.rmtree(config_path)

        config_data = {
            "agent_id": agent_id,
            "model": agent["model"],
            "system_prompt": agent["system_prompt"],
            "tools": agent["tools"],
            "max_iterations": agent["max_iterations"],
            "utility_model": agent.get("utility_model", "claude-sonnet-4-6"),
            "api_keys": agent.get("api_keys", {}),
            "provider_aliases": agent.get("provider_aliases", {}),
            "litellm_prefixes": agent.get("litellm_prefixes", {}),
        }

        fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, json.dumps(config_data, indent=2).encode())
        finally:
            os.close(fd)
        _agent_readable(config_path)

        return config_path

    def _delete_agent_config(self, agent_id: str) -> None:
        config_path = _agent_bind_data_root() / "agent-configs" / f"{agent_id}.json"
        try:
            if config_path.is_dir():
                shutil.rmtree(config_path)
            else:
                config_path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- Recovery --

    async def recover_existing_container(
        self, key: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if a container exists in Docker and recover it."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f",
            "{{.State.Running}} {{(index (index .NetworkSettings.Ports \"18791/tcp\") 0).HostPort}}",
            key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return None

        parts = stdout.decode().strip().split()
        if len(parts) < 2:
            return None

        is_running = parts[0].lower() == "true"
        host_port = int(parts[1])

        if not is_running:
            await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return None

        self._port_map[key] = host_port
        worker_url = f"http://localhost:{host_port}"
        cid_proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.Id}}", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        cid_out, _ = await cid_proc.communicate()
        container_id = cid_out.decode().strip()[:12]

        return {
            "container_id": container_id,
            "worker_url": worker_url,
            "host_id": "local",
        }

    # -- Host-mode container (backward compat) --

    async def get_or_create_host_container(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None = None,
        agent_name: str = "agent",
    ) -> str:
        """Create a host-mode container (sleep infinity). Returns container_id."""
        slug = agent_name.lower().replace(" ", "-")
        key = f"bond-{slug}-{agent_id}"

        # Check existing
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-aq", "--filter", f"name={key}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        existing_id = stdout.decode().strip()
        if existing_id:
            rm_proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", existing_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await rm_proc.communicate()

        cmd = [
            "docker", "run", "-d",
            "--name", key,
            "--network", "bond-network",
            "--memory", "2048m",
            "--cpus", "2",
        ]

        if workspace_mounts:
            for mount in workspace_mounts:
                host_path = os.path.expanduser(mount.get("host_path", ""))
                mount_name = mount.get("mount_name", "workspace")
                container_path = mount.get("container_path") or f"/workspace/{mount_name}"
                readonly = mount.get("readonly", False)
                mount_str = f"{host_path}:{container_path}"
                if readonly:
                    mount_str += ":ro"
                cmd.extend(["-v", mount_str])

        self._append_credential_mounts(cmd, workspace_mounts)
        cmd.extend([sandbox_image, "sleep", "infinity"])

        stdout = await self._docker_run_with_conflict_retry(cmd, key, agent_id)
        container_id = stdout.decode().strip()[:12]

        # SSH setup
        await self._setup_ssh(container_id)

        return container_id

    async def _setup_ssh(self, container_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-u", "root", container_id,
            "sh", "-c",
            "if [ -d /tmp/.ssh ]; then "
            "  USER_HOME=$(getent passwd $(docker inspect --format '{{.Config.User}}' 2>/dev/null || echo node) | cut -d: -f6 || echo /home/node); "
            "  mkdir -p $USER_HOME/.ssh /root/.ssh; "
            "  cp -r /tmp/.ssh/* /root/.ssh/ 2>/dev/null; "
            "  cp -r /tmp/.ssh/* $USER_HOME/.ssh/ 2>/dev/null; "
            "  chmod 700 /root/.ssh $USER_HOME/.ssh 2>/dev/null; "
            "  chmod 600 /root/.ssh/* $USER_HOME/.ssh/* 2>/dev/null; "
            "  chown -R $(stat -c '%u:%g' $USER_HOME) $USER_HOME/.ssh 2>/dev/null; "
            "  echo ssh_setup_done; "
            "fi",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if b"ssh_setup_done" in stdout:
            logger.info("SSH keys configured in container %s", container_id)
