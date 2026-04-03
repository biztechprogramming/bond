"""Bond Host Daemon — lightweight FastAPI service for remote container hosts.

Design Doc 089: Remote Container Hosts §4.5
Runs on each remote machine to manage Docker containers on behalf of Bond.

Usage:
    python -m backend.app.sandbox.bond_host_daemon --port 18795 --max-agents 8
    # Or: uvicorn backend.app.sandbox.bond_host_daemon:app --port 18795
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("bond.host_daemon")

__version__ = "0.1.0"
_API_VERSION = "v1"
_MIN_GATEWAY_VERSION = "0.90.0"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfig:
    port: int = 18795
    max_agents: int = 4
    workspace_root: str = "/var/bond/workspaces"
    shared_root: str = "/var/bond/shared"
    auth_token: str = ""  # Shared secret for gateway auth
    heartbeat_timeout_minutes: int = 10


_config = DaemonConfig()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Bond Host Daemon", version=__version__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ContainerSpec(BaseModel):
    key: str
    image: str
    repo_url: str | None = None
    repo_branch: str = "main"
    env_vars: dict[str, str] = {}
    agent_config: str = ""  # JSON string
    ssh_private_key: str = ""
    vault_data: str | None = None  # base64-encoded
    shared_memory_snapshot: str | None = None  # base64-encoded
    resource_limits: dict[str, Any] = {}


class ContainerResponse(BaseModel):
    container_id: str
    worker_url: str


# ---------------------------------------------------------------------------
# Gateway heartbeat monitor (Design Doc 089 §7.2)
# ---------------------------------------------------------------------------


class GatewayHeartbeatMonitor:
    def __init__(self, timeout_minutes: int = 10):
        self._last_contact = time.time()
        self._timeout = timeout_minutes * 60

    def touch(self):
        self._last_contact = time.time()

    @property
    def seconds_since_contact(self) -> float:
        return time.time() - self._last_contact

    async def monitor_loop(self):
        while True:
            if time.time() - self._last_contact > self._timeout:
                logger.warning("Gateway heartbeat timeout — initiating graceful shutdown")
                await _graceful_shutdown_all()
                self._last_contact = time.time()
            await asyncio.sleep(30)


_heartbeat = GatewayHeartbeatMonitor()

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Validate auth token on all requests except /health."""
    _heartbeat.touch()
    if _config.auth_token and request.url.path != "/health":
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {_config.auth_token}":
            raise HTTPException(401, "Invalid auth token")
    return await call_next(request)


# ---------------------------------------------------------------------------
# Startup / recovery (Design Doc 089 §4.5)
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_recovery():
    """Reconcile with Docker and clean up stale resources."""
    os.makedirs(_config.workspace_root, exist_ok=True)
    os.makedirs(_config.shared_root, exist_ok=True)

    await _reconcile_running_containers()
    await _cleanup_stale_credentials()

    # Start heartbeat monitor
    asyncio.create_task(_heartbeat.monitor_loop())
    logger.info("Bond host daemon v%s started (max_agents=%d)", __version__, _config.max_agents)


async def _reconcile_running_containers():
    """Re-discover running bond-agent-* containers."""
    containers = await _list_bond_containers()
    logger.info("Found %d existing bond-agent containers on startup", len(containers))


async def _cleanup_stale_credentials():
    """Remove credential dirs for containers that no longer exist."""
    if not os.path.exists("/dev/shm"):
        return
    for entry in os.listdir("/dev/shm"):
        if entry.startswith("bond-creds-"):
            key = entry.replace("bond-creds-", "")
            if not await _container_exists(key):
                shutil.rmtree(f"/dev/shm/{entry}", ignore_errors=True)
                logger.info("Cleaned up stale credentials for %s", key)


# ---------------------------------------------------------------------------
# Container lifecycle endpoints
# ---------------------------------------------------------------------------


@app.post("/containers", response_model=ContainerResponse)
async def create_container(spec: ContainerSpec):
    """Create an agent container on this machine."""

    # Idempotency: return existing container if it matches
    existing = await _find_container(spec.key)
    if existing and existing.get("running"):
        return ContainerResponse(
            container_id=existing["id"],
            worker_url=f"http://localhost:{existing['port']}",
        )

    # Enforce local max_agents (daemon-side enforcement, P0)
    running = await _list_bond_containers()
    if len(running) >= _config.max_agents:
        raise HTTPException(
            429,
            f"Host at capacity ({len(running)}/{_config.max_agents})",
        )

    # 1. Ensure image is available
    await _pull_or_build_image(spec.image)

    # 2. Prepare workspace via git clone
    workspace_dir = os.path.join(_config.workspace_root, spec.key)
    if spec.repo_url:
        await _git_clone_with_verify(spec.repo_url, spec.repo_branch, workspace_dir)

    # 3. Write agent config
    config_path = _write_agent_config(spec.key, spec.agent_config)

    # 4. Write SSH keys to tmpfs
    ssh_dir = _setup_ssh_keys(spec.key, spec.ssh_private_key) if spec.ssh_private_key else None

    # 5. Write shared memory snapshot
    shared_dir = None
    if spec.shared_memory_snapshot:
        shared_dir = _setup_shared_memory(spec.key, spec.shared_memory_snapshot)

    # 6. docker run
    # NOTE: No bond_api_url — workers do not call back to Bond host (§3.2 Decision 5)
    memory = spec.resource_limits.get("memory_mb", 2048)
    cpus = spec.resource_limits.get("cpus", 2.0)

    docker_cmd = [
        "docker", "run", "-d",
        "--name", spec.key,
        "--memory", f"{memory}m",
        "--cpus", str(cpus),
        "-P",  # Let Docker assign ports
    ]

    # Environment variables
    for env_key, env_val in spec.env_vars.items():
        docker_cmd.extend(["-e", f"{env_key}={env_val}"])

    # Mounts
    if spec.repo_url and os.path.exists(workspace_dir):
        docker_cmd.extend(["-v", f"{workspace_dir}:/workspace:rw"])

    if config_path:
        docker_cmd.extend(["-v", f"{config_path}:/config/agent.json:ro"])

    if ssh_dir:
        docker_cmd.extend(["-v", f"{ssh_dir}:/tmp/.ssh:ro"])

    if shared_dir:
        docker_cmd.extend(["-v", f"{shared_dir}:/data/shared:ro"])

    docker_cmd.extend([
        spec.image,
        "--port", "18791",
        "--data-dir", "/data",
        "--config", "/config/agent.json",
    ])

    proc = await asyncio.create_subprocess_exec(
        *docker_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(500, f"docker run failed: {stderr.decode()}")

    container_id = stdout.decode().strip()[:12]

    # 7. Get assigned port
    port = await _get_container_port(container_id, "18791/tcp")

    logger.info("Created container %s for %s (port %d)", container_id, spec.key, port)
    return ContainerResponse(
        container_id=container_id,
        worker_url=f"http://localhost:{port}",
    )


@app.delete("/containers/{key}")
async def destroy_container(key: str):
    """Stop and remove a container, cleaning up workspace and credentials."""
    await _docker_stop_and_remove(key)

    workspace_dir = os.path.join(_config.workspace_root, key)
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir, ignore_errors=True)

    creds_dir = f"/dev/shm/bond-creds-{key}"
    if os.path.exists(creds_dir):
        shutil.rmtree(creds_dir, ignore_errors=True)

    return {"status": "destroyed"}


@app.get("/containers/{key}/health")
async def container_health(key: str):
    """Health check a specific container."""
    container = await _find_container(key)
    if not container:
        raise HTTPException(404, f"Container {key} not found")
    return {
        "container_id": container["id"],
        "running": container["running"],
        "port": container.get("port"),
    }


@app.get("/containers/{key}/logs")
async def container_logs(key: str, tail: int = 50):
    """Get container logs."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "logs", key, "--tail", str(tail),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {"logs": (stdout + stderr).decode(errors="replace")}


@app.get("/containers")
async def list_containers():
    """List all bond-agent-* containers."""
    containers = await _list_bond_containers()
    return {"containers": containers}


# ---------------------------------------------------------------------------
# Host health endpoint (Design Doc 089 §4.5)
# ---------------------------------------------------------------------------


@app.get("/health")
async def host_health():
    """Report this machine's resource availability."""
    cpu_percent = 0.0
    mem_available_mb = 0
    disk_available_gb = 0

    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem_available_mb = psutil.virtual_memory().available // 1024 // 1024
        disk_available_gb = psutil.disk_usage("/").free // 1024 ** 3
    except ImportError:
        # psutil not installed — return zeros
        pass

    running = await _list_bond_containers()

    return {
        "daemon_version": __version__,
        "api_version": _API_VERSION,
        "min_gateway_version": _MIN_GATEWAY_VERSION,
        "system_time": datetime.now(timezone.utc).isoformat(),
        "cpu_percent": cpu_percent,
        "memory_available_mb": mem_available_mb,
        "disk_available_gb": disk_available_gb,
        "running_containers": len(running),
        "max_agents": _config.max_agents,
    }


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


async def _list_bond_containers() -> list[dict]:
    """List all bond-agent-* containers."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-a",
        "--filter", "name=bond-",
        "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    containers = []
    for line in stdout.decode().strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            containers.append({
                "id": parts[0],
                "name": parts[1],
                "status": parts[2],
                "running": parts[2].startswith("Up"),
            })
    return containers


async def _find_container(key: str) -> dict | None:
    """Find a container by name."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "inspect", "-f",
        "{{.Id}}\t{{.State.Running}}\t{{.Name}}",
        key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None

    parts = stdout.decode().strip().split("\t")
    if len(parts) < 3:
        return None

    port = 0
    try:
        port = await _get_container_port(key, "18791/tcp")
    except Exception:
        pass

    return {
        "id": parts[0][:12],
        "running": parts[1].lower() == "true",
        "name": parts[2].lstrip("/"),
        "port": port,
    }


async def _container_exists(key: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker", "inspect", key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def _docker_stop_and_remove(key: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker", "rm", "-f", key,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _get_container_port(container_id: str, port_spec: str) -> int:
    """Get the host port mapped to a container port."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "port", container_id, port_spec,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    # Output: 0.0.0.0:32768 or :::32768
    output = stdout.decode().strip()
    if not output:
        raise RuntimeError(f"No port mapping found for {container_id}:{port_spec}")
    # Take the first line, get the port after the last colon
    return int(output.split("\n")[0].rsplit(":", 1)[1])


async def _pull_or_build_image(image: str) -> None:
    """Ensure Docker image is available locally."""
    # Check if image exists
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        return  # Image exists

    # Try to pull
    logger.info("Pulling image %s...", image)
    proc = await asyncio.create_subprocess_exec(
        "docker", "pull", image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(500, f"Failed to pull image {image}: {stderr.decode()}")


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


async def _git_clone_with_verify(url: str, branch: str, dest: str) -> None:
    """Clone and verify integrity."""
    if os.path.exists(dest):
        shutil.rmtree(dest)

    env = {
        **os.environ,
        "GIT_LFS_SKIP_SMUDGE": "1",
    }

    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--branch", branch, "--depth", "1", url, dest,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(422, f"Git clone failed: {stderr.decode()}")

    # Verify clone integrity
    verify = await asyncio.create_subprocess_exec(
        "git", "-C", dest, "rev-parse", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    v_out, _ = await verify.communicate()
    if verify.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(422, f"Clone verification failed for {url}")

    # Strip credential info from .git/config
    await asyncio.create_subprocess_exec(
        "git", "-C", dest, "config", "--remove-section", "credential",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


# ---------------------------------------------------------------------------
# Credential helpers (Design Doc 089 §6.3)
# ---------------------------------------------------------------------------


def _write_agent_config(key: str, agent_config_json: str) -> str | None:
    if not agent_config_json:
        return None
    config_dir = os.path.join(_config.workspace_root, ".configs")
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    config_path = os.path.join(config_dir, f"{key}.json")
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, agent_config_json.encode())
    finally:
        os.close(fd)
    return config_path


def _setup_ssh_keys(key: str, ssh_private_key: str) -> str | None:
    if not ssh_private_key:
        return None
    tmpfs_dir = f"/dev/shm/bond-creds-{key}"
    os.makedirs(tmpfs_dir, mode=0o700, exist_ok=True)
    key_path = os.path.join(tmpfs_dir, "id_ed25519")
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, ssh_private_key.encode())
    finally:
        os.close(fd)
    return tmpfs_dir


def _setup_shared_memory(key: str, snapshot_b64: str) -> str:
    """Write shared memory snapshot to local directory."""
    import base64
    shared_dir = os.path.join(_config.shared_root, key)
    os.makedirs(shared_dir, mode=0o700, exist_ok=True)
    with open(os.path.join(shared_dir, "shared.db"), "wb") as f:
        f.write(base64.b64decode(snapshot_b64))
    return shared_dir


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


async def _graceful_shutdown_all():
    """Gracefully stop all agent containers (give them time to push work)."""
    containers = await _list_bond_containers()
    running = [c for c in containers if c.get("running")]
    if not running:
        return

    logger.warning("Shutting down %d agent containers gracefully", len(running))
    for c in running:
        # Give 60s grace period
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", "-t", "60", c["name"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    logger.info("All containers stopped")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Bond Host Daemon")
    parser.add_argument("--port", type=int, default=18795)
    parser.add_argument("--max-agents", type=int, default=4)
    parser.add_argument("--auth-token", type=str, default="")
    parser.add_argument("--heartbeat-timeout", type=int, default=10, help="Minutes")
    args = parser.parse_args()

    _config.port = args.port
    _config.max_agents = args.max_agents
    _config.auth_token = args.auth_token
    _config.heartbeat_timeout_minutes = args.heartbeat_timeout

    _heartbeat._timeout = args.heartbeat_timeout * 60

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
