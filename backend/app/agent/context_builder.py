"""Context building for the agent loop.

Builds the full system prompt, handles plan-aware continuation,
memory injection, prompt hierarchy, sliding window, compression, etc.

Extracted from worker._run_agent_loop (lines ~930-1230).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.worker")


@dataclass
class ContextBundle:
    """Result of building agent context."""
    full_system_prompt: str = ""
    windowed_history: list[dict] = field(default_factory=list)
    compressed_history: list[dict] = field(default_factory=list)
    compression_stats: dict[str, Any] = field(default_factory=dict)
    tier1_meta: list[dict] = field(default_factory=list)
    tier3_meta: list[dict] = field(default_factory=list)
    fragment_manifest: list[dict] = field(default_factory=list)
    category_manifest: str | None = None
    is_continuation: bool = False
    has_active_plan: bool = False
    active_plan_id: str | None = None
    lessons_content: str | None = None


async def build_agent_context(
    user_message: str,
    history: list[dict],
    conversation_id: str,
    config: dict[str, Any],
    agent_db: Any,
    agent_id: str,
    persistence: Any,
    plan_id: str = "",
    event_queue: Any = None,
    sse_event_fn: Any = None,
    utility_kwargs: dict | None = None,
    discover_workspace_fn: Any = None,
    mcp_proxy: Any = None,
) -> ContextBundle:
    """Build the full context for the agent loop.

    This assembles: plan continuation, memory search, prompt hierarchy,
    sliding window, progressive decay, and compression.
    """
    import os
    from backend.app.agent.context_compression import compress_file_results
    from backend.app.agent.context_decay import apply_progressive_decay
    from backend.app.agent.context_pipeline import (
        COMPRESSION_THRESHOLD,
        VERBATIM_MESSAGE_COUNT,
        _estimate_tokens,
        _compress_history,
        _log_compression_stats,
        _apply_sliding_window,
    )

    system_prompt = config["system_prompt"] or "You are a helpful AI assistant."
    ctx = ContextBundle()

    # --- Plan-Aware Continuation (Design Doc 034) ---
    try:
        from backend.app.agent.tools.work_plan import load_active_plan, format_plan_context, format_recovery_context
        from backend.app.agent.continuation import (
            classify_intent,
            ContinuationIntent,
            resolve_plan_position,
            build_continuation_context,
            build_checkpoint_from_history,
            format_checkpoint_context,
        )

        active_plan = await load_active_plan(agent_db, agent_id, conversation_id=conversation_id, plan_id=plan_id)
        if active_plan:
            ctx.has_active_plan = True
            ctx.active_plan_id = active_plan["id"]

        intent = classify_intent(user_message, ctx.has_active_plan)
        logger.info("Continuation intent: %s (has_plan=%s)", intent.value, ctx.has_active_plan)

        if intent in (ContinuationIntent.CONTINUE, ContinuationIntent.ADJUST) and active_plan:
            ctx.is_continuation = True
            workspace_dir = os.environ.get("WORKSPACE_DIR", "/workspace")
            position = await resolve_plan_position(active_plan, workspace_dir)

            adjustment = user_message if intent == ContinuationIntent.ADJUST else None
            continuation_ctx = build_continuation_context(position, active_plan, adjustment)
            plan_id_ctx = format_plan_context(active_plan)

            history = [{"role": "user", "content": continuation_ctx + "\n\n" + plan_id_ctx}]

            logger.info(
                "Continuation: plan %s, %d/%d complete, next=%s, history replaced (%d tokens)",
                ctx.active_plan_id,
                len(position.completed_items),
                position.total_items,
                position.next_item.get("title", "none") if position.next_item else "none",
                len(continuation_ctx) // 4,
            )

            if event_queue is not None and sse_event_fn:
                await event_queue.put(sse_event_fn("status", {
                    "state": "continuing",
                    "plan_id": ctx.active_plan_id,
                    "progress": f"{len(position.completed_items)}/{position.total_items}",
                    "next_item": position.next_item.get("title", "") if position.next_item else "",
                }))

        elif intent == ContinuationIntent.CONTINUE and not active_plan and history:
            ctx.is_continuation = True
            checkpoint = build_checkpoint_from_history(history)
            checkpoint_ctx = format_checkpoint_context(checkpoint)
            history = [{"role": "user", "content": checkpoint_ctx}]
            logger.info("Continuation (no plan): checkpoint built, history replaced")

        elif active_plan:
            in_progress = [i for i in active_plan.get("items", []) if i["status"] == "in_progress"]
            if in_progress:
                plan_ctx = format_recovery_context(active_plan) + "\n\n" + format_plan_context(active_plan)
            else:
                plan_ctx = format_plan_context(active_plan)
            history = [{"role": "user", "content": plan_ctx}] + history
            logger.info("Injected active plan context for plan %s (%d items)", ctx.active_plan_id, len(active_plan.get("items", [])))

    except Exception as e:
        logger.debug("Plan-aware continuation skipped: %s", e)

    # --- Memory Search ---
    recent_memories: list[dict] = []
    try:
        from backend.app.agent.tools.native import handle_search_memory
        res = await handle_search_memory(
            {"query": user_message, "limit": 3},
            {"agent_db": agent_db}
        )
        recent_memories = res.get("results", [])
    except Exception:
        pass

    prompt_parts = [system_prompt]
    if recent_memories:
        mem_text = "\n".join([f"- {m['content']}" for m in recent_memories])
        prompt_parts.append(f"## Relevant Memories\n{mem_text}")

    full_system_prompt = "\n\n".join(prompt_parts)

    # --- Prompt Hierarchy: Tier 1 + Category Manifest ---
    from backend.app.agent.manifest import load_manifest, get_tier1_content, get_tier1_meta
    from backend.app.agent.tools.dynamic_loader import generate_manifest as _generate_category_manifest

    _prompts_dir = Path("/bond/prompts")
    if not _prompts_dir.exists():
        _prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

    _fragment_manifest = load_manifest(_prompts_dir)
    ctx.fragment_manifest = _fragment_manifest

    _tier1_content = get_tier1_content(_fragment_manifest)
    ctx.tier1_meta = get_tier1_meta(_fragment_manifest)
    if _tier1_content:
        full_system_prompt = full_system_prompt + "\n\n" + _tier1_content

    # Tier 3: semantic router
    try:
        from backend.app.agent.fragment_router import (
            build_route_layer,
            get_tier3_meta,
            select_fragments_by_similarity,
        )
        build_route_layer(_prompts_dir)
        tier3_picks = await select_fragments_by_similarity(user_message, top_k=5)
        if tier3_picks:
            tier3_content = "\n\n---\n\n".join(f.content for f in tier3_picks)
            full_system_prompt = full_system_prompt + "\n\n" + tier3_content
            ctx.tier3_meta = get_tier3_meta(tier3_picks)
    except ImportError as e:
        logger.debug("Tier 3 semantic router unavailable (missing dep: %s) — skipping", e.name)

    # Category manifest
    import backend.app.worker as _worker_module
    _category_manifest = _worker_module._prompt_manifest_cache
    if _category_manifest is None:
        _category_manifest = _generate_category_manifest(_prompts_dir)
        _worker_module._prompt_manifest_cache = _category_manifest
    ctx.category_manifest = _category_manifest
    if _category_manifest:
        full_system_prompt = full_system_prompt + "\n\n" + _category_manifest

    # Auto-skills
    try:
        from backend.app.agent.tools.skills import _get_router, init_router
        await init_router(persistence=persistence)
        skill_router = _get_router()
        skills_prompt = await skill_router.get_relevant_skills_prompt(
            user_message, session_id=conversation_id,
        )
        if skills_prompt:
            full_system_prompt += (
                "\n\n## Skills\n"
                "Before answering, scan these matched skills. If one clearly applies, "
                "use the `skills` tool with action='read' and the skill name to load "
                "its full instructions, then follow them.\n"
                + skills_prompt
            )
    except Exception:
        logger.debug("Skills injection skipped", exc_info=True)

    # Workspace context
    if discover_workspace_fn:
        workspace_ctx = discover_workspace_fn()
        if workspace_ctx:
            full_system_prompt = full_system_prompt + "\n\n" + workspace_ctx

    # Learned lessons (Doc 049)
    try:
        from backend.app.agent.critic import load_lessons
        _lessons_content = load_lessons()
        if _lessons_content:
            full_system_prompt += _lessons_content
            logger.debug("Injected learned lessons (%d chars)", len(_lessons_content))
        ctx.lessons_content = _lessons_content
    except Exception:
        logger.debug("Lessons injection skipped", exc_info=True)

    # MCP integrations summary (Design Doc 054: proxy-aware)
    try:
        # Check for proxy client first (worker context), then host-side manager
        _mcp_proxy = mcp_proxy
        if _mcp_proxy and _mcp_proxy._tool_cache:
            _mcp_server_names = sorted(set(t.get("server", "") for t in _mcp_proxy._tool_cache))
            _mcp_summary = (
                "## MCP Integrations\n"
                "Connected external services (via MCP proxy): "
                + ", ".join(_mcp_server_names)
                + "\nTools from these servers are prefixed `mcp_<server>_`. "
                "If asked about a service you don't have an MCP connection for, "
                "say so directly — don't search the filesystem for it."
            )
            full_system_prompt += "\n\n" + _mcp_summary
        else:
            # Host-side: check MCPManager connection pools
            from backend.app.mcp import mcp_manager
            if mcp_manager.connection_pools:
                from backend.app.mcp.manager import parse_connection_key
                _pool_servers = sorted(set(
                    parse_connection_key(k)[0] for k in mcp_manager.connection_pools
                ))
                _healthy = [s for s in _pool_servers if any(
                    p.has_healthy_connection for k, p in mcp_manager.connection_pools.items()
                    if parse_connection_key(k)[0] == s
                )]
                _mcp_summary = (
                    "## MCP Integrations\n"
                    "Connected external services (via MCP servers): "
                    + ", ".join(_healthy)
                )
                _unhealthy = [s for s in _pool_servers if s not in _healthy]
                if _unhealthy:
                    _mcp_summary += "\nDisconnected: " + ", ".join(_unhealthy)
                _mcp_summary += (
                    "\nTools from these servers are prefixed `mcp_<server>_`. "
                    "If asked about a service you don't have an MCP connection for, "
                    "say so directly — don't search the filesystem for it."
                )
                full_system_prompt += "\n\n" + _mcp_summary
            else:
                full_system_prompt += (
                    "\n\n## MCP Integrations\n"
                    "No MCP servers are connected. If asked about external service "
                    "integrations (e.g. time tracking, CRM), say you don't currently "
                    "have access rather than searching the filesystem."
                )
    except Exception:
        pass

    # --- Sliding Window ---
    windowed_history = history
    if history:
        windowed_history = await _apply_sliding_window(
            history, conversation_id, config, utility_kwargs or {},
            agent_db=agent_db,
        )

    # --- Iteration-Aware File Result Compression (Design Doc 098, Phase 6) ---
    if windowed_history:
        # Estimate current iteration from message count (tool-call pairs)
        _tool_count = sum(1 for m in windowed_history if m.get("role") == "tool")
        windowed_history = compress_file_results(windowed_history, _tool_count)

    # --- Progressive Decay ---
    if windowed_history:
        total_tokens = sum(_estimate_tokens(m.get("content", "")) for m in windowed_history)
        if total_tokens >= COMPRESSION_THRESHOLD and len(windowed_history) > VERBATIM_MESSAGE_COUNT:
            head = windowed_history[:-VERBATIM_MESSAGE_COUNT]
            tail = windowed_history[-VERBATIM_MESSAGE_COUNT:]
            tail = apply_progressive_decay(tail)
            windowed_history = head + tail
        else:
            windowed_history = apply_progressive_decay(windowed_history)

    ctx.windowed_history = windowed_history

    # --- Compression ---
    compressed_history = windowed_history
    compression_stats = {"original_tokens": 0, "compressed_tokens": 0}
    if windowed_history:
        compressed_history, compression_stats = await _compress_history(
            windowed_history, conversation_id, config, utility_kwargs or {},
            agent_db=agent_db,
        )

    ctx.compressed_history = compressed_history
    ctx.compression_stats = compression_stats

    # Emit compression stats
    if event_queue is not None and sse_event_fn and compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD:
        await event_queue.put(sse_event_fn("status", {
            "state": "context_compressed",
            "original_tokens": compression_stats["original_tokens"],
            "compressed_tokens": compression_stats["compressed_tokens"],
            "tools_pruned": compression_stats.get("tools_pruned", 0),
        }))

    # Log compression audit trail
    await _log_compression_stats(
        conversation_id, 0, compression_stats, {"selected": len(ctx.tier1_meta), "total": len(_fragment_manifest)},
        config.get("utility_model", "claude-sonnet-4-6"),
        agent_db=agent_db,
    )

    ctx.full_system_prompt = full_system_prompt
    return ctx
