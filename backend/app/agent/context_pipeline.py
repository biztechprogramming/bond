"""Context distillation pipeline — history compression, sliding window,
and tool output pruning.

Extracted from worker.py to keep the agent loop module focused on
orchestration while this module handles context window management.

Fragment selection has moved to backend.app.agent.fragment_router (Doc 027 Phase 3).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

logger = logging.getLogger("bond.agent.worker")

# ---------------------------------------------------------------------------
# Configuration constants (will be DB-configurable later)
# ---------------------------------------------------------------------------

VERBATIM_MESSAGE_COUNT = 4   # Recent messages kept as-is
COMPRESSION_THRESHOLD = 8000  # Don't compress if under this token count
SUMMARY_MAX_WORDS = 100
TOPIC_MAX_MESSAGES = 8        # Force topic boundary after this many messages

# Sliding window: max messages loaded from DB per turn
HISTORY_WINDOW_SIZE = 20
# Update rolling summary when this many new messages accumulate
SUMMARY_UPDATE_THRESHOLD = 10


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Fast token estimation — ~4 chars per token for English."""
    if not text:
        return 0
    return len(text) // 4


def _estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, dict):
            total += _estimate_tokens(json.dumps(content))
        # Tool calls add overhead
        if msg.get("tool_calls"):
            total += _estimate_tokens(json.dumps(msg["tool_calls"]))
    return total


# ---------------------------------------------------------------------------
# Tool output pruning (rule-based, no LLM call)
# ---------------------------------------------------------------------------

def _prune_tool_result(msg: dict, age: str) -> dict:
    """Rule-based tool output pruning. No LLM call needed.

    age: 'current' | 'recent' | 'old'
    """
    if msg.get("role") != "tool":
        return msg

    content = msg.get("content", "")
    if not isinstance(content, str):
        return msg

    token_count = _estimate_tokens(content)

    # Small results: keep as-is regardless of age
    if token_count < 500:
        return msg

    # Current topic: keep verbatim
    if age == "current":
        return msg

    # Try to parse as JSON for structured pruning
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if age == "recent":
        # file_read: keep first/last 10 lines + summary
        if parsed and isinstance(parsed, dict):
            if "content" in parsed and isinstance(parsed["content"], str):
                lines = parsed["content"].splitlines()
                if len(lines) > 25:
                    head = "\n".join(lines[:10])
                    tail = "\n".join(lines[-10:])
                    pruned = {**parsed, "content": f"{head}\n\n<< {len(lines) - 20} LINES OMITTED >>\n\n{tail}"}
                    return {**msg, "content": json.dumps(pruned)}

        # Generic: truncate long text
        if token_count > 2000:
            half = 800 * 4  # ~800 tokens worth of chars
            truncated = content[:half] + f"\n\n<< {token_count - 1600} TOKENS OMITTED >>\n\n" + content[-half:]
            return {**msg, "content": truncated}

    elif age == "old":
        # Aggressive: one-line summary
        if parsed and isinstance(parsed, dict):
            # Try to extract meaningful summary fields
            summary_parts = []
            for key in ("file_path", "path", "command", "query", "url", "status", "exit_code", "error"):
                if key in parsed:
                    val = str(parsed[key])[:100]
                    summary_parts.append(f"{key}={val}")
            if summary_parts:
                return {**msg, "content": f"[Pruned tool result: {', '.join(summary_parts)}]"}

        # Fallback: first 200 chars
        return {**msg, "content": f"[Pruned: {content[:200]}...]"}

    return msg



# ---------------------------------------------------------------------------
# History compression (tiered summarization)
# ---------------------------------------------------------------------------

async def _compress_history(
    messages: list[dict],
    conversation_id: str,
    config: dict,
    extra_kwargs: dict,
    *,
    agent_db: Any = None,
) -> tuple[list[dict], dict]:
    """Compress conversation history using tiered summarization.

    Returns (compressed_messages, stats_dict).
    """
    import time as _time
    start_time = _time.time()

    total_tokens = _estimate_messages_tokens(messages)
    stats: dict[str, Any] = {
        "original_tokens": total_tokens,
        "compressed_tokens": total_tokens,
        "verbatim_messages": 0,
        "topics_summarized": 0,
        "tools_pruned": 0,
        "cache_hits": 0,
    }

    # Don't compress short histories
    if total_tokens < COMPRESSION_THRESHOLD or len(messages) <= VERBATIM_MESSAGE_COUNT:
        stats["verbatim_messages"] = len(messages)
        return messages, stats

    utility_model = config.get("utility_model", "claude-sonnet-4-6")

    # Split: compressible (older) vs verbatim (recent)
    verbatim = messages[-VERBATIM_MESSAGE_COUNT:]
    compressible = messages[:-VERBATIM_MESSAGE_COUNT]
    stats["verbatim_messages"] = len(verbatim)

    if not compressible:
        return messages, stats

    # --- Stage 3: Tool Output Pruning (rule-based, fast) ---
    # Classify messages by age relative to verbatim boundary
    pruned_compressible = []
    tools_pruned = 0
    # First half of compressible = "old", second half = "recent"
    midpoint = len(compressible) // 2

    for i, msg in enumerate(compressible):
        age = "old" if i < midpoint else "recent"
        pruned = _prune_tool_result(msg, age)
        if pruned is not msg:
            tools_pruned += 1
        pruned_compressible.append(pruned)

    stats["tools_pruned"] = tools_pruned

    # --- Stage 2: History Summarization ---
    # Check for cached summaries
    cached_summary = None
    cache_covers_to = 0

    if agent_db:
        try:
            cursor = await agent_db.execute(
                "SELECT summary, covers_to FROM context_summaries "
                "WHERE conversation_id = ? ORDER BY covers_to DESC LIMIT 1",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row:
                cached_summary = row[0]
                cache_covers_to = row[1]
                stats["cache_hits"] = 1
        except Exception as e:
            logger.debug("Failed to load cached summary: %s", e)

    # Determine what needs summarizing
    if cached_summary and cache_covers_to > 0:
        # We have a cached summary covering messages 0..cache_covers_to
        # Only need to summarize messages cache_covers_to..end_of_compressible
        already_summarized_count = min(cache_covers_to, len(pruned_compressible))
        new_messages_to_summarize = pruned_compressible[already_summarized_count:]

        if not new_messages_to_summarize:
            # Cache covers everything — use cached summary + verbatim
            compressed = [{"role": "user", "content": f"[Previous conversation summary]\n{cached_summary}"}]
            compressed.extend(verbatim)
            stats["compressed_tokens"] = _estimate_messages_tokens(compressed)
            stats["topics_summarized"] = 1
            stats["processing_time_ms"] = int((_time.time() - start_time) * 1000)
            return compressed, stats

        # Summarize only the new portion
        messages_to_summarize = new_messages_to_summarize
        existing_context = f"Previous summary: {cached_summary}\n\n"
    else:
        messages_to_summarize = pruned_compressible
        existing_context = ""

    # Build text representation for summarization
    summary_input_lines = []
    for msg in messages_to_summarize:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            # Truncate very long messages for the summary input
            if len(content) > 1000:
                content = content[:500] + f"\n[...{len(content) - 1000} chars omitted...]\n" + content[-500:]
            summary_input_lines.append(f"{role}: {content}")
        elif msg.get("tool_calls"):
            calls = msg["tool_calls"]
            if isinstance(calls, list):
                for tc in calls:
                    fn = tc.get("function", {})
                    summary_input_lines.append(f"assistant: [called {fn.get('name', '?')}({fn.get('arguments', '')[:100]})]")

    summary_input = "\n".join(summary_input_lines)

    if not summary_input.strip():
        return messages, stats

    # Call utility model for summarization
    try:
        summary_prompt = f"""Summarize the following conversation history concisely. Preserve:
- Key decisions made and their reasoning
- File paths, variable names, error codes, and specific technical details
- What was attempted and whether it succeeded or failed
- Current state of the work

Do NOT include:
- Verbose tool output content (just note what tool was called and the key result)
- Pleasantries or filler
- Redundant information

{existing_context}Conversation to summarize:
{summary_input}

Write a concise summary in {SUMMARY_MAX_WORDS}-{SUMMARY_MAX_WORDS * 2} words. Use bullet points for clarity. Start directly with the content, no preamble."""

        response = await litellm.acompletion(
            model=utility_model,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.0,
            max_tokens=1024,
            **extra_kwargs,
        )

        summary = response.choices[0].message.content or ""
        stats["topics_summarized"] += 1

        # Combine: existing cached summary + new summary if applicable
        if cached_summary:
            full_summary = f"{cached_summary}\n\n{summary}"
        else:
            full_summary = summary

        # Cache the summary
        if agent_db:
            try:
                from ulid import ULID
                summary_id = str(ULID())
                summary_tokens = _estimate_tokens(full_summary)
                await agent_db.execute(
                    "INSERT OR REPLACE INTO context_summaries "
                    "(id, conversation_id, tier, covers_from, covers_to, "
                    "original_token_count, summary, summary_token_count, utility_model) "
                    "VALUES (?, ?, 'topic', 0, ?, ?, ?, ?, ?)",
                    (
                        summary_id,
                        conversation_id,
                        len(pruned_compressible),
                        _estimate_messages_tokens(pruned_compressible),
                        full_summary,
                        summary_tokens,
                        utility_model,
                    ),
                )
                await agent_db.commit()
            except Exception as e:
                logger.debug("Failed to cache summary: %s", e)

        # Assemble compressed history
        compressed = [{"role": "user", "content": f"[Previous conversation summary]\n{full_summary}"}]
        compressed.extend(verbatim)

        stats["compressed_tokens"] = _estimate_messages_tokens(compressed)
        stats["processing_time_ms"] = int((_time.time() - start_time) * 1000)

        logger.info(
            "History compression: %d→%d tokens (%.0f%% reduction), %d tools pruned, cache=%s, %dms",
            stats["original_tokens"], stats["compressed_tokens"],
            (1 - stats["compressed_tokens"] / max(stats["original_tokens"], 1)) * 100,
            tools_pruned,
            "hit" if stats["cache_hits"] else "miss",
            stats["processing_time_ms"],
        )

        return compressed, stats

    except Exception as e:
        raise RuntimeError(f"History compression failed — refusing to send uncompressed history to primary model: {e}") from e


# ---------------------------------------------------------------------------
# Compression stats logging
# ---------------------------------------------------------------------------

async def _log_compression_stats(
    conversation_id: str,
    turn_number: int,
    stats: dict,
    fragment_stats: dict,
    utility_model: str,
    *,
    agent_db: Any = None,
) -> None:
    """Log compression statistics to the agent DB for auditing."""
    if not agent_db:
        return
    try:
        from ulid import ULID
        await agent_db.execute(
            "INSERT INTO context_compression_log "
            "(id, conversation_id, turn_number, original_tokens, compressed_tokens, "
            "stages_applied, fragments_selected, fragments_total, topics_summarized, "
            "tools_pruned, processing_time_ms, utility_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(ULID()),
                conversation_id,
                turn_number,
                stats.get("original_tokens", 0),
                stats.get("compressed_tokens", 0),
                json.dumps(["fragment_selection", "history_compression", "tool_pruning"]),
                fragment_stats.get("selected", 0),
                fragment_stats.get("total", 0),
                stats.get("topics_summarized", 0),
                stats.get("tools_pruned", 0),
                stats.get("processing_time_ms", 0),
                utility_model,
            ),
        )
        await agent_db.commit()
    except Exception as e:
        logger.debug("Failed to log compression stats: %s", e)


# ---------------------------------------------------------------------------
# Sliding window
# ---------------------------------------------------------------------------

async def _apply_sliding_window(
    history: list[dict],
    conversation_id: str,
    config: dict,
    extra_kwargs: dict,
    *,
    agent_db: Any = None,
) -> list[dict]:
    """Apply sliding window: keep last HISTORY_WINDOW_SIZE messages, prepend rolling summary.

    If history exceeds the window, summarize the overflow and store as a rolling summary
    in the agent DB for reuse on next turn.

    Returns the windowed history with optional summary prefix.
    """
    if len(history) <= HISTORY_WINDOW_SIZE:
        return history

    utility_model = config.get("utility_model", "claude-sonnet-4-6")

    # Split: overflow (to summarize) + window (to keep)
    overflow = history[:-HISTORY_WINDOW_SIZE]
    window = history[-HISTORY_WINDOW_SIZE:]

    # Check for existing rolling summary in agent DB
    existing_summary = ""
    summary_covers_to = 0
    if agent_db:
        try:
            cursor = await agent_db.execute(
                "SELECT summary, covers_to FROM context_summaries "
                "WHERE conversation_id = ? ORDER BY covers_to DESC LIMIT 1",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row:
                existing_summary = row[0]
                summary_covers_to = row[1]
        except Exception as e:
            logger.debug("Failed to load rolling summary: %s", e)

    # Determine how many overflow messages are already covered by existing summary
    new_overflow_start = min(summary_covers_to, len(overflow))
    new_overflow = overflow[new_overflow_start:]

    if not new_overflow and existing_summary:
        # Existing summary covers everything — just prepend it
        summary_msg = {"role": "user", "content": f"[Previous conversation summary]\n{existing_summary}"}
        return [summary_msg] + window

    # Need to summarize new overflow messages
    if new_overflow:
        summary_lines = []
        if existing_summary:
            summary_lines.append(f"Previous summary: {existing_summary}")
            summary_lines.append("")

        for msg in new_overflow:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                if len(content) > 500:
                    content = content[:250] + f"\n[...{len(content) - 500} chars omitted...]\n" + content[-250:]
                summary_lines.append(f"{role}: {content}")
            elif msg.get("tool_calls"):
                calls = msg["tool_calls"]
                if isinstance(calls, list):
                    for tc in calls:
                        fn = tc.get("function", {})
                        summary_lines.append(f"assistant: [called {fn.get('name', '?')}]")

        summary_input = "\n".join(summary_lines)

        try:
            response = await litellm.acompletion(
                model=utility_model,
                messages=[{"role": "user", "content": (
                    f"Summarize this conversation history in {SUMMARY_MAX_WORDS}-{SUMMARY_MAX_WORDS * 2} words. "
                    "Preserve key decisions, file paths, technical details, what was attempted and results. "
                    "Use bullet points. Start directly with content.\n\n"
                    f"{summary_input}"
                )}],
                temperature=0.0,
                max_tokens=1024,
                **extra_kwargs,
            )
            new_summary = response.choices[0].message.content or ""

            # Combine with existing
            if existing_summary:
                full_summary = f"{existing_summary}\n\n{new_summary}"
            else:
                full_summary = new_summary

            # Cache the updated summary
            if agent_db:
                try:
                    from ulid import ULID
                    summary_tokens = _estimate_tokens(full_summary)
                    await agent_db.execute(
                        "INSERT OR REPLACE INTO context_summaries "
                        "(id, conversation_id, tier, covers_from, covers_to, "
                        "original_token_count, summary, summary_token_count, utility_model) "
                        "VALUES (?, ?, 'rolling', 0, ?, ?, ?, ?, ?)",
                        (
                            str(ULID()),
                            conversation_id,
                            len(overflow),
                            _estimate_messages_tokens(overflow),
                            full_summary,
                            summary_tokens,
                            utility_model,
                        ),
                    )
                    await agent_db.commit()
                except Exception as e:
                    logger.debug("Failed to cache rolling summary: %s", e)

            summary_msg = {"role": "user", "content": f"[Previous conversation summary]\n{full_summary}"}
            return [summary_msg] + window

        except Exception as e:
            raise RuntimeError(f"Rolling summary failed — refusing to drop context silently: {e}") from e

    return window
