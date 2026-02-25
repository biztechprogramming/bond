"""Agent turn endpoint — the core chat API with conversation persistence."""

from __future__ import annotations

import json

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.agent.loop import agent_turn
from backend.app.db.session import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentTurnRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    stream: bool = False


class AgentTurnResponse(BaseModel):
    response: str
    conversation_id: str
    message_id: str


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
    """Load message history from conversation_messages table."""
    result = await db.execute(
        text(
            "SELECT role, content, tool_calls, tool_call_id "
            "FROM conversation_messages "
            "WHERE conversation_id = :conv_id "
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


async def _save_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    tool_calls: str | None = None,
    tool_call_id: str | None = None,
) -> str:
    """Save a message to conversation_messages and return its ID."""
    msg_id = str(ULID())
    await db.execute(
        text(
            "INSERT INTO conversation_messages (id, conversation_id, role, content, tool_calls, tool_call_id) "
            "VALUES (:id, :conv_id, :role, :content, :tool_calls, :tool_call_id)"
        ),
        {
            "id": msg_id,
            "conv_id": conversation_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tool_call_id": tool_call_id,
        },
    )
    return msg_id


@router.post("/turn")
async def post_agent_turn(req: AgentTurnRequest, db: AsyncSession = Depends(get_db)):
    """Execute an agent turn: send a message, get a response."""
    # Get or create conversation
    conversation_id = await _get_or_create_conversation(db, req.conversation_id)

    # Load history from DB
    history = await _load_history(db, conversation_id)

    # Load agent config from conversation
    agent_result = await db.execute(
        text(
            "SELECT agent_id FROM conversations WHERE id = :id"
        ),
        {"id": conversation_id},
    )
    conv_row = agent_result.mappings().first()
    agent_id = conv_row["agent_id"] if conv_row else None

    if req.stream:
        result = await agent_turn(
            req.message, history, stream=True, db=db, agent_id=agent_id
        )

        async def generate():
            async for chunk in result:
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    result = await agent_turn(
        req.message, history, stream=False, db=db, agent_id=agent_id
    )

    # Save user message
    await _save_message(db, conversation_id, "user", req.message)

    # Save assistant response
    assistant_msg_id = await _save_message(db, conversation_id, "assistant", result)

    # Get current message count
    count_result = await db.execute(
        text("SELECT message_count FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    current_count = count_result.fetchone()[0]

    # Auto-title on first exchange
    if current_count == 0:
        title = req.message[:50].strip()
        if len(req.message) > 50:
            title += "..."
        await db.execute(
            text("UPDATE conversations SET title = :title WHERE id = :id"),
            {"id": conversation_id, "title": title},
        )

    # Update message count
    await db.execute(
        text(
            "UPDATE conversations SET message_count = message_count + 2 "
            "WHERE id = :id"
        ),
        {"id": conversation_id},
    )
    await db.commit()

    return AgentTurnResponse(
        response=result,
        conversation_id=conversation_id,
        message_id=assistant_msg_id,
    )
