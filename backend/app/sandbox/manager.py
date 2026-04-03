"""SandboxManager — Docker container lifecycle and code execution.

Refactored to use ContainerHostAdapter abstraction (Design Doc 089).
Delegates container operations to LocalContainerAdapter or RemoteContainerAdapter
based on HostRegistry placement decisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from backend.app.sandbox.adapters import (
    AgentContainerConfig,
    ContainerInfo,
    LocalContainerAdapter,
    ResourceLimits,
    _PROJECT_ROOT,
    _PORT_RANGE_END,
    _PORT_RANGE_START,
    _WORKER_INTERNAL_PORT,
)
from backend.app.sandbox.host_registry import HostRegistry, LocalHost, RemoteHost
from backend.app.sandbox.tunnel_manager import TunnelManager
from backend.app.sandbox.workspace_cloner import (
    cleanup_workspace_clones,
    detect_workspace_type,
    execute_clone_plan,
    generate_clone_plan,
    generate_dep_install_script,
)

logger = logging.getLogger("bond.sandbox.manager")


class SandboxManager:
    """Manages persistent Docker containers for agent code execution.

    Uses HostRegistry for placement decisions and ContainerHostAdapter
    for container lifecycle operations on local or remote hosts.
    """

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, Any]] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}

        # Load config for remote hosts
        from backend.app.config import load_bond_json
        config = load_bond_json()

        # Host registry and adapters (Design Doc 089)
        self._registry = HostRegistry(config)
        self._tunnel_manager = TunnelManager()
        self._local_adapter = LocalContainerAdapter()
        self._remote_adapters: dict[str, Any] = {}  # host_id -> RemoteContainerAdapter

    # -- Adapter resolution --

    def _get_adapter(self, host: RemoteHost | LocalHost) -> Any:
        """Get the appropriate adapter for a host."""
        if isinstance(host, LocalHost) or host.id == "local":
            return self._local_adapter

        if host.id not in self._remote_adapters:
            from backend.app.sandbox.remote_adapter import RemoteContainerAdapter
            self._remote_adapters[host.id] = RemoteContainerAdapter(
                host, self._tunnel_manager
            )
        return self._remote_adapters[host.id]

    def _adapter_for_container(self, container_info: dict) -> Any:
        """Get adapter for an existing tracked container."""
        host_id = container_info.get("host_id", "local")
        host = self._registry.get_host(host_id)
        if host is None:
            return self._local_adapter
        return self._get_adapter(host)

    # -- Port allocation (delegated to local adapter for backward compat) --

    @property
    def _port_map(self) -> dict[str, int]:
        """Backward compat: expose local adapter's port map."""
        return self._local_adapter._port_map

    @_port_map.setter
    def _port_map(self, value: dict[str, int]) -> None:
        self._local_adapter._port_map = value

    def _allocate_port(self, agent_key: str) -> int:
        return self._local_adapter._allocate_port(agent_key)

    def _release_port(self, agent_key: str) -> int | None:
        return self._local_adapter._release_port(agent_key)

    # -- Per-agent lock --

    def _get_agent_lock(self, agent_key: str) -> asyncio.Lock:
        if agent_key not in self._agent_locks:
            self._agent_locks[agent_key] = asyncio.Lock()
        return self._agent_locks[agent_key]

    # -- Agent config generation --

    def _write_agent_config(self, agent: dict) -> Path:
        return self._local_adapter._write_agent_config(agent)

    def _delete_agent_config(self, agent_id: str) -> None:
        self._local_adapter._delete_agent_config(agent_id)

    # -- Health wait --

    async def _wait_for_health(
        self,
        worker_url: str,
        agent_id: str,
        container_id: str,
        timeout: float = 30.0,
        interval: float = 0.5,
    ) -> None:
        """Poll worker /health until it responds with correct agent_id, or raise."""
        start = time.monotonic()
        last_error = ""

        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    logs = await self._capture_container_logs(container_id)
                    logger.error(
                        "Health check timeout for agent %s after %.1fs — container logs:\n%s",
                        agent_id, elapsed, logs,
                    )
                    raise RuntimeError(
                        f"Health check timeout for agent {agent_id} after {elapsed:.1f}s. "
                        f"Last error: {last_error}\nContainer logs:\n{logs}"
                    )

                try:
                    resp = await client.get(f"{worker_url}/health")
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") == "ok" and data.get("agent_id") == agent_id:
                            logger.info(
                                "Worker healthy for agent %s in %.1fs (container=%s)",
                                agent_id, time.monotonic() - start, container_id,
                            )
                            return
                        last_error = f"Unexpected health response: {data}"
                    else:
                        last_error = f"HTTP {resp.status_code}"
                except httpx.ConnectError:
                    last_error = "Connection refused"
                except httpx.HTTPError as exc:
                    last_error = str(exc)

                await asyncio.sleep(interval)

    async def _recover_existing_container(
        self, key: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if a container exists in Docker and recover it."""
        result = await self._local_adapter.recover_existing_container(key, agent_id)
        if result is None:
            return None

        worker_url = result["worker_url"]
        container_id = result["container_id"]

        try:
            await self._wait_for_health(worker_url, agent_id, container_id, timeout=5.0)
        except RuntimeError:
            logger.warning("Recovered container %s unhealthy, removing", key)
            await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return None

        self._containers[key] = {
            "container_id": container_id,
            "worker_url": worker_url,
            "worker_port": self._local_adapter._port_map.get(key),
            "host_id": "local",
            "last_used": time.time(),
        }
        logger.info(
            "Recovered running container %s for agent %s",
            container_id, agent_id,
        )
        return {"worker_url": worker_url, "container_id": container_id}

    async def _capture_container_logs(self, container_id: str, tail: int = 50) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "logs", container_id, "--tail", str(tail),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return (stdout + stderr).decode(errors="replace")
        except Exception:
            return "<failed to capture logs>"

    # ------------------------------------------------------------------
    # ensure_running — host-aware (Design Doc 089 §7.1)
    # ------------------------------------------------------------------

    async def ensure_running(self, agent: dict) -> dict[str, Any]:
        """Ensure agent's containerized worker is running.

        Returns {"worker_url": "http://localhost:{port}", "container_id": "abc123"}.
        Raises RuntimeError if container fails to start or health check times out.
        """
        agent_id = agent["id"]
        agent_name = agent.get("name", "agent").lower().replace(" ", "-")
        key = f"bond-{agent_name}-{agent_id}"
        lock = self._get_agent_lock(key)

        async with lock:
            # Normalize current mounts for comparison
            current_mounts = sorted(
                [repr({k: (v if v is not None else '') for k, v in m.items()})
                 for m in agent.get("workspace_mounts", [])],
            )

            # Config fingerprint
            api_keys_hash = hashlib.sha256(
                json.dumps(sorted(agent.get("api_keys", {}).items()), separators=(",", ":")).encode()
            ).hexdigest()[:16]
            current_config_fingerprint = (
                f"{agent.get('model', '')}|{agent.get('utility_model', '')}|{api_keys_hash}"
            )

            # Check if already running + healthy
            if key in self._containers:
                info = self._containers[key]
                cid = info["container_id"]
                worker_url = info.get("worker_url", "")
                tracked_mounts = info.get("mounts", [])
                tracked_config = info.get("config_fingerprint", "")
                host_id = info.get("host_id", "local")

                if tracked_mounts != current_mounts:
                    logger.info("Agent %s mounts changed, recreating worker container %s", agent_id, key)
                    await self.destroy_agent_container(agent_id, keep_clones=True)
                elif tracked_config and tracked_config != current_config_fingerprint:
                    logger.info(
                        "Agent %s config changed (was: %s, now: %s), recreating worker container %s",
                        agent_id, tracked_config, current_config_fingerprint, key,
                    )
                    await self.destroy_agent_container(agent_id, keep_clones=True)
                elif await self._is_running(cid, host_id):
                    try:
                        await self._wait_for_health(worker_url, agent_id, cid, timeout=5.0)
                        self._containers[key]["last_used"] = time.time()
                        return {"worker_url": worker_url, "container_id": cid}
                    except RuntimeError:
                        logger.warning(
                            "Worker unhealthy in running container %s for agent %s, destroying",
                            cid, agent_id,
                        )
                        await self.destroy_agent_container(agent_id, keep_clones=True)
                else:
                    logger.warning(
                        "Container %s for agent %s died, recreating",
                        cid, agent_id,
                    )
                    await self.destroy_agent_container(agent_id, keep_clones=True)
            else:
                # Not in memory — check Docker directly (after backend restart)
                existing = await self._recover_existing_container(key, agent_id)
                if existing:
                    if key in self._containers:
                        self._containers[key]["mounts"] = current_mounts
                        self._containers[key]["config_fingerprint"] = current_config_fingerprint
                    return existing

            # Determine placement (Design Doc 089 §3.2)
            host = await self._registry.get_placement(agent)
            adapter = self._get_adapter(host)

            # Build host-path-independent config
            container_config = self._build_container_config(agent)

            # Create container on target host
            config_path: Path | None = None
            port: int | None = None
            try:
                if isinstance(host, LocalHost) or host.id == "local":
                    # Local path: use _create_worker_container (preserves test compat)
                    port = self._allocate_port(key)
                    config_path = self._write_agent_config(agent)

                    container_id, clone_info, dep_script = await self._create_worker_container(
                        agent, key, port, config_path,
                    )
                    worker_url = f"http://localhost:{port}"

                    self._containers[key] = {
                        "container_id": container_id,
                        "worker_url": worker_url,
                        "worker_port": port,
                        "host_id": "local",
                        "last_used": time.time(),
                        "mounts": current_mounts,
                        "config_fingerprint": current_config_fingerprint,
                        "clone_info": clone_info,
                        "dep_install_script": dep_script,
                        "deps_installed": False,
                    }

                    await self._wait_for_health(worker_url, agent_id, container_id)

                    self._registry.increment_running("local")
                    return {"worker_url": worker_url, "container_id": container_id}
                else:
                    # Remote path
                    container_info = await adapter.create_container(agent, key, container_config)

                    self._containers[key] = {
                        "container_id": container_info.container_id,
                        "worker_url": container_info.worker_url,
                        "host_id": container_info.host_id,
                        "last_used": time.time(),
                        "mounts": current_mounts,
                        "config_fingerprint": current_config_fingerprint,
                        "clone_info": [],
                        "dep_install_script": None,
                        "deps_installed": False,
                    }

                    await self._wait_for_health(
                        container_info.worker_url, agent_id, container_info.container_id
                    )

                # Update registry running count
                self._registry.increment_running(container_info.host_id)

                return {
                    "worker_url": container_info.worker_url,
                    "container_id": container_info.container_id,
                }

            except Exception:
                logger.error(
                    "Failed to create container for agent %s: %s",
                    agent_id, str(asyncio.current_task()),
                )
                if key in self._containers:
                    del self._containers[key]
                self._release_port(key)
                self._delete_agent_config(agent_id)
                raise

    def _build_container_config(self, agent: dict) -> AgentContainerConfig:
        """Build a host-path-independent container config."""
        agent_id = agent["id"]

        # Read shared memory snapshot for remote hosts
        shared_snapshot = None
        shared_dir = _PROJECT_ROOT / "data" / "shared" / "shared.db"
        if shared_dir.exists():
            try:
                shared_snapshot = shared_dir.read_bytes()
            except Exception:
                pass

        return AgentContainerConfig(
            agent_id=agent_id,
            sandbox_image=agent.get("sandbox_image", "bond-agent-worker"),
            repo_url=agent.get("repo_url"),
            repo_branch=agent.get("repo_branch", "main"),
            env_vars=agent.get("env_vars", {}),
            agent_config_json=json.dumps({
                "agent_id": agent_id,
                "model": agent["model"],
                "system_prompt": agent["system_prompt"],
                "tools": agent["tools"],
                "max_iterations": agent["max_iterations"],
                "utility_model": agent.get("utility_model", "claude-sonnet-4-6"),
                "api_keys": agent.get("api_keys", {}),
                "provider_aliases": agent.get("provider_aliases", {}),
                "litellm_prefixes": agent.get("litellm_prefixes", {}),
            }),
            shared_memory_snapshot=shared_snapshot,
            resource_limits=ResourceLimits(
                memory_mb=agent.get("memory_mb", 2048),
                cpus=agent.get("cpus", 2.0),
            ),
        )

    # ------------------------------------------------------------------
    # Lazy dependency installation
    # ------------------------------------------------------------------

    async def ensure_deps_installed(self, agent_key_or_id: str) -> dict:
        key = None
        for k in self._containers:
            if k == agent_key_or_id or k.endswith(agent_key_or_id):
                key = k
                break

        if key is None:
            return {"installed": False, "output": "No container found"}

        info = self._containers[key]

        if info.get("deps_installed", False):
            return {"installed": False, "output": "Already installed"}

        script = info.get("dep_install_script")
        if not script:
            return {"installed": False, "output": "No dependencies detected"}

        container_id = info["container_id"]
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "sh", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = (stdout + stderr).decode(errors="replace")

        if proc.returncode == 0:
            self._containers[key]["deps_installed"] = True
            logger.info("Installed dependencies in container %s", container_id)
            return {"installed": True, "output": output}
        else:
            logger.warning("Dependency install failed in %s: %s", container_id, output)
            return {"installed": False, "output": output}

    # ------------------------------------------------------------------
    # Shared credential mounts (backward compat)
    # ------------------------------------------------------------------

    @staticmethod
    def _append_credential_mounts(
        cmd: list[str],
        workspace_mounts: list[dict] | None = None,
    ) -> None:
        LocalContainerAdapter._append_credential_mounts(cmd, workspace_mounts)

    # ------------------------------------------------------------------
    # Host-mode container (backward compat — Task 7)
    # ------------------------------------------------------------------

    async def get_or_create_container(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None = None,
        agent_name: str = "agent",
    ) -> str:
        slug = agent_name.lower().replace(" ", "-")
        key = f"bond-{slug}-{agent_id}"
        lock = self._get_agent_lock(key)

        async with lock:
            return await self._get_or_create_container_inner(
                agent_id, sandbox_image, workspace_mounts, agent_name, key
            )

    async def _get_or_create_container_inner(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None,
        agent_name: str,
        key: str,
    ) -> str:
        current_mounts = sorted(
            [repr({k: (v if v is not None else '') for k, v in m.items()})
             for m in (workspace_mounts or [])],
        )

        if key in self._containers:
            cid = self._containers[key]["container_id"]
            tracked_mounts = self._containers[key].get("mounts", [])
            if await self._is_running(cid):
                if tracked_mounts == current_mounts:
                    self._containers[key]["last_used"] = time.time()
                    return cid
                logger.info("Agent %s mounts changed, recreating container %s", agent_id, key)
                rm_proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", cid,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await rm_proc.communicate()
            del self._containers[key]

        container_id = await self._local_adapter.get_or_create_host_container(
            agent_id, sandbox_image, workspace_mounts, agent_name
        )

        self._containers[key] = {
            "container_id": container_id,
            "host_id": "local",
            "last_used": time.time(),
            "mounts": current_mounts,
        }
        logger.info("Created sandbox container %s for agent %s", container_id, agent_id)
        return container_id

    async def execute(
        self,
        container_id: str,
        language: str,
        code: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute code inside a persistent container via docker exec."""
        if language == "python":
            cmd = ["docker", "exec", container_id, "python3", "-c", code]
        elif language == "shell":
            cmd = ["docker", "exec", container_id, "sh", "-c", code]
        else:
            return {"error": f"Unsupported language: {language}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}

    # ------------------------------------------------------------------
    # Cleanup lifecycle
    # ------------------------------------------------------------------

    async def destroy_agent_container(
        self, agent_id: str, *, keep_clones: bool = False,
    ) -> bool:
        key = None
        for k in list(self._containers.keys()):
            if k.endswith(agent_id):
                key = k
                break

        if not key:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a", "--format", "{{.Names}}",
                "--filter", f"name=bond-.*-{agent_id}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            names = stdout.decode().strip().split("\n")
            for name in names:
                if name.endswith(agent_id):
                    key = name
                    break

        if not key:
            key = f"bond-agent-{agent_id}"

        # Get host_id before removing tracking
        host_id = "local"
        if key in self._containers:
            host_id = self._containers[key].get("host_id", "local")

        # Release port and delete config
        released_port = self._release_port(key)
        self._delete_agent_config(agent_id)

        if key in self._containers:
            del self._containers[key]

        self._agent_locks.pop(key, None)

        if not keep_clones:
            try:
                await cleanup_workspace_clones(agent_id)
            except Exception as e:
                logger.warning("Failed to clean up workspace clones for agent %s: %s", agent_id, e)

        # Destroy via adapter
        if host_id != "local":
            adapter = self._adapter_for_container({"host_id": host_id})
            result = await adapter.destroy_container(key)
        else:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            result = proc.returncode == 0

        if result:
            self._registry.decrement_running(host_id)
            port_info = f" (port {released_port} released)" if released_port else ""
            logger.info("Destroyed container for agent %s%s", agent_id, port_info)

        return result

    async def cleanup_idle(self, max_idle_seconds: int = 3600) -> int:
        now = time.time()
        to_remove = []
        for key, info in self._containers.items():
            if now - info["last_used"] > max_idle_seconds:
                to_remove.append(key)

        released_ports = []
        for key in to_remove:
            cid = self._containers[key]["container_id"]
            agent_id = key[-26:]

            await asyncio.create_subprocess_exec(
                "docker", "stop", cid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.create_subprocess_exec(
                "docker", "rm", cid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            port = self._release_port(key)
            if port:
                released_ports.append(str(port))
            self._delete_agent_config(agent_id)
            self._agent_locks.pop(key, None)

            host_id = self._containers[key].get("host_id", "local")
            self._registry.decrement_running(host_id)

            del self._containers[key]

        if to_remove:
            logger.info(
                "Cleaned up %d idle containers (ports released: %s)",
                len(to_remove),
                ", ".join(released_ports) if released_ports else "none",
            )

        return len(to_remove)

    async def destroy_agent_data(self, agent_id: str) -> None:
        import shutil

        await self.destroy_agent_container(agent_id)

        agent_data_dir = _PROJECT_ROOT / "data" / "agents" / agent_id
        try:
            shutil.rmtree(str(agent_data_dir))
            logger.info("Removed data directory for agent %s: %s", agent_id, agent_data_dir)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Failed to remove data directory for agent %s: %s", agent_id, e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_worker_container(
        self,
        agent: dict,
        key: str,
        port: int,
        config_path: Path,
    ) -> tuple:
        """Legacy wrapper: delegates to LocalContainerAdapter.create_container().

        Returns (container_id, clone_info, dep_install_script) to match old signature.
        """
        # Ensure the port is allocated in the local adapter
        self._local_adapter._port_map[key] = port
        config = self._build_container_config(agent)
        result = await self._local_adapter.create_container(agent, key, config)
        if isinstance(result, tuple):
            container_info, clone_info, dep_script = result
        else:
            container_info = result
            clone_info = []
            dep_script = None
        return container_info.container_id, clone_info, dep_script

    async def _docker_run_with_conflict_retry(
        self,
        cmd: list[str],
        container_name: str,
        agent_id: str,
        max_retries: int = 5,
    ) -> bytes:
        """Legacy wrapper for backward compatibility."""
        return await self._local_adapter._docker_run_with_conflict_retry(
            cmd, container_name, agent_id, max_retries
        )

    async def _is_running(self, container_id: str, host_id: str = "local") -> bool:
        """Check if a container is running (local or remote)."""
        if host_id != "local":
            adapter = self._adapter_for_container({"host_id": host_id})
            return await adapter.is_running(container_id)

        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().lower() == "true"

    def _agent_data_dir(self, agent_id: str) -> Path:
        return _PROJECT_ROOT / "data" / "agents" / agent_id

    # ------------------------------------------------------------------
    # Remote host management helpers
    # ------------------------------------------------------------------

    async def start_tunnel_health_checks(self) -> None:
        """Start background tunnel health monitoring."""
        self._tunnel_manager.start_health_check_loop(self._registry)

    async def recover_remote_state(self) -> None:
        """On startup, recover running containers from all remote hosts."""
        results = await self._tunnel_manager.recover_after_restart(self._registry)
        for host_id, containers in results.items():
            for c in containers:
                logger.info("Recovered remote container %s on %s", c.get("name"), host_id)

    async def shutdown(self) -> None:
        """Clean shutdown: close tunnels and adapters."""
        await self._tunnel_manager.close_all()
        for adapter in self._remote_adapters.values():
            if hasattr(adapter, "close"):
                await adapter.close()


# Singleton instance
_sandbox_manager: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager()
    return _sandbox_manager
