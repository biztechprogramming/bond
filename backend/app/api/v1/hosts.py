"""Remote Hosts API — CRUD + health checks for remote container hosts.

Design Doc 089: Remote Container Hosts §13
Phase 2.5: Settings-Driven Configuration (DB-backed)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.services.container_host_service import ContainerHostService

logger = logging.getLogger("bond.api.hosts")

router = APIRouter(prefix="/hosts", tags=["hosts"])

_service = ContainerHostService()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class HostCreate(BaseModel):
    id: str
    name: str
    host: str
    port: int = 22
    user: str = "bond"
    ssh_key: str = ""
    daemon_port: int = 8990
    max_agents: int = 4
    memory_mb: int = 0
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
    memory_mb: int | None = None
    labels: list[str] | None = None
    enabled: bool | None = None
    status: str | None = None

class HostResponse(BaseModel):
    id: str
    name: str
    host: str
    port: int
    user: str
    daemon_port: int
    max_agents: int
    memory_mb: int = 0
    labels: list[str]
    enabled: bool
    status: str
    is_local: bool = False
    running_count: int = 0

class ContainerSettingsUpdate(BaseModel):
    settings: dict[str, str]

class ImportConfig(BaseModel):
    remote_hosts: list[dict[str, Any]] = []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_registry():
    """Get the HostRegistry singleton for runtime state (running counts)."""
    try:
        from backend.app.sandbox.manager import get_sandbox_manager
        return get_sandbox_manager()._registry
    except Exception:
        return None

def _db_host_to_response(host: dict) -> dict:
    """Convert a DB host dict to API response dict."""
    registry = _get_registry()
    running_count = 0
    if registry:
        reg_host = registry.get_host(host["id"])
        if reg_host:
            running_count = reg_host.running_count

    return {
        "id": host["id"],
        "name": host["name"],
        "host": host["host"],
        "port": host.get("port", 22),
        "user": host.get("user", "bond"),
        "daemon_port": host.get("daemon_port", 8990),
        "max_agents": host.get("max_agents", 4),
        "memory_mb": host.get("memory_mb", 0),
        "labels": host.get("labels", []),
        "enabled": host.get("enabled", True),
        "status": host.get("status", "active"),
        "is_local": host.get("is_local", False),
        "running_count": running_count,
        "daemon_installed": bool(host.get("has_auth_token", False)),
    }

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_hosts(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """List all configured hosts (local + remote) with status."""
    hosts = await _service.list_all(db)
    return [_db_host_to_response(h) for h in hosts]

@router.post("")
async def add_host(body: HostCreate, db: AsyncSession = Depends(get_db)) -> dict:
    """Add a new remote host."""
    existing = await _service.get(db, body.id)
    if existing:
        raise HTTPException(409, f"Host '{body.id}' already exists")

    created = await _service.create(db, body.model_dump())
    logger.info("Added remote host: %s (%s)", body.id, body.host)
    return _db_host_to_response(created)

@router.put("/{host_id}")
async def update_host(host_id: str, body: HostUpdate, db: AsyncSession = Depends(get_db)) -> dict:
    """Update a remote host configuration."""
    if host_id == "local":
        # Allow limited updates to local host (max_agents, enabled)
        allowed = {"max_agents", "enabled", "status"}
        updates = body.model_dump(exclude_none=True)
        disallowed = set(updates.keys()) - allowed
        if disallowed:
            raise HTTPException(400, f"Cannot modify these fields on local host: {disallowed}")

    updates = body.model_dump(exclude_none=True)
    updated = await _service.update(db, host_id, updates)
    if not updated:
        raise HTTPException(404, f"Host '{host_id}' not found")

    logger.info("Updated host: %s", host_id)
    return _db_host_to_response(updated)

@router.delete("/{host_id}")
async def remove_host(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Remove a remote host."""
    if host_id == "local":
        raise HTTPException(400, "Cannot remove the local host")

    if not await _service.delete(db, host_id):
        raise HTTPException(404, f"Host '{host_id}' not found")

    logger.info("Removed remote host: %s", host_id)
    return {"status": "removed", "id": host_id}

@router.get("/settings")
async def get_container_settings(db: AsyncSession = Depends(get_db)) -> dict:
    """Get all container.* settings."""
    return await _service.get_container_settings(db)

@router.put("/settings")
async def update_container_settings(
    body: ContainerSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update container.* settings."""
    return await _service.update_container_settings(db, body.settings)

@router.post("/import")
async def import_hosts(body: ImportConfig, db: AsyncSession = Depends(get_db)) -> dict:
    """One-time import from bond.json / env vars."""
    imported = await _service.import_from_config(db, body.model_dump())
    return {"imported": len(imported), "hosts": [_db_host_to_response(h) for h in imported]}

@router.get("/{host_id}/health")
async def host_health(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Detailed health check for a specific host."""
    host = await _service.get(db, host_id)
    if not host:
        raise HTTPException(404, f"Host '{host_id}' not found")

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

    # Remote host — test via SSH tunnel + daemon
    try:
        from backend.app.sandbox.manager import get_sandbox_manager
        tunnel_manager = get_sandbox_manager()._tunnel_manager
        registry = get_sandbox_manager()._registry
        reg_host = registry.get_host(host_id)
        if not reg_host:
            return {"host_id": host_id, "online": False, "error": "Host not in registry"}

        tunnel = await tunnel_manager.ensure_tunnel(reg_host)
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{tunnel.local_url}/health")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        return {"host_id": host_id, "online": False, "error": str(e)}

@router.post("/{host_id}/test")
async def test_host(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Test SSH connectivity to a remote host."""
    host = await _service.get(db, host_id)
    if not host or host_id == "local":
        raise HTTPException(404, f"Remote host '{host_id}' not found")

    results = {}

    try:
        from backend.app.sandbox.manager import get_sandbox_manager
        manager = get_sandbox_manager()
        tunnel_manager = manager._tunnel_manager
        registry = manager._registry

        # Ensure registry has loaded hosts from DB (picks up newly-added hosts)
        await registry.load_from_db()

        reg_host = registry.get_host(host_id)
        if not reg_host:
            # Host exists in DB but not in registry — force a full refresh
            await registry.refresh()
            reg_host = registry.get_host(host_id)
        if not reg_host:
            # Bypass registry: build a temporary RemoteHost from DB data for testing
            from backend.app.sandbox.host_registry import RemoteHost
            import json as _json
            labels = host.get("labels", [])
            if isinstance(labels, str):
                labels = _json.loads(labels)
            reg_host = RemoteHost(
                id=host["id"],
                name=host["name"],
                host=host["host"],
                port=host.get("port", 22),
                user=host.get("user", "bond"),
                ssh_key=host.get("ssh_key_decrypted", ""),
                daemon_port=host.get("daemon_port", 8990),
                max_agents=host.get("max_agents", 4),
                labels=labels,
                enabled=host.get("enabled", True),
            )
            reason = "disabled" if not host.get("enabled", True) else "not yet loaded"
            logger.info("Host '%s' not in registry (%s), using temporary RemoteHost for test", host_id, reason)

        tunnel = await tunnel_manager.ensure_tunnel(reg_host)
        results["ssh"] = {"status": "ok"}

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
async def validate_host(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Comprehensive remote host validation (Design Doc 089 §12.3)."""
    return await test_host(host_id, db)

# ---------------------------------------------------------------------------
# Daemon installation endpoints (Phase 2, Gap 1)
# ---------------------------------------------------------------------------

@router.post("/{host_id}/install-daemon")
async def install_daemon(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Install bond-host-daemon on a remote host."""
    host = await _service.get(db, host_id)
    if not host or host_id == "local":
        raise HTTPException(404, f"Remote host '{host_id}' not found")

    ssh_key = host.get("ssh_key_decrypted", "")

    # If SSH key is configured, write it to a temp file; otherwise use system defaults (~/.ssh)
    import tempfile, os
    tmp_path: str | None = None
    try:
        if ssh_key:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(ssh_key)
            tmp.close()
            os.chmod(tmp.name, 0o600)
            tmp_path = tmp.name

        from backend.app.services.daemon_installer import DaemonInstaller
        installer = DaemonInstaller()
        result = await installer.install(
            host=host["host"],
            port=host.get("port", 22),
            user=host.get("user", "bond"),
            ssh_key_path=tmp_path,
            daemon_port=host.get("daemon_port", 8990),
        )
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    # Store auth token in DB if install succeeded
    if result.get("success") and result.get("auth_token"):
        from backend.app.core.crypto import encrypt_value
        encrypted_token = encrypt_value(result["auth_token"])
        from sqlalchemy import text as sql_text
        await db.execute(
            sql_text("UPDATE container_hosts SET auth_token = :token, updated_at = datetime('now') WHERE id = :id"),
            {"token": encrypted_token, "id": host_id},
        )
        await db.commit()

        # Refresh registry to pick up new token
        registry = _get_registry()
        if registry:
            await registry.refresh()

    return result

@router.post("/{host_id}/uninstall-daemon")
async def uninstall_daemon(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Remove bond-host-daemon from a remote host."""
    host = await _service.get(db, host_id)
    if not host or host_id == "local":
        raise HTTPException(404, f"Remote host '{host_id}' not found")

    ssh_key = host.get("ssh_key_decrypted", "")

    # If SSH key is configured, write it to a temp file; otherwise use system defaults (~/.ssh)
    import tempfile, os
    tmp_path: str | None = None
    try:
        if ssh_key:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(ssh_key)
            tmp.close()
            os.chmod(tmp.name, 0o600)
            tmp_path = tmp.name

        from backend.app.services.daemon_installer import DaemonInstaller
        installer = DaemonInstaller()
        result = await installer.uninstall(
            host=host["host"],
            port=host.get("port", 22),
            user=host.get("user", "bond"),
            ssh_key_path=tmp_path,
        )
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    # Clear auth token
    if result.get("success"):
        from sqlalchemy import text as sql_text
        await db.execute(
            sql_text("UPDATE container_hosts SET auth_token = NULL, updated_at = datetime('now') WHERE id = :id"),
            {"id": host_id},
        )
        await db.commit()

    return result

@router.get("/{host_id}/daemon-status")
async def daemon_status(host_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Check if bond-host-daemon is running on a remote host."""
    host = await _service.get(db, host_id)
    if not host or host_id == "local":
        raise HTTPException(404, f"Remote host '{host_id}' not found")

    ssh_key = host.get("ssh_key_decrypted", "")

    # If SSH key is configured, write it to a temp file; otherwise use system defaults (~/.ssh)
    import tempfile, os
    tmp_path: str | None = None
    try:
        if ssh_key:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False)
            tmp.write(ssh_key)
            tmp.close()
            os.chmod(tmp.name, 0o600)
            tmp_path = tmp.name

        from backend.app.services.daemon_installer import DaemonInstaller
        installer = DaemonInstaller()
        result = await installer.check_status(
            host=host["host"],
            port=host.get("port", 22),
            user=host.get("user", "bond"),
            ssh_key_path=tmp_path,
        )
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    return result
