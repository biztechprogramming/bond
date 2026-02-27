"""Progressive decay for tool results in conversation context.

Tool results are compressed based on how many turns ago they occurred:
- Turn 0 (just returned): Full content, capped at MAX_TOOL_RESULT_TOKENS
- Turn 1-2: Head/tail with content-aware rules per tool type
- Turn 3-5: One-line summary
- Turn 6+: Tool name + args only
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Max tokens for a fresh tool result (turn 0)
MAX_TOOL_RESULT_TOKENS = 1500
# Chars per token estimate
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n<< {_estimate_tokens(text) - max_tokens} tokens omitted >>\n\n" + text[-half:]


def _extract_tool_info(msg: dict) -> tuple[str, dict] | None:
    """Try to extract tool name and args from surrounding context."""
    content = msg.get("content", "")
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            # Try common result fields to identify tool type
            if "file_path" in parsed or "path" in parsed:
                return "file_read", parsed
            if "exit_code" in parsed:
                return "code_execute", parsed
            if "results" in parsed:
                return "search_memory", parsed
            if "url" in parsed:
                return "web_read", parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _summarize_file_read(content: str, parsed: dict | None) -> str:
    """Content-aware summary for file_read results."""
    path = ""
    file_content = content
    if parsed:
        path = parsed.get("file_path", parsed.get("path", ""))
        file_content = parsed.get("content", content)

    if not isinstance(file_content, str):
        file_content = str(file_content)

    lines = file_content.splitlines()
    line_count = len(lines)

    # Detect language from extension
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    lang_map = {"py": "Python", "ts": "TypeScript", "js": "JavaScript", "rs": "Rust",
                "go": "Go", "java": "Java", "rb": "Ruby", "sql": "SQL", "md": "Markdown"}
    lang = lang_map.get(ext, ext.upper() if ext else "text")

    return f"[file_read: {path} — {line_count} lines, {lang}]"


def _head_tail_file(content: str, parsed: dict | None, head: int = 15, tail: int = 15) -> str:
    """Keep head + tail lines of file content."""
    file_content = content
    path = ""
    if parsed:
        path = parsed.get("file_path", parsed.get("path", ""))
        file_content = parsed.get("content", content)

    if not isinstance(file_content, str):
        return content

    lines = file_content.splitlines()
    if len(lines) <= head + tail + 5:
        return content  # small enough, keep as-is

    head_lines = "\n".join(lines[:head])
    tail_lines = "\n".join(lines[-tail:])
    omitted = len(lines) - head - tail

    if parsed:
        result = {**parsed, "content": f"{head_lines}\n\n<< {omitted} lines omitted >>\n\n{tail_lines}"}
        return json.dumps(result)
    return f"{head_lines}\n\n<< {omitted} lines omitted >>\n\n{tail_lines}"


def _summarize_code_execute(content: str, parsed: dict | None) -> str:
    """One-line summary for code execution results."""
    exit_code = "?"
    output_lines = 0
    if parsed:
        exit_code = parsed.get("exit_code", parsed.get("status", "?"))
        stdout = parsed.get("stdout", parsed.get("output", ""))
        if isinstance(stdout, str):
            output_lines = len(stdout.splitlines())
    return f"[code_execute: exit {exit_code}, {output_lines} lines output]"


def _head_tail_code_execute(content: str, parsed: dict | None, tail: int = 30) -> str:
    """Keep last N lines of execution output (most relevant)."""
    if not parsed:
        lines = content.splitlines()
        if len(lines) <= tail + 5:
            return content
        return "\n".join(lines[-tail:])

    result = dict(parsed)
    for key in ("stdout", "output"):
        if key in result and isinstance(result[key], str):
            lines = result[key].splitlines()
            if len(lines) > tail + 5:
                result[key] = f"<< {len(lines) - tail} lines omitted >>\n" + "\n".join(lines[-tail:])
    # Also truncate stderr
    if "stderr" in result and isinstance(result["stderr"], str):
        stderr_lines = result["stderr"].splitlines()
        if len(stderr_lines) > 20:
            result["stderr"] = "\n".join(stderr_lines[-20:])
    return json.dumps(result)


def _summarize_search_memory(content: str, parsed: dict | None) -> str:
    """One-line summary for memory search results."""
    count = 0
    if parsed and "results" in parsed:
        count = len(parsed["results"])
    return f"[search_memory: {count} results returned]"


def _summarize_generic(content: str, parsed: dict | None, tool_name: str = "") -> str:
    """Generic one-line summary."""
    tokens = _estimate_tokens(content)
    if parsed and isinstance(parsed, dict):
        parts = []
        for key in ("file_path", "path", "command", "query", "url", "status", "exit_code", "error"):
            if key in parsed:
                val = str(parsed[key])[:80]
                parts.append(f"{key}={val}")
        if parts:
            return f"[{tool_name or 'tool'}: {', '.join(parts)}]"
    return f"[{tool_name or 'tool'} result: {tokens} tokens]"


def _tool_call_only(msg: dict, messages: list[dict], msg_idx: int) -> str:
    """Reduce to just tool name + args from the preceding assistant tool_call."""
    tool_call_id = msg.get("tool_call_id", "")
    # Find matching assistant message with tool_calls
    for i in range(msg_idx - 1, max(msg_idx - 5, -1), -1):
        prev = messages[i]
        if prev.get("role") == "assistant" and prev.get("tool_calls"):
            calls = prev["tool_calls"]
            if isinstance(calls, list):
                for tc in calls:
                    if tc.get("id") == tool_call_id:
                        fn = tc.get("function", {})
                        name = fn.get("name", "?")
                        args = fn.get("arguments", "")
                        if isinstance(args, str) and len(args) > 100:
                            args = args[:100] + "..."
                        return f"[called {name}({args})]"
    return f"[tool result: {_estimate_tokens(msg.get('content', ''))} tokens]"


def apply_progressive_decay(
    messages: list[dict],
    current_turn_index: int | None = None,
) -> list[dict]:
    """Apply progressive decay to tool results in message list.

    Messages are not modified in-place — new dicts are returned for changed messages.
    Non-tool messages pass through unchanged.

    Args:
        messages: The conversation messages (system message should NOT be included)
        current_turn_index: Index of the current user message (end of list if None).
            Used to calculate turn distance for each tool result.

    Returns:
        New list with decayed tool results.
    """
    if not messages:
        return messages

    # Find turn boundaries (each user message = new turn)
    turn_starts: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            turn_starts.append(i)

    if current_turn_index is None:
        current_turn_index = len(turn_starts)

    total_turns = len(turn_starts)

    def _get_turn_age(msg_idx: int) -> int:
        """How many turns ago was this message?"""
        turn = 0
        for i, start in enumerate(turn_starts):
            if msg_idx >= start:
                turn = i
        return max(0, total_turns - turn - 1)

    result = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            result.append(msg)
            continue

        content = msg.get("content", "")
        if not isinstance(content, str):
            result.append(msg)
            continue

        tokens = _estimate_tokens(content)
        turns_ago = _get_turn_age(i)

        # Small results: keep as-is regardless of age
        if tokens < 200:
            result.append(msg)
            continue

        # Parse for content-aware handling
        tool_info = _extract_tool_info(msg)
        tool_name = tool_info[0] if tool_info else ""
        parsed = tool_info[1] if tool_info else None

        if turns_ago == 0:
            # Turn 0: full content, capped
            if tokens > MAX_TOOL_RESULT_TOKENS:
                new_content = _truncate_to_tokens(content, MAX_TOOL_RESULT_TOKENS)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)

        elif turns_ago <= 2:
            # Turn 1-2: head/tail with content-aware rules
            if tool_name == "file_read" or (parsed and ("content" in parsed or "file_path" in parsed)):
                new_content = _head_tail_file(content, parsed)
            elif tool_name == "code_execute" or (parsed and "exit_code" in parsed):
                new_content = _head_tail_code_execute(content, parsed)
            elif tool_name == "search_memory" or (parsed and "results" in parsed):
                # Keep top 2 results
                if parsed and "results" in parsed and isinstance(parsed["results"], list):
                    trimmed = {**parsed, "results": parsed["results"][:2]}
                    new_content = json.dumps(trimmed)
                else:
                    new_content = _truncate_to_tokens(content, 500)
            else:
                new_content = _truncate_to_tokens(content, 500)
            result.append({**msg, "content": new_content})

        elif turns_ago <= 5:
            # Turn 3-5: one-line summary
            if tool_name == "file_read" or (parsed and "file_path" in parsed):
                summary = _summarize_file_read(content, parsed)
            elif tool_name == "code_execute" or (parsed and "exit_code" in parsed):
                summary = _summarize_code_execute(content, parsed)
            elif tool_name == "search_memory" or (parsed and "results" in parsed):
                summary = _summarize_search_memory(content, parsed)
            else:
                summary = _summarize_generic(content, parsed, tool_name)
            result.append({**msg, "content": summary})

        else:
            # Turn 6+: tool name + args only
            summary = _tool_call_only(msg, messages, i)
            result.append({**msg, "content": summary})

    # Log savings
    original_tokens = sum(_estimate_tokens(m.get("content", "")) for m in messages if m.get("role") == "tool")
    decayed_tokens = sum(_estimate_tokens(m.get("content", "")) for m in result if m.get("role") == "tool")
    if original_tokens > 0:
        savings = original_tokens - decayed_tokens
        logger.info(
            "Progressive decay: tool results %d→%d tokens (saved %d, %.0f%%)",
            original_tokens, decayed_tokens, savings,
            (savings / original_tokens * 100) if original_tokens else 0,
        )

    return result
