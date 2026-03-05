import json
import logging
from ulid import ULID
import httpx

from backend.app.core.spacetimedb import get_stdb
from backend.app.agent.interrupts import register_turn, unregister_turn

logger = logging.getLogger(__name__)

def _sse(event: str, data: dict) -> str:
    """Format an SSE event."""
    return f"event: {event}\\ndata: {json.dumps(data)}\\n\\n"

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
        register_turn(conversation_id)
        yield _sse("status", {"state": "thinking", "conversation_id": conversation_id})

        timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
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
                        elif event_type == "done":
                            response_content = data.get("response", response_content)
                            tool_calls_made = data.get("tool_calls_made", 0)
                        elif event_type == "error":
                            yield _sse("error", data)

        # Save assistant message to SpacetimeDB
        msg_id = str(ULID())
        await stdb.call_reducer("add_conversation_message", [
            msg_id,
            conversation_id,
            "assistant",
            response_content,
            "", # tool_calls
            "", # tool_call_id
            0,  # token_count
            "delivered"
        ])

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
