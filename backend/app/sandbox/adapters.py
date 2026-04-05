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

    # -- Credential mounts (shared helper) --

    @staticmethod
    def _append_credential_mounts(
        cmd: list[str],
        workspace_mounts: list[dict] | None = None,
    ) -> None:
        """Append Claude Code credential and SSH mounts to a docker run command."""
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            cmd.extend(["-v", f"{claude_json}:/home/bond-agent/.claude.json:ro"])

        claude_credentials = Path.home() / ".claude" / ".credentials.json"
        if claude_credentials.exists():
            cmd.extend(["-v", f"{claude_credentials}:/home/bond-agent/.claude/.credentials.json:rw"])

        claude_settings = Path.home() / ".claude" / "settings.json"
        if claude_settings.exists():
            cmd.extend(["-v", f"{claude_settings}:/home/bond-agent/.claude/settings.json:ro"])

        ssh_dir = Path.home() / ".ssh"
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

        # Pass gateway API key so the worker's persistence client can authenticate
        bond_api_key = os.environ.get("BOND_API_KEY", "")
        if not bond_api_key:
            key_path = Path.home() / ".bond" / "data" / ".gateway_key"
            if key_path.exists():
                bond_api_key = key_path.read_text().strip()
        if bond_api_key:
            cmd.extend(["-e", f"BOND_API_KEY={bond_api_key}"])

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
                        logger.info("Injected broker token for agent %s", agent_id)
                else:
                    logger.warning("Failed to get broker token: %d %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Could not issue broker token for agent %s: %s", agent_id, e)

        # --- Mounts ---
        project_root = _PROJECT_ROOT
        agent_name = agent.get("name", "")
        is_deploy_agent = agent_name.startswith("deploy-")

        if is_deploy_agent:
            cmd.extend(["-v", f"{project_root}:/bond:ro"])
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

                mount_str = f"{effective_host_path}:{container_path}"
                if readonly:
                    mount_str += ":ro"
                cmd.extend(["-v", mount_str])

        # Agent data directory
        agent_data_dir = _PROJECT_ROOT / "data" / "agents" / agent_id
        os.makedirs(str(agent_data_dir), exist_ok=True)
        cmd.extend(["-v", f"{agent_data_dir}:/data:rw"])

        # Shared memory
        shared_dir = project_root / "data" / "shared"
        os.makedirs(str(shared_dir), exist_ok=True)
        cmd.extend(["-v", f"{shared_dir}:/data/shared:ro"])

        # Skills DB
        skills_db = project_root / "data" / "skills.db"
        if skills_db.exists():
            cmd.extend(["-v", f"{skills_db}:/data/skills.db:rw"])

        # Credentials + SSH
        self._append_credential_mounts(cmd, workspace_mounts)

        # Agent config file
        cmd.extend(["-v", f"{config_path}:/config/agent.json:ro"])

        # Vault data
        from backend.app.config import get_settings
        bond_home = Path(get_settings().bond_home)
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

    def _write_agent_config(self, agent: dict) -> Path:
        agent_id = agent["id"]
        config_dir = _PROJECT_ROOT / "data" / "agent-configs"
        os.makedirs(str(config_dir), mode=0o700, exist_ok=True)
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

        fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config_data, indent=2).encode())
        finally:
            os.close(fd)

        return config_path

    def _delete_agent_config(self, agent_id: str) -> None:
        config_path = _PROJECT_ROOT / "data" / "agent-configs" / f"{agent_id}.json"
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
