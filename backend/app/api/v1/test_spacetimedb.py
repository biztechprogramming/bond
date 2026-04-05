"""Test SpacetimeDB API — manage test SpacetimeDB instance for visual UI testing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.core.spacetimedb import get_stdb

logger = logging.getLogger("bond.api.test_spacetimedb")

router = APIRouter(prefix="/test-spacetimedb", tags=["test-spacetimedb"])

CONTAINER_NAME = "bond-test-spacetimedb"
SCRIPT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "skills", "visual-ui-test", "scripts")
)
SETUP_SCRIPT = os.path.join(SCRIPT_DIR, "setup-test-spacetimedb.sh")
TEARDOWN_SCRIPT = os.path.join(SCRIPT_DIR, "teardown-test-spacetimedb.sh")

# Settings keys
SETTINGS_PREFIX = "test_spacetimedb"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 18797
DEFAULT_MODULE = "bond-core-v2"


# ── Request/Response models ──────────────────────────────────────────────────


class ConnectivityRequest(BaseModel):
    host: str
    port: int


class ContainerConnectivityRequest(BaseModel):
    host: str
    port: int
    container_host_id: str | None = None


class SettingsUpdate(BaseModel):
    host: str
    port: int
    module: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _escape(s: str) -> str:
    return s.replace("'", "''")


async def _get_setting(key: str, default: str) -> str:
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT value FROM settings WHERE key = '{_escape(key)}'")
    if rows:
        return rows[0]["value"]
    return default


async def _get_host() -> str:
    return await _get_setting(f"{SETTINGS_PREFIX}.host", DEFAULT_HOST)


async def _get_port() -> int:
    val = await _get_setting(f"{SETTINGS_PREFIX}.port", str(DEFAULT_PORT))
    try:
        return int(val)
    except ValueError:
        return DEFAULT_PORT


async def _get_module() -> str:
    return await _get_setting(f"{SETTINGS_PREFIX}.module", DEFAULT_MODULE)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status():
    """Check if the test SpacetimeDB container is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [line for line in result.stdout.strip().split("\n") if line.strip()]
        if lines:
            info = json.loads(lines[0])
            return {
                "running": True,
                "port": await _get_port(),
                "host": await _get_host(),
                "container_id": info.get("ID"),
                "uptime": info.get("Status"),
            }
    except Exception as e:
        logger.warning("Failed to check docker status: %s", e)

    return {
        "running": False,
        "port": await _get_port(),
        "host": await _get_host(),
        "container_id": None,
        "uptime": None,
    }


@router.post("/start")
async def start_instance():
    """Run the setup script and stream progress as SSE."""

    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            "bash", SETUP_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            evt = json.dumps({"type": "info", "message": text})
            yield f"data: {evt}\n\n"
        code = await proc.wait()
        done_evt = json.dumps({
            "status": "done",
            "success": code == 0,
            "step": "complete",
            "message": "Setup completed successfully." if code == 0 else f"Setup failed with exit code {code}.",
        })
        yield f"data: {done_evt}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/stop")
async def stop_instance():
    """Run the teardown script."""
    try:
        result = subprocess.run(
            ["bash", TEARDOWN_SCRIPT],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout + result.stderr,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-connectivity")
async def test_connectivity(req: ConnectivityRequest):
    """Test connectivity to a SpacetimeDB instance from the backend host."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
             f"http://{req.host}:{req.port}/v1/health"],
            capture_output=True, text=True, timeout=10,
        )
        latency = round((time.monotonic() - start) * 1000)
        reachable = result.returncode == 0
        return {"reachable": reachable, "latency_ms": latency, "error": None if reachable else f"HTTP {result.stdout}"}
    except subprocess.TimeoutExpired:
        return {"reachable": False, "latency_ms": 10000, "error": "Timeout"}
    except Exception as e:
        return {"reachable": False, "latency_ms": 0, "error": str(e)}


@router.post("/test-from-container")
async def test_from_container(req: ContainerConnectivityRequest):
    """Test connectivity from inside an agent container (or via SSH to a host)."""
    curl_cmd = f"curl -sf -o /dev/null -w '%{{http_code}}' http://{req.host}:{req.port}/v1/health"

    start = time.monotonic()
    try:
        if req.container_host_id:
            cmd = ["ssh", "-o", "ConnectTimeout=5", req.container_host_id, curl_cmd]
        else:
            cmd = ["bash", "-c", curl_cmd]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        latency = round((time.monotonic() - start) * 1000)
        reachable = result.returncode == 0
        return {"reachable": reachable, "latency_ms": latency, "error": None if reachable else f"Exit {result.returncode}"}
    except subprocess.TimeoutExpired:
        return {"reachable": False, "latency_ms": 15000, "error": "Timeout"}
    except Exception as e:
        return {"reachable": False, "latency_ms": 0, "error": str(e)}


@router.get("/settings")
async def get_settings():
    """Return saved test SpacetimeDB settings."""
    return {
        "host": await _get_host(),
        "port": await _get_port(),
        "module": await _get_module(),
    }


@router.put("/settings")
async def update_settings(req: SettingsUpdate):
    """Save test SpacetimeDB settings."""
    stdb = get_stdb()
    pairs = {
        f"{SETTINGS_PREFIX}.host": req.host,
        f"{SETTINGS_PREFIX}.port": str(req.port),
        f"{SETTINGS_PREFIX}.module": req.module,
    }
    for key, value in pairs.items():
        await stdb.call_reducer("set_setting", [key, value])
    return {"host": req.host, "port": req.port, "module": req.module}
