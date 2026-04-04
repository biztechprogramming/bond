"""Remote Container Adapter — creates containers on remote machines via bond-host-daemon.

Design Doc 089: Remote Container Hosts §4.4
"""

from __future__ import annotations

import base64
import logging
from dataclasses import asdict
from typing import Any

import httpx

from backend.app.sandbox.adapters import (
    AgentContainerConfig,
    ContainerHostAdapter,
    ContainerInfo,
    HostStatus,
)
from backend.app.sandbox.host_registry import RemoteHost
from backend.app.sandbox.tunnel_manager import TunnelManager

logger = logging.getLogger("bond.sandbox.remote_adapter")


class RemoteContainerAdapter:
    """Creates containers on a remote machine via bond-host-daemon.

    Design Doc 089 §4.4
    """

    def __init__(self, host: RemoteHost, tunnel_manager: TunnelManager):
        self._host = host
        self._tunnel_manager = tunnel_manager
        self._client = httpx.AsyncClient(timeout=60.0)

    @property
    def host_id(self) -> str:
        return self._host.id

    def _auth_headers(self) -> dict[str, str]:
        """Build auth headers if the daemon requires a token."""
        if self._host.auth_token:
            return {"Authorization": f"Bearer {self._host.auth_token}"}
        return {}

    async def create_container(
        self,
        agent: dict,
        key: str,
        config: AgentContainerConfig,
    ) -> ContainerInfo:
        """Create an agent container on the remote machine."""
        tunnel = await self._tunnel_manager.ensure_tunnel(self._host)

        # Build payload
        payload = {
            "key": key,
            "image": config.sandbox_image,
            "repo_url": config.repo_url,
            "repo_branch": config.repo_branch,
            "env_vars": config.env_vars,
            "agent_config": config.agent_config_json,
            "ssh_private_key": config.ssh_private_key,
            "resource_limits": asdict(config.resource_limits),
        }

        if config.vault_data:
            payload["vault_data"] = base64.b64encode(config.vault_data).decode()
        if config.shared_memory_snapshot:
            payload["shared_memory_snapshot"] = base64.b64encode(
                config.shared_memory_snapshot
            ).decode()

        resp = await self._client.post(
            f"{tunnel.local_url}/containers",
            json=payload,
            headers=self._auth_headers(),
        )

        if resp.status_code == 429:
            raise RuntimeError(
                f"Remote host {self._host.id} at capacity: {resp.json().get('detail', '')}"
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to create container on {self._host.id}: "
                f"HTTP {resp.status_code}: {resp.text}"
            )

        result = resp.json()

        # Set up SSH port forward for the worker's SSE port
        worker_port = int(result["worker_url"].rsplit(":", 1)[1])
        local_port = await tunnel.add_port_forward(key, worker_port)
        worker_url = f"http://localhost:{local_port}"

        return ContainerInfo(
            container_id=result["container_id"],
            host_id=self._host.id,
            worker_url=worker_url,
        )

    async def destroy_container(self, key: str) -> bool:
        tunnel = self._tunnel_manager.get_tunnel(self._host.id)
        if not tunnel:
            logger.warning("No tunnel to %s for destroying %s", self._host.id, key)
            return False

        try:
            resp = await self._client.delete(
                f"{tunnel.local_url}/containers/{key}",
                headers=self._auth_headers(),
            )
            # Clean up port forward
            await tunnel.remove_port_forward(key)
            return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to destroy container %s on %s: %s", key, self._host.id, e)
            return False

    async def is_running(self, key: str) -> bool:
        tunnel = self._tunnel_manager.get_tunnel(self._host.id)
        if not tunnel:
            return False

        try:
            resp = await self._client.get(
                f"{tunnel.local_url}/containers/{key}/health",
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("running", False)
            return False
        except Exception:
            return False

    async def get_logs(self, key: str, tail: int = 50) -> str:
        tunnel = self._tunnel_manager.get_tunnel(self._host.id)
        if not tunnel:
            return "<no tunnel to host>"

        try:
            resp = await self._client.get(
                f"{tunnel.local_url}/containers/{key}/logs",
                params={"tail": tail},
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("logs", "")
            return f"<HTTP {resp.status_code}>"
        except Exception as e:
            return f"<error: {e}>"

    async def get_worker_url(self, key: str) -> str:
        tunnel = self._tunnel_manager.get_tunnel(self._host.id)
        if not tunnel:
            raise RuntimeError(f"No tunnel to {self._host.id}")

        local_port = tunnel.get_worker_local_port(key)
        if local_port:
            return f"http://localhost:{local_port}"

        # Try to get from daemon and set up forward
        resp = await self._client.get(
            f"{tunnel.local_url}/containers/{key}/health",
            headers=self._auth_headers(),
        )
        if resp.status_code == 200:
            remote_port = resp.json().get("port", 0)
            if remote_port:
                local_port = await tunnel.add_port_forward(key, remote_port)
                return f"http://localhost:{local_port}"

        raise RuntimeError(f"Cannot determine worker URL for {key} on {self._host.id}")

    async def health(self) -> HostStatus:
        tunnel = self._tunnel_manager.get_tunnel(self._host.id)
        if not tunnel:
            return HostStatus(
                host_id=self._host.id,
                online=False,
            )

        try:
            resp = await self._client.get(
                f"{tunnel.local_url}/health",
                headers=self._auth_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                return HostStatus(
                    host_id=self._host.id,
                    online=True,
                    cpu_percent=data.get("cpu_percent", 0),
                    memory_available_mb=data.get("memory_available_mb", 0),
                    disk_available_gb=data.get("disk_available_gb", 0),
                    running_containers=data.get("running_containers", 0),
                    max_agents=data.get("max_agents", self._host.max_agents),
                    daemon_version=data.get("daemon_version", ""),
                    api_version=data.get("api_version", ""),
                )
        except Exception:
            pass

        return HostStatus(host_id=self._host.id, online=False)

    async def close(self) -> None:
        await self._client.aclose()
