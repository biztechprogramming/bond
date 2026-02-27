"""Anthropic prompt cache management and in-loop tool result decay.

Extracted from worker.py to isolate cache breakpoint logic and
in-loop compression from the main agent loop.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("bond.agent.worker")


def _estimate_tokens(text: str) -> int:
    """Fast token estimation — ~4 chars per token for English."""
    if not text:
        return 0
    return len(text) // 4


def _advance_cache_breakpoint(messages: list[dict], old_bp_index: int) -> int:
    """Advance Anthropic cache_control breakpoint 2 toward the end of messages.

    Strategy: Only advance the breakpoint when enough new messages
    (>= _CACHE_BP_ADVANCE_THRESHOLD) have accumulated after the current
    breakpoint. This keeps the prefix stable for multiple consecutive LLM
    calls, maximizing cache hits.

    When we DO advance, we move to messages[-2] (second-to-last), so only
    the very latest message pair pays full input price.

    Returns the new breakpoint index.
    """
    if len(messages) < 3:
        return old_bp_index

    # Small conversations won't meet Anthropic's minimum cache block size (2048 tokens for Opus).
    if len(messages) < 12:
        return old_bp_index

    new_bp_index = len(messages) - 2

    # Nothing to do if breakpoint hasn't moved
    if new_bp_index == old_bp_index:
        return old_bp_index

    # Only advance if enough messages have accumulated past the breakpoint.
    # Each tool call adds ~2 messages (assistant + tool result). With a
    # threshold of 12, the breakpoint stays stable for ~6 consecutive calls,
    # maximizing cache hits. The uncached tail (messages past the breakpoint)
    # is small relative to the cached prefix, so this is a net win.
    _CACHE_BP_ADVANCE_THRESHOLD = 12
    gap = new_bp_index - old_bp_index
    if gap < _CACHE_BP_ADVANCE_THRESHOLD:
        logger.debug("Cache BP2: holding at index %d (gap=%d < threshold=%d, msgs=%d)",
                      old_bp_index, gap, _CACHE_BP_ADVANCE_THRESHOLD, len(messages))
        return old_bp_index

    logger.info("Cache BP2: advancing %d → %d (gap=%d, msgs=%d)",
                old_bp_index, new_bp_index, gap, len(messages))

    # Clear cache_control from old breakpoint (skip system prompt)
    if old_bp_index > 0 and old_bp_index < len(messages):
        old_msg = messages[old_bp_index]
        if isinstance(old_msg.get("content"), list):
            for block in old_msg["content"]:
                if isinstance(block, dict) and "cache_control" in block:
                    del block["cache_control"]

    # Set cache_control on new breakpoint target
    target = messages[new_bp_index]
    if isinstance(target.get("content"), str):
        # Convert string content to block format (one-time, stable after)
        target["content"] = [{
            "type": "text",
            "text": target["content"],
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(target.get("content"), list):
        last_block = target["content"][-1] if target["content"] else None
        if last_block and isinstance(last_block, dict):
            last_block["cache_control"] = {"type": "ephemeral"}

    return new_bp_index


def _decay_in_loop_tool_results(messages: list[dict], preturn_count: int, *, frozen_up_to: int = 0) -> list[dict]:
    """Compress tool results accumulated during the current turn's tool loop.

    Keeps messages before the turn untouched. For in-turn messages:
    - Last 4 messages (2 tool call/result pairs): verbatim
    - Older tool results: aggressively compressed

    The frozen_up_to parameter protects all messages at indices < frozen_up_to
    from modification (preserves Anthropic prompt cache prefix stability).
    """
    # The compressible zone starts after whichever is later:
    # the pre-turn boundary or the cache-frozen zone
    compress_start = max(preturn_count, frozen_up_to)

    if len(messages) <= compress_start + 4:
        return messages

    frozen = messages[:compress_start]
    compressible = messages[compress_start:]

    # Split: older compressible messages vs recent (last 4)
    older = compressible[:-4]
    recent = compressible[-4:]

    compressed_older = []
    tokens_saved = 0
    for msg in older:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                # Try to extract key info from JSON results
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        summary_parts = []
                        for key in ("path", "file_path", "status", "exit_code", "error", "stdout", "stderr"):
                            if key in parsed and parsed[key]:
                                val = str(parsed[key])
                                if len(val) > 150:
                                    val = val[:75] + "..." + val[-75:]
                                summary_parts.append(f"{key}: {val}")
                        if "content" in parsed:
                            size = len(parsed["content"])
                            summary_parts.append(f"content: [{size} chars]")
                        if "size" in parsed:
                            summary_parts.append(f"size: {parsed['size']}")
                        compressed = "[Compressed] " + "; ".join(summary_parts)
                        tokens_saved += (_estimate_tokens(content) - _estimate_tokens(compressed))
                        compressed_older.append({**msg, "content": compressed})
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

                # Non-JSON: keep first/last lines
                lines = content.splitlines()
                if len(lines) > 10:
                    compressed = "\n".join(lines[:3]) + f"\n[...{len(lines)-6} lines omitted...]\n" + "\n".join(lines[-3:])
                    tokens_saved += (_estimate_tokens(content) - _estimate_tokens(compressed))
                    compressed_older.append({**msg, "content": compressed})
                    continue

        elif msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content:
                if msg.get("tool_calls"):
                    # Has tool_calls: strip reasoning text to first sentence or 100 chars
                    truncated = content[:100].split(". ")[0] + "..." if len(content) > 100 else content
                    if truncated != content:
                        tokens_saved += (_estimate_tokens(content) - _estimate_tokens(truncated))
                        compressed_older.append({**msg, "content": truncated})
                        continue
                else:
                    # Content-only assistant message: keep first 200 chars
                    if len(content) > 200:
                        truncated = content[:200] + "..."
                        tokens_saved += (_estimate_tokens(content) - _estimate_tokens(truncated))
                        compressed_older.append({**msg, "content": truncated})
                        continue

        compressed_older.append(msg)

    if tokens_saved > 0:
        logger.info("In-loop decay: compressed %d tokens from older tool results", tokens_saved)

    return frozen + compressed_older + recent
