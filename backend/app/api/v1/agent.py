"""Agent turn endpoint — the core chat API with conversation persistence.

Supports both legacy JSON request-response and SSE streaming modes.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.agent.loop import agent_turn
from backend.app.agent.interrupts import (
    register_turn,
    unregister_turn,
    check_interrupt,
)
from backend.app.db.session import get_db
from backend.app.sandbox.manager import get_sandbox_manager

logger = logging.getLogger("bond.agent.api")

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentTurnRequest(BaseModel):
    message: str | None = None
    conversation_id: str | None = None
    stream: bool = False


class AgentTurnResponse(BaseModel):
    response: str
    conversation_id: str
    message_id: str
    queued_count: int = 0


async def _get_or_create_conversation(
    db: AsyncSession, conversation_id: str | None
) -> str:
    """Return existing conversation_id or create a new one with the default agent."""
    if conversation_id:
        result = await db.execute(
            text("SELECT id FROM conversations WHERE id = :id"),
            {"id": conversation_id},
        )
        if result.fetchone() is not None:
            return conversation_id

    # Create new conversation with default agent
    agent_result = await db.execute(
        text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1")
    )
    agent_row = agent_result.fetchone()
    agent_id = agent_row[0] if agent_row else "default"

    conv_id = str(ULID())
    await db.execute(
        text(
            "INSERT INTO conversations (id, agent_id, channel) "
            "VALUES (:id, :agent_id, 'webchat')"
        ),
        {"id": conv_id, "agent_id": agent_id},
    )
    await db.commit()
    return conv_id


async def _load_history(db: AsyncSession, conversation_id: str) -> list[dict]:
    """Load delivered message history from conversation_messages table."""
    result = await db.execute(
        text(
            "SELECT role, content, tool_calls, tool_call_id "
            "FROM conversation_messages "
            "WHERE conversation_id = :conv_id "
            "AND status = 'delivered' "
            "ORDER BY created_at"
        ),
        {"conv_id": conversation_id},
    )
    messages = []
    for row in result.mappings().all():
        msg: dict = {"role": row["role"], "content": row["content"]}
        if row["tool_calls"]:
            msg["tool_calls"] = json.loads(row["tool_calls"]) if isinstance(row["tool_calls"], str) else row["tool_calls"]
        if row["tool_call_id"]:
            msg["tool_call_id"] = row["tool_call_id"]
        messages.append(msg)
    return messages


async def _load_queued_messages(db: AsyncSession, conversation_id: str) -> list[dict]:
    """Load queued messages and mark them as delivered."""
    result = await db.execute(
        text(
            "SELECT id, role, content FROM conversation_messages "
            "WHERE conversation_id = :conv_id AND status = 'queued' "
            "ORDER BY created_at"
        ),
        {"conv_id": conversation_id},
    )
    rows = result.mappings().all()
    if not rows:
        return []

    ids = [r["id"] for r in rows]
    messages = [{"role": r["role"], "content": r["content"]} for r in rows]

    # Mark as delivered
    for msg_id in ids:
        await db.execute(
            text("UPDATE conversation_messages SET status = 'delivered' WHERE id = :id"),
            {"id": msg_id},
        )
    await db.commit()

    return messages


async def _get_queued_count(db: AsyncSession, conversation_id: str) -> int:
    """Count remaining queued messages."""
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE conversation_id = :conv_id AND status = 'queued'"
        ),
        {"conv_id": conversation_id},
    )
    return result.fetchone()[0]


async def _save_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: str | None = None,
    tool_call_id: str | None = None,
    status: str = "delivered",
) -> str:
    """Save a message to conversation_messages and return its ID."""
    msg_id = str(ULID())
    await db.execute(
        text(
            "INSERT INTO conversation_messages "
            "(id, conversation_id, role, content, tool_calls, tool_call_id, status) "
            "VALUES (:id, :conv_id, :role, :content, :tool_calls, :tool_call_id, :status)"
        ),
        {
            "id": msg_id,
            "conv_id": conversation_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
            "status": status,
        },
    )
    return msg_id


def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/turn")
async def post_agent_turn(req: AgentTurnRequest, db: AsyncSession = Depends(get_db)):
    """Execute an agent turn with SSE streaming or legacy JSON response.

    When stream=True, returns SSE events:
      - status: agent state changes (thinking, tool_calling, responding)
      - chunk: streaming text content
      - tool_call: tool invocation info
      - tool_result: tool execution result
      - new_input: new queued messages injected mid-turn
      - done: turn complete with final message_id and queued_count
    """
    # Get or create conversation
    conversation_id = await _get_or_create_conversation(db, req.conversation_id)

    # If message is provided directly (legacy), queue it first
    if req.message:
        msg_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, role, content, status) "
                "VALUES (:id, :conv_id, 'user', :content, 'queued')"
            ),
            {"id": msg_id, "conv_id": conversation_id, "content": req.message},
        )
        await db.execute(
            text("UPDATE conversations SET message_count = message_count + 1 WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()

    # Load history and queued messages
    history = await _load_history(db, conversation_id)
    queued = await _load_queued_messages(db, conversation_id)

    if not queued and not history:
        # Nothing to process
        if req.stream:
            async def empty_stream():
                yield _sse_event("done", {"message_id": None, "queued_count": 0, "conversation_id": conversation_id})
            return StreamingResponse(empty_stream(), media_type="text/event-stream")
        return AgentTurnResponse(
            response="", conversation_id=conversation_id, message_id="", queued_count=0
        )

    # Combine history with newly queued messages
    full_messages = history + queued

    # Auto-title on first user message
    first_user = next((m["content"] for m in full_messages if m["role"] == "user"), None)
    if first_user:
        count_result = await db.execute(
            text("SELECT message_count FROM conversations WHERE id = :id"),
            {"id": conversation_id},
        )
        current_count = count_result.fetchone()[0]
        if current_count <= 1:
            title = first_user[:50].strip()
            if len(first_user) > 50:
                title += "..."
            await db.execute(
                text("UPDATE conversations SET title = :title WHERE id = :id"),
                {"id": conversation_id, "title": title},
            )
            await db.commit()

    # Load agent config
    agent_result = await db.execute(
        text("SELECT agent_id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    conv_row = agent_result.mappings().first()
    agent_id = conv_row["agent_id"] if conv_row else None

    if req.stream:
        return StreamingResponse(
            _stream_agent_turn(conversation_id, full_messages, agent_id, db),
            media_type="text/event-stream",
        )

    # Legacy non-streaming path
    user_msg = queued[-1]["content"] if queued else (req.message or "")
    result = await agent_turn(
        user_msg, history, stream=False, db=db, agent_id=agent_id
    )

    assistant_msg_id = await _save_message(db, conversation_id, "assistant", result)

    await db.execute(
        text("UPDATE conversations SET message_count = message_count + 1 WHERE id = :id"),
        {"id": conversation_id},
    )
    await db.commit()

    remaining = await _get_queued_count(db, conversation_id)

    return AgentTurnResponse(
        response=result,
        conversation_id=conversation_id,
        message_id=assistant_msg_id,
        queued_count=remaining,
    )


async def _stream_agent_turn(
    conversation_id: str,
    messages: list[dict],
    agent_id: str | None,
    db: AsyncSession,
):
    """SSE generator for a streaming agent turn with interrupt support."""
    register_turn(conversation_id)
    try:
        yield _sse_event("status", {"state": "thinking", "conversation_id": conversation_id})

        # Extract the last user message for the agent call
        user_msg = ""
        history = []
        for m in messages:
            if m["role"] == "user":
                user_msg = m["content"]
            history.append(m)

        # Remove the last user message from history (agent_turn adds it)
        if history and history[-1]["role"] == "user":
            user_msg = history.pop()["content"]

        result = await agent_turn(
            user_msg, history, stream=False, db=db, agent_id=agent_id
        )

        # Check for interrupt and new queued messages between steps
        if check_interrupt(conversation_id):
            new_queued = await _load_queued_messages(db, conversation_id)
            if new_queued:
                yield _sse_event("new_input", {
                    "count": len(new_queued),
                    "messages": [m["content"] for m in new_queued],
                })

        # Send response chunks (for now, single chunk since agent_turn returns string)
        yield _sse_event("chunk", {"content": result})

        # Save assistant message
        assistant_msg_id = await _save_message(db, conversation_id, "assistant", result)
        await db.execute(
            text("UPDATE conversations SET message_count = message_count + 1 WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()

        remaining = await _get_queued_count(db, conversation_id)

        yield _sse_event("done", {
            "message_id": assistant_msg_id,
            "conversation_id": conversation_id,
            "queued_count": remaining,
        })
    finally:
        unregister_turn(conversation_id)


# -- Agent resolution --


@router.get("/resolve")
async def resolve_agent(
    conversation_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Resolve how to route a turn: container worker or host-mode backend.

    The gateway calls this before every turn to determine routing.
    """
    resolved_agent_id: str | None = agent_id
    resolved_conversation_id: str | None = conversation_id

    if conversation_id:
        # Look up existing conversation
        result = await db.execute(
            text("SELECT id, agent_id FROM conversations WHERE id = :id"),
            {"id": conversation_id},
        )
        row = result.mappings().first()
        if row is None:
            if not agent_id:
                raise HTTPException(status_code=400, detail="Conversation not found and no agent_id provided")
        else:
            # Explicit agent_id from user takes priority over conversation's stored agent
            if not agent_id or agent_id == "default":
                resolved_agent_id = row["agent_id"]
            elif agent_id != row["agent_id"]:
                # User switched agents — update the conversation record
                await db.execute(
                    text("UPDATE conversations SET agent_id = :aid WHERE id = :cid"),
                    {"aid": agent_id, "cid": row["id"]},
                )
                await db.commit()
            resolved_conversation_id = row["id"]
    elif not agent_id:
        raise HTTPException(status_code=400, detail="Either conversation_id or agent_id is required")

    if not resolved_agent_id:
        # No conversation found, use provided agent_id — create conversation
        resolved_agent_id = agent_id

    # Resolve "default" to the actual default agent
    if resolved_agent_id == "default":
        default_result = await db.execute(
            text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1"),
        )
        default_row = default_result.mappings().first()
        if default_row:
            resolved_agent_id = default_row["id"]
        else:
            raise HTTPException(status_code=404, detail="No default agent configured")

    # Look up agent
    agent_result = await db.execute(
        text("SELECT id, name, display_name, sandbox_image, model, system_prompt, tools, max_iterations FROM agents WHERE id = :id"),
        {"id": resolved_agent_id},
    )
    agent_row = agent_result.mappings().first()
    if agent_row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Fetch workspace mounts
    mounts_result = await db.execute(
        text("SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = :id"),
        {"id": resolved_agent_id},
    )
    workspace_mounts = [
        {
            "host_path": m["host_path"],
            "mount_name": m["mount_name"],
            "container_path": m["container_path"] or f"/workspace/{m['mount_name']}",
            "readonly": bool(m["readonly"]),
        }
        for m in mounts_result.mappings().all()
    ]

    # Create conversation if needed
    if not resolved_conversation_id or (conversation_id and not await _conversation_exists(db, conversation_id)):
        resolved_conversation_id = await _get_or_create_conversation(db, resolved_conversation_id)

    # Fetch prompt fragments for this agent
    frag_result = await db.execute(
        text(
            "SELECT pf.content, apf.enabled FROM agent_prompt_fragments apf "
            "JOIN prompt_fragments pf ON pf.id = apf.fragment_id "
            "WHERE apf.agent_id = :id AND pf.is_active = 1 "
            "ORDER BY apf.rank"
        ),
        {"id": resolved_agent_id},
    )
    prompt_fragments = [
        {"content": r["content"], "enabled": bool(r["enabled"])}
        for r in frag_result.mappings().all()
    ]

    sandbox_image = agent_row["sandbox_image"]
    if sandbox_image:
        # Containerized agent — ensure worker is running
        try:
            sandbox_manager = get_sandbox_manager()

            # Build agent dict for ensure_running
            # API keys are resolved inside the container via the mounted Vault
            agent_dict = {
                "id": agent_row["id"],
                "name": agent_row["name"],
                "sandbox_image": sandbox_image,
                "model": agent_row["model"],
                "system_prompt": agent_row["system_prompt"],
                "tools": json.loads(agent_row["tools"]),
                "max_iterations": agent_row["max_iterations"],
                "prompt_fragments": prompt_fragments,
                "workspace_mounts": workspace_mounts,
            }
            info = await sandbox_manager.ensure_running(agent_dict)
            return {
                "mode": "container",
                "worker_url": info["worker_url"],
                "agent_id": agent_row["id"],
                "agent_name": agent_row["name"],
                "agent_display_name": agent_row.get("display_name", agent_row["name"]),
                "conversation_id": resolved_conversation_id,
            }
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
    else:
        return {
            "mode": "host",
            "agent_id": agent_row["id"],
            "agent_name": agent_row["name"],
            "agent_display_name": agent_row.get("display_name", agent_row["name"]),
            "conversation_id": resolved_conversation_id,
        }


async def _conversation_exists(db: AsyncSession, conversation_id: str) -> bool:
    """Check if a conversation exists."""
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    return result.fetchone() is not None
