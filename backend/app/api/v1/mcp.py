"""MCP API Router."""

from __future__ import annotations

import json
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
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
async def list_servers(db: AsyncSession = Depends(get_db)):
    """List all configured MCP servers."""
    result = await db.execute(text("SELECT * FROM mcp_servers"))
    rows = result.mappings().all()
    
    servers = []
    for row in rows:
        server = dict(row)
        server["args"] = json.loads(server["args"])
        server["env"] = json.loads(server["env"])
        
        # Add runtime status
        conn = mcp_manager.connections.get(server["name"])
        server["status"] = "running" if conn and conn.session else "stopped"
        
        servers.append(server)
    
    return servers

@router.post("/servers", response_model=MCPServerRead)
async def create_server(data: MCPServerCreate, db: AsyncSession = Depends(get_db)):
    """Create a new MCP server configuration."""
    server_id = str(uuid4())
    try:
        await db.execute(
            text("""
                INSERT INTO mcp_servers (id, name, command, args, env, enabled, agent_id)
                VALUES (:id, :name, :command, :args, :env, :enabled, :agent_id)
            """),
            {
                "id": server_id,
                "name": data.name,
                "command": data.command,
                "args": json.dumps(data.args),
                "env": json.dumps(data.env),
                "enabled": 1 if data.enabled else 0,
                "agent_id": data.agent_id
            }
        )
        await db.commit()
        
        # If enabled, try to start it immediately
        if data.enabled:
            config = MCPServerConfig(
                name=data.name,
                command=data.command,
                args=data.args,
                env=data.env,
                enabled=data.enabled
            )
            await mcp_manager.add_server(config)
            
        return {**data.dict(), "id": server_id, "status": "running" if data.enabled else "stopped"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create server: {str(e)}")

@router.delete("/servers/{server_id}")
async def delete_server(server_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an MCP server configuration."""
    # Get name first to stop it
    result = await db.execute(text("SELECT name FROM mcp_servers WHERE id = :id"), {"id": server_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Server not found")
    
    server_name = row[0]
    
    # Stop the server
    conn = mcp_manager.connections.get(server_name)
    if conn:
        await conn.stop()
        del mcp_manager.connections[server_name]
    
    await db.execute(text("DELETE FROM mcp_servers WHERE id = :id"), {"id": server_id})
    await db.commit()
    return {"status": "deleted"}

@router.post("/servers/{server_id}/toggle")
async def toggle_server(server_id: str, db: AsyncSession = Depends(get_db)):
    """Toggle enabled status of an MCP server."""
    result = await db.execute(text("SELECT * FROM mcp_servers WHERE id = :id"), {"id": server_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Server not found")
    
    new_status = 0 if row["enabled"] else 1
    server_name = row["name"]
    
    await db.execute(
        text("UPDATE mcp_servers SET enabled = :status WHERE id = :id"),
        {"status": new_status, "id": server_id}
    )
    await db.commit()
    
    if new_status == 1:
        # Start
        config = MCPServerConfig(
            name=server_name,
            command=row["command"],
            args=json.loads(row["args"]),
            env=json.loads(row["env"]),
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
