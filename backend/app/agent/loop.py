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

from backend.app.agent.llm import (
    chat_completion, _resolve_api_key, get_instructor_client,
    get_context_limit, COMPACTION_THRESHOLD, HARD_CEILING,
    ContextOverflowError, classify_overflow_error,
)
from backend.app.agent.context_pipeline import _estimate_messages_tokens
from backend.app.agent.tools import build_registry
from backend.app.agent.tools.definitions import get_pydantic_definitions
from backend.app.agent.tools.tool_result import ToolResult, ToolTimer
from backend.app.agent.tool_result_cache import ToolResultCache
from backend.app.agent.interrupts import check_interrupt
from backend.app.agent.continuation import (
    LightweightCheckpoint, ToolCallRecord, save_checkpoint,
    load_checkpoint, format_checkpoint_context,
    IterationBudget, build_checkpoint_from_history,
)
from backend.app.core.oauth import get_oauth_extra_headers

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
        "max_iterations": 80,
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
        "max_iterations": 80,
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


async def _check_token_budget(messages: list[dict], model: str) -> list[dict]:
    """Check token usage against model limits; compact if needed (Doc 090).

    Uses simple message dropping since the full _compress_history requires
    conversation_id and config that aren't available in loop.py's context.
    Returns the (possibly compacted) message list.
    """
    token_count = _estimate_messages_tokens(messages)
    context_limit = get_context_limit(model)
    usage_ratio = token_count / context_limit

    logger.info(
        "token_budget_check token_count=%d context_limit=%d usage_ratio=%.1f%% model=%s",
        token_count, context_limit, usage_ratio * 100, model,
    )

    if usage_ratio < COMPACTION_THRESHOLD:
        return messages

    # Determine target based on severity
    if usage_ratio >= HARD_CEILING:
        target_tokens = int(context_limit * 0.60)
        logger.warning(
            "token_budget_hard_ceiling token_count=%d limit=%d — aggressive compaction to %d",
            token_count, context_limit, target_tokens,
        )
    else:
        target_tokens = int(context_limit * 0.70)
        logger.info(
            "token_budget_proactive_compaction token_count=%d limit=%d — compacting to %d",
            token_count, context_limit, target_tokens,
        )

    # Simple compaction: keep system message + most recent messages, drop middle
    if not messages:
        return messages

    # Always keep the system message (index 0) and the last 4 messages
    keep_tail = 4
    if len(messages) <= keep_tail + 1:
        return messages  # too few messages to compact

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    start = 1 if system_msg else 0
    tail = messages[-keep_tail:]

    # Drop messages from the middle until we're under target
    middle = messages[start:-keep_tail]
    compacted_middle: list[dict] = []
    current_estimate = (
        _estimate_messages_tokens([system_msg] if system_msg else [])
        + _estimate_messages_tokens(tail)
    )

    # Walk middle from newest to oldest, keeping as many as fit
    for msg in reversed(middle):
        msg_tokens = _estimate_messages_tokens([msg])
        if current_estimate + msg_tokens <= target_tokens:
            compacted_middle.insert(0, msg)
            current_estimate += msg_tokens

    result = []
    if system_msg:
        result.append(system_msg)
    result.extend(compacted_middle)
    result.extend(tail)

    tokens_after = _estimate_messages_tokens(result)
    logger.info(
        "token_budget_compacted before=%d after=%d dropped=%d messages",
        token_count, tokens_after, len(messages) - len(result),
    )

    return result


# ---------------------------------------------------------------------------
# Doc 091: Overflow Recovery — 3-tier recovery chain
# ---------------------------------------------------------------------------

MAX_OVERFLOW_RETRIES = 3
MAX_TRUNCATION_RETRIES = 3


def _aggressive_compact(messages: list[dict], keep_recent_turns: int = 3) -> list[dict]:
    """Drop all tool call/result messages older than keep_recent_turns.

    Preserves system messages and recent conversation.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    # Keep only the last N pairs of messages
    recent = non_system[-(keep_recent_turns * 2):]

    return system_msgs + recent


def _emergency_collapse(messages: list[dict]) -> list[dict]:
    """Nuclear option: keep only the system prompt and the last 2 non-system messages."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    last_two = non_system[-2:] if len(non_system) >= 2 else non_system

    collapsed = system_msgs + last_two
    logger.warning(
        "emergency_collapse original_messages=%d collapsed_messages=%d",
        len(messages), len(collapsed),
    )
    return collapsed


async def _llm_call_with_overflow_recovery(
    messages: list[dict],
    model: str,
    llm_coro_factory,
) -> tuple[Any, list[dict]]:
    """Wrap an LLM call with 3-tier overflow recovery.

    *llm_coro_factory* is a callable(messages) -> awaitable that makes the
    actual LLM call.  Returns (response, possibly_compacted_messages).
    """
    last_error: Exception | None = None
    tiers = ["standard", "aggressive", "emergency"]

    for attempt in range(MAX_OVERFLOW_RETRIES):
        try:
            response = await llm_coro_factory(messages)
            return response, messages
        except Exception as exc:
            overflow = classify_overflow_error(exc)
            if overflow is None:
                raise  # not an overflow error — propagate
            last_error = overflow
            tier = tiers[attempt]
            logger.warning(
                "overflow_recovery_attempt attempt=%d tier=%s error=%s",
                attempt + 1, tier, str(exc)[:200],
            )

            context_limit = get_context_limit(model)
            tokens_before = _estimate_messages_tokens(messages)

            if attempt == 0:
                # Tier 1: Standard compaction — keep system + fit as many recent msgs
                messages = await _check_token_budget(messages, model)
                # Force tighter target if _check_token_budget didn't compact enough
                if _estimate_messages_tokens(messages) == tokens_before:
                    messages = _aggressive_compact(messages, keep_recent_turns=5)
            elif attempt == 1:
                # Tier 2: Aggressive — drop old tool results entirely
                messages = _aggressive_compact(messages, keep_recent_turns=3)
            else:
                # Tier 3: Emergency collapse
                messages = _emergency_collapse(messages)

            tokens_after = _estimate_messages_tokens(messages)
            logger.info(
                "overflow_compaction tier=%s tokens_before=%d tokens_after=%d",
                tier, tokens_before, tokens_after,
            )

    # All retries exhausted
    logger.error("overflow_recovery_exhausted attempts=%d", MAX_OVERFLOW_RETRIES)
    raise last_error  # type: ignore[misc]


def _classify_stop_reason(iteration: int, max_iterations: int, checkpoint: LightweightCheckpoint, error: Exception | None = None) -> str:
    """Classify why the agent loop stopped."""
    if checkpoint.stop_reason:
        return checkpoint.stop_reason
    if error is not None:
        error_str = str(error).lower()
        TRANSIENT_PATTERNS = ["rate_limit", "429", "503", "overloaded", "timeout", "connection"]
        if any(p in error_str for p in TRANSIENT_PATTERNS):
            return "transient_error"
        return f"error: {type(error).__name__}: {str(error)[:200]}"
    if iteration >= max_iterations - 1:
        return "budget_exhausted"
    return "completed"


def _summarize_progress(iteration: int, checkpoint: LightweightCheckpoint) -> str:
    """Build a human-readable progress summary."""
    parts = []
    parts.append(f"{iteration + 1} iterations")
    total = checkpoint.successful_tool_calls + checkpoint.failed_tool_calls
    if total:
        parts.append(f"{total} tool calls ({checkpoint.successful_tool_calls} ok, {checkpoint.failed_tool_calls} failed)")
    if checkpoint.files_modified:
        parts.append(f"{len(checkpoint.files_modified)} files modified")
    return ", ".join(parts) if parts else "no progress recorded"


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
        "shell_find", "shell_ls", "file_search", "git_info",
        "shell_wc", "shell_tree", "file_list",
    ]
    for _util_tool in _SHELL_UTILITY_TOOLS:
        if _util_tool not in agent_tools:
            agent_tools.append(_util_tool)

    max_iterations = agent.get("max_iterations", 100)
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

    # Doc 049: Inject learned lessons from approved/ directory
    try:
        from backend.app.agent.critic import load_lessons
        _lessons = load_lessons()
        if _lessons:
            full_system += _lessons
    except Exception:
        pass

    # Build messages
    messages: list[dict] = [{"role": "system", "content": full_system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Build tool definitions
    registry = build_registry()
    
    # Inject MCP tools (Design Doc 054: host-side via proxy or direct manager)
    all_enabled_tools = list(agent_tools)
    try:
        from backend.app.mcp import mcp_manager
        # Host-side loop: use MCPManager directly (connection pools)
        await mcp_manager.ensure_servers_loaded(agent_id=agent["id"])
        await mcp_manager.refresh_tools(registry)

        for name in registry.registered_names:
            if name.startswith("mcp_") and name not in all_enabled_tools:
                all_enabled_tools.append(name)
    except Exception as e:
        logger.error(f"Failed to refresh MCP tools: {e}")

    # Tool selection is done per-iteration inside the loop (with momentum).
    from backend.app.agent.tool_selection import select_tools, compact_tool_schema

    # Doc 049: simplified outcome tracking (loop.py is the fallback path)
    # Full outcome tracking is in worker.py; loop.py just avoids errors.

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
        # OAuth tokens (sk-ant-oat) need extra headers for the Anthropic API
        oauth_headers = get_oauth_extra_headers(api_key)
        if oauth_headers:
            extra_kwargs["extra_headers"] = oauth_headers
            logger.info("Detected OAuth token — injecting extra headers")

    # Inject OAuth system prompt prefix if needed (centralized)
    from backend.app.core.oauth import ensure_oauth_system_prefix
    ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)

    logger.info(
        "Agent turn (tool-use): agent=%s model=%s tools=%d messages=%d",
        agent["name"], model_string, len(all_enabled_tools), len(messages),
    )

    # Doc 065: Tool result caching
    _cache_enabled = os.environ.get("TOOL_CACHE_ENABLED", "true").lower() != "false"
    _cache_shadow = os.environ.get("TOOL_CACHE_SHADOW_MODE", "true").lower() != "false"
    session_cache = ToolResultCache(shadow_mode=_cache_shadow) if _cache_enabled else None

    # Adaptive max_tokens: start low, escalate on truncation
    TOKEN_TIERS = [8192, 32768, 65536]
    current_tier = 0
    continuation_attempts = 0

    # Doc 096: Progress checkpointing
    # Derive conversation_id from context (agent_id or fallback)
    _conversation_id = str(agent.get("id", "unknown"))
    loop_checkpoint = LightweightCheckpoint()
    loop_checkpoint.last_user_request = user_message[:500]

    # Load any existing checkpoint for resume
    try:
        existing_checkpoint = await load_checkpoint(_conversation_id)
        if existing_checkpoint:
            checkpoint_context = format_checkpoint_context(existing_checkpoint)
            messages.insert(1, {"role": "system", "content": checkpoint_context})
            logger.info("Loaded existing checkpoint for conversation %s", _conversation_id)
    except Exception:
        logger.debug("Failed to load checkpoint (non-fatal)", exc_info=True)

    # Doc 096: Budget tracker
    budget = IterationBudget(total=max_iterations)
    _checkpoint_saved_at_threshold = False
    _exit_error: Exception | None = None

    # Tool-use loop (wrapped in try/except for checkpoint safety)
    try:
        for iteration in range(max_iterations):
            budget.tick()
            # Doc 096: Check interrupt before LLM call
            if check_interrupt(_conversation_id):
                loop_checkpoint.stop_reason = "interrupted"
                break
    
            loop_checkpoint.turn_number = iteration
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
                iteration=iteration,
            )
            raw_defs = registry.get_definitions_for(selected_tool_names)
            tool_defs = [compact_tool_schema(td) for td in raw_defs]
    
            # Doc 090: Pre-call token budget check
            messages = await _check_token_budget(messages, model_string)
    
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
                    from backend.app.mcp import mcp_manager as _loop_mcp_manager
                    tool_name = _loop_mcp_manager.resolve_tool_name(tool_call_obj.__class__.__name__)
                    
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
                    overflow = classify_overflow_error(e)
                    if overflow is not None:
                        # Doc 091: overflow during Instructor — recover with standard path
                        logger.warning("Instructor call hit overflow, attempting recovery")
                        async def _fallback_call(msgs):
                            return await litellm.acompletion(
                                model=model_string, messages=msgs,
                                tools=tool_defs if tool_defs else None,
                                temperature=0.7, max_tokens=current_max_tokens,
                                **extra_kwargs,
                            )
                        response, messages = await _llm_call_with_overflow_recovery(
                            messages, model_string, _fallback_call,
                        )
                    else:
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
                # Doc 091: wrap with overflow recovery
                async def _standard_call(msgs):
                    return await litellm.acompletion(
                        model=model_string, messages=msgs,
                        tools=tool_defs if tool_defs else None,
                        temperature=0.7, max_tokens=current_max_tokens,
                        **extra_kwargs,
                    )
                response, messages = await _llm_call_with_overflow_recovery(
                    messages, model_string, _standard_call,
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
                        # Doc 065: check cache before executing
                        if session_cache:
                            cached = session_cache.check(name, args, iteration)
                            if cached:
                                formatted = session_cache.format_cache_hit(cached, iteration)
                                if formatted is not None:
                                    return tc, {"output": formatted}
                        # Doc 092: wrap with structured error handling
                        with ToolTimer() as _t:
                            try:
                                raw_result = await registry.execute(name, args, tool_context)
                            except Exception as _exc:
                                _tr = ToolResult.from_error(
                                    error=f"{type(_exc).__name__}: {_exc}",
                                    tool_name=name,
                                    duration_ms=_t.duration_ms,
                                )
                                logger.error("tool_execution_error tool=%s error=%s", name, _exc)
                                return tc, {"error": _tr.to_message_content()}
                        # Doc 065: store result and record mutations
                        if session_cache:
                            result_text = json.dumps(raw_result) if isinstance(raw_result, dict) else str(raw_result)
                            session_cache.store(name, args, result_text, iteration)
                            session_cache.record_mutation(name, args, iteration)
                            if name == "code_execute":
                                session_cache.revalidate_after_execute()
                        return tc, raw_result
    
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
                            loop_checkpoint.stop_reason = "completed"
                            return result.get("message", "")

                        # Doc 096: Record parallel tool call in checkpoint
                        _par_name = parallel_batch[parallel_results.index(item)][1] if not isinstance(item, Exception) else "unknown"
                        _par_success = not isinstance(item, Exception) and "error" not in result
                        _par_summary = str(result)[:200] if not isinstance(item, Exception) else str(item)[:200]
                        loop_checkpoint.completed_actions.append(ToolCallRecord(
                            tool_name=_par_name, success=_par_success, output_summary=_par_summary,
                        ))
                        if _par_success:
                            loop_checkpoint.successful_tool_calls += 1
                        else:
                            loop_checkpoint.failed_tool_calls += 1
    
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result),
                        })
    
                # Execute sequential batch in order
                for tool_call, tool_name, tool_args in sequential_batch:
                    logger.info("Tool call [%d]: %s(%s)", iteration, tool_name, list(tool_args.keys()))
    
                    # Doc 065: check cache before executing
                    if session_cache:
                        cached = session_cache.check(tool_name, tool_args, iteration)
                        if cached:
                            formatted = session_cache.format_cache_hit(cached, iteration)
                            if formatted is not None:
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": formatted,
                                })
                                continue
    
                    if tool_name not in all_enabled_tools:
                        result = {"error": f"Tool '{tool_name}' is not enabled for this agent."}
                    else:
                        # Doc 092: wrap with ToolResult for structured error handling
                        with ToolTimer() as _timer:
                            try:
                                result = await registry.execute(tool_name, tool_args, tool_context)
                            except Exception as _exc:
                                _tool_result = ToolResult.from_error(
                                    error=f"{type(_exc).__name__}: {_exc}",
                                    tool_name=tool_name,
                                    duration_ms=_timer.duration_ms,
                                )
                                logger.error("tool_execution_error tool=%s error=%s", tool_name, _exc)
                                result = {"error": _tool_result.to_message_content()}
    
                    # Doc 065: store result and record mutations
                    if session_cache:
                        result_text = json.dumps(result) if isinstance(result, dict) else str(result)
                        session_cache.store(tool_name, tool_args, result_text, iteration)
                        session_cache.record_mutation(tool_name, tool_args, iteration)
                        if tool_name == "code_execute":
                            session_cache.revalidate_after_execute()
    
                    # Doc 096: Record sequential tool call in checkpoint
                    _seq_success = "error" not in result
                    _seq_summary = str(result)[:200]
                    _seq_duration = int(_timer.duration_ms) if '_timer' in dir() else 0
                    loop_checkpoint.completed_actions.append(ToolCallRecord(
                        tool_name=tool_name, success=_seq_success, output_summary=_seq_summary,
                        duration_ms=_seq_duration,
                    ))
                    if _seq_success:
                        loop_checkpoint.successful_tool_calls += 1
                    else:
                        loop_checkpoint.failed_tool_calls += 1
                    # Track file modifications
                    if tool_name in ("file_edit", "file_write") and _seq_success:
                        _fpath = tool_args.get("path", tool_args.get("file_path", ""))
                        if _fpath and _fpath not in loop_checkpoint.files_modified:
                            loop_checkpoint.files_modified.append(_fpath)
    
                    # Check for terminal tool (respond)
                    if result.get("_terminal"):
                        loop_checkpoint.stop_reason = "completed"
                        return result.get("message", "")

                    # Doc 092: Format tool result with structured error feedback
                    if isinstance(result, dict) and "error" in result and not result.get("_terminal"):
                        _content = result["error"] if isinstance(result["error"], str) else json.dumps(result)
                        logger.info("tool_failure_feedback tool=%s error=%s", tool_name, result.get("error"))
                    else:
                        _content = json.dumps(result)
    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": _content,
                    })
    
                    # Doc 096: Check interrupt after tool execution
                    if check_interrupt(_conversation_id):
                        loop_checkpoint.stop_reason = "interrupted"
                        break

                # Doc 096: Mid-loop budget-threshold checkpoint save
                if budget.should_checkpoint and not _checkpoint_saved_at_threshold:
                    loop_checkpoint.progress_summary = _summarize_progress(iteration, loop_checkpoint)
                    if workspace_dirs:
                        loop_checkpoint.capture_git_state(workspace_dirs[0])
                    try:
                        await save_checkpoint(_conversation_id, agent_id or "", loop_checkpoint)
                        _checkpoint_saved_at_threshold = True
                        logger.info("Mid-loop checkpoint saved at %s budget", f"{budget.pct_used:.0%}")
                    except Exception:
                        logger.debug("Mid-loop checkpoint save failed (non-fatal)", exc_info=True)

                # Doc 096: Inject budget-awareness messages
                budget_msg = budget.get_budget_message()
                if budget_msg:
                    messages.append({"role": "system", "content": budget_msg})
            else:
                # Text response — return it
                loop_checkpoint.stop_reason = "completed"
                return message.content or ""
    except Exception as exc:
        _exit_error = exc
        logger.error("Agent loop crashed: %s", exc, exc_info=True)

    # Doc 096: Save checkpoint on loop exit (always runs — normal exit or crash)
    try:
        if not loop_checkpoint.stop_reason:
            loop_checkpoint.stop_reason = _classify_stop_reason(iteration, max_iterations, loop_checkpoint, error=_exit_error)
        loop_checkpoint.progress_summary = _summarize_progress(iteration, loop_checkpoint)
        # Merge uncommitted changes from git
        if workspace_dirs:
            git_checkpoint = build_checkpoint_from_history(messages, workspace_dirs[0])
            loop_checkpoint.uncommitted_changes = git_checkpoint.uncommitted_changes
            loop_checkpoint.capture_git_state(workspace_dirs[0])
        await save_checkpoint(_conversation_id, agent_id or "", loop_checkpoint)
        logger.info("Exit checkpoint saved: stop_reason=%s", loop_checkpoint.stop_reason)
    except Exception:
        logger.warning("Failed to save exit checkpoint", exc_info=True)

    # Re-raise if the loop crashed (checkpoint has been saved)
    if _exit_error is not None:
        raise _exit_error

    logger.warning("Agent hit max iterations (%d)", max_iterations)

    # Auto-delegate to coding agent if we made edits but ran out of time
    _has_edits = any(
        any(
            tc.get("function", {}).get("name") in ("file_edit", "file_write")
            for tc in msg.get("tool_calls", [])
        )
        for msg in messages
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    )
    _called_coding_agent = any(
        any(
            tc.get("function", {}).get("name") == "coding_agent"
            for tc in msg.get("tool_calls", [])
        )
        for msg in messages
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    )

    if _has_edits and not _called_coding_agent and "coding_agent" in all_enabled_tools:
        try:
            from backend.app.agent.pre_gather import build_handoff_context
            handoff_ctx = build_handoff_context(messages)
            _user_msg = user_message[:2000]
            _working_dir = os.path.expanduser(
                agent.get("workspace_mounts", [{}])[0].get("host_path", "/workspace")
            ) if agent.get("workspace_mounts") else os.environ.get("WORKSPACE_DIR", "/workspace")

            # Doc 096: Enrich handoff with checkpoint data
            _checkpoint_section = ""
            if loop_checkpoint.failed_approaches:
                _checkpoint_section += (
                    "\n## Approaches That Failed (don't retry)\n"
                    + "\n".join(f"- {a}" for a in loop_checkpoint.failed_approaches)
                    + "\n"
                )
            if loop_checkpoint.decisions:
                _checkpoint_section += (
                    "\n## Decisions Already Made\n"
                    + "\n".join(f"- {d}" for d in loop_checkpoint.decisions)
                    + "\n"
                )

            _handoff_task = (
                f"CONTINUE AND COMPLETE this task that ran out of iteration budget.\n\n"
                f"## Original User Request\n{_user_msg}\n\n"
                f"## Files Already Read\n{handoff_ctx['files_read']}\n\n"
                f"## Changes Already Made\n{handoff_ctx['edits_made']}\n\n"
                f"{_checkpoint_section}"
                f"## Instructions\n"
                f"Pick up where the previous agent left off. Complete the remaining work."
            )

            loop_checkpoint.stop_reason = "auto_delegated"
            if workspace_dirs:
                loop_checkpoint.capture_git_state(workspace_dirs[0])
            try:
                await save_checkpoint(_conversation_id, agent_id or "", loop_checkpoint)
            except Exception:
                logger.debug("Pre-delegation checkpoint save failed", exc_info=True)

            _ca_result = await registry.execute("coding_agent", {
                "task": _handoff_task,
                "working_directory": _working_dir,
                "agent_type": "claude",
                "timeout_minutes": 30,
            }, tool_context)

            if not _ca_result.get("error"):
                logger.info("Auto-delegated to coding_agent from loop.py fallback")
                return (
                    "I used all my iterations exploring the codebase, so I've handed off "
                    "the remaining work to a coding agent running in the background."
                )
        except Exception as e:
            logger.error("Auto-delegation failed in loop.py: %s", e)

    return "I ran out of iteration budget for this request. Please try again, or consider breaking the request into smaller parts."
