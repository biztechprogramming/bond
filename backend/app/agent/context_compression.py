"""Iteration-aware result compression for file reading tools.

Design Doc 098, Phase 6: After N iterations, compress old file_read/file_search
results into compact summaries to prevent context window from growing linearly.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Start compressing after this many iterations
COMPRESSION_ITERATION_THRESHOLD = 10
# Don't bother compressing results smaller than this (tokens)
MIN_TOKENS_TO_COMPRESS = 200
CHARS_PER_TOKEN = 4

# Language detection from file extension
_LANG_MAP = {
    "py": "Python", "ts": "TypeScript", "js": "JavaScript", "tsx": "TypeScript",
    "jsx": "JavaScript", "rs": "Rust", "go": "Go", "java": "Java", "rb": "Ruby",
    "sql": "SQL", "md": "Markdown", "sh": "Shell", "yml": "YAML", "yaml": "YAML",
    "json": "JSON", "css": "CSS", "html": "HTML", "c": "C", "cpp": "C++",
    "cs": "C#", "php": "PHP", "swift": "Swift", "kt": "Kotlin",
}

# Symbol extraction patterns per language
_SYMBOL_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "Python": [
        ("Classes", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("Key functions", re.compile(r"^def\s+(\w+)", re.MULTILINE)),
    ],
    "TypeScript": [
        ("Classes", re.compile(r"\bclass\s+(\w+)", re.MULTILINE)),
        ("Key functions", re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("Exports", re.compile(r"export\s+(?:const|let|var)\s+(\w+)", re.MULTILINE)),
    ],
    "JavaScript": [
        ("Classes", re.compile(r"\bclass\s+(\w+)", re.MULTILINE)),
        ("Key functions", re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("Exports", re.compile(r"export\s+(?:const|let|var)\s+(\w+)", re.MULTILINE)),
    ],
}


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def _parse_content(msg: dict) -> dict | None:
    """Parse JSON content from a tool message, returning None on failure."""
    content = msg.get("content", "")
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _is_file_read_result(msg: dict) -> bool:
    """Check if a tool result message is from file_read or file_search."""
    if msg.get("role") != "tool":
        return False
    parsed = _parse_content(msg)
    if not parsed:
        return False
    # file_read: has path/file_path + content
    if ("path" in parsed or "file_path" in parsed) and "content" in parsed:
        return True
    # file_search: has results/matches with file context
    if "results" in parsed or "matches" in parsed:
        # Distinguish from search_memory by checking for file-related keys
        items = parsed.get("results", parsed.get("matches", []))
        if isinstance(items, list) and items:
            first = items[0] if isinstance(items[0], dict) else {}
            if "path" in first or "file" in first or "file_path" in first:
                return True
    return False


def _is_error_result(msg: dict) -> bool:
    """Check if a tool result contains an error."""
    parsed = _parse_content(msg)
    if not parsed:
        return False
    return "error" in parsed


def _is_already_compressed(msg: dict) -> bool:
    """Check if result already has 'compressed': true."""
    parsed = _parse_content(msg)
    if not parsed:
        return False
    return parsed.get("compressed") is True


def _extract_file_path(msg: dict) -> str | None:
    """Extract file path from a file_read/file_search result."""
    parsed = _parse_content(msg)
    if not parsed:
        return None
    return parsed.get("path") or parsed.get("file_path") or None


def _detect_language(path: str) -> str:
    """Detect language from file extension."""
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return _LANG_MAP.get(ext, ext.upper() if ext else "text")


def _extract_symbols(content: str, language: str) -> list[str]:
    """Extract key symbols (classes, functions) from file content.

    Returns a list of strings like "Classes: Foo, Bar" or "Key functions: baz, qux".
    """
    patterns = _SYMBOL_PATTERNS.get(language)
    if not patterns:
        return []

    parts = []
    for label, pattern in patterns:
        matches = pattern.findall(content)
        # Deduplicate while preserving order, limit to 8
        seen: set[str] = set()
        unique = []
        for m in matches:
            if m not in seen and not m.startswith("_"):
                seen.add(m)
                unique.append(m)
            if len(unique) >= 8:
                break
        if unique:
            parts.append(f"{label}: {', '.join(unique)}")
    return parts


def _build_file_summary(content: str, parsed: dict, iteration: int) -> str:
    """Build a compact summary of a file read result.

    Returns a JSON string with path, summary, and compressed flag.
    """
    path = parsed.get("path") or parsed.get("file_path", "unknown")
    file_content = parsed.get("content", "")
    if not isinstance(file_content, str):
        file_content = str(file_content)

    line_count = len(file_content.splitlines())
    language = _detect_language(path)

    summary_parts = [f"{language} module ({line_count} lines)"]
    symbols = _extract_symbols(file_content, language)
    summary_parts.extend(symbols)
    summary_parts.append(f"Read at iteration {iteration}")

    return json.dumps({
        "path": path,
        "summary": ". ".join(summary_parts) + ".",
        "compressed": True,
    })


def _build_search_summary(parsed: dict, iteration: int) -> str:
    """Build a compact summary of a file_search result."""
    items = parsed.get("results", parsed.get("matches", []))
    count = len(items) if isinstance(items, list) else 0
    # Extract file paths mentioned
    paths: list[str] = []
    if isinstance(items, list):
        for item in items[:5]:
            if isinstance(item, dict):
                p = item.get("path") or item.get("file") or item.get("file_path")
                if p and p not in paths:
                    paths.append(p)

    summary_parts = [f"file_search: {count} results"]
    if paths:
        summary_parts.append(f"Files: {', '.join(paths)}")
    summary_parts.append(f"Searched at iteration {iteration}")

    return json.dumps({
        "summary": ". ".join(summary_parts) + ".",
        "compressed": True,
    })


def _find_latest_read_per_file(messages: list[dict]) -> dict[str, int]:
    """Map file_path -> message index of the most recent read for that file."""
    latest: dict[str, int] = {}
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        if not _is_file_read_result(msg):
            continue
        path = _extract_file_path(msg)
        if path:
            latest[path] = i  # overwrites earlier reads
    return latest


def _estimate_iteration(msg_idx: int, total_messages: int, current_iteration: int) -> int:
    """Estimate which iteration a message was created at based on position.

    Uses linear interpolation: message position / total messages * current iteration.
    """
    if total_messages <= 1:
        return current_iteration
    return max(0, int(msg_idx / total_messages * current_iteration))


def compress_file_results(
    messages: list[dict],
    current_iteration: int,
    current_batch_start: int | None = None,
) -> list[dict]:
    """Compress old file_read results in message history.

    Args:
        messages: The conversation messages
        current_iteration: Current iteration number in the agent loop
        current_batch_start: Index in messages where the current iteration's
            tool results begin (these are never compressed)

    Returns:
        New list with compressed file results. Non-file-read messages unchanged.

    Rules:
    - Only activates after COMPRESSION_ITERATION_THRESHOLD iterations
    - Never compresses the most recent read of any file
    - Never compresses results from current iteration batch
    - Preserves error results
    - Already-compressed results pass through unchanged
    """
    if current_iteration < COMPRESSION_ITERATION_THRESHOLD:
        return messages

    if not messages:
        return messages

    latest_reads = _find_latest_read_per_file(messages)
    latest_indices = set(latest_reads.values())

    if current_batch_start is None:
        current_batch_start = len(messages)

    result = []
    compressed_count = 0

    for i, msg in enumerate(messages):
        # Only process tool messages
        if msg.get("role") != "tool":
            result.append(msg)
            continue

        # Never compress current batch
        if i >= current_batch_start:
            result.append(msg)
            continue

        # Not a file read/search result — pass through
        if not _is_file_read_result(msg):
            result.append(msg)
            continue

        # Already compressed — pass through
        if _is_already_compressed(msg):
            result.append(msg)
            continue

        # Preserve error results
        if _is_error_result(msg):
            result.append(msg)
            continue

        # Never compress the most recent read of a file
        if i in latest_indices:
            result.append(msg)
            continue

        # Too small to bother compressing
        content = msg.get("content", "")
        if _estimate_tokens(content) < MIN_TOKENS_TO_COMPRESS:
            result.append(msg)
            continue

        # Compress it
        parsed = _parse_content(msg)
        if not parsed:
            result.append(msg)
            continue

        iteration = _estimate_iteration(i, len(messages), current_iteration)

        if ("path" in parsed or "file_path" in parsed) and "content" in parsed:
            new_content = _build_file_summary(content, parsed, iteration)
        else:
            new_content = _build_search_summary(parsed, iteration)

        result.append({**msg, "content": new_content})
        compressed_count += 1

    if compressed_count > 0:
        logger.info(
            "Iteration-aware compression: compressed %d file results (iteration %d)",
            compressed_count, current_iteration,
        )

    return result
