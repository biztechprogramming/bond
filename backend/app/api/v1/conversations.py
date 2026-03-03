"""Conversations API — CRUD for conversations and messages."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.agent.interrupts import set_interrupt, is_turn_active
from backend.app.db.session import get_db

logger = logging.getLogger("bond.api.conversations")


async def _sync_conversation_to_spacetimedb(
    conv_id: str,
    agent_id: str,
    channel: str,
    title: str,
) -> None:
    """Fire-and-forget sync of a conversation to SpacetimeDB via the Gateway."""
    try:
        import httpx
        from backend.app.config import get_settings
        gw = f"http://localhost:{get_settings().gateway_port}/api/v1/sync/conversations"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(gw, json={
                "id": conv_id,
                "agentId": agent_id,
                "channel": channel,
                "title": title,
            })
    except Exception as e:
        logger.warning("Failed to sync conversation %s to SpacetimeDB: %s", conv_id, e)

router = APIRouter(prefix="/conversations", tags=["conversations"])


# -- Pydantic models --


class ConversationCreate(BaseModel):
    agent_id: str | None = None
    channel: str = "webchat"
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str


class QueueMessageRequest(BaseModel):
    content: str
    role: str = "user"


class SaveAssistantMessageRequest(BaseModel):
    role: str
    content: str
    tool_calls_made: int = 0


# -- Helpers --


async def _get_default_agent_id(db: AsyncSession) -> str:
    result = await db.execute(
        text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1")
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="No default agent configured")
    return row[0]


# -- Endpoints --


@router.post("")
async def create_conversation(
    body: ConversationCreate, db: AsyncSession = Depends(get_db)
):
    agent_id = body.agent_id
    if not agent_id:
        agent_id = await _get_default_agent_id(db)

    # Verify agent exists
    result = await db.execute(
        text("SELECT id FROM agents WHERE id = :id"), {"id": agent_id}
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    conv_id = str(ULID())
    await db.execute(
        text(
            "INSERT INTO conversations (id, agent_id, channel, title) "
            "VALUES (:id, :agent_id, :channel, :title)"
        ),
        {
            "id": conv_id,
            "agent_id": agent_id,
            "channel": body.channel,
            "title": body.title,
        },
    )
    await db.commit()
    await _sync_conversation_to_spacetimedb(conv_id, agent_id, body.channel, body.title or "")

    return await _get_conversation(db, conv_id)


@router.get("")
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text(
            "SELECT c.id, c.agent_id, c.channel, c.title, c.is_active, "
            "c.message_count, c.created_at, c.updated_at, a.display_name as agent_name "
            "FROM conversations c "
            "LEFT JOIN agents a ON c.agent_id = a.id "
            "ORDER BY c.updated_at DESC"
        )
    )
    rows = result.mappings().all()
    return [
        {
            "id": r["id"],
            "agent_id": r["agent_id"],
            "agent_name": r["agent_name"],
            "channel": r["channel"],
            "title": r["title"],
            "is_active": bool(r["is_active"]),
            "message_count": r["message_count"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db)
):
    conv = await _get_conversation(db, conversation_id, include_messages=True)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    # Verify conversation exists
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_result = await db.execute(
        text(
            "SELECT id, role, content, tool_calls, tool_call_id, token_count, created_at "
            "FROM conversation_messages "
            "WHERE conversation_id = :conv_id "
            "ORDER BY created_at "
            "LIMIT :limit OFFSET :offset"
        ),
        {"conv_id": conversation_id, "limit": limit, "offset": offset},
    )
    rows = messages_result.mappings().all()
    return [dict(r) for r in rows]


@router.put("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.execute(
        text("UPDATE conversations SET title = :title WHERE id = :id"),
        {"id": conversation_id, "title": body.title},
    )
    await db.commit()
    return await _get_conversation(db, conversation_id)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.execute(
        text("DELETE FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    await db.commit()
    return {"status": "deleted", "conversation_id": conversation_id}


@router.delete("/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: str, message_id: str, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        text("SELECT id FROM conversation_messages WHERE id = :id AND conversation_id = :cid"),
        {"id": message_id, "cid": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Message not found")

    await db.execute(
        text("DELETE FROM conversation_messages WHERE id = :id"),
        {"id": message_id},
    )
    # Update message count
    await db.execute(
        text("UPDATE conversations SET message_count = message_count - 1 WHERE id = :id"),
        {"id": conversation_id},
    )
    await db.commit()
    return {"status": "deleted", "message_id": message_id}


# -- Message queue --


@router.post("/{conversation_id}/messages")
async def save_or_queue_message(
    conversation_id: str,
    body: QueueMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save a message to a conversation.

    - role='user': queued for the next agent turn (original behavior)
    - role='assistant': saved as delivered (used by gateway after container turns)
    """
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if body.role == "assistant":
        msg_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO conversation_messages (id, conversation_id, role, content, status) "
                "VALUES (:id, :conv_id, 'assistant', :content, 'delivered')"
            ),
            {
                "id": msg_id,
                "conv_id": conversation_id,
                "content": body.content,
            },
        )
        await db.execute(
            text(
                "UPDATE conversations SET message_count = message_count + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": conversation_id},
        )
        await db.commit()
        return {
            "message_id": msg_id,
            "conversation_id": conversation_id,
        }

    if body.role != "user":
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'assistant'")

    # Auto-title from first user message if untitled
    title_result = await db.execute(
        text("SELECT title, agent_id, channel FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    title_row = title_result.fetchone()
    auto_title = title_row[0] if title_row else ""
    if title_row and not title_row[0]:
        auto_title = body.content.strip()[:80]
        if len(body.content.strip()) > 80:
            auto_title = auto_title.rsplit(" ", 1)[0] + "..."
        await db.execute(
            text("UPDATE conversations SET title = :title WHERE id = :id"),
            {"title": auto_title, "id": conversation_id},
        )
        # Sync updated title to SpacetimeDB
        await _sync_conversation_to_spacetimedb(
            conversation_id,
            title_row[1] if title_row else "",
            title_row[2] if title_row else "webchat",
            auto_title,
        )

    # Original user message queuing logic
    msg_id = str(ULID())
    await db.execute(
        text(
            "INSERT INTO conversation_messages (id, conversation_id, role, content, status) "
            "VALUES (:id, :conv_id, :role, :content, 'queued')"
        ),
        {
            "id": msg_id,
            "conv_id": conversation_id,
            "role": body.role,
            "content": body.content,
        },
    )

    # Get queue position
    pos_result = await db.execute(
        text(
            "SELECT COUNT(*) FROM conversation_messages "
            "WHERE conversation_id = :conv_id AND status = 'queued'"
        ),
        {"conv_id": conversation_id},
    )
    queue_position = pos_result.fetchone()[0]

    # Update message count
    await db.execute(
        text(
            "UPDATE conversations SET message_count = message_count + 1 "
            "WHERE id = :id"
        ),
        {"id": conversation_id},
    )
    await db.commit()

    return {
        "message_id": msg_id,
        "status": "queued",
        "queue_position": queue_position,
    }


# -- Interrupt --


@router.post("/{conversation_id}/interrupt")
async def interrupt_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Signal the agent to interrupt and check for new messages."""
    result = await db.execute(
        text("SELECT id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not is_turn_active(conversation_id):
        return {"status": "no_active_turn"}

    set_interrupt(conversation_id)
    return {"status": "interrupt_sent"}


# -- Internal helpers --


async def _get_conversation(
    db: AsyncSession, conv_id: str, *, include_messages: bool = False
) -> dict | None:
    result = await db.execute(
        text(
            "SELECT c.*, a.display_name as agent_name "
            "FROM conversations c "
            "LEFT JOIN agents a ON c.agent_id = a.id "
            "WHERE c.id = :id"
        ),
        {"id": conv_id},
    )
    row = result.mappings().first()
    if row is None:
        return None

    conv = {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "agent_name": row["agent_name"],
        "channel": row["channel"],
        "title": row["title"],
        "is_active": bool(row["is_active"]),
        "message_count": row["message_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

    if include_messages:
        messages_result = await db.execute(
            text(
                "SELECT id, role, content, tool_calls, tool_call_id, token_count, created_at "
                "FROM conversation_messages "
                "WHERE conversation_id = :conv_id "
                "ORDER BY created_at"
            ),
            {"conv_id": conv_id},
        )
        conv["messages"] = [dict(r) for r in messages_result.mappings().all()]

    return conv
