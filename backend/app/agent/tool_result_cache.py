"""Tool result caching with content hashing (Design Doc 065).

Per-session cache for tool results. Eliminates redundant file reads by
returning compact references when content hasn't changed, or diffs when
the agent itself modified the file.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bond.agent.tool_result_cache")

# Phase 1: file_read only. web_fetch added in Phase 3.
CACHEABLE_TOOLS = frozenset({"file_read"})

# Tools that invalidate cached file content.
FILE_MUTATING_TOOLS = frozenset({"file_write", "file_edit"})

# Max diff size before we skip the cache hit and return full content.
MAX_DIFF_LINES = 50
MAX_DIFF_CHARS = 2000

TOOL_CACHE_MAX_ENTRIES = 100
TOOL_CACHE_MAX_CONTENT_SIZE = 50000


@dataclass
class CachedToolResult:
    tool_name: str
    args_hash: str
    resolved_path: str
    content: str
    token_count: int
    fingerprint: str  # "mtime:size" for files
    turn_number: int
    timestamp: datetime


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    tokens_saved: int = 0
    diff_too_large: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


def _count_tokens(text: str) -> int:
    """Simple token count estimate."""
    return len(text) // 4


class ToolResultCache:
    """Per-session cache for tool results."""

    def __init__(self, shadow_mode: bool = False):
        self._cache: OrderedDict[str, CachedToolResult] = OrderedDict()
        self._file_mutations: dict[str, int] = {}  # resolved_path -> turn of last mutation
        self._shadow_mode = shadow_mode
        self._stats = CacheStats()

    @property
    def stats(self) -> CacheStats:
        return self._stats

    def check(self, tool_name: str, args: dict, turn: int) -> CachedToolResult | None:
        """Check if we have a valid cached result for this tool call."""
        if tool_name not in CACHEABLE_TOOLS:
            return None

        if args.get("force"):
            return None

        key = self._make_key(tool_name, args)
        cached = self._cache.get(key)
        if cached is None:
            self._stats.misses += 1
            logger.info("Cache miss: %s(%s)", tool_name, self._resolve_path(tool_name, args))
            return None

        # Validate freshness
        if not self._is_fresh(cached):
            del self._cache[key]
            self._stats.misses += 1
            logger.info("Cache miss (stale): %s(%s)", tool_name, cached.resolved_path)
            return None

        # Move to end for LRU tracking
        self._cache.move_to_end(key)

        self._stats.hits += 1
        self._stats.tokens_saved += cached.token_count

        logger.info(
            "Cache hit: %s(%s) — %d tokens saved",
            tool_name, cached.resolved_path, cached.token_count,
        )

        # Shadow mode: log the hit but don't actually return cached result
        if self._shadow_mode:
            return None

        # Handle partial file reads from cached full content
        if tool_name == "file_read" and (args.get("line_start") or args.get("line_end")):
            return self._extract_range(cached, args)

        return cached

    def store(self, tool_name: str, args: dict, result: str, turn: int):
        """Cache a tool result."""
        if tool_name not in CACHEABLE_TOOLS:
            return

        if len(result) > TOOL_CACHE_MAX_CONTENT_SIZE:
            logger.debug("Skipping cache store: result too large (%d chars)", len(result))
            return

        resolved_path = self._resolve_path(tool_name, args)
        key = self._make_key(tool_name, args)
        fingerprint = self._get_fingerprint(tool_name, resolved_path)

        self._cache[key] = CachedToolResult(
            tool_name=tool_name,
            args_hash=key,
            resolved_path=resolved_path,
            content=result,
            token_count=_count_tokens(result),
            fingerprint=fingerprint,
            turn_number=turn,
            timestamp=datetime.now(timezone.utc),
        )

        # Move to end (most recently used) and enforce LRU eviction
        self._cache.move_to_end(key)
        while len(self._cache) > TOOL_CACHE_MAX_ENTRIES:
            evicted_key, _ = self._cache.popitem(last=False)
            logger.info("Cache eviction: %s", evicted_key)

    def record_mutation(self, tool_name: str, args: dict, turn: int):
        """Record that a tool mutated a file (for change-aware re-reads)."""
        if tool_name in FILE_MUTATING_TOOLS:
            resolved = self._resolve_path(tool_name, args)
            if resolved:
                self._file_mutations[resolved] = turn

    def revalidate_after_execute(self):
        """Re-stat all cached file paths after code_execute; drop any that changed."""
        dropped = 0
        for key, cached in list(self._cache.items()):
            if cached.tool_name == "file_read":
                if not self._is_fresh(cached):
                    del self._cache[key]
                    dropped += 1
        logger.info("Revalidation after code_execute: dropped %d/%d entries", dropped, dropped + len(self._cache))

    def format_cache_hit(self, cached: CachedToolResult, current_turn: int) -> str | None:
        """Format a cache hit response for the agent.

        Returns None if the diff is too large (caller should do a full re-read).
        """
        mutation_turn = self._file_mutations.get(cached.resolved_path)

        if mutation_turn and mutation_turn > cached.turn_number:
            # File was modified by agent since last read — show diff
            result = self._format_diff_response(cached, current_turn, mutation_turn)
            if result is None:
                self._stats.diff_too_large += 1
            return result
        else:
            # File unchanged
            return self._format_unchanged_response(cached, current_turn)

    def _format_diff_response(
        self, cached: CachedToolResult, current_turn: int, mutation_turn: int
    ) -> str | None:
        """Generate a diff between the cached content and current file on disk."""
        try:
            current_content = Path(cached.resolved_path).read_text()
        except OSError:
            return None  # file gone — force a real read to surface the error

        diff_lines = list(difflib.unified_diff(
            cached.content.splitlines(keepends=True),
            current_content.splitlines(keepends=True),
            fromfile=f"turn {cached.turn_number} version",
            tofile="current",
        ))

        diff_text = "".join(diff_lines)
        if len(diff_lines) > MAX_DIFF_LINES or len(diff_text) > MAX_DIFF_CHARS:
            return None  # diff too large — caller should do full re-read

        current_line_count = current_content.count("\n") + 1
        current_tokens = _count_tokens(current_content)

        return (
            f"\U0001f4cb Cache hit: file_read(\"{cached.resolved_path}\")\n"
            f"   Last read: turn {cached.turn_number}\n"
            f"   Status: MODIFIED by you in turn {mutation_turn}\n"
            f"   Changes since last read:\n\n"
            f"{diff_text}\n\n"
            f"   Full file: {current_line_count} lines, {current_tokens} tokens\n"
            f"   To re-read the full file, call file_read with force=true."
        )

    def _format_unchanged_response(self, cached: CachedToolResult, current_turn: int) -> str:
        """Format response for an unchanged file, including a brief excerpt."""
        content_lines = cached.content.splitlines()
        line_count = len(content_lines)

        excerpt_lines = content_lines[:5]
        excerpt = "\n".join(f"   | {i + 1}: {line}" for i, line in enumerate(excerpt_lines))

        return (
            f"\U0001f4cb Cache hit: file_read(\"{cached.resolved_path}\")\n"
            f"   Last read: turn {cached.turn_number}\n"
            f"   Status: UNCHANGED (mtime unchanged since last read)\n"
            f"   Content: {line_count} lines, {cached.token_count} tokens\n\n"
            f"   First 5 lines:\n"
            f"{excerpt}\n\n"
            f"   To re-read the full file, call file_read with force=true."
        )

    def _resolve_path(self, tool_name: str, args: dict) -> str:
        """Extract and canonicalize the target path/URL from tool args."""
        if tool_name in ("file_read", "file_write", "file_edit"):
            raw = args.get("path", args.get("file_path", ""))
            return str(Path(raw).resolve()) if raw else ""
        if tool_name == "web_fetch":
            return args.get("url", "")
        return ""

    def _get_fingerprint(self, tool_name: str, resolved_path: str) -> str:
        """Compute the freshness fingerprint for a cached entry."""
        if tool_name == "file_read":
            try:
                stat = os.stat(resolved_path)
                fp = f"{stat.st_mtime}:{stat.st_size}"
                logger.debug("Fingerprint for %s: %s", resolved_path, fp)
                return fp
            except OSError:
                return ""
        return ""

    def _make_key(self, tool_name: str, args: dict) -> str:
        """Generate a stable cache key from tool name and args."""
        if tool_name in ("file_read", "file_write", "file_edit"):
            key = f"{tool_name}:{self._resolve_path(tool_name, args)}"
            logger.debug("Cache key (file): %s", key)
            return key
        # Non-file tools: key by full normalized args (excluding "force")
        filtered = {k: v for k, v in sorted(args.items()) if k != "force"}
        key = f"{tool_name}:{json.dumps(filtered, sort_keys=True)}"
        logger.debug("Cache key (non-file): %s", key)
        return key

    def _is_fresh(self, cached: CachedToolResult) -> bool:
        """Check if cached result is still valid."""
        if cached.tool_name == "file_read":
            try:
                stat = os.stat(cached.resolved_path)
                current_fp = f"{stat.st_mtime}:{stat.st_size}"
                logger.debug(
                    "Freshness check: cached=%s current=%s",
                    cached.fingerprint, current_fp,
                )
                return cached.fingerprint == current_fp
            except OSError:
                self._file_mutations.pop(cached.resolved_path, None)
                return False

        if cached.tool_name == "web_fetch":
            age = (datetime.now(timezone.utc) - cached.timestamp).total_seconds()
            return age < 300  # 5 minute TTL

        return False

    def _extract_range(self, cached: CachedToolResult, args: dict) -> CachedToolResult:
        """Extract a line range from a cached full-file result."""
        lines = cached.content.splitlines(keepends=True)
        start = (args.get("line_start") or 1) - 1  # convert to 0-indexed
        end = args.get("line_end") or len(lines)
        extracted = "".join(lines[start:end])
        return CachedToolResult(
            tool_name=cached.tool_name,
            args_hash=cached.args_hash,
            resolved_path=cached.resolved_path,
            content=extracted,
            token_count=_count_tokens(extracted),
            fingerprint=cached.fingerprint,
            turn_number=cached.turn_number,
            timestamp=cached.timestamp,
        )
