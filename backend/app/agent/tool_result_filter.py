"""Utility-model filter for tool results.

Passes large tool results through a cheap utility model to extract only
the parts relevant to the current task. This prevents bloated tool output
from consuming the primary model's context window across subsequent calls.
"""

from __future__ import annotations

import json
import logging
import re

import litellm

logger = logging.getLogger(__name__)

# Tool results under this size (chars) skip the filter — not worth the utility call
FILTER_THRESHOLD = 1500  # ~375 tokens

# Tools that should never be filtered (tiny results or final output)
SKIP_TOOLS = frozenset({
    "respond",        # final output to user
    "file_write",     # small confirmation
    "memory_save",    # small confirmation
    "memory_update",  # small confirmation
    "memory_delete",  # small confirmation
    "notify",         # small confirmation
    "cron",           # small confirmation
})

# Max output size from the filter (chars)
FILTER_MAX_OUTPUT = 3000  # ~750 tokens


async def filter_tool_result(
    tool_name: str,
    tool_args: dict,
    raw_result: dict,
    user_message: str,
    last_assistant_content: str,
    utility_model: str,
    utility_kwargs: dict,
) -> str:
    """Filter a tool result through the utility model.

    Returns the filtered result as a JSON string ready for the messages array.
    If filtering is skipped or fails, returns the raw result as JSON.

    Args:
        tool_name: Name of the tool that produced the result
        tool_args: Arguments the tool was called with
        raw_result: The raw dict result from tool execution
        user_message: The user's original message (for relevance context)
        last_assistant_content: Last assistant message before this tool call
        utility_model: Model ID for the utility model
        utility_kwargs: Extra kwargs for the utility model (api_key, etc.)
    """
    raw_json = json.dumps(raw_result)

    # Skip small results and exempt tools
    if tool_name in SKIP_TOOLS or len(raw_json) < FILTER_THRESHOLD:
        return raw_json

    # Build context for the utility model
    # Include tool name + args so it knows what was requested
    args_summary = json.dumps(tool_args)
    if len(args_summary) > 500:
        args_summary = args_summary[:500] + "..."

    # For file_read, include the path prominently
    path = tool_args.get("path", tool_args.get("file_path", ""))

    prompt = f"""You are a tool result filter. Your job is to extract ONLY the relevant parts of a tool result.

CONTEXT:
- User's goal: {user_message[:500]}
- Assistant's intent: {last_assistant_content[:300] if last_assistant_content else "Starting task"}
- Tool called: {tool_name}({args_summary})
{f'- File: {path}' if path else ''}

RAW TOOL RESULT:
{raw_json[:15000]}

INSTRUCTIONS:
- Extract only the parts relevant to the user's goal and the assistant's stated intent
- For code files: keep relevant functions/classes with line numbers, omit unrelated code
- For build output: keep error messages and warnings, omit successful compilation lines
- For search/grep: keep matching results, omit noise
- For errors: keep the full error message and stack trace
- Preserve file paths, line numbers, variable names, error codes exactly
- Return valid JSON in the same schema as the input (same keys)
- If the entire result is relevant, return it unchanged
- Maximum output: {FILTER_MAX_OUTPUT} characters

Return ONLY the filtered JSON result, nothing else."""

    try:
        response = await litellm.acompletion(
            model=utility_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1024,
            **utility_kwargs,
        )

        filtered = response.choices[0].message.content or raw_json
        # Strip markdown fences if present
        filtered = filtered.strip()
        if filtered.startswith("```"):
            filtered = filtered.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # Validate it's parseable JSON — if not, use raw
        try:
            json.loads(filtered)
        except json.JSONDecodeError:
            # Utility model returned non-JSON — wrap it
            filtered = json.dumps({"filtered_result": filtered})

        original_tokens = len(raw_json) // 4
        filtered_tokens = len(filtered) // 4
        savings = original_tokens - filtered_tokens

        if savings > 0:
            logger.info(
                "Tool filter [%s]: %d → %d tokens (~%d saved)",
                tool_name, original_tokens, filtered_tokens, savings,
            )
        else:
            logger.debug("Tool filter [%s]: no reduction (%d tokens)", tool_name, original_tokens)

        return filtered

    except Exception as e:
        logger.warning("Tool filter failed for %s, using raw result: %s", tool_name, e)
        return raw_json


# ---------------------------------------------------------------------------
# Rule-based pruning (no LLM call)
# ---------------------------------------------------------------------------

# ANSI escape code pattern (terminal color codes)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
# 3+ consecutive blank lines
_EXCESS_BLANKS_RE = re.compile(r'\n{3,}')


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from a string."""
    return _ANSI_RE.sub('', text)


def _collapse_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines to 1 blank line."""
    return _EXCESS_BLANKS_RE.sub('\n\n', text)


def _clean_string_value(value: str) -> str:
    """Apply safe string cleaning: strip ANSI codes and collapse blank lines."""
    value = _strip_ansi(value)
    value = _collapse_blank_lines(value)
    return value


def rule_based_prune(tool_name: str, tool_args: dict, result: dict) -> dict | None:
    """Apply 100%-safe rule-based pruning to tool results before the utility model filter.

    Returns a modified result dict if a rule applies, or None to fall through
    to the utility model filter.

    ONLY contains rules that are guaranteed safe — no assumptions about
    what content is important.
    """
    if not isinstance(result, dict):
        return None

    modified = False

    # Rule 1: Strip ANSI escape codes from code_execute stdout/stderr
    if tool_name == "code_execute":
        new_result = dict(result)
        for key in ("stdout", "stderr"):
            val = new_result.get(key, "")
            if isinstance(val, str) and val:
                cleaned = _clean_string_value(val)
                if cleaned != val:
                    new_result[key] = cleaned
                    modified = True
        if modified:
            return new_result

    # Rule 2: Collapse excessive blank lines and strip ANSI in any string content
    # Applied to all tools — blank lines and ANSI codes have zero informational value
    _has_cleanable = any(
        isinstance(v, str) and ('\n\n\n' in v or '\x1b' in v)
        for v in result.values()
    )
    if _has_cleanable:
        new_result = {}
        for key, val in result.items():
            if isinstance(val, str) and val:
                cleaned = _clean_string_value(val)
                if cleaned != val:
                    modified = True
                    new_result[key] = cleaned
                else:
                    new_result[key] = val
            else:
                new_result[key] = val
        if modified:
            return new_result

    # Rule 3: search_memory with 0 results — skip utility model (already tiny)
    if tool_name == "search_memory":
        count = result.get("count", len(result.get("results", [])))
        if count == 0:
            return result  # return as-is, skip the filter

    # Rule 4: file_read / file_write — skip utility model if result is small
    if tool_name in ("file_read", "file_write"):
        if len(json.dumps(result)) < 2000:
            return result  # already structured, filter can't improve

    return None
