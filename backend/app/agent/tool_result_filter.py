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
from litellm.cost_calculator import completion_cost as _litellm_completion_cost

logger = logging.getLogger(__name__)

# Tool results under this size (chars) skip the filter — not worth the utility call
FILTER_THRESHOLD = 6000  # ~1500 tokens (Phase 1C: raised from 1500)

# Tools that should never be filtered (tiny results or final output)
SKIP_TOOLS = frozenset({
    "respond",        # final output to user
    "file_write",     # small confirmation
    "memory_save",    # small confirmation
    "memory_update",  # small confirmation
    "memory_delete",  # small confirmation
    "notify",         # small confirmation
    "cron",           # small confirmation
    # Shell utility tools — always small and structured (Phase 1C)
    "shell_find",
    "shell_ls",
    "file_search",
    "shell_tree",
    "shell_wc",
    "git_info",
    "project_search",
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
    langfuse_metadata: dict | None = None,
    indexed: bool = False,
) -> str | tuple[str, float]:
    """Filter a tool result through the utility model.

    Returns the filtered result as a JSON string ready for the messages array,
    or a tuple of (result_json, cost) when cost can be calculated.
    If filtering is skipped or fails, returns the raw result as JSON.

    Args:
        tool_name: Name of the tool that produced the result
        tool_args: Arguments the tool was called with
        raw_result: The raw dict result from tool execution
        user_message: The user's original message (for relevance context)
        last_assistant_content: Last assistant message before this tool call
        utility_model: Model ID for the utility model
        utility_kwargs: Extra kwargs for the utility model (api_key, etc.)
        indexed: If True, the content has been indexed into FTS5 and the
            summary should include a ctx_search hint (Design Doc 075).
    """
    raw_json = json.dumps(raw_result)

    # Skip small results and exempt tools
    if tool_name in SKIP_TOOLS or len(raw_json) < FILTER_THRESHOLD:
        return raw_json, 0.0

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
        _filter_meta = langfuse_metadata or {}
        from backend.app.core.oauth import ensure_oauth_system_prefix
        _filter_msgs = [{"role": "user", "content": prompt}]
        ensure_oauth_system_prefix(_filter_msgs, extra_kwargs=utility_kwargs)
        response = await litellm.acompletion(
            model=utility_model,
            messages=_filter_msgs,
            temperature=0.0,
            max_tokens=1024,
            metadata=_filter_meta if _filter_meta else None,
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

        # Calculate real cost for this filter call
        try:
            call_cost = _litellm_completion_cost(completion_response=response, model=utility_model)
        except Exception:
            call_cost = 0.0

        if savings > 0:
            logger.info(
                "Tool filter [%s]: %d → %d tokens (~%d saved) cost=$%.4f",
                tool_name, original_tokens, filtered_tokens, savings, call_cost,
            )
        else:
            logger.debug("Tool filter [%s]: no reduction (%d tokens) cost=$%.4f", tool_name, original_tokens, call_cost)

        # Append ctx_search hint when content has been indexed (Design Doc 075)
        if indexed:
            try:
                parsed = json.loads(filtered)
                if isinstance(parsed, dict):
                    parsed["_indexed"] = True
                    parsed["_ctx_hint"] = (
                        "The full output has been indexed. "
                        "Use ctx_search(queries=[...]) to retrieve specific details."
                    )
                    filtered = json.dumps(parsed)
            except (json.JSONDecodeError, TypeError):
                pass

        return filtered, call_cost

    except Exception as e:
        logger.warning("Tool filter failed for %s, using raw result: %s", tool_name, e)
        return raw_json, 0.0


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

    # Rule 1: Strip ANSI escape codes + truncate large stdout from code_execute
    if tool_name == "code_execute":
        new_result = dict(result)
        for key in ("stdout", "stderr"):
            val = new_result.get(key, "")
            if isinstance(val, str) and val:
                cleaned = _clean_string_value(val)
                # Phase 1C: truncate stdout > 4K to first/last 1K
                if key == "stdout" and len(cleaned) > 4000:
                    cleaned = cleaned[:1000] + f"\n\n[...{len(cleaned) - 2000} chars truncated...]\n\n" + cleaned[-1000:]
                    modified = True
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

    # Rule 4: file_read — truncate to first/last 50 lines if > 200 lines (Phase 1C)
    if tool_name == "file_read":
        content = result.get("content", "")
        if isinstance(content, str):
            lines = content.splitlines()
            if len(lines) > 200:
                truncated_lines = lines[:50] + [f"\n[...{len(lines) - 100} lines truncated...]\n"] + lines[-50:]
                new_result = dict(result)
                new_result["content"] = "\n".join(truncated_lines)
                new_result["_truncated"] = True
                new_result["_original_lines"] = len(lines)
                return new_result
        if len(json.dumps(result)) < 2000:
            return result

    # Rule 4b: file_write — skip utility model if result is small
    if tool_name == "file_write":
        if len(json.dumps(result)) < 2000:
            return result  # already structured, filter can't improve

    # Rule 5: file_search — cap at 30 matches (Phase 1C)
    if tool_name == "file_search":
        output = result.get("output", result.get("stdout", result.get("content", "")))
        if isinstance(output, str):
            lines = output.splitlines()
            if len(lines) > 30:
                new_result = dict(result)
                key = "output" if "output" in result else ("stdout" if "stdout" in result else "content")
                if key in result:
                    new_result[key] = "\n".join(lines[:30]) + f"\n[...{len(lines) - 30} more matches truncated]"
                    return new_result

    return None
