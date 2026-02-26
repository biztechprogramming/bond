"""Agent loop — tool-use loop with auto-RAG and configurable agent profiles.

Loads agent config from DB, injects RAG context, calls LLM with tools,
executes tool calls in a loop until a text response or max iterations.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator

import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.llm import chat_completion, _resolve_api_key
from backend.app.agent.tools import build_registry

logger = logging.getLogger("bond.agent.loop")

DEFAULT_SYSTEM_PROMPT = """\
You are Bond, a helpful personal AI assistant. You are running locally on the \
user's machine. Be concise, helpful, and friendly. If you don't know something, \
say so directly.\
"""


async def _load_default_agent(db: AsyncSession) -> dict[str, Any]:
    """Load the default agent config from the database."""
    fallback = {
        "id": "default",
        "name": "bond",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "model": "anthropic/claude-sonnet-4-20250514",
        "sandbox_image": None,
        "tools": '["respond","search_memory","memory_save","memory_update"]',
        "max_iterations": 25,
        "auto_rag": 1,
        "auto_rag_limit": 5,
        "workspace_mounts": [],
    }
    try:
        result = await db.execute(
            text("SELECT * FROM agents WHERE is_default = 1 LIMIT 1")
        )
    except Exception:
        logger.debug("Could not query agents table, using fallback config")
        return fallback
    row = result.mappings().first()
    if row is None:
        return fallback

    agent = dict(row)

    # Load workspace mounts
    mounts_result = await db.execute(
        text("SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = :id"),
        {"id": agent["id"]},
    )
    agent["workspace_mounts"] = [
        {"host_path": m["host_path"], "mount_name": m["mount_name"], "container_path": m["container_path"], "readonly": bool(m["readonly"])}
        for m in mounts_result.mappings().all()
    ]

    return agent


async def _auto_rag(db: AsyncSession, query: str, limit: int) -> str:
    """Run hybrid search for auto-RAG context injection."""
    try:
        from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
        from backend.app.foundations.knowledge.search import HybridSearcher

        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(db, caps)
        results = await searcher.search(
            table_name="memories",
            query_text=query,
            limit=limit,
        )
        if not results:
            return ""

        context_parts = [f"- {r.content}" for r in results]
        return "\n\nRelevant context from memory:\n" + "\n".join(context_parts)
    except Exception as e:
        logger.debug("Auto-RAG failed (non-fatal): %s", e)
        return ""


async def _load_agent_by_id(db: AsyncSession, agent_id: str) -> dict[str, Any]:
    """Load a specific agent config by ID."""
    fallback = {
        "id": agent_id,
        "name": "bond",
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "model": "anthropic/claude-sonnet-4-20250514",
        "sandbox_image": None,
        "tools": '["respond","search_memory","memory_save","memory_update"]',
        "max_iterations": 25,
        "auto_rag": 1,
        "auto_rag_limit": 5,
        "workspace_mounts": [],
    }
    try:
        result = await db.execute(
            text("SELECT * FROM agents WHERE id = :id"),
            {"id": agent_id},
        )
    except Exception:
        logger.debug("Could not query agents table for id=%s, using fallback", agent_id)
        return fallback
    row = result.mappings().first()
    if row is None:
        return await _load_default_agent(db)

    agent = dict(row)

    mounts_result = await db.execute(
        text("SELECT host_path, mount_name, container_path, readonly FROM agent_workspace_mounts WHERE agent_id = :id"),
        {"id": agent["id"]},
    )
    agent["workspace_mounts"] = [
        {"host_path": m["host_path"], "mount_name": m["mount_name"], "container_path": m["container_path"], "readonly": bool(m["readonly"])}
        for m in mounts_result.mappings().all()
    ]

    return agent


async def agent_turn(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    *,
    system_prompt: str | None = None,
    stream: bool = False,
    db: AsyncSession | None = None,
    agent_id: str | None = None,
) -> str | AsyncIterator[str]:
    """Execute a single agent turn with tool-use loop.

    If db is provided, loads agent config and runs the full tool-use loop.
    Otherwise falls back to simple LLM call (backward compatible).
    """
    if db is None or stream:
        # Fallback to simple mode (no tools, streaming support)
        messages = [
            {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        logger.info("Agent turn (simple): %d messages in context", len(messages))
        return await chat_completion(messages, stream=stream)

    # Full tool-use loop
    if agent_id:
        agent = await _load_agent_by_id(db, agent_id)
    else:
        agent = await _load_default_agent(db)
    agent_tools = json.loads(agent["tools"]) if isinstance(agent["tools"], str) else agent["tools"]
    max_iterations = agent.get("max_iterations", 25)
    effective_prompt = system_prompt or agent.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

    # Auto-RAG: inject relevant memories
    rag_context = ""
    if agent.get("auto_rag"):
        rag_limit = agent.get("auto_rag_limit", 5)
        rag_context = await _auto_rag(db, user_message, rag_limit)

    full_system = effective_prompt
    if rag_context:
        full_system += rag_context

    # Build messages
    messages: list[dict] = [{"role": "system", "content": full_system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Build tool definitions
    registry = build_registry()
    tool_defs = registry.get_definitions_for(agent_tools)

    # Build context for tool handlers
    workspace_dirs = [os.path.expanduser(m["host_path"]) for m in agent.get("workspace_mounts", [])]
    tool_context: dict[str, Any] = {
        "db": db,
        "agent_id": agent["id"],
        "sandbox_image": agent.get("sandbox_image"),
        "workspace_dirs": workspace_dirs,
        "workspace_mounts": agent.get("workspace_mounts", []),
    }

    # Resolve LLM settings
    model_string = agent.get("model", "anthropic/claude-sonnet-4-20250514")
    provider = model_string.split("/")[0] if "/" in model_string else "anthropic"
    api_key = await _resolve_api_key(provider)

    extra_kwargs: dict = {}
    if api_key:
        extra_kwargs["api_key"] = api_key

    logger.info(
        "Agent turn (tool-use): agent=%s model=%s tools=%d messages=%d",
        agent["name"], model_string, len(tool_defs), len(messages),
    )

    # Tool-use loop
    for iteration in range(max_iterations):
        response = await litellm.acompletion(
            model=model_string,
            messages=messages,
            tools=tool_defs if tool_defs else None,
            temperature=0.7,
            max_tokens=16384,
            **extra_kwargs,
        )

        choice = response.choices[0]
        message = choice.message

        # Check for tool calls
        if message.tool_calls:
            messages.append(message.model_dump())

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info("Tool call [%d]: %s(%s)", iteration, tool_name, list(tool_args.keys()))

                if tool_name not in agent_tools:
                    result = {"error": f"Tool '{tool_name}' is not enabled for this agent."}
                else:
                    result = await registry.execute(tool_name, tool_args, tool_context)

                # Check for terminal tool (respond)
                if result.get("_terminal"):
                    return result.get("message", "")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })
        else:
            # Text response — return it
            return message.content or ""

    logger.warning("Agent hit max iterations (%d)", max_iterations)
    return "I've reached my maximum number of steps for this request. Please try rephrasing or breaking your request into smaller parts."
