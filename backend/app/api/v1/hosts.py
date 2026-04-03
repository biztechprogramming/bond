"""Remote Hosts API — CRUD + health checks for remote container hosts.

Design Doc 089: Remote Container Hosts §13
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("bond.api.hosts")

router = APIRouter(prefix="/hosts", tags=["hosts"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HostCreate(BaseModel):
    id: str
    name: str
    host: str
    port: int = 22
    user: str = "bond"
    ssh_key: str = "~/.ssh/id_ed25519"
    daemon_port: int = 18795
    max_agents: int = 4
    labels: list[str] = []
    enabled: bool = True


class HostUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    ssh_key: str | None = None
    daemon_port: int | None = None
    max_agents: int | None = None
    labels: list[str] | None = None
    enabled: bool | None = None
    status: str | None = None


class HostResponse(BaseModel):
    id: str
    name: str
    host: str
    port: int
    user: str
    ssh_key: str
    daemon_port: int
    max_agents: int
    labels: list[str]
    enabled: bool
    status: str
    running_count: int = 0


# ---------------------------------------------------------------------------
# Registry access
# ---------------------------------------------------------------------------


def _get_registry():
    """Get the HostRegistry singleton."""
    from backend.app.sandbox.manager import get_sandbox_manager
    return get_sandbox_manager()._registry


def _get_tunnel_manager():
    """Get the TunnelManager singleton."""
    from backend.app.sandbox.manager import get_sandbox_manager
    return get_sandbox_manager()._tunnel_manager


def _host_to_response(host) -> dict:
    """Convert a host object to API response dict."""
    return {
        "id": host.id,
        "name": host.name,
        "host": host.host,
        "port": getattr(host, "port", 22),
        "user": getattr(host, "user", "bond"),
        "ssh_key": getattr(host, "ssh_key", ""),
        "daemon_port": getattr(host, "daemon_port", 18795),
        "max_agents": host.max_agents,
        "labels": getattr(host, "labels", []),
        "enabled": host.enabled,
        "status": host.status,
        "running_count": host.running_count,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_hosts() -> list[dict]:
    """List all configured hosts (local + remote) with status."""
    registry = _get_registry()
    result = []
    for host in registry.get_all_hosts():
        result.append(_host_to_response(host))
    return result


@router.post("")
async def add_host(body: HostCreate) -> dict:
    """Add a new remote host."""
    from backend.app.sandbox.host_registry import RemoteHost

    registry = _get_registry()

    if registry.get_host(body.id):
        raise HTTPException(409, f"Host '{body.id}' already exists")

    host = RemoteHost(
        id=body.id,
        name=body.name,
        host=body.host,
        port=body.port,
        user=body.user,
        ssh_key=body.ssh_key,
        daemon_port=body.daemon_port,
        max_agents=body.max_agents,
        labels=body.labels,
        enabled=body.enabled,
    )
    registry.add_host(host)
    logger.info("Added remote host: %s (%s)", body.id, body.host)
    return _host_to_response(host)


@router.put("/{host_id}")
async def update_host(host_id: str, body: HostUpdate) -> dict:
    """Update a remote host configuration."""
    registry = _get_registry()

    if host_id == "local":
        raise HTTPException(400, "Cannot modify the local host")

    updates = body.model_dump(exclude_none=True)
    host = registry.update_host(host_id, updates)
    if not host:
        raise HTTPException(404, f"Host '{host_id}' not found")

    logger.info("Updated remote host: %s", host_id)
    return _host_to_response(host)


@router.delete("/{host_id}")
async def remove_host(host_id: str) -> dict:
    """Remove a remote host."""
    registry = _get_registry()

    if host_id == "local":
        raise HTTPException(400, "Cannot remove the local host")

    if not registry.remove_host(host_id):
        raise HTTPException(404, f"Host '{host_id}' not found")

    logger.info("Removed remote host: %s", host_id)
    return {"status": "removed", "id": host_id}


@router.get("/{host_id}/health")
async def host_health(host_id: str) -> dict:
    """Detailed health check for a specific host."""
    registry = _get_registry()

    if host_id == "local":
        from backend.app.sandbox.adapters import LocalContainerAdapter
        adapter = LocalContainerAdapter()
        status = await adapter.health()
        return {
            "host_id": "local",
            "online": True,
            "running_containers": status.running_containers,
            "max_agents": status.max_agents,
        }

    host = registry.get_host(host_id)
    if not host:
        raise HTTPException(404, f"Host '{host_id}' not found")

    tunnel_manager = _get_tunnel_manager()
    try:
        tunnel = await tunnel_manager.ensure_tunnel(host)
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{tunnel.local_url}/health")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        return {
            "host_id": host_id,
            "online": False,
            "error": str(e),
        }


@router.post("/{host_id}/test")
async def test_host(host_id: str) -> dict:
    """Test SSH connectivity to a remote host."""
    registry = _get_registry()
    host = registry.get_host(host_id)
    if not host or host_id == "local":
        raise HTTPException(404, f"Host '{host_id}' not found")

    results = {}

    # Test SSH
    tunnel_manager = _get_tunnel_manager()
    try:
        tunnel = await tunnel_manager.ensure_tunnel(host)
        results["ssh"] = {"status": "ok"}

        # Test daemon
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{tunnel.local_url}/health")
            if resp.status_code == 200:
                data = resp.json()
                results["daemon"] = {
                    "status": "ok",
                    "version": data.get("daemon_version", ""),
                    "api_version": data.get("api_version", ""),
                }
                results["resources"] = {
                    "cpu_percent": data.get("cpu_percent", 0),
                    "memory_available_mb": data.get("memory_available_mb", 0),
                    "disk_available_gb": data.get("disk_available_gb", 0),
                }
            else:
                results["daemon"] = {"status": "error", "http_status": resp.status_code}

    except Exception as e:
        results["ssh"] = {"status": "error", "error": str(e)}

    return results


@router.post("/{host_id}/validate")
async def validate_host(host_id: str) -> dict:
    """Comprehensive remote host validation (Design Doc 089 §12.3)."""
    # Alias to test for now — full validation with docker/git/disk checks
    # will be added when the CLI onboarding flow is built
    return await test_host(host_id)
