"""MCP API Router."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.core.spacetimedb import get_stdb
from backend.app.mcp import mcp_manager, MCPServerConfig
from backend.app.mcp.manager import _is_stdb_none

logger = logging.getLogger("bond.mcp.api")

router = APIRouter(prefix="/mcp", tags=["mcp"])


class MCPServerRead(BaseModel):
    id: str
    name: str
    command: str
    args: List[str]
    env: dict
    enabled: bool
    agent_id: Optional[str] = None
    status: str = "stopped"

class MCPServerCreate(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = []
    env: Optional[dict] = {}
    enabled: Optional[bool] = True
    agent_id: Optional[str] = None

class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[dict] = None
    enabled: Optional[bool] = None
    agent_id: Optional[str] = None


class MCPServerTestRequest(BaseModel):
    name: str = "test"
    command: str
    args: Optional[List[str]] = []
    env: Optional[dict] = {}


class MCPServerTestResponse(BaseModel):
    success: bool
    status: str
    tools: List[dict] = []
    connect_time_ms: int
    error: Optional[str] = None


class MCPServerStatusItem(BaseModel):
    name: str
    scope: str = "global"
    enabled: bool = True
    status: str
    healthy_connections: int = 0
    pool_size: int = 2
    tools: List[str] = []
    tool_count: int = 0
    last_error: Optional[str] = None
    last_checked: Optional[str] = None


# --- Proxy endpoints (called by Gateway broker) ---

class MCPProxyCallRequest(BaseModel):
    tool_name: str
    arguments: dict = {}
    agent_id: str

class MCPProxyCallResponse(BaseModel):
    result: Optional[str] = None
    error: Optional[str] = None


def check_mcp_acl(agent_id: str, tool_name: str) -> bool:
    """Check if an agent is allowed to use a specific MCP tool.

    Currently allows all — ACL enforcement is done at the Gateway broker layer.
    This is a hook for future backend-side restrictions.
    """
    return True


@router.get("/proxy/tools")
async def proxy_list_tools(agent_id: str):
    """List available MCP tools for an agent. Called by Gateway broker."""
    await mcp_manager.ensure_servers_loaded(agent_id=agent_id)
    tools = await mcp_manager.list_tools(scope=agent_id)
    # Also include global-scope tools
    global_tools = await mcp_manager.list_tools(scope="global")

    # Merge, dedup by name
    seen = {t["name"] for t in tools}
    for t in global_tools:
        if t["name"] not in seen:
            tools.append(t)
            seen.add(t["name"])

    return {"tools": tools}


@router.post("/proxy/call", response_model=MCPProxyCallResponse)
async def proxy_call_tool(req: MCPProxyCallRequest):
    """Execute an MCP tool. Called by Gateway broker."""
    if not check_mcp_acl(req.agent_id, req.tool_name):
        raise HTTPException(status_code=403, detail=f"Agent {req.agent_id} not allowed to use {req.tool_name}")

    await mcp_manager.ensure_servers_loaded(agent_id=req.agent_id)

    # Parse bond tool name: mcp_{server}_{tool}
    if not req.tool_name.startswith("mcp_"):
        raise HTTPException(status_code=400, detail=f"Invalid MCP tool name: {req.tool_name}")

    # Find matching server from pools
    server_name = None
    mcp_tool_name = None
    for key in mcp_manager.connection_pools:
        sname, _ = key.split("::", 1) if "::" in key else (key, "global")
        prefix = f"mcp_{sname}_"
        if req.tool_name.startswith(prefix):
            server_name = sname
            mcp_tool_name = req.tool_name[len(prefix):]
            break

    if not server_name or not mcp_tool_name:
        raise HTTPException(status_code=404, detail=f"No server found for tool: {req.tool_name}")

    result = await mcp_manager.call_tool(server_name, mcp_tool_name, req.arguments, scope=req.agent_id)
    if "error" in result:
        return MCPProxyCallResponse(error=result["error"])
    return MCPProxyCallResponse(result=result.get("result", ""))


# --- Status and test endpoints ---

@router.get("/servers/status")
async def get_servers_status():
    """Get live status of all MCP server connection pools."""
    await mcp_manager.ensure_servers_loaded()
    pool_status = mcp_manager.get_pool_status()

    stdb = get_stdb()
    rows = await stdb.query("SELECT * FROM mcp_servers")

    servers = []
    seen_names = set()

    for key, ps in pool_status.items():
        seen_names.add(ps["server"])
        servers.append(ps)

    for row in rows:
        name = row["name"]
        if name not in seen_names:
            servers.append({
                "server": name,
                "scope": "global",
                "pool_size": 0,
                "healthy_connections": 0,
                "has_healthy": False,
                "status": "disabled" if not row["enabled"] else "stopped",
                "tools": [],
                "tool_count": 0,
                "last_error": None,
                "last_checked": None,
            })

    return {"servers": servers}


@router.post("/servers/test", response_model=MCPServerTestResponse)
async def test_mcp_server(config: MCPServerTestRequest):
    """Test an MCP server connection without saving it."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from contextlib import AsyncExitStack

    start = time.monotonic()

    params = StdioServerParameters(
        command=config.command,
        args=config.args or [],
        env={**os.environ, **(config.env or {})}
    )

    exit_stack = AsyncExitStack()
    try:
        read, write = await asyncio.wait_for(
            exit_stack.enter_async_context(stdio_client(params)),
            timeout=10.0
        )
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=10.0)
        tools_result = await asyncio.wait_for(session.list_tools(), timeout=10.0)
        tools = [{"name": t.name, "description": t.description or ""} for t in tools_result.tools]
        elapsed = int((time.monotonic() - start) * 1000)

        return MCPServerTestResponse(
            success=True,
            status="connected",
            tools=tools,
            connect_time_ms=elapsed,
            error=None
        )
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return MCPServerTestResponse(
            success=False,
            status="error",
            tools=[],
            connect_time_ms=elapsed,
            error="Connection timed out after 10 seconds"
        )
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return MCPServerTestResponse(
            success=False,
            status="error",
            tools=[],
            connect_time_ms=elapsed,
            error=str(e)
        )
    finally:
        try:
            await exit_stack.aclose()
        except Exception:
            pass


# --- Existing CRUD endpoints ---

@router.get("/servers", response_model=List[MCPServerRead])
async def list_servers(agent_id: Optional[str] = None):
    """List configured MCP servers. If agent_id is provided, returns global + agent-specific."""
    stdb = get_stdb()

    rows = await stdb.query("SELECT * FROM mcp_servers")

    if agent_id:
        filtered_rows = []
        for row in rows:
            row_agent_id = row.get("agent_id")
            if _is_stdb_none(row_agent_id) or row_agent_id == agent_id:
                filtered_rows.append(row)
        rows = filtered_rows

    servers = []
    for row in rows:
        server = dict(row)
        if _is_stdb_none(server.get("agent_id")):
            server["agent_id"] = None
        elif server.get("agent_id") == "":
            server["agent_id"] = None

        server["args"] = json.loads(server["args"]) if server["args"] else []
        server["env"] = json.loads(server["env"]) if server["env"] else {}

        # Check pool status
        pool_key = f"{server['name']}::global"
        pool = mcp_manager.connection_pools.get(pool_key)
        if pool:
            if pool.has_healthy_connection:
                server["status"] = "connected"
            elif pool.last_error:
                server["status"] = "error"
            else:
                server["status"] = "stopped"
        else:
            server["status"] = "disabled" if not server["enabled"] else "stopped"

        servers.append(server)

    return servers

@router.post("/servers", response_model=MCPServerRead)
async def create_server(data: MCPServerCreate):
    """Create a new MCP server configuration."""
    stdb = get_stdb()
    server_id = str(uuid4())

    try:
        success = await stdb.call_reducer("add_mcp_server", [
            server_id,
            data.name,
            data.command,
            json.dumps(data.args or []),
            json.dumps(data.env or {}),
            data.agent_id if data.agent_id else None
        ])

        if not success:
            raise HTTPException(status_code=500, detail="Failed to create server in SpacetimeDB")

        if data.enabled:
            config = MCPServerConfig(
                name=data.name,
                command=data.command,
                args=data.args or [],
                env=data.env or {},
                enabled=data.enabled
            )
            await mcp_manager.add_server(config)

        return {
            "id": server_id,
            "name": data.name,
            "command": data.command,
            "args": data.args or [],
            "env": data.env or {},
            "enabled": data.enabled,
            "agent_id": data.agent_id,
            "status": "running" if data.enabled else "stopped"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create server: {str(e)}")

@router.delete("/servers/{server_id}")
async def delete_server(server_id: str):
    """Delete an MCP server configuration."""
    stdb = get_stdb()

    rows = await stdb.query(f"SELECT name FROM mcp_servers WHERE id = '{server_id}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    server_name = rows[0]["name"]

    # Stop all pools for this server
    keys_to_remove = [k for k in mcp_manager.connection_pools if k.startswith(f"{server_name}::")]
    for key in keys_to_remove:
        await mcp_manager.connection_pools[key].stop()
        del mcp_manager.connection_pools[key]

    success = await stdb.call_reducer("delete_mcp_server", [server_id])
    if not success:
        await stdb.query(f"DELETE FROM mcp_servers WHERE id = '{server_id}'")

    return {"status": "deleted"}

@router.post("/servers/{server_id}/toggle")
async def toggle_server(server_id: str):
    """Toggle enabled status of an MCP server."""
    stdb = get_stdb()

    rows = await stdb.query(f"SELECT * FROM mcp_servers WHERE id = '{server_id}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    row = rows[0]
    new_enabled = not row["enabled"]
    server_name = row["name"]

    success = await stdb.call_reducer("update_mcp_server", [
        server_id,
        row["name"],
        row["command"],
        row["args"],
        row["env"],
        new_enabled,
        row.get("agent_id")
    ])

    if not success:
        logger.warning(f"Failed to update MCP server {server_id} via reducer")

    if new_enabled:
        config = MCPServerConfig(
            name=server_name,
            command=row["command"],
            args=json.loads(row["args"]) if row["args"] else [],
            env=json.loads(row["env"]) if row["env"] else {},
            enabled=True
        )
        await mcp_manager.add_server(config)
        return {"status": "started"}
    else:
        keys_to_remove = [k for k in mcp_manager.connection_pools if k.startswith(f"{server_name}::")]
        for key in keys_to_remove:
            pool = mcp_manager.connection_pools.get(key)
            if pool:
                await pool.stop()
        return {"status": "stopped"}
