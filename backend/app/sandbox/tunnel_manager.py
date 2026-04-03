"""SSH Tunnel Manager — manages SSH tunnels to remote container hosts.

Design Doc 089: Remote Container Hosts §4.7
Uses ControlMaster for SSH multiplexing (§3.2 Decision 3).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("bond.sandbox.tunnel_manager")


@dataclass
class SSHTunnel:
    """Represents an SSH tunnel to a remote host."""

    host_id: str
    host: str
    port: int
    user: str
    ssh_key: str
    remote_port: int  # daemon port on remote
    local_port: int  # local port forwarding to remote daemon
    control_path: str
    process: asyncio.subprocess.Process | None = None
    _worker_forwards: dict[str, int] = field(default_factory=dict)  # key -> local_port

    @property
    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.returncode is None

    @property
    def local_url(self) -> str:
        return f"http://localhost:{self.local_port}"

    @classmethod
    async def connect(
        cls,
        host_id: str,
        host: str,
        port: int,
        user: str,
        ssh_key: str,
        remote_port: int,
        control_path: str,
    ) -> SSHTunnel:
        """Establish an SSH tunnel with ControlMaster multiplexing."""
        # Find a free local port for the daemon tunnel
        local_port = await _find_free_port()

        ssh_key_expanded = os.path.expanduser(ssh_key)

        cmd = [
            "ssh",
            "-N",  # No remote command
            "-f",  # Go to background after auth
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={control_path}",
            "-o", "ControlPersist=600",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "ConnectTimeout=10",
            "-o", "ExitOnForwardFailure=yes",
            "-i", ssh_key_expanded,
            "-p", str(port),
            "-L", f"{local_port}:localhost:{remote_port}",
            f"{user}@{host}",
        ]

        logger.info(
            "Establishing SSH tunnel to %s@%s:%d (daemon port %d -> local %d)",
            user, host, port, remote_port, local_port,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # ssh -f backgrounds after auth, so wait for it
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            raise ConnectionError(
                f"SSH tunnel to {host}:{port} failed: {err}"
            )

        # The process backgrounded itself. Find the master via control socket.
        tunnel = cls(
            host_id=host_id,
            host=host,
            port=port,
            user=user,
            ssh_key=ssh_key,
            remote_port=remote_port,
            local_port=local_port,
            control_path=control_path,
            process=proc,
        )

        logger.info("SSH tunnel established to %s (local port %d)", host_id, local_port)
        return tunnel

    async def add_port_forward(self, container_key: str, remote_worker_port: int) -> int:
        """Add a port forward for a worker container via ControlMaster."""
        local_port = await _find_free_port()

        cmd = [
            "ssh",
            "-o", f"ControlPath={self.control_path}",
            "-O", "forward",
            "-L", f"{local_port}:localhost:{remote_worker_port}",
            f"{self.user}@{self.host}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ConnectionError(
                f"Failed to add port forward for {container_key}: {stderr.decode()}"
            )

        self._worker_forwards[container_key] = local_port
        logger.info(
            "Added port forward for %s: local %d -> remote %d",
            container_key, local_port, remote_worker_port,
        )
        return local_port

    async def remove_port_forward(self, container_key: str) -> None:
        """Remove a worker port forward."""
        local_port = self._worker_forwards.pop(container_key, None)
        if local_port is None:
            return

        cmd = [
            "ssh",
            "-o", f"ControlPath={self.control_path}",
            "-O", "cancel",
            "-L", f"{local_port}:localhost:0",
            f"{self.user}@{self.host}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    def get_worker_local_port(self, container_key: str) -> int | None:
        return self._worker_forwards.get(container_key)

    async def close(self) -> None:
        """Close the SSH tunnel and all port forwards."""
        # Send exit to control master
        cmd = [
            "ssh",
            "-o", f"ControlPath={self.control_path}",
            "-O", "exit",
            f"{self.user}@{self.host}",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        self._worker_forwards.clear()
        logger.info("SSH tunnel to %s closed", self.host_id)


class TunnelManager:
    """Manages SSH tunnels to all remote hosts with health monitoring.

    Design Doc 089 §4.7
    """

    def __init__(self) -> None:
        self._tunnels: dict[str, SSHTunnel] = {}  # host_id -> tunnel
        self._health_task: asyncio.Task | None = None
        self._consecutive_failures: dict[str, int] = {}

    async def ensure_tunnel(self, host) -> SSHTunnel:
        """Get or create a tunnel to the given host."""
        from backend.app.sandbox.host_registry import RemoteHost

        tunnel = self._tunnels.get(host.id)
        if tunnel and tunnel.is_alive:
            return tunnel

        control_path = f"/tmp/bond-ssh-{host.id}"

        tunnel = await SSHTunnel.connect(
            host_id=host.id,
            host=host.host,
            port=host.port,
            user=host.user,
            ssh_key=host.ssh_key,
            remote_port=host.daemon_port,
            control_path=control_path,
        )
        self._tunnels[host.id] = tunnel
        self._consecutive_failures[host.id] = 0
        return tunnel

    def get_tunnel(self, host_id: str) -> SSHTunnel | None:
        return self._tunnels.get(host_id)

    def start_health_check_loop(self, registry) -> None:
        """Start periodic tunnel health checks (every 30s)."""
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(
                self._health_check_loop(registry)
            )

    async def _health_check_loop(self, registry) -> None:
        """Periodic health check — reconnect dead tunnels, mark hosts offline."""
        while True:
            for host_id, tunnel in list(self._tunnels.items()):
                if not tunnel.is_alive:
                    host = registry.get_host(host_id)
                    if not host:
                        continue
                    try:
                        new_tunnel = await SSHTunnel.connect(
                            host_id=host.id,
                            host=host.host,
                            port=host.port,
                            user=host.user,
                            ssh_key=host.ssh_key,
                            remote_port=host.daemon_port,
                            control_path=f"/tmp/bond-ssh-{host.id}",
                        )
                        self._tunnels[host_id] = new_tunnel
                        self._consecutive_failures[host_id] = 0
                        registry.mark_active(host_id)
                        logger.info("Reconnected tunnel to %s", host_id)
                    except Exception:
                        failures = self._consecutive_failures.get(host_id, 0) + 1
                        self._consecutive_failures[host_id] = failures
                        logger.warning(
                            "Tunnel to %s is dead, reconnect failed (%d/3)",
                            host_id, failures,
                        )
                        if failures >= 3:
                            registry.mark_unreachable(host_id)
            await asyncio.sleep(30)

    async def recover_after_restart(self, registry) -> dict[str, list]:
        """On gateway startup, reconnect to all remote hosts and query for running containers."""
        results = {}
        for host in registry.get_all_remote_hosts():
            if not host.enabled:
                continue
            try:
                tunnel = await self.ensure_tunnel(host)
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{tunnel.local_url}/containers")
                    if resp.status_code == 200:
                        running = resp.json().get("containers", [])
                        results[host.id] = running
                        logger.info("Recovered %d containers from %s", len(running), host.id)
            except Exception as e:
                logger.warning("Could not recover state from %s: %s", host.id, e)
        return results

    async def close_all(self) -> None:
        """Close all tunnels."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        for tunnel in self._tunnels.values():
            try:
                await tunnel.close()
            except Exception:
                pass
        self._tunnels.clear()


async def _find_free_port() -> int:
    """Find a free local port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]
