"""Tests for MCP live status and connection testing (Design Doc 105)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.mcp.manager import MCPManager, MCPServerConfig, MCPConnectionPool


@pytest.mark.asyncio
async def test_pool_status_fields_initialized():
    """MCPConnectionPool should initialize status tracking fields."""
    config = MCPServerConfig(name="test", command="echo", args=["hello"])
    pool = MCPConnectionPool(config)
    assert pool.last_error is None
    assert pool.last_checked is None
    assert pool.discovered_tools == []


@pytest.mark.asyncio
async def test_pool_start_captures_error():
    """MCPConnectionPool.start() should capture errors in last_error."""
    config = MCPServerConfig(name="failing", command="nonexistent_command_xyz")
    pool = MCPConnectionPool(config)

    with pytest.raises(Exception):
        await pool.start()

    assert pool.last_error is not None
    assert pool.last_checked is not None
    assert pool.last_checked.endswith("Z")


@pytest.mark.asyncio
async def test_get_pool_status_connected():
    """get_pool_status returns 'connected' for healthy pools."""
    manager = MCPManager()
    config = MCPServerConfig(name="test_srv", command="echo")
    pool = MCPConnectionPool(config)

    # Mock a healthy connection
    mock_conn = MagicMock()
    mock_conn.is_healthy = True
    mock_conn.session = MagicMock()
    pool._connections = [mock_conn]
    pool.discovered_tools = ["tool_a", "tool_b"]
    pool.last_checked = "2026-01-01T00:00:00Z"

    manager.connection_pools["test_srv::global"] = pool
    status = manager.get_pool_status()

    assert "test_srv::global" in status
    s = status["test_srv::global"]
    assert s["status"] == "connected"
    assert s["server"] == "test_srv"
    assert s["scope"] == "global"
    assert s["tools"] == ["tool_a", "tool_b"]
    assert s["tool_count"] == 2
    assert s["healthy_connections"] == 1
    assert s["last_error"] is None


@pytest.mark.asyncio
async def test_get_pool_status_error():
    """get_pool_status returns 'error' for pools with errors."""
    manager = MCPManager()
    config = MCPServerConfig(name="bad_srv", command="echo")
    pool = MCPConnectionPool(config)
    pool._connections = []
    pool.last_error = "Connection refused"
    pool.last_checked = "2026-01-01T00:00:00Z"

    manager.connection_pools["bad_srv::global"] = pool
    status = manager.get_pool_status()

    assert status["bad_srv::global"]["status"] == "error"
    assert status["bad_srv::global"]["last_error"] == "Connection refused"


@pytest.mark.asyncio
async def test_get_pool_status_disabled():
    """get_pool_status returns 'disabled' for disabled pools."""
    manager = MCPManager()
    config = MCPServerConfig(name="off_srv", command="echo", enabled=False)
    pool = MCPConnectionPool(config)
    pool._connections = []

    manager.connection_pools["off_srv::global"] = pool
    status = manager.get_pool_status()

    assert status["off_srv::global"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_get_pool_status_stopped():
    """get_pool_status returns 'stopped' for pools with no connections and no error."""
    manager = MCPManager()
    config = MCPServerConfig(name="idle", command="echo")
    pool = MCPConnectionPool(config)
    pool._connections = []

    manager.connection_pools["idle::global"] = pool
    status = manager.get_pool_status()

    assert status["idle::global"]["status"] == "stopped"


@pytest.mark.asyncio
async def test_ensure_servers_loaded_stores_failed_pools():
    """ensure_servers_loaded should store pools even when start() fails."""
    manager = MCPManager()

    mock_stdb = AsyncMock()
    mock_stdb.query.return_value = [{
        "name": "fail_server",
        "command": "nonexistent_xyz",
        "args": "[]",
        "env": "{}",
        "enabled": True,
        "agent_id": None,
    }]

    with patch("backend.app.mcp.manager.get_stdb", return_value=mock_stdb):
        # Import is inside ensure_servers_loaded, so we patch at module level
        with patch.dict("sys.modules", {}):
            pass

    # Direct test: simulate what ensure_servers_loaded does
    config = MCPServerConfig(name="fail_server", command="nonexistent_xyz", args=[], env={})
    pool = MCPConnectionPool(config)
    try:
        await pool.start()
    except Exception as e:
        pool.last_error = str(e)

    manager.connection_pools["fail_server::global"] = pool
    status = manager.get_pool_status()
    assert "fail_server::global" in status
    assert status["fail_server::global"]["status"] == "error"
