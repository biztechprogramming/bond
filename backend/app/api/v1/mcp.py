"""MCP API Router."""

from __future__ import annotations

import json
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.core.spacetimedb import get_stdb
from backend.app.mcp import mcp_manager, MCPServerConfig

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

@router.get("/servers", response_model=List[MCPServerRead])
async def list_servers(agent_id: Optional[str] = None):
    """List configured MCP servers. If agent_id is provided, returns global + agent-specific."""
    stdb = get_stdb()
    
    # Get all servers (SpacetimeDB doesn't support IS NULL well)
    rows = await stdb.query("SELECT * FROM mcp_servers")
    
    if agent_id:
        # Filter in Python: servers with this agent_id or global (agent_id is none)
        filtered_rows = []
        for row in rows:
            row_agent_id = row.get("agent_id")
            # Check if agent_id is "none" (global) or matches
            if (isinstance(row_agent_id, dict) and "none" in row_agent_id) or row_agent_id == agent_id:
                filtered_rows.append(row)
        rows = filtered_rows
    # else: return all rows
    
    servers = []
    for row in rows:
        server = dict(row)
        # Handle agent_id which might be {"none": []} for None
        if isinstance(server.get("agent_id"), dict) and "none" in server["agent_id"]:
            server["agent_id"] = None
        elif server.get("agent_id") == "":
            server["agent_id"] = None
            
        server["args"] = json.loads(server["args"]) if server["args"] else []
        server["env"] = json.loads(server["env"]) if server["env"] else {}
        
        # Add runtime status
        conn = mcp_manager.connections.get(server["name"])
        server["status"] = "running" if conn and conn.session else "stopped"
        
        servers.append(server)
    
    return servers

@router.post("/servers", response_model=MCPServerRead)
async def create_server(data: MCPServerCreate):
    """Create a new MCP server configuration."""
    stdb = get_stdb()
    server_id = str(uuid4())
    
    try:
        # Call add_mcp_server reducer
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
        
        # If enabled, try to start it immediately
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
    
    # Get name first to stop it
    rows = await stdb.query(f"SELECT name FROM mcp_servers WHERE id = '{server_id}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    
    server_name = rows[0]["name"]
    
    # Stop the server
    conn = mcp_manager.connections.get(server_name)
    if conn:
        await conn.stop()
        del mcp_manager.connections[server_name]
    
    # Call delete_mcp_server reducer if it exists, otherwise use SQL
    success = await stdb.call_reducer("delete_mcp_server", [server_id])
    if not success:
        # Fallback to SQL DELETE
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
    
    # Update using update_mcp_server reducer if it exists
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
        # SpacetimeDB might not support SQL UPDATE, just log the error
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to update MCP server {server_id} via reducer, SQL UPDATE might not work in SpacetimeDB")
        # Don't try SQL UPDATE as SpacetimeDB might not support it
        # await stdb.query(f"UPDATE mcp_servers SET enabled = {str(new_enabled).lower()} WHERE id = '{server_id}'")
    
    if new_enabled:
        # Start
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
        # Stop
        conn = mcp_manager.connections.get(server_name)
        if conn:
            await conn.stop()
            # We don't delete from manager connections, just stop the session
        return {"status": "stopped"}
