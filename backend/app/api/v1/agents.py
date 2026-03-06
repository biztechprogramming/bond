"""Fixed agents API with implemented write endpoints for SpacetimeDB."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ulid import ULID

from backend.app.core.spacetimedb import get_stdb
from backend.app.agent.tools.definitions import TOOL_SUMMARIES

logger = logging.getLogger("bond.api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])

# ── Pydantic models ──────────────────────────────────────────────────────────

class WorkspaceMount(BaseModel):
    host_path: str
    mount_name: str
    container_path: str = ""
    readonly: bool = False

class ChannelConfig(BaseModel):
    channel: str
    enabled: bool = True
    sandbox_override: str | None = None

class AgentCreate(BaseModel):
    name: str
    display_name: str
    system_prompt: str
    model: str
    utility_model: str = "claude-sonnet-4-6"
    sandbox_image: str | None = None
    tools: list[str] = []
    max_iterations: int = 25
    auto_rag: bool = True
    auto_rag_limit: int = 5
    workspace_mounts: list[WorkspaceMount] = []
    channels: list[ChannelConfig] = []
    tool_access_mode: str = "allow"
    channel_access_mode: str = "allow"
    mcp_access_mode: str = "allow"
    mcp_servers: list[str] = []

class AgentUpdate(BaseModel):
    name: str | None = None
    display_name: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    utility_model: str | None = None
    sandbox_image: str | None = None
    tools: list[str] | None = None
    max_iterations: int | None = None
    auto_rag: bool | None = None
    auto_rag_limit: int | None = None
    workspace_mounts: list[WorkspaceMount] | None = None
    channels: list[ChannelConfig] | None = None
    tool_access_mode: str | None = None
    channel_access_mode: str | None = None
    mcp_access_mode: str | None = None
    mcp_servers: list[str] | None = None

# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_agent_by_id(agent_id: str) -> dict:
    """Fetch an agent with its workspace mounts and channels from SpacetimeDB."""
    stdb = get_stdb()
    
    # Get agent
    rows = await stdb.query(f"SELECT * FROM agents WHERE id = '{agent_id}'")
    if not rows:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    row = rows[0]
    
    # Get workspace mounts
    mounts_rows = await stdb.query(f"SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
    workspace_mounts = [
        {
            "host_path": m["host_path"],
            "mount_name": m["mount_name"],
            "container_path": m["container_path"] or f"/workspace/{m['mount_name']}",
            "readonly": bool(m["readonly"]),
        }
        for m in mounts_rows
    ]
    
    # Get channels (if table exists)
    channels = []
    try:
        channels_rows = await stdb.query(f"SELECT channel, enabled, sandbox_override FROM agent_channels WHERE agent_id = '{agent_id}'")
        channels = [
            {
                "channel": c["channel"],
                "enabled": bool(c["enabled"]),
                "sandbox_override": c["sandbox_override"],
            }
            for c in channels_rows
        ]
    except:
        # Table might not exist yet
        pass
    
    # Parse tools JSON
    tools = row["tools"]
    if isinstance(tools, str):
        try:
            tools = json.loads(tools)
        except json.JSONDecodeError:
            tools = []
            
    # Parse mcp_servers JSON
    mcp_servers = row.get("mcp_servers", "[]")
    if isinstance(mcp_servers, str):
        try:
            mcp_servers = json.loads(mcp_servers)
        except json.JSONDecodeError:
            mcp_servers = []
    
    agent = {
        "id": agent_id,
        "name": row["name"],
        "display_name": row["display_name"],
        "system_prompt": row["system_prompt"],
        "model": row["model"],
        "utility_model": row["utility_model"] or "claude-sonnet-4-6",
        "tools": tools,
        "mcp_servers": mcp_servers,
        "sandbox_image": row["sandbox_image"],
        "max_iterations": int(row["max_iterations"] or 10),
        "auto_rag": bool(row.get("auto_rag", True)),
        "auto_rag_limit": int(row.get("auto_rag_limit", 5)),
        "is_active": bool(row["is_active"]),
        "is_default": bool(row["is_default"]),
        "workspace_mounts": workspace_mounts,
        "channels": channels,
        "tool_access_mode": row.get("tool_access_mode", "allow"),
        "channel_access_mode": row.get("channel_access_mode", "allow"),
        "mcp_access_mode": row.get("mcp_access_mode", "allow"),
        "created_at": row["created_at"],
    }
    
    return agent

def _escape_sql(value):
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")

# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def list_agents():
    """List all agents with workspace mounts and channels."""
    stdb = get_stdb()
    # Query agents from SpacetimeDB
    rows = await stdb.query("SELECT id, name, display_name, system_prompt, model, utility_model, tools, mcp_servers, sandbox_image, max_iterations, is_active, is_default, created_at, tool_access_mode, channel_access_mode, mcp_access_mode FROM agents")
    
    # Sort in Python: default agents first, then by name
    rows = sorted(rows, key=lambda x: (not x["is_default"], x["name"].lower()))
    
    agents = []
    for row in rows:
        agent_id = row["id"]
        # Get workspace mounts
        mounts_rows = await stdb.query(f"SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
        workspace_mounts = [
            {
                "host_path": m["host_path"],
                "mount_name": m["mount_name"],
                "container_path": m["container_path"] or f"/workspace/{m['mount_name']}",
                "readonly": bool(m["readonly"]),
            }
            for m in mounts_rows
        ]
        
        # Get channels
        channels = []
        try:
            channels_rows = await stdb.query(f"SELECT channel, enabled, sandbox_override FROM agent_channels WHERE agent_id = '{agent_id}'")
            channels = [
                {
                    "channel": c["channel"],
                    "enabled": bool(c["enabled"]),
                    "sandbox_override": c["sandbox_override"],
                }
                for c in channels_rows
            ]
        except:
            pass
        
        # Parse tools JSON
        tools = row["tools"]
        if isinstance(tools, str):
            try:
                tools = json.loads(tools)
            except json.JSONDecodeError:
                tools = []
        
        # Parse mcp_servers JSON
        mcp_servers = row.get("mcp_servers", "[]")
        if isinstance(mcp_servers, str):
            try:
                mcp_servers = json.loads(mcp_servers)
            except json.JSONDecodeError:
                mcp_servers = []
        
        agent = {
            "id": agent_id,
            "name": row["name"],
            "display_name": row["display_name"],
            "system_prompt": row["system_prompt"],
            "model": row["model"],
            "utility_model": row["utility_model"] or "claude-sonnet-4-6",
            "tools": tools,
            "mcp_servers": mcp_servers,
            "sandbox_image": row["sandbox_image"],
            "max_iterations": int(row["max_iterations"] or 10),
            "is_active": bool(row["is_active"]),
            "is_default": bool(row["is_default"]),
            "workspace_mounts": workspace_mounts,
            "channels": channels,
            "tool_access_mode": row.get("tool_access_mode", "allow"),
            "channel_access_mode": row.get("channel_access_mode", "allow"),
            "mcp_access_mode": row.get("mcp_access_mode", "allow"),
            "created_at": row["created_at"],
        }
        agents.append(agent)
    
    return agents

@router.get("/tools")
async def list_tools():
    """List all available tools with name + description."""
    return [
        {"name": name, "description": desc}
        for name, desc in TOOL_SUMMARIES.items()
    ]

@router.get("/sandbox-images")
async def list_sandbox_images():
    """List available Docker images for sandbox execution."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "--format={{.Repository}}:{{.Tag}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return []
        images = [
            line.strip() for line in stdout.decode().splitlines()
            if line.strip() and "<none>" not in line
        ]
        return images
    except Exception:
        return []

@router.get("/browse-dirs")
async def browse_directories(path: str = "/", show_hidden: bool = False):
    """List directories at the given path for the workspace mount picker."""
    from pathlib import Path as P

    target = P(path).resolve()
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    dirs = []
    try:
        for entry in sorted(target.iterdir()):
            if not show_hidden and entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append({
                    "name": entry.name,
                    "path": str(entry),
                })
    except PermissionError:
        pass

    return {
        "current": str(target),
        "parent": str(target.parent) if target != target.parent else None,
        "directories": dirs,
    }

@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Get a single agent with mounts and channels."""
    return await _get_agent_by_id(agent_id)

@router.post("")
async def create_agent(body: AgentCreate):
    """Create a new agent with mounts and channels."""
    stdb = get_stdb()
    agent_id = str(ULID())
    created_at = int(time.time() * 1000)
    
    # Check if agent with this name already exists
    existing = await stdb.query(f"SELECT id FROM agents WHERE name = '{body.name}'")
    if existing:
        raise HTTPException(status_code=400, detail=f"Agent with name '{body.name}' already exists")
    
    # Prepare SQL
    tools_json = json.dumps(body.tools)
    mcp_servers_json = json.dumps(body.mcp_servers)
    
    query = f"""
    INSERT INTO agents (
        id, name, display_name, system_prompt, model, utility_model, 
        tools, mcp_servers, sandbox_image, max_iterations, auto_rag, auto_rag_limit,
        is_active, is_default, created_at,
        tool_access_mode, channel_access_mode, mcp_access_mode
    ) VALUES (
        '{agent_id}', 
        '{_escape_sql(body.name)}', 
        '{_escape_sql(body.display_name)}', 
        '{_escape_sql(body.system_prompt)}', 
        '{_escape_sql(body.model)}', 
        '{_escape_sql(body.utility_model)}', 
        '{_escape_sql(tools_json)}', 
        '{_escape_sql(mcp_servers_json)}', 
        '{_escape_sql(body.sandbox_image)}', 
        {body.max_iterations}, 
        {1 if body.auto_rag else 0}, 
        {body.auto_rag_limit},
        1, 0, {created_at},
        '{_escape_sql(body.tool_access_mode)}',
        '{_escape_sql(body.channel_access_mode)}',
        '{_escape_sql(body.mcp_access_mode)}'
    )
    """
    await stdb.execute(query)
    
    # Add workspace mounts
    for mount in body.workspace_mounts:
        mount_query = f"""
        INSERT INTO agent_workspace_mounts (agent_id, host_path, mount_name, container_path, readonly)
        VALUES ('{agent_id}', '{_escape_sql(mount.host_path)}', '{_escape_sql(mount.mount_name)}', '{_escape_sql(mount.container_path)}', {1 if mount.readonly else 0})
        """
        await stdb.execute(mount_query)
        
    # Add channels
    for channel in body.channels:
        channel_query = f"""
        INSERT INTO agent_channels (agent_id, channel, enabled, sandbox_override)
        VALUES ('{agent_id}', '{_escape_sql(channel.channel)}', {1 if channel.enabled else 0}, '{_escape_sql(channel.sandbox_override)}')
        """
        await stdb.execute(channel_query)
        
    return await _get_agent_by_id(agent_id)

@router.patch("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate):
    """Update an agent."""
    stdb = get_stdb()
    
    # Check if agent exists
    existing = await stdb.query(f"SELECT * FROM agents WHERE id = '{agent_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Build update query
    updates = []
    if body.name is not None:
        updates.append(f"name = '{_escape_sql(body.name)}'")
    if body.display_name is not None:
        updates.append(f"display_name = '{_escape_sql(body.display_name)}'")
    if body.system_prompt is not None:
        updates.append(f"system_prompt = '{_escape_sql(body.system_prompt)}'")
    if body.model is not None:
        updates.append(f"model = '{_escape_sql(body.model)}'")
    if body.utility_model is not None:
        updates.append(f"utility_model = '{_escape_sql(body.utility_model)}'")
    if body.tools is not None:
        updates.append(f"tools = '{_escape_sql(json.dumps(body.tools))}'")
    if body.mcp_servers is not None:
        updates.append(f"mcp_servers = '{_escape_sql(json.dumps(body.mcp_servers))}'")
    if body.sandbox_image is not None:
        updates.append(f"sandbox_image = '{_escape_sql(body.sandbox_image)}'")
    if body.max_iterations is not None:
        updates.append(f"max_iterations = {body.max_iterations}")
    if body.auto_rag is not None:
        updates.append(f"auto_rag = {1 if body.auto_rag else 0}")
    if body.auto_rag_limit is not None:
        updates.append(f"auto_rag_limit = {body.auto_rag_limit}")
    if body.tool_access_mode is not None:
        updates.append(f"tool_access_mode = '{_escape_sql(body.tool_access_mode)}'")
    if body.channel_access_mode is not None:
        updates.append(f"channel_access_mode = '{_escape_sql(body.channel_access_mode)}'")
    if body.mcp_access_mode is not None:
        updates.append(f"mcp_access_mode = '{_escape_sql(body.mcp_access_mode)}'")
        
    if updates:
        query = f"UPDATE agents SET {', '.join(updates)} WHERE id = '{agent_id}'"
        await stdb.execute(query)
        
    # Update workspace mounts if provided
    if body.workspace_mounts is not None:
        # Delete existing
        await stdb.execute(f"DELETE FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
        # Add new
        for mount in body.workspace_mounts:
            mount_query = f"""
            INSERT INTO agent_workspace_mounts (agent_id, host_path, mount_name, container_path, readonly)
            VALUES ('{agent_id}', '{_escape_sql(mount.host_path)}', '{_escape_sql(mount.mount_name)}', '{_escape_sql(mount.container_path)}', {1 if mount.readonly else 0})
            """
            await stdb.execute(mount_query)
            
    # Update channels if provided
    if body.channels is not None:
        # Delete existing
        await stdb.execute(f"DELETE FROM agent_channels WHERE agent_id = '{agent_id}'")
        # Add new
        for channel in body.channels:
            channel_query = f"""
            INSERT INTO agent_channels (agent_id, channel, enabled, sandbox_override)
            VALUES ('{agent_id}', '{_escape_sql(channel.channel)}', {1 if channel.enabled else 0}, '{_escape_sql(channel.sandbox_override)}')
            """
            await stdb.execute(channel_query)
            
    return await _get_agent_by_id(agent_id)

@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent."""
    stdb = get_stdb()
    
    # Check if agent exists
    existing = await stdb.query(f"SELECT is_default FROM agents WHERE id = '{agent_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if bool(existing[0]["is_default"]):
        raise HTTPException(status_code=400, detail="Cannot delete the default agent")
    
    # Delete agent
    await stdb.execute(f"DELETE FROM agents WHERE id = '{agent_id}'")
    # Delete mounts
    await stdb.execute(f"DELETE FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
    # Delete channels
    await stdb.execute(f"DELETE FROM agent_channels WHERE agent_id = '{agent_id}'")
    
    return {"status": "deleted"}
