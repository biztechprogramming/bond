"""Fixed agents API with implemented write endpoints for SpacetimeDB."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ulid import ULID

from backend.app.core.spacetimedb import get_stdb
from backend.app.agent.tools.definitions import TOOL_SUMMARIES

logger = logging.getLogger("bond.api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])


# ── Pydantic models ──────────────────────────────────────────


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


# ── Helpers ───────────────────────────────────────────────────


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
    
    agent = {
        "id": agent_id,
        "name": row["name"],
        "display_name": row["display_name"],
        "system_prompt": row["system_prompt"],
        "model": row["model"],
        "utility_model": row["utility_model"] or "claude-sonnet-4-6",
        "tools": tools,
        "sandbox_image": row["sandbox_image"],
        "max_iterations": int(row["max_iterations"] or 10),
        "auto_rag": bool(row.get("auto_rag", True)),
        "auto_rag_limit": int(row.get("auto_rag_limit", 5)),
        "is_active": bool(row["is_active"]),
        "is_default": bool(row["is_default"]),
        "workspace_mounts": workspace_mounts,
        "channels": channels,
        "created_at": row["created_at"],
    }
    
    return agent


def _escape_sql(value):
    """Escape single quotes for SQL."""
    if value is None:
        return ''
    return str(value).replace("'", "''")


# ── Endpoints ─────────────────────────────────────────────────


@router.get("")
async def list_agents():
    """List all agents with workspace mounts and channels."""
    stdb = get_stdb()
    # Query agents from SpacetimeDB (SpacetimeDB doesn't support ORDER BY)
    rows = await stdb.query("SELECT id, name, display_name, system_prompt, model, utility_model, tools, sandbox_image, max_iterations, is_active, is_default, created_at FROM agents")
    
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
            # Table might not exist yet
            pass
        
        # Parse tools JSON
        tools = row["tools"]
        if isinstance(tools, str):
            try:
                tools = json.loads(tools)
            except json.JSONDecodeError:
                tools = []
        
        agent = {
            "id": agent_id,
            "name": row["name"],
            "display_name": row["display_name"],
            "system_prompt": row["system_prompt"],
            "model": row["model"],
            "utility_model": row["utility_model"] or "claude-sonnet-4-6",
            "tools": tools,
            "sandbox_image": row["sandbox_image"],
            "max_iterations": int(row["max_iterations"] or 10),
            "is_active": bool(row["is_active"]),
            "is_default": bool(row["is_default"]),
            "workspace_mounts": workspace_mounts,
            "channels": channels,
            "created_at": row["created_at"],
        }
        agents.append(agent)
    
    return agents


@router.get("/tools")
async def list_tools():
    """List all 14 available tools with name + description."""
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
    
    # Insert agent
    tools_json = json.dumps(body.tools or [])
    await stdb.query(f"""
        INSERT INTO agents (
            id, name, display_name, system_prompt, model, utility_model,
            tools, sandbox_image, max_iterations, is_active, is_default, created_at
        ) VALUES (
            '{agent_id}',
            '{body.name}',
            '{body.display_name}',
            '{body.system_prompt}',
            '{body.model}',
            '{body.utility_model}',
            '{tools_json}',
            '{body.sandbox_image or ""}',
            {body.max_iterations},
            true,
            false,
            {created_at}
        )
    """)
    
    # Insert workspace mounts
    for mount in body.workspace_mounts:
        mount_id = str(ULID())
        await stdb.query(f"""
            INSERT INTO agent_workspace_mounts (
                id, agent_id, host_path, mount_name, container_path, readonly
            ) VALUES (
                '{mount_id}',
                '{agent_id}',
                '{mount.host_path}',
                '{mount.mount_name}',
                '{mount.container_path or f"/workspace/{mount.mount_name}"}',
                {str(mount.readonly).lower()}
            )
        """)
    
    # Insert channels
    for channel in body.channels:
        # Use SQL INSERT - no reducer for channels yet
        await stdb.query(f"""
            INSERT INTO agent_channels (agent_id, channel, enabled, sandbox_override)
            VALUES (
                '{agent_id}',
                '{channel.channel}',
                {str(channel.enabled).lower()},
                '{channel.sandbox_override or ""}'
            )
        """)
    
    # Return the created agent
    return await _get_agent_by_id(agent_id)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    stdb: SpacetimeDB = Depends(get_stdb)
):
    # Check if agent exists
    existing = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")

    updates = []

    if body.name is not None:
        # Check if new name is already taken by another agent
        name_check = await stdb.query(f"SELECT id FROM agents WHERE name = '{_escape_sql(body.name)}' AND id != '{agent_id}'")
        if name_check:
            raise HTTPException(status_code=400, detail=f"Agent with name '{body.name}' already exists")
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
        tools_json = json.dumps(body.tools)
        updates.append(f"tools = '{_escape_sql(tools_json)}'")

    if body.sandbox_image is not None:
        updates.append(f"sandbox_image = '{_escape_sql(body.sandbox_image)}'")

    if body.max_iterations is not None:
        updates.append(f"max_iterations = {body.max_iterations}")

    if body.auto_rag is not None:
        updates.append(f"auto_rag = {str(body.auto_rag).lower()}")

    if body.auto_rag_limit is not None:
        updates.append(f"auto_rag_limit = {body.auto_rag_limit}")

    if updates:
        await stdb.query(f"""
            UPDATE agents
            SET { ", ".join(updates) }
            WHERE id = '{agent_id}'
        """)

    # Update workspace mounts if provided
    if body.workspace_mounts is not None:
        # Delete existing mounts
        await stdb.query(f"DELETE FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
        
        # Add new mounts
        for mount in body.workspace_mounts:
            mount_id = str(ULID())
            await stdb.query(f"""
                INSERT INTO agent_workspace_mounts (
                    id, agent_id, host_path, mount_name, container_path, readonly
                ) VALUES (
                    '{mount_id}',
                    '{agent_id}',
                    '{_escape_sql(mount.host_path)}',
                    '{_escape_sql(mount.mount_name)}',
                    '{_escape_sql(mount.container_path or f"/workspace/{mount.mount_name}")}',
                    {str(mount.readonly).lower()}
                )
            """)

    # Update channels if provided
    if body.channels is not None:
        try:
            await stdb.query(f"DELETE FROM agent_channels WHERE agent_id = '{agent_id}'")
        except Exception:
            pass  # Table might not exist
        
        for channel in body.channels:
            await stdb.query(f"""
                INSERT INTO agent_channels (agent_id, channel, enabled, sandbox_override)
                VALUES (
                    '{agent_id}',
                    '{_escape_sql(channel.channel)}',
                    {str(channel.enabled).lower()},
                    '{_escape_sql(channel.sandbox_override or "")}'
                )
            """)

    return await _get_agent_by_id(agent_id)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent."""
    stdb = get_stdb()
    
    # Check if agent exists
    existing = await stdb.query(f"SELECT id, is_default FROM agents WHERE id = '{agent_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Don't allow deleting the default agent
    if existing[0]["is_default"]:
        raise HTTPException(status_code=400, detail="Cannot delete the default agent")
    
    # Delete in transaction-like order (foreign keys might not be enforced)
    try:
        await stdb.query(f"DELETE FROM agent_channels WHERE agent_id = '{agent_id}'")
    except:
        pass  # Table might not exist
    
    await stdb.query(f"DELETE FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
    await stdb.query(f"DELETE FROM agents WHERE id = '{agent_id}'")
    
    return {"success": True, "message": f"Agent {agent_id} deleted"}


@router.post("/{agent_id}/default")
async def set_default_agent(agent_id: str):
    """Set an agent as the default."""
    stdb = get_stdb()
    
    # Check if agent exists
    existing = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # First, unset any existing default
    await stdb.query("UPDATE agents SET is_default = false WHERE is_default = true")
    
    # Set this agent as default
    await stdb.query(f"UPDATE agents SET is_default = true WHERE id = '{agent_id}'")
    
    # Return the updated agent
    return await _get_agent_by_id(agent_id)