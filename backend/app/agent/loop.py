"""Agent loop — tool-use loop with auto-RAG and configurable agent profiles.

Loads agent config from DB, injects RAG context, calls LLM with tools,
executes tool calls in a loop until a text response or max iterations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Union

import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.llm import chat_completion, _resolve_api_key, get_instructor_client
from backend.app.agent.tools import build_registry
from backend.app.agent.tools.definitions import get_pydantic_definitions

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

    # Auto-inject shell utility tools — always available, read-only
    _SHELL_UTILITY_TOOLS = [
        "shell_find", "shell_ls", "shell_grep", "git_info",
        "shell_wc", "shell_head", "shell_tree",
    ]
    for _util_tool in _SHELL_UTILITY_TOOLS:
        if _util_tool not in agent_tools:
            agent_tools.append(_util_tool)

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

    # Auto-skills: surface relevant skills so the agent knows they exist
    try:
        from backend.app.agent.tools.skills import _get_router, init_router
        await init_router()
        skill_router = _get_router()
        skills_prompt = await skill_router.get_relevant_skills_prompt(
            user_message, session_id=str(agent.get("id", "")),
        )
        if skills_prompt:
            full_system += (
                "\n\n## Skills\n"
                "Before answering, scan these matched skills. If one clearly applies, "
                "use the `skills` tool with action='read' and the skill name to load "
                "its full instructions, then follow them.\n"
                + skills_prompt
            )
    except Exception:
        logger.debug("Skills injection skipped", exc_info=True)

    # Build messages
    messages: list[dict] = [{"role": "system", "content": full_system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Build tool definitions
    registry = build_registry()
    
    # Inject MCP tools
    all_enabled_tools = list(agent_tools)
    try:
        from backend.app.mcp import mcp_manager
        # Load both global and agent-specific servers for this turn
        # NO SQLITE FALLBACK - use SpacetimeDB directly
        await mcp_manager.load_servers_from_db(None, agent_id=agent["id"])
        await mcp_manager.refresh_tools(registry)
        
        # Add MCP tools to the agent_tools list
        for name in registry.registered_names:
            if name.startswith("mcp_") and name not in all_enabled_tools:
                all_enabled_tools.append(name)
    except Exception as e:
        logger.error(f"Failed to refresh MCP tools: {e}")

    # Tool selection is done per-iteration inside the loop (with momentum).
    from backend.app.agent.tool_selection import select_tools, compact_tool_schema

    # Build context for tool handlers
    workspace_dirs = [os.path.expanduser(m["host_path"]) for m in agent.get("workspace_mounts", [])]
    tool_context: dict[str, Any] = {
        "db": db,
        "agent_id": agent["id"],
        "agent_name": agent.get("name", "agent"),
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
        agent["name"], model_string, len(all_enabled_tools), len(messages),
    )

    # Adaptive max_tokens: start low, escalate on truncation
    TOKEN_TIERS = [8192, 32768, 65536]
    current_tier = 0
    continuation_attempts = 0

    # Tool-use loop
    for iteration in range(max_iterations):
        current_max_tokens = TOKEN_TIERS[current_tier]

        # Re-select tools each iteration (momentum from recent tool calls)
        recent_tools: list[str] = []
        for msg in messages:
            if msg.get("role") == "assistant":
                tcs = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
                if tcs and isinstance(tcs, list):
                    for tc in tcs:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", None)
                        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
                        if name:
                            recent_tools.append(name)
        selected_tool_names = select_tools(
            user_message=user_message,
            enabled_tools=all_enabled_tools,
            recent_tools_used=recent_tools[-10:] if recent_tools else None,
            agent_name=agent.get("name"),
        )
        raw_defs = registry.get_definitions_for(selected_tool_names)
        tool_defs = [compact_tool_schema(td) for td in raw_defs]

        # Use Instructor for validated tool calls
        pydantic_tools = get_pydantic_definitions(selected_tool_names)
        if pydantic_tools:
            # Create a Union of all available tools
            # Instructor will handle the routing and validation
            if len(pydantic_tools) > 1:
                ToolUnion = Union[tuple(pydantic_tools)]
            else:
                ToolUnion = pydantic_tools[0]
            
            # Patch the call to use Instructor
            instructor_client = get_instructor_client()
            
            try:
                # Instructor's mode=instructor.Mode.TOOLS translates Pydantic to JSON Schema
                # and handles the response back into Pydantic objects.
                tool_call_obj = await instructor_client(
                    model=model_string,
                    messages=messages,
                    response_model=ToolUnion,
                    max_retries=2, # Self-correction turns!
                    temperature=0.7,
                    max_tokens=current_max_tokens,
                    **extra_kwargs,
                )
                
                # Convert Instructor result back into a format the loop expects
                # (Simulating a LiteLLM response object for minimum code disruption)
                
                # Resolve tool name (handles both native snake_case and MCP PascalCase)
                from backend.app.mcp import mcp_manager
                tool_name = mcp_manager.resolve_tool_name(tool_call_obj.__class__.__name__)
                
                args = tool_call_obj.model_dump(exclude_none=True)
                
                # Mock a response object
                class MockMessage:
                    def __init__(self, name, args):
                        self.content = None
                        self.tool_calls = [type('TC', (), {
                            'function': type('FN', (), {'name': name, 'arguments': json.dumps(args)}),
                            'id': f"call_{iteration}"
                        })]
                
                class MockChoice:
                    def __init__(self, msg):
                        self.message = msg
                        self.finish_reason = "tool_calls"
                
                class MockResponse:
                    def __init__(self, choice):
                        self.choices = [choice]
                
                response = MockResponse(MockChoice(MockMessage(tool_name, args)))
            except Exception as e:
                logger.error("Instructor tool call failed: %s", e)
                # Fallback to standard litellm on error
                response = await litellm.acompletion(
                    model=model_string,
                    messages=messages,
                    tools=tool_defs if tool_defs else None,
                    temperature=0.7,
                    max_tokens=current_max_tokens,
                    **extra_kwargs,
                )
        else:
            response = await litellm.acompletion(
                model=model_string,
                messages=messages,
                tools=tool_defs if tool_defs else None,
                temperature=0.7,
                max_tokens=current_max_tokens,
                **extra_kwargs,
            )

        choice = response.choices[0]
        message = choice.message

        # Handle truncation with continuation
        if choice.finish_reason == "length":
            continuation_attempts += 1
            if continuation_attempts > 3:
                return "Output token limit exceeded repeatedly. Try breaking the task into smaller pieces."
            if current_tier < len(TOKEN_TIERS) - 1:
                current_tier += 1
                logger.info("Escalating max_tokens to %d after truncation", TOKEN_TIERS[current_tier])
            partial = message.content or ""
            if partial:
                messages.append({"role": "assistant", "content": partial})
                messages.append({"role": "user", "content": "Your response was cut off. Please continue exactly where you left off."})
            continue

        current_tier = 0
        continuation_attempts = 0

        # Check for tool calls
        if message.tool_calls:
            messages.append(message.model_dump())

            # ── Parallel execution of independent tool calls ──
            # Tools that only read state can run concurrently.
            # Side-effecting or terminal tools must run sequentially.
            PARALLELIZABLE_TOOLS = frozenset({
                "file_read", "search_memory", "code_execute",
                "web_search", "web_read",
            })

            # Parse all tool calls upfront
            parsed_calls = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                parsed_calls.append((tc, tc.function.name, args))

            # Separate into parallel-safe and sequential groups
            parallel_batch = []
            sequential_batch = []
            for tc, name, args in parsed_calls:
                if name in PARALLELIZABLE_TOOLS and name in all_enabled_tools:
                    parallel_batch.append((tc, name, args))
                else:
                    sequential_batch.append((tc, name, args))

            # Execute parallel batch concurrently
            if parallel_batch:
                async def _exec_one(tc, name, args):
                    logger.info("Tool call [%d] (parallel): %s(%s)", iteration, name, list(args.keys()))
                    return tc, await registry.execute(name, args, tool_context)

                parallel_results = await asyncio.gather(
                    *[_exec_one(tc, name, args) for tc, name, args in parallel_batch],
                    return_exceptions=True,
                )
                for item in parallel_results:
                    if isinstance(item, Exception):
                        # Find the matching tool call for error reporting
                        tc = parallel_batch[parallel_results.index(item)][0]
                        result = {"error": f"Parallel execution failed: {item}"}
                    else:
                        tc, result = item

                    if result.get("_terminal"):
                        return result.get("message", "")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    })

            # Execute sequential batch in order
            for tool_call, tool_name, tool_args in sequential_batch:
                logger.info("Tool call [%d]: %s(%s)", iteration, tool_name, list(tool_args.keys()))

                if tool_name not in all_enabled_tools:
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
