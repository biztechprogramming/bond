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

from backend.app.agent.interrupts import set_interrupt, is_turn_active, get_worker_url
from backend.app.api.v1.turn_stdb import _stream_container_turn_stdb
from backend.app.core.spacetimedb import get_stdb
from backend.app.sandbox.manager import get_sandbox_manager
from backend.app.core.crypto import decrypt_value, is_encrypted

logger = logging.getLogger("bond.api.conversations")


async def _sync_conversation_to_spacetimedb(
    conv_id: str,
    agent_id: str,
    channel: str,
    title: str,
) -> None:
    """Call create_conversation reducer directly."""
    stdb = get_stdb()
    await stdb.call_reducer("create_conversation", [conv_id, agent_id, channel, title or ""])

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


# -- Endpoints --


@router.post("")
async def create_conversation(
    body: ConversationCreate
):
    stdb = get_stdb()
    agent_id = body.agent_id
    if not agent_id:
        default_agents = await stdb.query("SELECT id FROM agents WHERE isDefault = true LIMIT 1")
        if not default_agents:
            agent_id = "01JBOND0000000000000DEFAULT"
        else:
            agent_id = default_agents[0]["id"]

    # Verify agent exists
    agent_rows = await stdb.query(f"SELECT id FROM agents WHERE id = '{agent_id}'")
    if not agent_rows:
        raise HTTPException(status_code=404, detail="Agent not found in SpacetimeDB")

    conv_id = body.id or str(ULID())
    await stdb.call_reducer("create_conversation", [conv_id, agent_id, body.channel or "webchat", body.title or ""])

    return {"id": conv_id, "agent_id": agent_id, "status": "created"}


@router.get("")
async def list_conversations():
    stdb = get_stdb()
    rows = await stdb.query(
        "SELECT id, agent_id, channel, title, is_active, message_count, created_at, updated_at FROM conversations"
    )
    # Perform join and sort in Python due to SpacetimeDB SQL limitations
    agent_rows = await stdb.query("SELECT id, display_name FROM agents")
    agents = {r["id"]: r["display_name"] for r in agent_rows}
    
    conversations = [
        {
            "id": r["id"],
            "agent_id": r["agent_id"],
            "agent_name": agents.get(r["agent_id"]),
            "channel": r["channel"],
            "title": r["title"],
            "is_active": bool(r["is_active"]),
            "message_count": r["message_count"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    conversations.sort(key=lambda x: x["updated_at"], reverse=True)
    return conversations


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str
):
    stdb = get_stdb()
    convs = await stdb.query(
        f"SELECT id, agent_id, channel, title, is_active, message_count, created_at, updated_at "
        f"FROM conversations WHERE id = '{conversation_id}'"
    )
    if not convs:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    r = convs[0]
    
    agent_rows = await stdb.query(f"SELECT display_name FROM agents WHERE id = '{r['agent_id']}'")
    agent_name = agent_rows[0]["display_name"] if agent_rows else None
    
    conv = {
        "id": r["id"],
        "agent_id": r["agent_id"],
        "agent_name": agent_name,
        "channel": r["channel"],
        "title": r["title"],
        "is_active": bool(r["is_active"]),
        "message_count": r["message_count"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }

    messages_rows = await stdb.query(
        f"SELECT id, role, content, toolCalls, toolCallId, tokenCount, status, createdAt "
        f"FROM conversationMessages "
        f"WHERE conversationId = '{conversation_id}'"
    )
    # Sort in Python
    messages_rows.sort(key=lambda x: x["createdAt"])
    
    conv["messages"] = [
        {
            "id": mr["id"],
            "role": mr["role"],
            "content": mr["content"],
            "tool_calls": mr["toolCalls"],
            "tool_call_id": mr["toolCallId"],
            "token_count": mr["tokenCount"],
            "status": mr["status"],
            "created_at": mr["createdAt"]
        }
        for mr in messages_rows
    ]

    return conv


@router.get("/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    stdb = get_stdb()
    messages_result = await stdb.query(
        f"SELECT id, role, content, tool_calls, tool_call_id, token_count, created_at "
        f"FROM conversationMessages "
        f"WHERE conversation_id = '{conversation_id}'"
    )
    messages_result.sort(key=lambda x: x["created_at"])
    return messages_result[offset : offset + limit]


@router.put("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
):
    stdb = get_stdb()
    await stdb.call_reducer("update_conversation", [conversation_id, body.title])
    return {"status": "updated"}


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str
):
    stdb = get_stdb()
    await stdb.call_reducer("delete_conversation", [conversation_id])
    return {"status": "deleted", "conversation_id": conversation_id}


@router.delete("/{conversation_id}/messages/{message_id}")
async def delete_message(
    conversation_id: str, message_id: str
):
    stdb = get_stdb()
    await stdb.call_reducer("delete_conversation_message", [message_id, conversation_id])
    return {"status": "deleted", "message_id": message_id}


# -- Message queue --


@router.post("/{conversation_id}/messages")
async def save_or_queue_message(
    conversation_id: str,
    body: QueueMessageRequest,
):
    """Save a message to a conversation.

    - role='user': queued for the next agent turn (original behavior)
    - role='assistant': saved as delivered (used by gateway after container turns)
    """
    stdb = get_stdb()

    if body.role == "assistant":
        msg_id = str(ULID())
        await stdb.call_reducer("add_conversation_message", [
            msg_id, conversation_id, "assistant", body.content, "", "", 0, "delivered"
        ])
        return {
            "message_id": msg_id,
            "conversation_id": conversation_id,
        }

    if body.role != "user":
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'assistant'")

    # Auto-title from first user message if untitled
    convs = await stdb.query(f"SELECT title, agent_id, channel FROM conversations WHERE id = '{conversation_id}'")
    if not convs:
         raise HTTPException(status_code=404, detail="Conversation not found")
    
    title_row = convs[0]
    if not title_row.get("title"):
        auto_title = body.content.strip()[:80]
        if len(body.content.strip()) > 80:
            auto_title = auto_title.rsplit(" ", 1)[0] + "..."
        await stdb.call_reducer("update_conversation", [conversation_id, auto_title])

    # Original user message queuing logic
    msg_id = str(ULID())
    await stdb.call_reducer("add_conversation_message", [
        msg_id, conversation_id, "user", body.content, "", "", 0, "queued"
    ])

    return {
        "message_id": msg_id,
        "status": "queued",
    }


# -- Interrupt --


@router.post("/{conversation_id}/interrupt")
async def interrupt_conversation(
    conversation_id: str
):
    """Signal the agent to interrupt and check for new messages.

    For container-based agents, also forwards the interrupt to the worker
    so the agent loop actually stops.
    """
    if not is_turn_active(conversation_id):
        return {"status": "no_active_turn"}

    # Set the in-process flag (stops the SSE proxy loop)
    set_interrupt(conversation_id)

    # Forward to worker container if one is running
    worker_url = get_worker_url(conversation_id)
    if worker_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                await client.post(
                    f"{worker_url}/interrupt",
                    json={"new_messages": []},
                )
            logger.info("Interrupt forwarded to worker at %s", worker_url)
        except Exception as e:
            logger.warning("Failed to forward interrupt to worker: %s", e)

    return {"status": "interrupt_sent"}


@router.get("/{conversation_id}/coding-agent/events")
async def coding_agent_events(conversation_id: str):
    """Proxy SSE stream of coding agent diffs from the worker container.

    The frontend subscribes to this endpoint when a coding agent is active.
    """
    worker_url = get_worker_url(conversation_id)
    if not worker_url:
        return StreamingResponse(
            iter(["event: error\ndata: {\"message\": \"No active worker\"}\n\n"]),
            media_type="text/event-stream",
        )

    async def proxy_stream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0)) as client:
                async with client.stream(
                    "GET",
                    f"{worker_url}/coding-agent/events/{conversation_id}",
                ) as response:
                    async for chunk in response.aiter_text():
                        yield chunk
        except Exception as e:
            logger.error("Coding agent events proxy error: %s", e)
            yield f"event: error\ndata: {{\"message\": \"{str(e)}\"}}\n\n"

    return StreamingResponse(proxy_stream(), media_type="text/event-stream")


# -- Turn endpoint --


class ConversationTurnRequest(BaseModel):
    message: str | None = None
    plan_id: str | None = None
    agent_id: str | None = None  # only used when creating a brand-new conversation
    channel: str | None = None   # "webchat", "telegram", "whatsapp" — defaults to webchat


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\\ndata: {json.dumps(data)}\\n\\n"


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
):
    """Start an agent turn for a conversation.

    This is the single routing entry point. The gateway calls this and relays
    SSE events to the frontend — no agent resolution in the gateway.

    The conversation's agent is always used. The agent cannot be changed by
    passing agent_id here (that only applies when creating a new conversation).
    """
    stdb = get_stdb()
    
    logger.info(f"[CONVERSATIONS] Starting turn for conversation={conversation_id}, message={req.message[:50] if req.message else 'None'}...")
    
    # Look up conversation in SpacetimeDB
    conv_rows = await stdb.query(f"SELECT id, agent_id FROM conversations WHERE id = '{conversation_id}'")
    conv_row = conv_rows[0] if conv_rows else None

    if conv_row is None:
        logger.info(f"[CONVERSATIONS] Conversation {conversation_id} not found, creating new one")
        # Auto-create with the specified agent or default from SpacetimeDB
        agent_id = req.agent_id
        if not agent_id:
            # Query SpacetimeDB for the default agent
            default_agents = await stdb.query("SELECT id FROM agents WHERE isDefault = true LIMIT 1")
            if not default_agents:
                # Last resort fallback if SpacetimeDB agents table is truly empty
                agent_id = "01JBOND0000000000000DEFAULT"
                logger.info(f"[CONVERSATIONS] No default agent found, using fallback: {agent_id}")
            else:
                agent_id = default_agents[0]["id"]
                logger.info(f"[CONVERSATIONS] Using default agent: {agent_id}")
        
        await stdb.call_reducer("create_conversation", [conversation_id, agent_id, req.channel or "webchat", ""])
        logger.info(f"[CONVERSATIONS] Created new conversation {conversation_id} with agent {agent_id}")
    else:
        # Existing conversation — use its agent
        agent_id = conv_row["agent_id"]
        logger.info(f"[CONVERSATIONS] Found existing conversation {conversation_id} with agent {agent_id}")

        # If the caller specifies a different agent and the conversation has
        # no messages yet, update the agent. This handles the case where the
        # user creates a conversation, then switches the agent dropdown before
        # sending the first message.
        if req.agent_id and req.agent_id != agent_id:
            msg_count = conv_row.get("message_count") or conv_row.get("messageCount") or 0
            if int(msg_count) == 0:
                # Verify the requested agent exists
                check = await stdb.query(f"SELECT id FROM agents WHERE id = '{req.agent_id}'")
                if check:
                    agent_id = req.agent_id
                    try:
                        await stdb.call_reducer("update_conversation_agent", [conversation_id, agent_id])
                        logger.info(f"[CONVERSATIONS] Updated empty conversation {conversation_id} agent to {agent_id}")
                    except Exception as e:
                        logger.warning(f"[CONVERSATIONS] Could not update conversation agent via reducer: {e}")
                        # If no reducer exists, the agent will still be used for this turn
                        # via the agent_id variable — just won't persist for future turns.
                else:
                    logger.warning(f"[CONVERSATIONS] Requested agent {req.agent_id} not found, keeping {agent_id}")

    # Save user message to SpacetimeDB
    if req.message:
        msg_id = str(ULID())
        saved = False
        
        # Try save_message (snake_case version of saveMessage)
        try:
            logger.info(f"[CONVERSATIONS] Attempting to save user message via save_message reducer: {msg_id}")
            success = await stdb.call_reducer("save_message", [
                msg_id,
                agent_id,
                conversation_id,
                "user",
                req.message,
                "{}" # metadata
            ])
            if success:
                logger.info(f"[CONVERSATIONS] Message saved via save_message: {msg_id}")
                saved = True
            else:
                logger.error(f"[CONVERSATIONS] save_message returned false for: {msg_id}")
        except Exception as e:
            logger.error(f"[CONVERSATIONS] save_message failed: {e}")
        
        # If save_message fails, use add_conversation_message instead
        if not saved:
            try:
                logger.info(f"[CONVERSATIONS] Attempting to save user message via add_conversation_message: {msg_id}")
                success = await stdb.call_reducer("add_conversation_message", [
                    msg_id,
                    conversation_id,
                    "user",
                    req.message,
                    "",  # toolCalls
                    "",  # toolCallId
                    0,   # tokenCount
                    "delivered"
                ])
                if success:
                    logger.info(f"[CONVERSATIONS] Message saved via add_conversation_message: {msg_id}")
                    saved = True
                else:
                    logger.error(f"[CONVERSATIONS] add_conversation_message returned false for: {msg_id}")
            except Exception as e:
                logger.error(f"[CONVERSATIONS] add_conversation_message also failed: {e}")
        
        if not saved:
            logger.error(f"[CONVERSATIONS] FAILED to save user message {msg_id} to SpacetimeDB!")
        else:
            logger.info(f"[CONVERSATIONS] Successfully saved user message {msg_id}")

        # Auto-title: set title from first user message if conversation is untitled
        try:
            title_rows = await stdb.query(
                f"SELECT title FROM conversations WHERE id = '{conversation_id}'"
            )
            if title_rows and not title_rows[0].get("title"):
                auto_title = req.message.strip()[:80]
                if len(req.message.strip()) > 80:
                    auto_title = auto_title.rsplit(" ", 1)[0] + "..."
                await stdb.call_reducer("update_conversation", [conversation_id, auto_title])
                logger.info(f"[CONVERSATIONS] Auto-titled conversation {conversation_id}: {auto_title}")
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to auto-title: {e}")

    # Load history from SpacetimeDB
    # Try conversation_messages first (migrated data), fall back to messages (new persistence)
    messages_rows = []
    try:
        # Try both camelCase and snake_case table names
        try:
            messages_rows = await stdb.query(
                f"SELECT role, content FROM conversationMessages WHERE conversationId = '{conversation_id}'"
            )
        except:
            messages_rows = await stdb.query(
                f"SELECT role, content FROM conversation_messages WHERE conversation_id = '{conversation_id}'"
            )
        # Sort in Python since we can't ORDER BY in SpacetimeDB without proper indexes
        messages_rows.sort(key=lambda x: x.get("createdAt", 0) or x.get("created_at", 0))
    except Exception as e:
        # Fall back to messages table if conversation_messages doesn't exist or fails
        logger.warning(f"Failed to query conversation_messages: {e}, trying messages table")
        try:
            messages_rows = await stdb.query(
                f"SELECT role, content FROM messages WHERE sessionId = '{conversation_id}' OR session_id = '{conversation_id}'"
            )
            # Try to sort by createdAt if available, otherwise by insertion order
            if messages_rows:
                # Try both camelCase and snake_case column names
                messages_rows.sort(key=lambda x: x.get("createdAt", 0) or x.get("created_at", 0))
        except Exception as e2:
            logger.error(f"Failed to query messages table: {e2}")
            messages_rows = []
    
    # Critical: extract history and current message for the agent call
    history = [{"role": r["role"], "content": r["content"]} for r in messages_rows]
    
    # If the user just sent a message, it's at the end of the history.
    # The worker/loop expects history EXCLUDING the current message.
    user_message = req.message or ""
    if not user_message and history and history[-1]["role"] == "user":
        user_message = history.pop()["content"]
    elif history and history[-1]["role"] == "user":
        # History already contains the message we just saved
        history.pop()

    # Look up agent in SpacetimeDB
    logger.info(f"[CONVERSATIONS] Looking up agent {agent_id} in SpacetimeDB")
    agent_rows = await stdb.query(f"SELECT * FROM agents WHERE id = '{agent_id}'")
    agent_row = agent_rows[0] if agent_rows else None
    if agent_row is None:
        logger.error(f"[CONVERSATIONS] Agent {agent_id} not found in SpacetimeDB")
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found in SpacetimeDB")

    if agent_row.get("sandbox_image") or agent_row.get("sandboxImage"):
        # Container agent — ensure running, proxy SSE
        sandbox_image = agent_row.get("sandbox_image") or agent_row.get("sandboxImage")
        logger.info(f"[CONVERSATIONS] Agent {agent_id} uses container image: {sandbox_image}")
        
        # Pull mounts from SpacetimeDB-ready formats
        workspace_mounts = []
        # Check for mounts table or field (try both case styles)
        mounts_rows = await stdb.query(f"SELECT hostPath, mountName, containerPath, readonly FROM agent_workspace_mounts WHERE agentId = '{agent_id}'")
        if not mounts_rows:
            mounts_rows = await stdb.query(f"SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = '{agent_id}'")
            
        if mounts_rows:
            workspace_mounts = [
                {
                    "host_path": m.get("hostPath") or m.get("host_path"),
                    "mount_name": m.get("mountName") or m.get("mount_name"),
                    "container_path": m.get("containerPath") or m.get("container_path") or f"/workspace/{m.get('mountName') or m.get('host_path')}",
                    "readonly": bool(m.get("readonly")),
                }
                for m in mounts_rows
            ]
            logger.info(f"[CONVERSATIONS] Agent has {len(workspace_mounts)} workspace mounts")
        else:
            logger.info(f"[CONVERSATIONS] Agent has no workspace mounts")

        # Load API keys from SpacetimeDB provider_api_keys table
        api_keys = {}
        try:
            key_rows = await stdb.query("SELECT providerId, encryptedValue FROM provider_api_keys")
            if not key_rows:
                key_rows = await stdb.query("SELECT provider_id, encrypted_value FROM provider_api_keys")
            for kr in key_rows:
                provider_id = kr.get("providerId") or kr.get("provider_id")
                encrypted_val = kr.get("encryptedValue") or kr.get("encrypted_value")
                if provider_id and encrypted_val:
                    try:
                        from backend.app.core.crypto import decrypt_value, is_encrypted
                        val = decrypt_value(encrypted_val)
                        if val:
                            api_keys[provider_id] = val
                    except Exception:
                        # If decryption fails, try as plaintext (dev mode)
                        from backend.app.core.crypto import is_encrypted
                        if not is_encrypted(encrypted_val):
                            api_keys[provider_id] = encrypted_val
            logger.info(f"[CONVERSATIONS] Loaded {len(api_keys)} API keys from provider_api_keys")
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to load API keys from SpacetimeDB: {e}")
            # Fallback: try environment variables
            import os
            for env_key in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"]:
                val = os.environ.get(env_key)
                if val:
                    provider = env_key.replace("_API_KEY", "").lower()
                    api_keys[provider] = val
            if api_keys:
                logger.info(f"[CONVERSATIONS] Loaded {len(api_keys)} API keys from environment")

        # Load provider aliases from SpacetimeDB
        provider_aliases = {}
        try:
            alias_rows = await stdb.query("SELECT alias, providerId FROM provider_aliases")
            if not alias_rows:
                alias_rows = await stdb.query("SELECT alias, provider_id FROM provider_aliases")
            for r in alias_rows:
                alias = r.get("alias")
                pid = r.get("providerId") or r.get("provider_id")
                if alias and pid:
                    provider_aliases[alias] = pid
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to load provider aliases: {e}")

        # Load provider ID → litellm prefix mapping for model normalization
        litellm_prefixes = {}
        try:
            prov_rows = await stdb.query("SELECT id, litellm_prefix FROM providers WHERE is_enabled = true")
            for r in prov_rows:
                pid = r.get("id")
                prefix = r.get("litellm_prefix") or r.get("litellmPrefix")
                if pid and prefix:
                    litellm_prefixes[pid] = prefix
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to load litellm prefixes: {e}")

        agent_dict = {
            "id": agent_row["id"],
            "name": agent_row["name"],
            "sandbox_image": sandbox_image,
            "model": agent_row["model"],
            "utility_model": agent_row.get("utility_model") or agent_row.get("utilityModel", "claude-sonnet-4-6"),
            "system_prompt": agent_row.get("system_prompt") or agent_row.get("systemPrompt"),
            "tools": json.loads(agent_row["tools"]) if isinstance(agent_row["tools"], str) and agent_row["tools"].strip() else (agent_row["tools"] if not isinstance(agent_row["tools"], str) else []),
            "max_iterations": int(agent_row.get("max_iterations") or agent_row.get("maxIterations") or 10),
            "workspace_mounts": workspace_mounts,
            "api_keys": api_keys,
            "provider_aliases": provider_aliases,
            "litellm_prefixes": litellm_prefixes,
        }

        logger.info(f"[CONVERSATIONS] Ensuring container is running for agent {agent_id}")
        try:
            info = await get_sandbox_manager().ensure_running(agent_dict)
            logger.info(f"[CONVERSATIONS] Container running at worker_url: {info['worker_url']}")
        except RuntimeError as e:
            logger.error(f"[CONVERSATIONS] Failed to start container: {e}")
            raise HTTPException(status_code=503, detail=str(e))

        logger.info(f"[CONVERSATIONS] Returning StreamingResponse for conversation {conversation_id}")
        return StreamingResponse(
            _stream_container_turn_stdb(info["worker_url"], history, conversation_id, req.plan_id, agent_id, user_message),
            media_type="text/event-stream",
            background=None,  # Disable background tasks that might buffer response
        )
    else:
        # Host-mode agent not supported without SQLite yet in this refactor
        raise HTTPException(status_code=501, detail="Host-mode agents not yet supported in SpacetimeDB mode")


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
