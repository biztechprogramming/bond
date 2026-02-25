"""Agents API — CRUD for agent profiles, tools listing, sandbox images."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.db.session import get_db
from backend.app.agent.tools.definitions import TOOL_SUMMARIES

logger = logging.getLogger("bond.api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])


# ── Pydantic models ──────────────────────────────────────────


class WorkspaceMount(BaseModel):
    host_path: str
    mount_name: str
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
    sandbox_image: str | None = None
    tools: list[str] | None = None
    max_iterations: int | None = None
    auto_rag: bool | None = None
    auto_rag_limit: int | None = None
    workspace_mounts: list[WorkspaceMount] | None = None
    channels: list[ChannelConfig] | None = None


# ── Helpers ───────────────────────────────────────────────────


async def _get_agent_with_relations(db: AsyncSession, agent_id: str) -> dict | None:
    """Fetch an agent with its workspace mounts and channels."""
    result = await db.execute(
        text("SELECT * FROM agents WHERE id = :id"), {"id": agent_id}
    )
    row = result.mappings().first()
    if row is None:
        return None

    agent = dict(row)
    agent["tools"] = json.loads(agent["tools"]) if isinstance(agent["tools"], str) else agent["tools"]
    agent["auto_rag"] = bool(agent["auto_rag"])
    agent["is_default"] = bool(agent["is_default"])
    agent["is_active"] = bool(agent["is_active"])

    # Fetch mounts
    mounts_result = await db.execute(
        text("SELECT * FROM agent_workspace_mounts WHERE agent_id = :id ORDER BY mount_name"),
        {"id": agent_id},
    )
    agent["workspace_mounts"] = [
        {
            "id": m["id"],
            "host_path": m["host_path"],
            "mount_name": m["mount_name"],
            "readonly": bool(m["readonly"]),
        }
        for m in mounts_result.mappings().all()
    ]

    # Fetch channels
    channels_result = await db.execute(
        text("SELECT * FROM agent_channels WHERE agent_id = :id ORDER BY channel"),
        {"id": agent_id},
    )
    agent["channels"] = [
        {
            "id": c["id"],
            "channel": c["channel"],
            "enabled": bool(c["enabled"]),
            "sandbox_override": c["sandbox_override"],
        }
        for c in channels_result.mappings().all()
    ]

    return agent


# ── Endpoints ─────────────────────────────────────────────────


@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db)):
    """List all agents with workspace mounts and channels."""
    result = await db.execute(text("SELECT id FROM agents ORDER BY is_default DESC, name"))
    ids = [row[0] for row in result.fetchall()]
    agents = []
    for agent_id in ids:
        agent = await _get_agent_with_relations(db, agent_id)
        if agent:
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
async def browse_directories(path: str = "/"):
    """List directories at the given path for the workspace mount picker."""
    from pathlib import Path as P

    target = P(path).resolve()
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    dirs = []
    try:
        for entry in sorted(target.iterdir()):
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
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single agent with mounts and channels."""
    agent = await _get_agent_with_relations(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("")
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new agent with mounts and channels."""
    agent_id = str(ULID())

    await db.execute(
        text(
            "INSERT INTO agents (id, name, display_name, system_prompt, model, "
            "sandbox_image, tools, max_iterations, auto_rag, auto_rag_limit, "
            "is_default, is_active) "
            "VALUES (:id, :name, :display_name, :system_prompt, :model, "
            ":sandbox_image, :tools, :max_iterations, :auto_rag, :auto_rag_limit, "
            "0, 1)"
        ),
        {
            "id": agent_id,
            "name": body.name,
            "display_name": body.display_name,
            "system_prompt": body.system_prompt,
            "model": body.model,
            "sandbox_image": body.sandbox_image,
            "tools": json.dumps(body.tools),
            "max_iterations": body.max_iterations,
            "auto_rag": 1 if body.auto_rag else 0,
            "auto_rag_limit": body.auto_rag_limit,
        },
    )

    # Insert workspace mounts
    for mount in body.workspace_mounts:
        mount_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO agent_workspace_mounts (id, agent_id, host_path, mount_name, readonly) "
                "VALUES (:id, :agent_id, :host_path, :mount_name, :readonly)"
            ),
            {
                "id": mount_id,
                "agent_id": agent_id,
                "host_path": mount.host_path,
                "mount_name": mount.mount_name,
                "readonly": 1 if mount.readonly else 0,
            },
        )

    # Insert channels
    for ch in body.channels:
        ch_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO agent_channels (id, agent_id, channel, enabled, sandbox_override) "
                "VALUES (:id, :agent_id, :channel, :enabled, :sandbox_override)"
            ),
            {
                "id": ch_id,
                "agent_id": agent_id,
                "channel": ch.channel,
                "enabled": 1 if ch.enabled else 0,
                "sandbox_override": ch.sandbox_override,
            },
        )

    await db.commit()
    return await _get_agent_with_relations(db, agent_id)


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, db: AsyncSession = Depends(get_db)):
    """Update an agent, replacing mounts and channels if provided."""
    # Check agent exists
    existing = await db.execute(
        text("SELECT id FROM agents WHERE id = :id"), {"id": agent_id}
    )
    if existing.fetchone() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Build SET clause dynamically
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.system_prompt is not None:
        updates["system_prompt"] = body.system_prompt
    if body.model is not None:
        updates["model"] = body.model
    if body.sandbox_image is not None:
        updates["sandbox_image"] = body.sandbox_image
    if body.tools is not None:
        updates["tools"] = json.dumps(body.tools)
    if body.max_iterations is not None:
        updates["max_iterations"] = body.max_iterations
    if body.auto_rag is not None:
        updates["auto_rag"] = 1 if body.auto_rag else 0
    if body.auto_rag_limit is not None:
        updates["auto_rag_limit"] = body.auto_rag_limit

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = agent_id
        await db.execute(
            text(f"UPDATE agents SET {set_clause} WHERE id = :id"),
            updates,
        )

    # Replace workspace mounts if provided
    if body.workspace_mounts is not None:
        await db.execute(
            text("DELETE FROM agent_workspace_mounts WHERE agent_id = :id"),
            {"id": agent_id},
        )
        for mount in body.workspace_mounts:
            mount_id = str(ULID())
            await db.execute(
                text(
                    "INSERT INTO agent_workspace_mounts (id, agent_id, host_path, mount_name, readonly) "
                    "VALUES (:id, :agent_id, :host_path, :mount_name, :readonly)"
                ),
                {
                    "id": mount_id,
                    "agent_id": agent_id,
                    "host_path": mount.host_path,
                    "mount_name": mount.mount_name,
                    "readonly": 1 if mount.readonly else 0,
                },
            )

    # Replace channels if provided
    if body.channels is not None:
        await db.execute(
            text("DELETE FROM agent_channels WHERE agent_id = :id"),
            {"id": agent_id},
        )
        for ch in body.channels:
            ch_id = str(ULID())
            await db.execute(
                text(
                    "INSERT INTO agent_channels (id, agent_id, channel, enabled, sandbox_override) "
                    "VALUES (:id, :agent_id, :channel, :enabled, :sandbox_override)"
                ),
                {
                    "id": ch_id,
                    "agent_id": agent_id,
                    "channel": ch.channel,
                    "enabled": 1 if ch.enabled else 0,
                    "sandbox_override": ch.sandbox_override,
                },
            )

    await db.commit()
    return await _get_agent_with_relations(db, agent_id)


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Delete an agent (rejects if default)."""
    result = await db.execute(
        text("SELECT is_default FROM agents WHERE id = :id"), {"id": agent_id}
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if row[0]:
        raise HTTPException(status_code=400, detail="Cannot delete the default agent")

    await db.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})
    await db.commit()
    return {"status": "deleted", "agent_id": agent_id}


@router.post("/{agent_id}/default")
async def set_default_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Set an agent as the default."""
    result = await db.execute(
        text("SELECT id FROM agents WHERE id = :id"), {"id": agent_id}
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Clear current default
    await db.execute(text("UPDATE agents SET is_default = 0 WHERE is_default = 1"))
    # Set new default
    await db.execute(
        text("UPDATE agents SET is_default = 1 WHERE id = :id"), {"id": agent_id}
    )
    await db.commit()
    return await _get_agent_with_relations(db, agent_id)
