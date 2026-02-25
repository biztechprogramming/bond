"""SandboxManager — Docker container lifecycle and code execution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("bond.sandbox.manager")


class SandboxManager:
    """Manages persistent Docker containers for agent code execution."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, Any]] = {}

    async def get_or_create_container(
        self,
        agent_id: str,
        sandbox_image: str,
        workspace_mounts: list[dict[str, str]] | None = None,
    ) -> str:
        """Find a running container for the agent or create a new one.

        Returns the container ID.
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
                host_path = mount.get("host_path", "")
                mount_name = mount.get("mount_name", "workspace")
                readonly = mount.get("readonly", False)
                mount_str = f"{host_path}:/workspace/{mount_name}"
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

    async def destroy_agent_container(self, agent_id: str) -> bool:
        """Destroy the sandbox container for an agent (e.g., after config change).

        Returns True if a container was destroyed.
        """
        key = f"bond-sandbox-{agent_id}"

        # Remove from tracking
        if key in self._containers:
            del self._containers[key]

        # Find and destroy the Docker container
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("Destroyed sandbox container for agent %s (settings changed)", agent_id)
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

        for key in to_remove:
            cid = self._containers[key]["container_id"]
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
            del self._containers[key]
            logger.info("Cleaned up idle container %s", cid)

        return len(to_remove)

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
