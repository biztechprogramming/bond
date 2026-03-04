"""Conversations API — CRUD for conversations and messages.

The authoritative routing entry point is POST /:id/turn — the gateway calls
this and relays SSE events to the frontend. Agent resolution lives here, not
in the gateway.
"""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.agent.interrupts import set_interrupt, is_turn_active, register_turn, unregister_turn
from backend.app.db.session import get_db
from backend.app.sandbox.manager import get_sandbox_manager
from backend.app.core.crypto import decrypt_value, is_encrypted

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
    id: str | None = None
    agent_id: str | None = None
    channel: str | None = "webchat"
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

    conv_id = body.id or str(ULID())
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


# -- Turn endpoint --


class ConversationTurnRequest(BaseModel):
    message: str | None = None
    plan_id: str | None = None
    agent_id: str | None = None  # only used when creating a brand-new conversation


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_container_turn(
    worker_url: str,
    messages: list[dict],
    conversation_id: str,
    plan_id: str | None,
    agent_id: str,
    db: AsyncSession,
):
    """Proxy SSE from a container worker, handle memory promotion inline, save messages."""
    response_content = ""
    tool_calls_made = 0
    event_type = ""

    try:
        register_turn(conversation_id)
        yield _sse("status", {"state": "thinking", "conversation_id": conversation_id})

        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{worker_url}/turn",
                json={"messages": messages, "conversation_id": conversation_id, "plan_id": plan_id},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:") and event_type:
                        try:
                            data = json.loads(line[len("data:"):].strip())
                        except json.JSONDecodeError:
                            continue

                        if event_type == "chunk":
                            response_content += data.get("content", "")
                            yield _sse("chunk", data)
                        elif event_type == "status":
                            yield _sse("status", data)
                        elif event_type in ("tool_call", "plan_created", "item_created", "item_updated", "plan_completed"):
                            yield _sse(event_type, data)
                        elif event_type == "memory":
                            # Promote inline — no gateway round-trip needed
                            logger.info(
                                "Memory promotion: agent=%s type=%s id=%s",
                                agent_id, data.get("type"), data.get("memory_id"),
                            )
                        elif event_type == "done":
                            response_content = data.get("response", response_content)
                            tool_calls_made = data.get("tool_calls_made", 0)
                        elif event_type == "error":
                            yield _sse("error", data)

        # Save assistant message
        msg_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, role, content, status) "
                "VALUES (:id, :cid, 'assistant', :content, 'delivered')"
            ),
            {"id": msg_id, "cid": conversation_id, "content": response_content},
        )
        await db.execute(
            text("UPDATE conversations SET message_count = message_count + 1 WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()

        logger.info(
            "Container turn complete: conversation=%s tool_calls=%d response_len=%d",
            conversation_id, tool_calls_made, len(response_content),
        )
        yield _sse("done", {
            "message_id": msg_id,
            "conversation_id": conversation_id,
            "tool_calls_made": tool_calls_made,
            "queued_count": 0,
        })
    except Exception as e:
        logger.error("Container turn error: conversation=%s error=%s", conversation_id, e)
        yield _sse("error", {"message": str(e)})
    finally:
        unregister_turn(conversation_id)


@router.post("/{conversation_id}/turn")
async def conversation_turn(
    conversation_id: str,
    req: ConversationTurnRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start an agent turn for a conversation.

    This is the single routing entry point. The gateway calls this and relays
    SSE events to the frontend — no agent resolution in the gateway.

    The conversation's agent is always used. The agent cannot be changed by
    passing agent_id here (that only applies when creating a new conversation).
    """
    # Look up conversation
    conv_result = await db.execute(
        text("SELECT id, agent_id FROM conversations WHERE id = :id"),
        {"id": conversation_id},
    )
    conv_row = conv_result.mappings().first()

    if conv_row is None:
        # Auto-create with the specified agent or default
        agent_id = req.agent_id
        if not agent_id:
            default = await db.execute(text("SELECT id FROM agents WHERE is_default = 1 LIMIT 1"))
            row = default.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No default agent and no agent_id provided")
            agent_id = row[0]
        await db.execute(
            text("INSERT INTO conversations (id, agent_id, channel) VALUES (:id, :aid, 'webchat')"),
            {"id": conversation_id, "aid": agent_id},
        )
        await db.commit()
        try:
            await _sync_conversation_to_spacetimedb(conversation_id, agent_id, "webchat", "")
        except Exception:
            pass
    else:
        # Existing conversation — agent is locked, ignore req.agent_id
        agent_id = conv_row["agent_id"]

    # Save user message
    if req.message:
        msg_id = str(ULID())
        await db.execute(
            text(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, role, content, status) "
                "VALUES (:id, :cid, 'user', :content, 'delivered')"
            ),
            {"id": msg_id, "cid": conversation_id, "content": req.message},
        )
        await db.execute(
            text("UPDATE conversations SET message_count = message_count + 1 WHERE id = :id"),
            {"id": conversation_id},
        )
        await db.commit()

    # Load history
    history_result = await db.execute(
        text(
            "SELECT role, content FROM conversation_messages "
            "WHERE conversation_id = :cid AND status = 'delivered' "
            "ORDER BY created_at"
        ),
        {"cid": conversation_id},
    )
    messages = [{"role": r["role"], "content": r["content"]} for r in history_result.mappings().all()]

    # Look up agent
    agent_result = await db.execute(
        text("SELECT id, name, display_name, sandbox_image, model, utility_model, system_prompt, tools, max_iterations FROM agents WHERE id = :id"),
        {"id": agent_id},
    )
    agent_row = agent_result.mappings().first()
    if agent_row is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    if agent_row["sandbox_image"]:
        # Container agent — ensure running, proxy SSE
        api_keys: dict[str, str] = {}
        for kr in (await db.execute(text("SELECT provider_id, encrypted_value FROM provider_api_keys"))).fetchall():
            try:
                val = decrypt_value(kr[1])
                if val:
                    api_keys[kr[0]] = val
            except Exception:
                if not is_encrypted(kr[1]):
                    api_keys[kr[0]] = kr[1]

        alias_rows = (await db.execute(text("SELECT alias, provider_id FROM provider_aliases"))).fetchall()

        mounts_result = await db.execute(
            text("SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = :id"),
            {"id": agent_id},
        )

        frag_result = await db.execute(
            text(
                "SELECT pf.id, pf.name, pf.display_name, pf.description, pf.content, "
                "pf.summary, pf.tier, pf.task_triggers, pf.token_estimate, apf.enabled, apf.rank "
                "FROM agent_prompt_fragments apf JOIN prompt_fragments pf ON pf.id = apf.fragment_id "
                "WHERE apf.agent_id = :id AND pf.is_active = 1 ORDER BY apf.rank"
            ),
            {"id": agent_id},
        )

        agent_dict = {
            "id": agent_row["id"],
            "name": agent_row["name"],
            "sandbox_image": agent_row["sandbox_image"],
            "model": agent_row["model"],
            "utility_model": agent_row["utility_model"],
            "system_prompt": agent_row["system_prompt"],
            "tools": json.loads(agent_row["tools"]),
            "max_iterations": agent_row["max_iterations"],
            "prompt_fragments": [dict(r) for r in frag_result.mappings().all()],
            "workspace_mounts": [
                {
                    "host_path": m["host_path"],
                    "mount_name": m["mount_name"],
                    "container_path": m["container_path"] or f"/workspace/{m['mount_name']}",
                    "readonly": bool(m["readonly"]),
                }
                for m in mounts_result.mappings().all()
            ],
            "api_keys": api_keys,
            "provider_aliases": {r[0]: r[1] for r in alias_rows},
        }

        try:
            info = await get_sandbox_manager().ensure_running(agent_dict)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        return StreamingResponse(
            _stream_container_turn(info["worker_url"], messages, conversation_id, req.plan_id, agent_id, db),
            media_type="text/event-stream",
        )
    else:
        # Host-mode agent — use existing agent_turn loop
        from backend.app.api.v1.agent import _stream_agent_turn
        return StreamingResponse(
            _stream_agent_turn(conversation_id, messages, agent_id, db),
            media_type="text/event-stream",
        )


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
