"""Agent turn endpoint — the core chat API with conversation persistence.

Supports both legacy JSON request-response and SSE streaming modes.
All data access goes through SpacetimeDB (migrated from SQLite).
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from ulid import ULID

from backend.app.agent.loop import agent_turn
from backend.app.agent.interrupts import (
    register_turn,
    unregister_turn,
    check_interrupt,
)
from backend.app.core.spacetimedb import get_stdb
from backend.app.core.crypto import decrypt_value, is_encrypted
from backend.app.sandbox import get_executor

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


async def _get_or_create_conversation(conversation_id: str | None) -> str:
    """Return existing conversation_id or create a new one with the default agent."""
    stdb = get_stdb()

    if conversation_id:
        rows = await stdb.query(
            f"SELECT id FROM conversations WHERE id = '{conversation_id}'"
        )
        if rows:
            return conversation_id

    # Create new conversation with default agent
    agent_rows = await stdb.query(
        "SELECT id FROM agents WHERE is_default = true"
    )
    agent_id = agent_rows[0]["id"] if agent_rows else "default"

    conv_id = str(ULID())
    now = int(time.time() * 1000)
    await stdb.call_reducer("import_conversation", [
        conv_id, agent_id, "webchat", "", True, 0, "", 0, "", now, now,
    ])

    return conv_id


async def _load_history(conversation_id: str) -> list[dict]:
    """Load delivered message history from conversation_messages table."""
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT role, content, tool_calls, tool_call_id "
        f"FROM conversation_messages "
        f"WHERE conversation_id = '{conversation_id}' "
        f"AND status = 'delivered' "
        f"ORDER BY created_at"
    )
    messages = []
    for row in rows:
        msg: dict = {"role": row["role"], "content": row["content"]}
        if row.get("tool_calls"):
            tc = row["tool_calls"]
            msg["tool_calls"] = json.loads(tc) if isinstance(tc, str) else tc
        if row.get("tool_call_id"):
            msg["tool_call_id"] = row["tool_call_id"]
        messages.append(msg)
    return messages


async def _load_queued_messages(conversation_id: str) -> list[dict]:
    """Load queued messages and mark them as delivered."""
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT id, role, content FROM conversation_messages "
        f"WHERE conversation_id = '{conversation_id}' AND status = 'queued' "
        f"ORDER BY created_at"
    )
    if not rows:
        return []

    messages = [{"role": r["role"], "content": r["content"]} for r in rows]

    # Mark as delivered via reducer
    for row in rows:
        await stdb.call_reducer("save_message", [
            row["id"], conversation_id, row["role"], row["content"],
            "", "", 0, "delivered", 0,
        ])

    return messages


async def _get_queued_count(conversation_id: str) -> int:
    """Count remaining queued messages."""
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT id FROM conversation_messages "
        f"WHERE conversation_id = '{conversation_id}' AND status = 'queued'"
    )
    return len(rows)


async def _save_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: str | None = None,
    tool_call_id: str | None = None,
    status: str = "delivered",
) -> str:
    """Save a message to conversation_messages and return its ID."""
    stdb = get_stdb()
    msg_id = str(ULID())
    now = int(time.time() * 1000)
    await stdb.call_reducer("add_conversation_message", [
        msg_id, conversation_id, role, content,
        tool_calls or "", tool_call_id or "", 0, status, now,
    ])
    return msg_id


def _sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/turn")
async def post_agent_turn(req: AgentTurnRequest):
    """Execute an agent turn with SSE streaming or legacy JSON response.

    When stream=True, returns SSE events:
      - status: agent state changes (thinking, tool_calling, responding)
      - chunk: streaming text content
      - tool_call: tool invocation info
      - tool_result: tool execution result
      - new_input: new queued messages injected mid-turn
      - done: turn complete with final message_id and queued_count
    """
    stdb = get_stdb()

    # Get or create conversation
    conversation_id = await _get_or_create_conversation(req.conversation_id)

    # If message is provided directly (legacy), queue it first
    if req.message:
        msg_id = str(ULID())
        now = int(time.time() * 1000)
        await stdb.call_reducer("add_conversation_message", [
            msg_id, conversation_id, "user", req.message,
            "", "", 0, "queued", now,
        ])
        await stdb.call_reducer("update_conversation", [
            conversation_id, None, None, None, None,
        ])

    # Load history and queued messages
    history = await _load_history(conversation_id)
    queued = await _load_queued_messages(conversation_id)

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
        conv_rows = await stdb.query(
            f"SELECT message_count FROM conversations WHERE id = '{conversation_id}'"
        )
        current_count = conv_rows[0]["message_count"] if conv_rows else 0
        if current_count <= 1:
            title = first_user[:50].strip()
            if len(first_user) > 50:
                title += "..."
            await stdb.call_reducer("update_conversation", [
                conversation_id, title, None, None, None,
            ])

    # Load agent config
    conv_rows = await stdb.query(
        f"SELECT agent_id FROM conversations WHERE id = '{conversation_id}'"
    )
    agent_id = conv_rows[0]["agent_id"] if conv_rows else None

    if req.stream:
        return StreamingResponse(
            _stream_agent_turn(conversation_id, full_messages, agent_id),
            media_type="text/event-stream",
        )

    # Legacy non-streaming path
    user_msg = queued[-1]["content"] if queued else (req.message or "")
    result = await agent_turn(
        user_msg, history, stream=False, agent_id=agent_id
    )

    assistant_msg_id = await _save_message(conversation_id, "assistant", result)

    remaining = await _get_queued_count(conversation_id)

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
            user_msg, history, stream=False, agent_id=agent_id
        )

        # Check for interrupt and new queued messages between steps
        if check_interrupt(conversation_id):
            new_queued = await _load_queued_messages(conversation_id)
            if new_queued:
                yield _sse_event("new_input", {
                    "count": len(new_queued),
                    "messages": [m["content"] for m in new_queued],
                })

        # Send response chunks (for now, single chunk since agent_turn returns string)
        yield _sse_event("chunk", {"content": result})

        # Save assistant message
        assistant_msg_id = await _save_message(conversation_id, "assistant", result)

        remaining = await _get_queued_count(conversation_id)

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
):
    """Resolve how to route a turn: container worker or host-mode backend.

    The gateway calls this before every turn to determine routing.
    """
    stdb = get_stdb()
    resolved_agent_id: str | None = agent_id
    resolved_conversation_id: str | None = conversation_id

    if conversation_id:
        # Look up existing conversation
        rows = await stdb.query(
            f"SELECT id, agent_id FROM conversations WHERE id = '{conversation_id}'"
        )
        if not rows:
            if not agent_id:
                raise HTTPException(status_code=400, detail="Conversation not found and no agent_id provided")
        else:
            # Existing conversation: agent is LOCKED to whoever created it.
            resolved_agent_id = rows[0]["agent_id"]
            resolved_conversation_id = rows[0]["id"]
    elif not agent_id:
        raise HTTPException(status_code=400, detail="Either conversation_id or agent_id is required")

    if not resolved_agent_id:
        resolved_agent_id = agent_id

    # Resolve "default" to the actual default agent
    if resolved_agent_id == "default":
        default_rows = await stdb.query(
            "SELECT id FROM agents WHERE is_default = true"
        )
        if default_rows:
            resolved_agent_id = default_rows[0]["id"]
        else:
            raise HTTPException(status_code=404, detail="No default agent configured")

    # Look up agent
    agent_rows = await stdb.query(
        f"SELECT id, name, display_name, sandbox_image, model, utility_model, "
        f"system_prompt, tools, max_iterations "
        f"FROM agents WHERE id = '{resolved_agent_id}'"
    )
    if not agent_rows:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent_row = agent_rows[0]

    # Fetch workspace mounts
    mount_rows = await stdb.query(
        f"SELECT host_path, mount_name, container_path, readonly "
        f"FROM agent_workspace_mounts WHERE agent_id = '{resolved_agent_id}'"
    )
    workspace_mounts = [
        {
            "host_path": m["host_path"],
            "mount_name": m["mount_name"],
            "container_path": m["container_path"] or f"/workspace/{m['mount_name']}",
            "readonly": bool(m["readonly"]),
        }
        for m in mount_rows
    ]

    # Create conversation if needed
    if not resolved_conversation_id or (conversation_id and not await _conversation_exists(conversation_id)):
        resolved_conversation_id = await _get_or_create_conversation(resolved_conversation_id)

    sandbox_image = agent_row["sandbox_image"]
    if sandbox_image:
        # Containerized agent — ensure worker is running
        try:
            sandbox_executor = get_executor()

            # Inject decrypted API keys from provider_api_keys table
            api_keys: dict[str, str] = {}
            key_rows = await stdb.query("SELECT provider_id, encrypted_value FROM provider_api_keys")
            for kr in key_rows:
                try:
                    val = decrypt_value(kr["encrypted_value"])
                    if val:
                        api_keys[kr["provider_id"]] = val
                except Exception:
                    if not is_encrypted(kr["encrypted_value"]):
                        api_keys[kr["provider_id"]] = kr["encrypted_value"]

            # Inject provider aliases so the worker can resolve model prefixes
            alias_rows = await stdb.query("SELECT alias, provider_id FROM provider_aliases")
            provider_aliases = {r["alias"]: r["provider_id"] for r in alias_rows}

            # Inject provider ID → litellm prefix mapping
            prov_rows = await stdb.query(
                "SELECT id, litellm_prefix FROM providers WHERE is_enabled = true"
            )
            litellm_prefixes = {r["id"]: r["litellm_prefix"] for r in prov_rows if r.get("litellm_prefix")}

            agent_dict = {
                "id": agent_row["id"],
                "name": agent_row["name"],
                "sandbox_image": sandbox_image,
                "model": agent_row["model"],
                "utility_model": agent_row["utility_model"],
                "system_prompt": agent_row["system_prompt"],
                "tools": json.loads(agent_row["tools"]) if isinstance(agent_row["tools"], str) else agent_row["tools"],
                "max_iterations": agent_row["max_iterations"],
                "workspace_mounts": workspace_mounts,
                "api_keys": api_keys,
                "provider_aliases": provider_aliases,
                "litellm_prefixes": litellm_prefixes,
            }
            info = await sandbox_executor.ensure_running(agent_dict)
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


class DestroyContainerRequest(BaseModel):
    agent_id: str


@router.post("/container/destroy")
async def destroy_agent_container(req: DestroyContainerRequest):
    """Destroy the running container for an agent.

    Called by the gateway after a branch change to ensure the container
    is removed.  The next message will trigger ensure_running() which
    creates a fresh container on the correct branch.
    """
    executor = get_executor()
    destroyed = await executor.destroy_agent_container(req.agent_id)
    return {"ok": True, "destroyed": destroyed}


async def _conversation_exists(conversation_id: str) -> bool:
    """Check if a conversation exists."""
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT id FROM conversations WHERE id = '{conversation_id}'"
    )
    return len(rows) > 0
