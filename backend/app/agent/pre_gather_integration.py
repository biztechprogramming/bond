"""Pre-Gather Integration — ties repo_map + plan + gather into a single call.

Called from worker.py before the main agent loop. Design Doc 038.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("bond.agent.pre_gather")


@dataclass
class PreGatherResult:
    """Result of the pre-gather phase."""

    plan: dict | None = None
    context_bundle: str = ""
    adaptive_budget: int | None = None
    delegate_to_coding_agent: bool = False


async def run_pre_gather(
    user_message: str,
    history: list[dict],
    conversation_id: str,
    model: str,
    api_key: str | None,
    extra_kwargs: dict[str, Any],
    utility_model: str,
    utility_kwargs: dict[str, Any],
    tool_registry: Any,
    tool_context: dict[str, Any],
    repo_root: str,
    *,
    max_iterations: int = 25,
    event_queue: Any = None,
    langfuse_meta: dict | None = None,
    interrupt_event: asyncio.Event | None = None,
    is_continuation: bool = False,
) -> PreGatherResult:
    """Run the full pre-gather flow: Repo Map → Plan → Gather.

    Returns a PreGatherResult. On any failure, returns a result with
    empty context_bundle so the caller falls through to normal behavior.
    """
    result = PreGatherResult()

    # Skip for continuations — context is already loaded
    if is_continuation:
        logger.info("Pre-gather: skipping for continuation")
        return result

    # ── Phase 0: Repo Map ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "mapping_repo"}))

    from backend.app.agent.repo_map import build_repo_map

    repo_map = ""
    if os.path.isdir(os.path.join(repo_root, ".git")):
        try:
            repo_map = await build_repo_map(repo_root)
        except Exception as e:
            logger.warning("Pre-gather: repo map failed: %s", e)

    if not repo_map:
        logger.info("Pre-gather: no repo map (not a git repo?), skipping plan phase")
        return result

    # ── Phase 1: Plan ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "planning"}))

    from backend.app.agent.pre_gather import (
        plan_phase,
        gather_phase,
        compress_gathered_context,
        compute_adaptive_budget,
        GATHER_TOKEN_BUDGET,
        _estimate_tokens,
    )

    # Build LLM kwargs for the plan call (same provider settings as primary model)
    plan_llm_kwargs = {}
    for k in ("api_base", "api_version", "organization"):
        if k in extra_kwargs:
            plan_llm_kwargs[k] = extra_kwargs[k]

    plan = await plan_phase(
        user_message=user_message,
        history=history,
        repo_map=repo_map,
        model=model,
        api_key=api_key,
        interrupt_event=interrupt_event,
        langfuse_meta=langfuse_meta,
        **plan_llm_kwargs,
    )

    if plan is None:
        logger.info("Pre-gather: no plan produced, falling through")
        return result

    result.plan = plan
    result.delegate_to_coding_agent = plan.get("delegate_to_coding_agent", False)

    # Compute adaptive budget from plan
    result.adaptive_budget = compute_adaptive_budget(plan, max_iterations)

    # Update langfuse metadata
    if langfuse_meta:
        trace_meta = langfuse_meta.get("trace_metadata", {})
        trace_meta["plan_complexity"] = plan.get("complexity")
        trace_meta["plan_files_requested"] = len(plan.get("files_to_read", []))
        trace_meta["plan_delegate"] = plan.get("delegate_to_coding_agent", False)
        langfuse_meta["trace_metadata"] = trace_meta

    # Simple tasks: no gathering needed
    if plan.get("complexity") == "simple" and not plan.get("files_to_read"):
        logger.info("Pre-gather: simple task, skipping gather phase")
        return result

    # ── Phase 2: Gather ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "gathering"}))

    context_bundle = await gather_phase(
        plan=plan,
        tool_registry=tool_registry,
        tool_context=tool_context,
        repo_root=repo_root,
    )

    if context_bundle:
        # Optional compression if over budget
        if _estimate_tokens(context_bundle) > GATHER_TOKEN_BUDGET:
            context_bundle = await compress_gathered_context(
                context_bundle,
                plan.get("approach", ""),
                utility_model,
                utility_kwargs,
            )

        result.context_bundle = context_bundle

        # Update langfuse
        if langfuse_meta:
            trace_meta = langfuse_meta.get("trace_metadata", {})
            trace_meta["gather_tokens"] = _estimate_tokens(context_bundle)
            langfuse_meta["trace_metadata"] = trace_meta

    logger.info(
        "Pre-gather complete: plan=%s, context=%d tokens, budget=%s, delegate=%s",
        plan.get("complexity"),
        _estimate_tokens(context_bundle) if context_bundle else 0,
        result.adaptive_budget,
        result.delegate_to_coding_agent,
    )

    return result


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    import json
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
