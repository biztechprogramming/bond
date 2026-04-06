"""Pre-Gather Integration — ties repo_map + plan + gather into a single call.

Called from worker.py before the main agent loop. Design Doc 038.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
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
    gather_metrics: Any = None  # Optional GatherMetrics


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

    from backend.app.agent.pre_gather import (
        plan_phase,
        workspace_plan_phase,
        deep_map_file_select,
        gather_phase,
        compress_gathered_context,
        compute_adaptive_budget,
        GATHER_TOKEN_BUDGET,
        _estimate_tokens,
    )

    # Build LLM kwargs for plan calls (same provider settings as primary model)
    plan_llm_kwargs = {}
    for k in ("api_base", "api_version", "organization", "extra_headers"):
        if k in extra_kwargs:
            plan_llm_kwargs[k] = extra_kwargs[k]

    is_single_repo = os.path.isdir(os.path.join(repo_root, ".git"))
    is_multi_repo = not is_single_repo

    # ── Detect multi-repo workspace (Design Doc 069) ──
    if is_multi_repo:
        plan = await _run_multi_repo_flow(
            user_message=user_message,
            history=history,
            repo_root=repo_root,
            model=model,
            api_key=api_key,
            plan_llm_kwargs=plan_llm_kwargs,
            interrupt_event=interrupt_event,
            langfuse_meta=langfuse_meta,
            event_queue=event_queue,
        )
    else:
        plan = await _run_single_repo_flow(
            user_message=user_message,
            history=history,
            repo_root=repo_root,
            model=model,
            api_key=api_key,
            plan_llm_kwargs=plan_llm_kwargs,
            interrupt_event=interrupt_event,
            langfuse_meta=langfuse_meta,
            event_queue=event_queue,
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
        trace_meta["plan_multi_repo"] = is_multi_repo
        langfuse_meta["trace_metadata"] = trace_meta

    # Simple tasks: no gathering needed
    if plan.get("complexity") == "simple" and not plan.get("files_to_read"):
        logger.info("Pre-gather: simple task, skipping gather phase")
        return result

    # ── Phase 2: Gather ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "gathering"}))

    gather_result = await gather_phase(
        plan=plan,
        tool_registry=tool_registry,
        tool_context=tool_context,
        repo_root=repo_root,
        cancellation_event=interrupt_event,
    )
    context_bundle, gather_metrics = gather_result
    result.gather_metrics = gather_metrics

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

    # Add gather metrics to langfuse
    if langfuse_meta and gather_metrics:
        trace_meta = langfuse_meta.get("trace_metadata", {})
        trace_meta["gather_speedup"] = gather_metrics.speedup
        trace_meta["gather_wall_ms"] = gather_metrics.wall_clock_ms
        trace_meta["gather_tasks_failed"] = gather_metrics.tasks_failed
        langfuse_meta["trace_metadata"] = trace_meta

    metrics_str = ""
    if gather_metrics:
        metrics_str = (
            f", gather_speedup={gather_metrics.speedup:.1f}x"
            f", gather_wall_ms={gather_metrics.wall_clock_ms:.0f}"
        )

    logger.info(
        "Pre-gather complete: plan=%s, context=%d tokens, budget=%s, delegate=%s, multi_repo=%s%s",
        plan.get("complexity"),
        _estimate_tokens(context_bundle) if context_bundle else 0,
        result.adaptive_budget,
        result.delegate_to_coding_agent,
        is_multi_repo,
        metrics_str,
    )

    return result


async def _run_single_repo_flow(
    user_message: str,
    history: list[dict],
    repo_root: str,
    model: str,
    api_key: str | None,
    plan_llm_kwargs: dict,
    interrupt_event: asyncio.Event | None,
    langfuse_meta: dict | None,
    event_queue: Any,
) -> dict | None:
    """Original single-repo flow: repo map → plan."""
    from backend.app.agent.pre_gather import plan_phase

    # ── Phase 0: Repo Map ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "mapping_repo"}))

    repo_map = ""
    try:
        from backend.app.agent.repomap import generate_repo_map

        repo_map = await generate_repo_map(
            repo_root=repo_root,
            token_budget=10_000,
            focus_files=_extract_focus_files(user_message),
        )
    except ImportError:
        logger.info("Pre-gather: tree-sitter not available, falling back to file tree")
        try:
            from backend.app.agent.repo_map import build_repo_map

            repo_map = await build_repo_map(repo_root)
        except Exception as e:
            logger.warning("Pre-gather: fallback repo map failed: %s", e)
    except Exception as e:
        logger.warning("Pre-gather: repo map failed: %s", e)

    if not repo_map:
        logger.info("Pre-gather: no repo map, skipping plan phase")
        return None

    # ── Phase 1: Plan ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "planning"}))

    return await plan_phase(
        user_message=user_message,
        history=history,
        repo_map=repo_map,
        model=model,
        api_key=api_key,
        interrupt_event=interrupt_event,
        langfuse_meta=langfuse_meta,
        **plan_llm_kwargs,
    )


async def _run_multi_repo_flow(
    user_message: str,
    history: list[dict],
    repo_root: str,
    model: str,
    api_key: str | None,
    plan_llm_kwargs: dict,
    interrupt_event: asyncio.Event | None,
    langfuse_meta: dict | None,
    event_queue: Any,
) -> dict | None:
    """Multi-repo flow (Design Doc 069): workspace overview → plan → deep map → file select.

    Falls back to single-repo flow if only one git repo is found.
    """
    from backend.app.agent.pre_gather import (
        plan_phase,
        workspace_plan_phase,
        deep_map_file_select,
    )
    from backend.app.agent.workspace_map import build_workspace_overview

    # ── Phase 0a: Workspace Overview ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "scanning_workspace"}))

    workspace_overview, discovered_repos = build_workspace_overview(repo_root)

    if not discovered_repos:
        logger.info("Pre-gather: no repos discovered in workspace, skipping")
        return None

    git_repos = [r for r in discovered_repos if r.is_git]

    # Single-repo shortcut: if only one git repo found, use single-repo flow
    if len(git_repos) == 1:
        logger.info("Pre-gather: single git repo found (%s), using single-repo flow", git_repos[0].name)
        return await _run_single_repo_flow(
            user_message=user_message,
            history=history,
            repo_root=git_repos[0].path,
            model=model,
            api_key=api_key,
            plan_llm_kwargs=plan_llm_kwargs,
            interrupt_event=interrupt_event,
            langfuse_meta=langfuse_meta,
            event_queue=event_queue,
        )

    if not workspace_overview:
        logger.info("Pre-gather: empty workspace overview, skipping")
        return None

    # ── Phase 1: Workspace Plan ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "planning"}))

    plan = await workspace_plan_phase(
        user_message=user_message,
        history=history,
        workspace_overview=workspace_overview,
        model=model,
        api_key=api_key,
        interrupt_event=interrupt_event,
        langfuse_meta=langfuse_meta,
        **plan_llm_kwargs,
    )

    if plan is None:
        return None

    repos_to_map = plan.get("repos_to_map", [])
    if not repos_to_map:
        logger.info("Pre-gather: no repos to map, using plan as-is")
        return plan

    # ── Phase 1b: Deep Map ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "mapping_repo"}))

    # Build a lookup of discovered repos by name
    repo_lookup = {r.name: r for r in discovered_repos if r.is_git}

    # Token budget allocation: Option C from design doc
    # First repo gets 8K, additional repos get 3K each
    deep_map_sections: list[str] = []

    async def _generate_deep_map(repo_name: str, budget: int) -> str | None:
        repo = repo_lookup.get(repo_name)
        if not repo:
            logger.warning("Pre-gather: repo '%s' not found in workspace", repo_name)
            return None
        try:
            from backend.app.agent.repomap import generate_repo_map

            result = await generate_repo_map(
                repo_root=repo.path,
                token_budget=budget,
                focus_files=_extract_focus_files(user_message),
            )
            if result:
                return f"=== {repo_name}/ (detailed) ===\n{result}"
        except ImportError:
            try:
                from backend.app.agent.repo_map import build_repo_map

                result = await build_repo_map(repo.path)
                if result:
                    return f"=== {repo_name}/ (file tree) ===\n{result}"
            except Exception as e:
                logger.warning("Pre-gather: fallback repo map for %s failed: %s", repo_name, e)
        except Exception as e:
            logger.warning("Pre-gather: deep map for %s failed: %s", repo_name, e)
        return None

    # Generate deep maps in parallel
    tasks = []
    for i, repo_name in enumerate(repos_to_map):
        budget = 8_000 if i == 0 else 3_000
        tasks.append(_generate_deep_map(repo_name, budget))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, str):
            deep_map_sections.append(r)
        elif isinstance(r, Exception):
            logger.warning("Pre-gather: deep map task failed: %s", r)

    if not deep_map_sections:
        logger.info("Pre-gather: no deep maps generated, using plan as-is")
        return plan

    deep_map = "\n\n".join(deep_map_sections)

    # ── Phase 1b: File Selection ──
    if event_queue is not None:
        await event_queue.put(_sse_event("status", {"state": "selecting_files"}))

    refined_files, refined_greps = await deep_map_file_select(
        approach=plan.get("approach", ""),
        deep_map=deep_map,
        initial_files=plan.get("files_to_read", []),
        model=model,
        api_key=api_key,
        langfuse_meta=langfuse_meta,
        **plan_llm_kwargs,
    )

    # Update plan with refined selections
    plan["files_to_read"] = refined_files
    if refined_greps:
        plan["grep_patterns"] = refined_greps

    logger.info(
        "Pre-gather multi-repo: %d repos mapped, %d files selected",
        len(deep_map_sections),
        len(refined_files),
    )

    return plan


def _extract_focus_files(user_message: str) -> list[str] | None:
    """Extract file paths mentioned in the user message.

    Looks for patterns like backend/app/foo.py, src/index.ts, etc.
    Returns None if no paths found.
    """
    # Match paths with at least one directory separator and a file extension
    pattern = r'(?:^|\s|[`"\'])([a-zA-Z0-9_./-]+/[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]+)'
    matches = re.findall(pattern, user_message)
    if not matches:
        return None
    # Deduplicate while preserving order
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result or None


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    import json
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
