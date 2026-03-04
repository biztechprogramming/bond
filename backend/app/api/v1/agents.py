"""Agents API — CRUD for agent profiles, tools listing, sandbox images."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from fastapi import APIRouter, HTTPException
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


# This function is no longer used after migrating to SpacetimeDB
# async def _get_agent_with_relations(db: AsyncSession, agent_id: str) -> dict | None:
#     """Fetch an agent with its workspace mounts and channels."""
#     # Removed for SpacetimeDB migration


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
    stdb = get_stdb()
    
    # Query agent from SpacetimeDB
    rows = await stdb.query(f"SELECT id, name, display_name, system_prompt, model, utility_model, tools, sandbox_image, max_iterations, is_active, is_default, created_at FROM agents WHERE id = '{agent_id}'")
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
        "is_active": bool(row["is_active"]),
        "is_default": bool(row["is_default"]),
        "workspace_mounts": workspace_mounts,
        "channels": channels,
        "created_at": row["created_at"],
    }
    
    return agent


@router.post("")
async def create_agent(body: AgentCreate):
    """Create a new agent with mounts and channels."""
    # TODO: Implement SpacetimeDB version
    # For now, return NotImplemented since we're migrating
    raise HTTPException(status_code=501, detail="Create agent not yet implemented for SpacetimeDB")


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate):
    """Update an existing agent."""
    # TODO: Implement SpacetimeDB version
    # For now, return NotImplemented since we're migrating
    raise HTTPException(status_code=501, detail="Update agent not yet implemented for SpacetimeDB")


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent."""
    # TODO: Implement SpacetimeDB version
    # For now, return NotImplemented since we're migrating
    raise HTTPException(status_code=501, detail="Delete agent not yet implemented for SpacetimeDB")


@router.post("/{agent_id}/default")
async def set_default_agent(agent_id: str):
    """Set an agent as the default."""
    # TODO: Implement SpacetimeDB version
    # For now, return NotImplemented since we're migrating
    raise HTTPException(status_code=501, detail="Set default agent not yet implemented for SpacetimeDB")
