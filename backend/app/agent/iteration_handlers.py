"""Per-iteration handlers for the agent loop.

Extracted from the for-loop body in worker._run_agent_loop.
These functions handle LLM response processing, budget management,
tool execution, loop detection, and lifecycle injection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.worker")


def handle_truncation(
    choice: Any,
    llm_message: Any,
    loop_state: Any,
    messages: list[dict],
    outcome: Any,
    cost: Any,
    langfuse_meta: dict,
) -> str | None:
    """Handle finish_reason=length — output was truncated.

    Returns:
        "continue" to retry iteration, "abort" to return error, or None if not truncated.
    """
    if choice.finish_reason != "length":
        return None

    loop_state.continuation_attempts += 1
    outcome.had_continuation = True
    partial_content = llm_message.content or ""

    if loop_state.continuation_attempts > loop_state.MAX_CONTINUATIONS:
        logger.error(
            "Aborting after %d continuation attempts — response keeps exceeding token limit",
            loop_state.continuation_attempts,
        )
        return "abort"

    if loop_state.current_tier < len(loop_state.TOKEN_TIERS) - 1:
        loop_state.current_tier += 1
        logger.info(
            "Truncated response — escalating max_tokens to %d (tier %d), attempting continuation %d/%d",
            loop_state.TOKEN_TIERS[loop_state.current_tier], loop_state.current_tier,
            loop_state.continuation_attempts, loop_state.MAX_CONTINUATIONS,
        )

    if partial_content:
        messages.append({"role": "assistant", "content": partial_content})
        messages.append({
            "role": "user",
            "content": "Your response was cut off due to the output length limit. Please continue exactly where you left off.",
        })
    else:
        logger.warning("Truncated with no content — retrying at higher tier")

    return "continue"


def handle_adaptive_budget(
    iteration: int,
    llm_message: Any,
    loop_state: Any,
    cost: Any,
):
    """Phase 2A: Set adaptive iteration budget on first iteration."""
    if iteration != 0 or loop_state.adaptive_budget_set:
        return

    loop_state.adaptive_budget_set = True
    max_iterations = loop_state.max_iterations

    if not llm_message.tool_calls:
        loop_state.adaptive_budget = min(max_iterations, 2)
        logger.info("Phase 2A: simple Q&A, budget=%d", loop_state.adaptive_budget)
    else:
        first_tool_names = [tc.function.name for tc in llm_message.tool_calls]
        has_edits = any(t in ("file_edit", "file_write") for t in first_tool_names)
        has_plan = any(t == "work_plan" for t in first_tool_names)
        has_reads = any(t in ("file_read", "shell_grep", "search_memory") for t in first_tool_names)
        if has_plan and len(first_tool_names) >= 5:
            loop_state.adaptive_budget = min(max_iterations, 25)
            logger.info("Phase 2A: complex multi-file, budget=%d", loop_state.adaptive_budget)
        elif has_edits:
            loop_state.adaptive_budget = min(max_iterations, 20)
            logger.info("Phase 2A: implementation, budget=%d", loop_state.adaptive_budget)
        elif has_reads and not has_edits:
            loop_state.adaptive_budget = min(max_iterations, 10)
            logger.info("Phase 2A: analysis, budget=%d", loop_state.adaptive_budget)
        else:
            loop_state.adaptive_budget = min(max_iterations, 8)
            logger.info("Phase 2A: file lookup, budget=%d", loop_state.adaptive_budget)

    cost.tracking["iteration_budget"] = loop_state.adaptive_budget


def handle_budget_escalation(
    iteration: int,
    llm_message: Any,
    loop_state: Any,
    messages: list[dict],
    tool_defs: list[dict],
) -> bool:
    """Handle approaching-budget logic. Returns True if loop should break."""
    from backend.app.agent.tool_selection import compact_tool_schema

    NON_CODING_WARN_THRESHOLD = 15
    if loop_state.is_coding_task:
        effective_threshold = int(loop_state.adaptive_budget * 0.8)
        effective_budget = loop_state.adaptive_budget
    else:
        effective_threshold = NON_CODING_WARN_THRESHOLD
        effective_budget = loop_state.max_iterations

    if not (iteration >= effective_threshold and iteration > 2
            and not any(tc.function.name == "coding_agent" for tc in (llm_message.tool_calls or []))):
        return False

    remaining = effective_budget - iteration - 1
    overbudget_by = iteration - effective_threshold

    if loop_state.is_coding_task:
        try:
            from backend.app.agent.pre_gather import build_handoff_context
            handoff_ctx = build_handoff_context(messages)
            handoff_msg = (
                f"SYSTEM: You are at iteration {iteration + 1}/{effective_budget}. "
                f"You have {remaining} iterations left. Hand off your remaining work "
                f"to the coding_agent tool NOW.\n\n"
                f"**Files you've read:**\n{handoff_ctx['files_read']}\n\n"
                f"**Changes you've made:**\n{handoff_ctx['edits_made']}\n\n"
                f"Write a coding_agent task prompt that covers the remaining work. "
                f"Include the file paths you've already identified. "
                f"The coding agent has access to the same repo — reference files by path, "
                f"don't paste their contents.\n\n"
                f"Spawn coding_agent in your next response. Do not read more files."
            )
        except Exception:
            handoff_msg = (
                f"SYSTEM: You are at iteration {iteration + 1}/{effective_budget}. "
                f"You have {remaining} iterations left. Hand off your remaining work "
                f"to the coding_agent tool now. Summarize what's done and what's left."
            )
        messages.append({"role": "user", "content": handoff_msg})
        logger.info("Budget escalation: iteration %d/%d, injecting coding_agent handoff", iteration + 1, effective_budget)

        if overbudget_by >= 4:
            from backend.app.agent.tools import TOOL_MAP as _FULL_TOOL_MAP
            forced_tools = []
            for tname in ("coding_agent", "respond", "say"):
                if tname in _FULL_TOOL_MAP:
                    forced_tools.append(compact_tool_schema(_FULL_TOOL_MAP[tname]))
            if forced_tools:
                tool_defs.clear()
                tool_defs.extend(forced_tools)
                logger.warning(
                    "Budget hard restriction: iteration %d, forced tool set to coding_agent+respond+say",
                    iteration + 1,
                )
    else:
        wrapup_msg = (
            f"SYSTEM: You are at iteration {iteration + 1}/{effective_budget} "
            f"with {remaining} iteration{'s' if remaining != 1 else ''} remaining. "
            f"Finish up your current approach and respond to the user."
        )
        messages.append({"role": "user", "content": wrapup_msg})
        logger.info("Budget wrap-up: iteration %d/%d, %d remaining (non-coding task)",
                    iteration + 1, effective_budget, remaining)

        if overbudget_by >= 4:
            from backend.app.agent.tools import TOOL_MAP as _FULL_TOOL_MAP
            forced_tools = []
            for tname in ("respond", "say"):
                if tname in _FULL_TOOL_MAP:
                    forced_tools.append(compact_tool_schema(_FULL_TOOL_MAP[tname]))
            if forced_tools:
                tool_defs.clear()
                tool_defs.extend(forced_tools)
                logger.warning(
                    "Budget hard restriction: iteration %d, forced tool set to respond+say (non-coding)",
                    iteration + 1,
                )

    if overbudget_by >= 8:
        logger.warning(
            "Budget hard cap: stopping loop at iteration %d (effective budget was %d)",
            iteration + 1, effective_budget,
        )
        return True

    return False


def handle_early_termination(
    iteration: int,
    loop_state: Any,
    messages: list[dict],
    tool_defs: list[dict],
):
    """Phase 2B: Early termination nudges for read-only tasks."""
    from backend.app.agent.tool_selection import compact_tool_schema

    if loop_state.has_made_consequential_call:
        return

    if iteration == 10:
        messages.append({
            "role": "user",
            "content": (
                "SYSTEM: You've gathered substantial context over 10 iterations without making "
                "any changes. Synthesize your findings and respond to the user now. "
                "Do not read more files."
            ),
        })
    elif iteration >= 15:
        from backend.app.agent.tools import TOOL_MAP as _FULL_TOOL_MAP
        forced = [compact_tool_schema(_FULL_TOOL_MAP[t]) for t in ("respond", "say") if t in _FULL_TOOL_MAP]
        if forced:
            tool_defs.clear()
            tool_defs.extend(forced)
        logger.info("Phase 2B: forced respond+say tool set at iteration %d", iteration)


def handle_batching_nudge(
    llm_message: Any,
    loop_state: Any,
    messages: list[dict],
):
    """Phase 1B: Batching nudge for single info-gathering calls."""
    if not llm_message.tool_calls:
        return

    iter_tool_names = [tc.function.name for tc in llm_message.tool_calls]

    is_single_info = (
        len(llm_message.tool_calls) == 1
        and iter_tool_names[0] in loop_state.INFO_GATHERING_TOOLS
        and not (llm_message.content and llm_message.content.strip())
    )
    if is_single_info:
        loop_state.consecutive_single_info_iterations += 1
        if loop_state.consecutive_single_info_iterations >= 3:
            messages.append({
                "role": "user",
                "content": (
                    "SYSTEM: You have made 3+ consecutive single-tool info-gathering calls. "
                    "This is inefficient. Batch ALL remaining information needs into a SINGLE "
                    "response with multiple tool calls. The system executes them in parallel."
                ),
            })
            logger.info("Phase 1B: strong batching nudge after %d consecutive single-tool iterations",
                        loop_state.consecutive_single_info_iterations)
        else:
            messages.append({
                "role": "user",
                "content": (
                    "SYSTEM: You made a single info-gathering call. If you need more information, "
                    "batch multiple tool calls in your next response."
                ),
            })
    else:
        loop_state.consecutive_single_info_iterations = 0


def detect_loop(
    tool_name: str,
    tool_args: dict,
    loop_state: Any,
) -> tuple[bool, str]:
    """Detect consecutive and cyclical loop patterns.

    Returns (is_loop_detected, loop_message).
    """
    args_sig = hashlib.md5(f"{tool_name}:{json.dumps(tool_args)[:200]}".encode()).hexdigest()[:8]
    loop_state.recent_tool_calls.append((tool_name, args_sig))

    # 1. Consecutive repetition
    if len(loop_state.recent_tool_calls) >= loop_state.REPETITION_THRESHOLD:
        last_n = loop_state.recent_tool_calls[-loop_state.REPETITION_THRESHOLD:]
        if all(tc == last_n[0] for tc in last_n):
            logger.warning(
                "Consecutive repetition detected: %s called %d times with same args",
                tool_name, loop_state.REPETITION_THRESHOLD,
            )
            return True, (
                f"SYSTEM: You have called '{tool_name}' with the same arguments "
                f"{loop_state.REPETITION_THRESHOLD} times in a row. You appear to be in a loop."
            )

    # 2. Cyclical repetition
    if len(loop_state.recent_tool_calls) >= loop_state.CYCLE_MIN_PERIOD * loop_state.CYCLE_REPEATS:
        for period in range(loop_state.CYCLE_MIN_PERIOD, loop_state.CYCLE_MAX_PERIOD + 1):
            needed = period * loop_state.CYCLE_REPEATS
            if len(loop_state.recent_tool_calls) < needed:
                continue
            tail = loop_state.recent_tool_calls[-needed:]
            cycle = tail[:period]
            is_cycle = all(
                tail[i] == cycle[i % period]
                for i in range(needed)
            )
            if is_cycle:
                cycle_tools = [c[0] for c in cycle]
                logger.warning(
                    "Cyclical loop detected: pattern %s repeated %d times (period=%d)",
                    cycle_tools, loop_state.CYCLE_REPEATS, period,
                )
                return True, (
                    f"SYSTEM: You are in a cyclical loop — repeating the pattern "
                    f"{' → '.join(cycle_tools)} ({loop_state.CYCLE_REPEATS} times). "
                    f"These actions have already been completed. Stop repeating them."
                )

    return False, ""


async def execute_tool_call(
    tool_call: Any,
    tool_name: str,
    tool_args: dict,
    agent_tools: list[str],
    registry: Any,
    tool_context: dict,
    parallel_precomputed: dict,
    interrupt_event: Any,
    state: Any,
    conversation_id: str,
    event_queue: Any = None,
) -> tuple[dict, float]:
    """Execute a single tool call, handling interrupts and parallel precomputation.

    Returns (result, duration).
    """
    if tool_name not in agent_tools:
        return {"error": f"Tool '{tool_name}' is not enabled."}, 0.0

    # Use precomputed parallel result if available
    if tool_call.id in parallel_precomputed:
        result, duration = parallel_precomputed[tool_call.id]
        logger.info("Using precomputed parallel result for %s (%.2fs)", tool_name, duration)
        return result, duration

    start_ts = time.time()
    tool_task = asyncio.create_task(
        registry.execute(tool_name, tool_args, tool_context)
    )
    interrupt_task = asyncio.create_task(
        interrupt_event.wait()
    )
    done, pending = await asyncio.wait(
        {tool_task, interrupt_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    if tool_task in done:
        result = tool_task.result()
    else:
        logger.info("Tool %s interrupted by user", tool_name)
        from backend.app.agent.tools.coding_agent import kill_coding_agent
        await kill_coding_agent(state.agent_id)
        result = {"error": "Tool execution interrupted by user"}
        interrupt_event.clear()
        if state.pending_messages:
            # Caller should handle injecting these
            pass

    duration = time.time() - start_ts
    return result, duration


def handle_lifecycle_injection(
    llm_message: Any,
    loop_state: Any,
    messages: list[dict],
    has_active_plan: bool,
    langfuse_meta: dict,
    agent_id: str,
):
    """Doc 024: Between-turn lifecycle phase detection and fragment injection."""
    from backend.app.agent.lifecycle import (
        LifecycleState,
        Phase,
        detect_phase,
        format_lifecycle_injection as format_lc_injection,
        load_lifecycle_fragments,
    )

    _prompts_dir = Path("/bond/prompts")
    if not _prompts_dir.exists():
        _prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

    loop_state.lifecycle_turn_number += 1
    tool_call_strings = [
        f"{tc.function.name}:{tc.function.arguments}"
        for tc in llm_message.tool_calls
    ]
    lc_state = LifecycleState(
        turn_number=loop_state.lifecycle_turn_number,
        last_tool_calls=tool_call_strings,
        has_work_plan=has_active_plan,
        work_plan_status="in_progress" if has_active_plan else None,
    )
    new_phase = detect_phase(lc_state)

    # Import Phase for comparison — it's already imported above
    lifecycle_phase = getattr(loop_state, '_lifecycle_phase', Phase.IDLE)

    if new_phase != lifecycle_phase:
        loop_state._lifecycle_phase = new_phase
        logger.info("Lifecycle phase changed to: %s", new_phase.name)

        # Remove previous lifecycle injection from system prompt
        sys_content = messages[0].get("content", "")
        if isinstance(sys_content, list):
            for block in sys_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block["text"]
                    marker = "\n\n## Current Phase: "
                    if marker in text:
                        block["text"] = text[:text.index(marker)]
                    break
        elif isinstance(sys_content, str):
            marker = "\n\n## Current Phase: "
            if marker in sys_content:
                sys_content = sys_content[:sys_content.index(marker)]
                messages[0]["content"] = sys_content

        # Inject new lifecycle fragments if not idle
        if new_phase != Phase.IDLE:
            lc_frags = load_lifecycle_fragments(new_phase, _prompts_dir)
            lc_injection = format_lc_injection(new_phase, lc_frags)
            if lc_injection:
                if isinstance(messages[0].get("content"), list):
                    for block in messages[0]["content"]:
                        if isinstance(block, dict) and block.get("type") == "text":
                            block["text"] += lc_injection
                            break
                else:
                    messages[0]["content"] += lc_injection
                loop_state.lifecycle_injected = True
                logger.info(
                    "Lifecycle injection: phase=%s fragments=%s",
                    new_phase.name,
                    [f.path for f in lc_frags],
                )

                # Update Langfuse metadata
                if langfuse_meta:
                    lc_meta = [
                        {
                            "source": "lifecycle-tier2",
                            "path": f.path,
                            "name": Path(f.path).stem,
                            "phase": new_phase.name,
                            "tokenEstimate": f.token_estimate,
                        }
                        for f in lc_frags
                    ]
                    _audit_fragments = langfuse_meta.get("fragments_injected", [])
                    _audit_fragments = [
                        f for f in _audit_fragments
                        if f.get("source") != "lifecycle-tier2"
                    ] + lc_meta
                    _fragment_names = [f.get("name", "") for f in _audit_fragments]
                    _fragment_total_tokens = sum(
                        f.get("tokens", f.get("tokenEstimate", 0))
                        for f in _audit_fragments
                    )
                    langfuse_meta.update({
                        "fragments_injected": _audit_fragments,
                        "fragment_count": len(_audit_fragments),
                        "fragment_names": _fragment_names,
                        "fragment_total_tokens": _fragment_total_tokens,
                        "tags": [
                            f"agent:{agent_id}",
                            f"fragments:{len(_audit_fragments)}",
                            f"phase:{new_phase.name}",
                        ] + [f"prompt:{n}" for n in _fragment_names],
                    })
                    langfuse_meta.get("trace_metadata", {}).update({
                        "fragment_count": len(_audit_fragments),
                        "fragment_names": _fragment_names,
                        "fragment_total_tokens": _fragment_total_tokens,
                        "lifecycle_phase": new_phase.name,
                    })
            else:
                loop_state.lifecycle_injected = False
        else:
            loop_state.lifecycle_injected = False
