import json
import logging
from ulid import ULID
import httpx

from backend.app.core.spacetimedb import get_stdb
from backend.app.agent.interrupts import register_turn, unregister_turn, is_interrupted

logger = logging.getLogger(__name__)

def _sse(event: str, data: dict) -> str:
    """Format an SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

async def _stream_container_turn_stdb(
    worker_url: str,
    history: list[dict],
    conversation_id: str,
    plan_id: str | None,
    agent_id: str,
    user_message: str,
):
    """Proxy SSE from a container worker, using SpacetimeDB for persistence."""
    stdb = get_stdb()
    response_content = ""
    tool_calls_made = 0
    event_type = ""

    try:
        logger.info(f"[TURN_STDB] Starting container turn for conversation={conversation_id}, agent={agent_id}, worker={worker_url}")
        register_turn(conversation_id, worker_url=worker_url)
        yield _sse("status", {"state": "thinking", "conversation_id": conversation_id})

        interrupted = False
        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.info(f"[TURN_STDB] Calling worker at {worker_url}/turn")
            async with client.stream(
                "POST",
                f"{worker_url}/turn",
                json={
                    "message": user_message,
                    "history": history,
                    "conversation_id": conversation_id,
                    "plan_id": plan_id
                },
            ) as resp:
                resp.raise_for_status()
                logger.info(f"[TURN_STDB] Worker responded with status {resp.status_code}")
                async for line in resp.aiter_lines():
                    # Check if we've been interrupted — stop proxying
                    if is_interrupted(conversation_id):
                        logger.info(f"[TURN_STDB] Interrupt detected, stopping SSE proxy for {conversation_id}")
                        interrupted = True
                        break
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                        logger.info(f"[TURN_STDB] Received SSE event: {event_type}")
                    elif line.startswith("data:") and event_type:
                        try:
                            data = json.loads(line[len("data:"):].strip())
                        except json.JSONDecodeError:
                            logger.info(f"[TURN_STDB] Failed to parse JSON data: {line}")
                            continue

                        if event_type == "chunk":
                            chunk_content = data.get("content", "")
                            response_content += chunk_content
                            logger.info(f"[TURN_STDB] Received chunk: {chunk_content[:50]}...")
                            yield _sse("chunk", data)
                        elif event_type == "status":
                            logger.info(f"[TURN_STDB] Worker status: {data}")
                            yield _sse("status", data)
                        elif event_type == "interim_message":
                            logger.info(f"[TURN_STDB] Interim message: {data.get('content', '')[:50]}...")
                            yield _sse("interim_message", data)
                        elif event_type in ("tool_call", "plan_created", "item_created", "item_updated", "plan_completed"):
                            logger.info(f"[TURN_STDB] {event_type}: {data}")
                            yield _sse(event_type, data)
                        elif event_type == "done":
                            response_content = data.get("response", response_content)
                            tool_calls_made = data.get("tool_calls_made", 0)
                            logger.info(f"[TURN_STDB] Worker done: tool_calls={tool_calls_made}, response_length={len(response_content)}")
                        elif event_type == "error":
                            logger.info(f"[TURN_STDB] Worker error: {data}")
                            yield _sse("error", data)

        if interrupted:
            # Save whatever partial response we got so far
            logger.info(f"[TURN_STDB] Turn interrupted for {conversation_id}, saving partial response ({len(response_content)} chars)")
            yield _sse("status", {"state": "interrupted", "conversation_id": conversation_id})

        # Save assistant message to SpacetimeDB (even partial on interrupt)
        msg_id = str(ULID())
        if response_content.strip():
            logger.info(f"[TURN_STDB] Saving assistant message to SpacetimeDB: id={msg_id}, conversation={conversation_id}, length={len(response_content)}")
            try:
                success = await stdb.call_reducer("add_conversation_message", [
                    msg_id,
                    conversation_id,
                    "assistant",
                    response_content,
                    "", # tool_calls
                    "", # tool_call_id
                    0,  # token_count
                    "delivered"
                ])
                if success:
                    logger.info(f"[TURN_STDB] Successfully saved assistant message {msg_id} to SpacetimeDB")
                else:
                    logger.error(f"[TURN_STDB] Failed to save assistant message {msg_id} to SpacetimeDB: reducer returned false")
            except Exception as e:
                logger.error(f"[TURN_STDB] Failed to save assistant message to SpacetimeDB: {e}", exc_info=True)
                raise
        else:
            logger.info(f"[TURN_STDB] No response content to save for {conversation_id}")

        logger.info(f"[TURN_STDB] Yielding final done event with message_id={msg_id}")
        yield _sse("done", {
            "message_id": msg_id,
            "conversation_id": conversation_id,
            "tool_calls_made": tool_calls_made,
            "queued_count": 0,
            "interrupted": interrupted,
            "response": response_content,
        })
    except Exception as e:
        logger.error(f"[TURN_STDB] Container turn error: conversation={conversation_id} error={e}", exc_info=True)
        yield _sse("error", {"message": str(e)})
    finally:
        logger.info(f"[TURN_STDB] Unregistering turn for conversation={conversation_id}")
        unregister_turn(conversation_id)
