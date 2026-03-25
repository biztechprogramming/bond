"""Result interceptor for automatic context indexing (Design Doc 075).

Sits between rule_based_prune() and filter_tool_result() in the tool result
pipeline. Measures cleaned output size, routes to the appropriate tier handler,
and indexes large outputs into the per-conversation FTS5 database.

Size tiers:
- Tier 1 (< 4KB):   Pass through unchanged
- Tier 2 (4-16KB):  Pass through + index in background
- Tier 3 (16-64KB): Index + summarize via utility model
- Tier 4 (> 64KB):  Index + summarize + warning
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from backend.app.agent.context_store import ContextStore

logger = logging.getLogger(__name__)

# File extensions that are considered "code" — these get normal (1×) decay
# even when indexed, because they are working material the agent references
# across multiple turns.
CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt",
    ".cs", ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".swift", ".scala",
    ".sh", ".bash", ".zsh", ".yaml", ".yml", ".toml", ".json", ".xml",
    ".html", ".css", ".scss", ".sql", ".md", ".txt", ".cfg", ".ini",
    ".env", ".dockerfile", ".tf", ".hcl", ".proto", ".graphql", ".vue",
    ".svelte",
})


def _is_code_file(tool_name: str, tool_args: dict) -> bool:
    """Check if the tool result is from a code file based on extension."""
    if tool_name not in ("file_read", "file_open", "file_view"):
        return False
    path = tool_args.get("path", tool_args.get("file_path", ""))
    if not path:
        return False
    # Handle dotfiles and names like "Dockerfile"
    dot_idx = path.rfind(".")
    if dot_idx == -1:
        # Check for extensionless known files
        basename = path.rsplit("/", 1)[-1].lower()
        return basename in ("dockerfile", "makefile", "rakefile", "gemfile")
    ext = path[dot_idx:].lower()
    return ext in CODE_EXTENSIONS


# Size thresholds in bytes
TIER_2_THRESHOLD = 4 * 1024      # 4KB
TIER_3_THRESHOLD = 16 * 1024     # 16KB
TIER_4_THRESHOLD = 64 * 1024     # 64KB

# Cache of ContextStore instances per conversation
_stores: dict[str, ContextStore] = {}


def get_store(conversation_id: str) -> ContextStore:
    """Get or create a ContextStore for a conversation."""
    if conversation_id not in _stores:
        _stores[conversation_id] = ContextStore(conversation_id)
    return _stores[conversation_id]


def close_store(conversation_id: str) -> None:
    """Close and remove a store from cache."""
    store = _stores.pop(conversation_id, None)
    if store:
        store.close()


def _measure_output(result: dict) -> int:
    """Measure the byte size of a tool result."""
    return len(json.dumps(result).encode("utf-8"))


def _extract_text_content(result: dict) -> str:
    """Extract the main text content from a tool result for indexing."""
    # Try common content fields
    for key in ("content", "stdout", "output", "result", "data"):
        val = result.get(key)
        if isinstance(val, str) and val:
            return val
    # Fallback: serialize the whole thing
    return json.dumps(result)


def get_tier(size_bytes: int) -> int:
    """Determine the size tier for a result."""
    if size_bytes < TIER_2_THRESHOLD:
        return 1
    elif size_bytes < TIER_3_THRESHOLD:
        return 2
    elif size_bytes < TIER_4_THRESHOLD:
        return 3
    else:
        return 4


async def intercept_tool_result(
    tool_name: str,
    tool_args: dict,
    result: dict,
    conversation_id: str,
    turn_number: int = 0,
    raw: bool = False,
) -> tuple[dict, bool]:
    """Intercept a tool result and apply tier-based handling.

    Args:
        tool_name: Name of the tool that produced the result.
        tool_args: Arguments the tool was called with.
        result: The tool result dict (already rule_based_pruned).
        conversation_id: Current conversation ID.
        turn_number: Current agent loop turn number.
        raw: If True, bypass summarization (still indexes).

    Returns:
        Tuple of (result_dict, indexed) where indexed indicates whether
        the content was indexed into FTS5.
    """
    size_bytes = _measure_output(result)
    tier = get_tier(size_bytes)

    if tier == 1:
        return result, False

    content = _extract_text_content(result)
    store = get_store(conversation_id)

    is_code = _is_code_file(tool_name, tool_args)

    if tier == 2:
        # Fire-and-forget background indexing
        asyncio.create_task(
            _background_index(store, content, tool_name, tool_args, turn_number)
        )
        # Pass through unchanged, but mark as indexed for accelerated decay
        indexed_result = dict(result)
        indexed_result["_indexed"] = True
        if is_code:
            indexed_result["_indexed_code"] = True
        return indexed_result, True

    # Tier 3 and 4: index synchronously (needed before we can return summary)
    source_id = store.index(content, tool_name, tool_args, turn_number)

    if raw:
        indexed_result = dict(result)
        indexed_result["_indexed"] = True
        if is_code:
            indexed_result["_indexed_code"] = True
        return indexed_result, True

    # Build summary header
    line_count = content.count("\n") + 1
    size_kb = size_bytes / 1024

    if tier == 4:
        header = f"⚠️ Very large output indexed: {tool_name} — {line_count:,} lines, {size_kb:.0f}KB"
    else:
        header = f"📋 Indexed: {tool_name} — {line_count:,} lines, {size_kb:.0f}KB"

    # Build search hint from tool args
    search_hints = []
    for key in ("path", "file_path", "pattern", "query", "code", "command"):
        if key in tool_args:
            val = str(tool_args[key])
            # Extract meaningful search terms
            words = [w for w in val.split() if len(w) > 3 and not w.startswith("-")][:3]
            search_hints.extend(words)

    hint_str = ", ".join(f'"{h}"' for h in search_hints[:3]) if search_hints else '"your query"'

    summary_result = {
        "_indexed": True,
        "_tier": tier,
        "_source_id": source_id,
        "_summary": header,
        "_search_hint": f"Use ctx_search(queries=[{hint_str}]) to see full details.",
    }
    if is_code:
        summary_result["_indexed_code"] = True

    if tier == 4:
        summary_result["_warning"] = (
            "This output was very large. Use specific ctx_search queries to find what you need. "
            "Do NOT re-run this command — the full output is already indexed."
        )

    return summary_result, True


async def _background_index(
    store: ContextStore,
    content: str,
    tool_name: str,
    tool_args: dict,
    turn_number: int,
) -> None:
    """Background indexing task for Tier 2 results."""
    try:
        # Run in executor to avoid blocking the event loop with SQLite I/O
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, store.index, content, tool_name, tool_args, turn_number,
        )
    except Exception as e:
        logger.warning("Background indexing failed for %s: %s", tool_name, e)
