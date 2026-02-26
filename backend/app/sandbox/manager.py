"""SandboxManager — Docker container lifecycle and code execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("bond.sandbox.manager")

# Port range for containerized worker agents (design doc §15.1)
_PORT_RANGE_START = 18791
_PORT_RANGE_END = 18890

# Internal port the worker always listens on inside the container
_WORKER_INTERNAL_PORT = 18791

# Project root: sandbox/manager.py -> backend/app/sandbox -> backend/app -> backend -> project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class SandboxManager:
    """Manages persistent Docker containers for agent code execution."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, Any]] = {}
        # Port tracking: agent_key -> allocated host port
        # Safe without locks — single-threaded asyncio event loop
        self._port_map: dict[str, int] = {}
        # Per-agent locks to prevent concurrent ensure_running() races
        self._agent_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Port allocation (Task 5)
    # ------------------------------------------------------------------

    def _allocate_port(self, agent_key: str) -> int:
        """Allocate an unused host port. Raises RuntimeError if range exhausted."""
        # If this agent already has a port, return it
        if agent_key in self._port_map:
            return self._port_map[agent_key]

        used_ports = set(self._port_map.values())

        for port in range(_PORT_RANGE_START, _PORT_RANGE_END + 1):
            if port in used_ports:
                continue
            # Verify port is actually free on the host
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("localhost", port)) != 0:
                    # Port is free
                    self._port_map[agent_key] = port
                    return port
            # Port in use by non-Bond process, skip

        running = len(self._port_map)
        raise RuntimeError(
            f"No available ports in range {_PORT_RANGE_START}\u2013{_PORT_RANGE_END}. "
            f"{running} agents running."
        )

    def _release_port(self, agent_key: str) -> int | None:
        """Release a port back to the pool. Returns the released port or None."""
        return self._port_map.pop(agent_key, None)

    # ------------------------------------------------------------------
    # Per-agent lock (Task 6)
    # ------------------------------------------------------------------

    def _get_agent_lock(self, agent_key: str) -> asyncio.Lock:
        if agent_key not in self._agent_locks:
            self._agent_locks[agent_key] = asyncio.Lock()
        return self._agent_locks[agent_key]

    # ------------------------------------------------------------------
    # Agent config generation (Task 3)
    # ------------------------------------------------------------------

    def _write_agent_config(self, agent: dict) -> Path:
        """Write agent config JSON with secure file permissions. Returns the path."""
        agent_id = agent["id"]
        config_dir = _PROJECT_ROOT / "data" / "agent-configs"
        os.makedirs(str(config_dir), mode=0o700, exist_ok=True)

        config_path = config_dir / f"{agent_id}.json"
        config_data = {
            "agent_id": agent_id,
            "model": agent["model"],
            "system_prompt": agent["system_prompt"],
            "tools": agent["tools"],
            "max_iterations": agent["max_iterations"],
            "prompt_fragments": agent.get("prompt_fragments", []),
        }

        # Use os.open with explicit mode to avoid race window between open() and chmod()
        fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(config_data, indent=2).encode())
        finally:
            os.close(fd)

        return config_path

    def _delete_agent_config(self, agent_id: str) -> None:
        """Delete the config file for an agent, if it exists."""
        config_path = _PROJECT_ROOT / "data" / "agent-configs" / f"{agent_id}.json"
        try:
            config_path.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Health wait (Task 4)
    # ------------------------------------------------------------------

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
                    # Capture docker logs for diagnostics
                    logs = await self._capture_container_logs(container_id)
                    logger.error(
                        "Health check timeout for agent %s after %.1fs \u2014 container logs:\n%s",
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
                        logger.warning(
                            "Unexpected HTTP %d from worker health for agent %s, retrying",
                            resp.status_code, agent_id,
                        )
                except httpx.ConnectError:
                    # Expected during startup, just retry
                    last_error = "Connection refused"
                except httpx.HTTPError as exc:
                    last_error = str(exc)

                await asyncio.sleep(interval)

    async def _recover_existing_container(
        self, key: str, agent_id: str,
    ) -> dict[str, Any] | None:
        """Check if a container with this name exists in Docker and recover it.

        Handles backend restarts where in-memory tracking is lost but the
        container is still running.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f",
            "{{.State.Running}} {{(index (index .NetworkSettings.Ports \"18791/tcp\") 0).HostPort}}",
            key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return None  # No such container

        parts = stdout.decode().strip().split()
        if len(parts) < 2:
            return None

        is_running = parts[0].lower() == "true"
        host_port = int(parts[1])

        if not is_running:
            # Container exists but stopped — remove it
            logger.info("Found stopped container %s, removing", key)
            await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", key,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return None

        # Container is running — recover tracking
        worker_url = f"http://localhost:{host_port}"
        cid_proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.Id}}", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        cid_out, _ = await cid_proc.communicate()
        container_id = cid_out.decode().strip()[:12]

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

        # Restore tracking
        self._port_map[key] = host_port
        self._containers[key] = {
            "container_id": container_id,
            "worker_url": worker_url,
            "worker_port": host_port,
            "last_used": time.time(),
        }
        logger.info(
            "Recovered running container %s for agent %s (port=%d)",
            container_id, agent_id, host_port,
        )
        return {"worker_url": worker_url, "container_id": container_id}

    async def _capture_container_logs(self, container_id: str, tail: int = 50) -> str:
        """Capture recent docker logs from a container."""
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
    # ensure_running — new high-level API (Task 6, 8)
    # ------------------------------------------------------------------

    async def ensure_running(self, agent: dict) -> dict[str, Any]:
        """Ensure agent's containerized worker is running.

        Returns {"worker_url": "http://localhost:{port}", "container_id": "abc123"}.
        Raises RuntimeError if container fails to start or health check times out.
        """
        agent_id = agent["id"]
        key = f"bond-sandbox-{agent_id}"
        lock = self._get_agent_lock(key)

        async with lock:
            # Check if container already running + healthy (Task 8)
            # First check in-memory tracking
            if key in self._containers:
                info = self._containers[key]
                cid = info["container_id"]
                worker_url = info.get("worker_url", "")

                if await self._is_running(cid):
                    try:
                        await self._wait_for_health(worker_url, agent_id, cid, timeout=5.0)
                        self._containers[key]["last_used"] = time.time()
                        return {"worker_url": worker_url, "container_id": cid}
                    except RuntimeError:
                        logger.warning(
                            "Worker unhealthy in running container %s for agent %s, destroying",
                            cid, agent_id,
                        )
                        await self.destroy_agent_container(agent_id)
                else:
                    logger.warning(
                        "Container %s for agent %s died, recreating",
                        cid, agent_id,
                    )
                    await self.destroy_agent_container(agent_id)
                # Fall through to create new container
            else:
                # Not in memory — check Docker directly (e.g., after backend restart)
                existing = await self._recover_existing_container(key, agent_id)
                if existing:
                    return existing

            # Create new worker container
            config_path: Path | None = None
            port: int | None = None
            try:
                port = self._allocate_port(key)
                config_path = self._write_agent_config(agent)

                container_id = await self._create_worker_container(
                    agent, key, port, config_path,
                )
                worker_url = f"http://localhost:{port}"

                self._containers[key] = {
                    "container_id": container_id,
                    "worker_url": worker_url,
                    "worker_port": port,
                    "config_path": str(config_path),
                    "last_used": time.time(),
                }

                # Wait for health
                await self._wait_for_health(worker_url, agent_id, container_id)

                return {"worker_url": worker_url, "container_id": container_id}

            except Exception:
                # Clean up partial state on failure
                logger.error(
                    "Failed to create container for agent %s: %s",
                    agent_id, str(asyncio.current_task()),
                )
                if key in self._containers:
                    del self._containers[key]
                self._release_port(key)
                if config_path:
                    self._delete_agent_config(agent_id)
                raise

    # ------------------------------------------------------------------
    # Worker container creation (Tasks 1, 2)
    # ------------------------------------------------------------------

    async def _create_worker_container(
        self,
        agent: dict,
        key: str,
        port: int,
        config_path: Path,
    ) -> str:
        """Create a Docker container running the agent worker."""
        agent_id = agent["id"]
        sandbox_image = agent["sandbox_image"]

        # Remove any stale container with the same name
        await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        cmd = [
            "docker", "run", "-d",
            "--name", key,
            "--memory", "512m",
            "--cpus", "1",
        ]

        # Port mapping: host_port -> internal worker port
        cmd.extend(["-p", f"{port}:{_WORKER_INTERNAL_PORT}"])

        # PYTHONPATH so worker can import backend.app.worker
        cmd.extend(["-e", "PYTHONPATH=/bond"])

        # --- Mounts (Task 2) ---

        # Bond library (read-only) — validate project root has the worker
        project_root = _PROJECT_ROOT
        worker_path = project_root / "backend" / "app" / "worker.py"
        if worker_path.exists():
            cmd.extend(["-v", f"{project_root}:/bond:ro"])
        else:
            raise RuntimeError(
                f"Cannot mount Bond library: {worker_path} not found. "
                f"Project root resolved to {project_root}"
            )

        # Workspace mounts
        workspace_mounts = agent.get("workspace_mounts", [])
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

        # Agent data: bind mount (host-accessible, persists across restarts)
        agent_data_dir = self._agent_data_dir(agent_id)
        os.makedirs(str(agent_data_dir), exist_ok=True)
        cmd.extend(["-v", f"{agent_data_dir}:/data:rw"])

        # Shared memory (read-only)
        shared_dir = project_root / "data" / "shared"
        os.makedirs(str(shared_dir), exist_ok=True)
        cmd.extend(["-v", f"{shared_dir}:/data/shared:ro"])

        # SSH keys (only if ~/.ssh exists and not already covered by a workspace mount)
        ssh_dir = Path.home() / ".ssh"
        workspace_targets = {m.get("container_path", "") for m in workspace_mounts}
        if ssh_dir.exists() and "/tmp/.ssh" not in workspace_targets:
            cmd.extend(["-v", f"{ssh_dir}:/tmp/.ssh:ro"])

        # Agent config file (read-only, mount specific file)
        cmd.extend(["-v", f"{config_path}:/config/agent.json:ro"])

        # Vault data (credentials.enc + .vault_key) for API key access (read-only)
        # BOND_HOME defaults to ~/.bond — vault files live in BOND_HOME/data/
        from backend.app.config import get_settings
        bond_home = Path(get_settings().bond_home)
        vault_data_dir = bond_home / "data"
        if vault_data_dir.exists():
            cmd.extend(["-v", f"{vault_data_dir}:/bond-home/data:ro"])

        # --- Entrypoint (Task 1) ---
        cmd.extend([
            sandbox_image,
            "python", "-m", "backend.app.worker",
            "--port", str(_WORKER_INTERNAL_PORT),
            "--data-dir", "/data",
            "--config", "/config/agent.json",
        ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode()
            logger.error("Failed to create container for agent %s: %s", agent_id, err_msg)
            raise RuntimeError(f"Failed to create container for agent {agent_id}: {err_msg}")

        container_id = stdout.decode().strip()[:12]
        logger.info(
            "Created worker container %s for agent %s (port=%d, image=%s)",
            container_id, agent_id, port, sandbox_image,
        )

        return container_id

    def _agent_data_dir(self, agent_id: str) -> Path:
        """Return the host-side data directory for an agent."""
        return _PROJECT_ROOT / "data" / "agents" / agent_id

    # ------------------------------------------------------------------
    # Host-mode container (backward compat — Task 7)
    # ------------------------------------------------------------------

    async def get_or_create_container(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None = None,
    ) -> str:
        """Find a running container for the agent or create a new one.

        Returns the container ID. Uses sleep infinity entrypoint (host mode).
        """
        key = f"bond-sandbox-{agent_id}"

        # Check if already tracked and running
        if key in self._containers:
            cid = self._containers[key]["container_id"]
            if await self._is_running(cid):
                self._containers[key]["last_used"] = time.time()
                return cid
            # Container died — remove tracking
            del self._containers[key]

        # Check if a container already exists with this name (from a previous run)
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-aq", "--filter", f"name={key}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        existing_id = stdout.decode().strip()

        if existing_id:
            # Start and reuse
            await asyncio.create_subprocess_exec(
                "docker", "start", existing_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._containers[key] = {
                "container_id": existing_id,
                "last_used": time.time(),
            }
            return existing_id

        # Create new container
        cmd = [
            "docker", "run", "-d",
            "--name", key,
            "--memory", "512m",
            "--cpus", "1",
        ]

        # Add workspace mounts
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

        cmd.extend([sandbox_image, "sleep", "infinity"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to create container: {stderr.decode()}")

        container_id = stdout.decode().strip()[:12]
        self._containers[key] = {
            "container_id": container_id,
            "last_used": time.time(),
        }
        logger.info("Created sandbox container %s for agent %s", container_id, agent_id)

        # Post-creation: copy SSH keys from /tmp/.ssh if mounted
        await self._setup_ssh(container_id)

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
    # Cleanup lifecycle (Task 9)
    # ------------------------------------------------------------------

    async def destroy_agent_container(self, agent_id: str) -> bool:
        """Destroy the sandbox container for an agent.

        Releases port, deletes config file, removes tracking.
        Docker volume persists by design (agent data survives restarts).
        Returns True if a container was destroyed.
        """
        key = f"bond-sandbox-{agent_id}"

        # Release allocated port
        released_port = self._release_port(key)

        # Delete config file
        self._delete_agent_config(agent_id)

        # Remove from tracking
        if key in self._containers:
            del self._containers[key]

        # Remove per-agent lock
        self._agent_locks.pop(key, None)

        # Find and destroy the Docker container
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0:
            port_info = f" (port {released_port} released)" if released_port else ""
            logger.info(
                "Destroyed container for agent %s%s",
                agent_id, port_info,
            )
            return True

        # No container existed — that's fine
        return False

    async def cleanup_idle(self, max_idle_seconds: int = 3600) -> int:
        """Stop containers idle for longer than max_idle_seconds. Returns count stopped."""
        now = time.time()
        to_remove = []
        for key, info in self._containers.items():
            if now - info["last_used"] > max_idle_seconds:
                to_remove.append(key)

        released_ports = []
        for key in to_remove:
            cid = self._containers[key]["container_id"]
            # Extract agent_id from key
            agent_id = key.removeprefix("bond-sandbox-")

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

            # Release port and delete config
            port = self._release_port(key)
            if port:
                released_ports.append(str(port))
            self._delete_agent_config(agent_id)
            self._agent_locks.pop(key, None)

            del self._containers[key]

        if to_remove:
            logger.info(
                "Cleaned up %d idle containers (ports released: %s)",
                len(to_remove),
                ", ".join(released_ports) if released_ports else "none",
            )

        return len(to_remove)

    async def destroy_agent_data(self, agent_id: str) -> None:
        """Permanently delete agent data — removes data directory and config.

        Called when an agent is deleted (not just stopped). Data is gone.
        """
        import shutil

        # Ensure container is destroyed first
        await self.destroy_agent_container(agent_id)

        # Remove the agent data directory
        agent_data_dir = self._agent_data_dir(agent_id)
        try:
            shutil.rmtree(str(agent_data_dir))
            logger.info("Removed data directory for agent %s: %s", agent_id, agent_data_dir)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(
                "Failed to remove data directory for agent %s: %s",
                agent_id, e,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _setup_ssh(self, container_id: str) -> None:
        """Copy SSH keys from /tmp/.ssh mount into the container user's home."""
        # Check if /tmp/.ssh exists in the container
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

    async def _is_running(self, container_id: str) -> bool:
        """Check if a container is currently running."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().lower() == "true"


# Singleton instance
_sandbox_manager: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager()
    return _sandbox_manager
