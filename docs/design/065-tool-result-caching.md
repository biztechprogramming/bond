# Design Doc 065: Tool Result Caching with Content Hashing

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 012 (Context Distillation Pipeline)

---

## 1. Problem

Bond's agent frequently re-reads the same files and re-fetches the same URLs within a single session, especially during edit-test-fix loops. A typical coding session looks like:

1. Read file A (500 lines, ~4k tokens)
2. Edit file A
3. Run tests
4. Read file A again to check the edit (another ~4k tokens)
5. Read file B for context (300 lines, ~2.5k tokens)
6. Edit file A again
7. Read file A again (~4k tokens)
8. Read file B again for reference (~2.5k tokens)

In this 8-turn sequence, the agent consumed ~17k tokens on file reads. But file B never changed — that's 5k tokens wasted. And if the agent's edit to file A was a 2-line change, re-reading the entire 500-line file just to verify is wasteful when a compact diff would suffice.

Doc 062 (Headroom) tried to address this by compressing tool outputs, but explicitly excluded `file_read` — the single biggest token consumer — because the agent needs verbatim content for edits. Caching sidesteps this entirely: if the file hasn't changed, don't re-send it.

## 2. What Bond Gets

1. **Eliminate redundant file reads** — if a file hasn't changed since last read, return a compact reference instead of the full content
2. **Change-aware re-reads** — if the agent edited a file and re-reads it, return only the diff or the changed region + surrounding context
3. **URL content caching** — for `web_fetch`, cache by URL with a 5-minute TTL; skip re-fetching unchanged pages
4. **Token savings without quality loss** — the agent gets exactly the content it needs, no compression artifacts, no lost line numbers

## 3. Design

### Cache Key Structure

```
cache_key = hash(tool_name, normalized_args)
```

Where staleness is checked per-tool after a cache key match:

| Tool | Fingerprint | Staleness Check |
|------|-------------|-----------------|
| `file_read` | file mtime + size | `os.stat()` — <1ms |
| `web_fetch` | timestamp | Time-based TTL (5 min) — no network call on cache check |
| `web_search` | query + timestamp bucket (5min) | Time-based expiry |
| `shell_grep` | Not cached in Phase 1 | Glob/directory targets make staleness checks expensive; revisit after measuring file_read savings |
| `code_execute` | Never cached | Non-deterministic output. Triggers batch staleness revalidation of all cached file entries (see `revalidate_after_execute`). |

### Cache Hit Responses

When a cache hit occurs, the tool result is replaced with a compact reference:

```
📋 Cache hit: file_read("src/worker.py")
   Last read: turn 3 (47 lines ago in conversation)
   Status: UNCHANGED (mtime unchanged since last read)
   Content: 523 lines, 4,102 tokens
   
   To re-read the full file, call file_read with force=true.
```

If the file *has* changed (mtime differs), but the agent was the one who changed it (via `file_write` or `file_edit` in the same session):

```
📋 Cache hit: file_read("src/worker.py")  
   Last read: turn 3
   Status: MODIFIED by you in turn 5 (file_edit)
   Changes since last read:
   
   --- turn 3 version
   +++ current
   @@ -42,3 +42,5 @@
        def process(self):
   -        return self.run()
   +        result = self.run()
   +        self.log(result)
   +        return result
   
   Full file: 525 lines, 4,118 tokens
   To re-read the full file, call file_read with force=true.
```

This gives the agent everything it needs to continue working without re-consuming the full file content.

The diff is always computed as `cached_content ↔ current_file_on_disk`. Multiple agent edits between reads are collapsed into a single diff against the last-read version. If the diff exceeds 50 lines or 2,000 characters, the cache hit is skipped and the full file is returned instead (a large diff doesn't save meaningful tokens).

### Cache Miss Behavior

On a cache miss (first read, or `force=true`), the tool executes normally and the result is cached:

```python
@dataclass
class CachedToolResult:
    tool_name: str
    args_hash: str
    resolved_path: str       # canonical path for file tools, URL for web tools
    content: str             # the full result
    token_count: int
    fingerprint: str         # mtime+size for files, timestamp for web
    turn_number: int         # when this was cached
    timestamp: datetime
```

## 4. Implementation

### Phase 1: File Read Caching (~1.5 days)

**New file:** `backend/app/agent/tool_result_cache.py`

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os

# Phase 1: file_read only. web_fetch added in Phase 3.
CACHEABLE_TOOLS = frozenset({
    "file_read",
})

# Tools that invalidate cached file content.
# file_write and file_edit invalidate by path.
# code_execute triggers batch revalidation (see revalidate_after_execute).
FILE_MUTATING_TOOLS = frozenset({
    "file_write",
    "file_edit",
})

# Max diff size before we skip the cache hit and return full content.
MAX_DIFF_LINES = 50
MAX_DIFF_CHARS = 2000


class ToolResultCache:
    """Per-session cache for tool results."""
    
    def __init__(self):
        self._cache: dict[str, CachedToolResult] = {}
        self._file_mutations: dict[str, int] = {}  # resolved_path → turn of last mutation
    
    def check(self, tool_name: str, args: dict, turn: int) -> CachedToolResult | None:
        """Check if we have a valid cached result for this tool call."""
        if tool_name not in CACHEABLE_TOOLS:
            return None
        
        if args.get("force"):
            return None
        
        key = self._make_key(tool_name, args)
        cached = self._cache.get(key)
        if cached is None:
            return None
        
        # Validate freshness
        if not self._is_fresh(cached):
            del self._cache[key]
            return None
        
        return cached
    
    def _is_fresh(self, cached: CachedToolResult) -> bool:
        """Check if cached result is still valid."""
        if cached.tool_name == "file_read":
            try:
                stat = os.stat(cached.resolved_path)
                return cached.fingerprint == f"{stat.st_mtime}:{stat.st_size}"
            except OSError:
                # File was deleted — clean up mutation tracking too
                self._file_mutations.pop(cached.resolved_path, None)
                return False
        
        if cached.tool_name == "web_fetch":
            age = (datetime.utcnow() - cached.timestamp).total_seconds()
            return age < 300  # 5 minute TTL
        
        return False
    
    def store(self, tool_name: str, args: dict, result: str, turn: int):
        """Cache a tool result."""
        if tool_name not in CACHEABLE_TOOLS:
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
            timestamp=datetime.utcnow(),
        )
    
    def record_mutation(self, tool_name: str, args: dict, turn: int):
        """Record that a tool mutated a file (for change-aware re-reads)."""
        if tool_name in FILE_MUTATING_TOOLS:
            resolved = self._resolve_path(tool_name, args)
            if resolved:
                self._file_mutations[resolved] = turn
    
    def revalidate_after_execute(self):
        """Re-stat all cached file paths after code_execute; drop any that changed.
        
        code_execute can write files as a side effect. Rather than invalidating
        the entire cache, we re-check each cached file's mtime+size. This is
        O(n) stat calls where n <= TOOL_CACHE_MAX_ENTRIES — microseconds total.
        """
        for key, cached in list(self._cache.items()):
            if cached.tool_name == "file_read":
                if not self._is_fresh(cached):
                    del self._cache[key]
    
    def format_cache_hit(self, cached: CachedToolResult, current_turn: int) -> str:
        """Format a cache hit response for the agent."""
        mutation_turn = self._file_mutations.get(cached.resolved_path)
        
        if mutation_turn and mutation_turn > cached.turn_number:
            # File was modified by agent since last read — show diff
            return self._format_diff_response(cached, current_turn, mutation_turn)
        else:
            # File unchanged
            return self._format_unchanged_response(cached, current_turn)
    
    def _format_diff_response(self, cached: CachedToolResult, current_turn: int, mutation_turn: int) -> str:
        """Generate a diff between the cached content and current file on disk.
        
        If the diff is too large (>MAX_DIFF_LINES or >MAX_DIFF_CHARS),
        returns None to signal the caller should do a full re-read instead.
        """
        try:
            current_content = Path(cached.resolved_path).read_text()
        except OSError:
            return None  # file gone — force a real read to surface the error
        
        import difflib
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
            f"📋 Cache hit: file_read(\"{cached.resolved_path}\")\n"
            f"   Last read: turn {cached.turn_number}\n"
            f"   Status: MODIFIED by you in turn {mutation_turn}\n"
            f"   Changes since last read:\n\n"
            f"{diff_text}\n\n"
            f"   Full file: {current_line_count} lines, {current_tokens} tokens\n"
            f"   To re-read the full file, call file_read with force=true."
        )
    
    def _format_unchanged_response(self, cached: CachedToolResult, current_turn: int) -> str:
        """Format response for an unchanged file."""
        return (
            f"📋 Cache hit: file_read(\"{cached.resolved_path}\")\n"
            f"   Last read: turn {cached.turn_number}\n"
            f"   Status: UNCHANGED (mtime unchanged since last read)\n"
            f"   Content: {cached.content.count(chr(10)) + 1} lines, {cached.token_count} tokens\n\n"
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
                return f"{stat.st_mtime}:{stat.st_size}"
            except OSError:
                return ""
        if tool_name == "web_fetch":
            return ""  # web_fetch uses timestamp-based TTL, no fingerprint needed
        return ""
    
    @staticmethod
    def _make_key(tool_name: str, args: dict) -> str:
        """Generate a stable cache key from tool name and normalized args."""
        # Exclude 'force' from key so force=true hits the same slot
        filtered = {k: v for k, v in sorted(args.items()) if k != "force"}
        import json
        return f"{tool_name}:{json.dumps(filtered, sort_keys=True)}"
```

### Phase 2: Hook into Tool Execution (~0.5 days)

**File changed:** `backend/app/agent/loop.py` (or wherever tool results are processed)

Before executing a tool:
```python
cached = session.tool_cache.check(tool_name, args, current_turn)
if cached:
    result = session.tool_cache.format_cache_hit(cached, current_turn)
    if result is not None:  # None means diff was too large — do full read
        return result, 0.0  # no cost
```

After executing a tool:
```python
session.tool_cache.store(tool_name, args, result, current_turn)
session.tool_cache.record_mutation(tool_name, args, current_turn)

# code_execute can write files as a side effect — revalidate all cached files
if tool_name == "code_execute":
    session.tool_cache.revalidate_after_execute()
```

### Phase 3: Web Fetch Caching (~0.5 days)

Add `"web_fetch"` to `CACHEABLE_TOOLS`. The staleness check is already implemented in `_is_fresh` using a 5-minute TTL — no network call required on cache check. The fingerprint is unused for web_fetch; freshness is determined solely by `timestamp` age.

```python
# Updated set in Phase 3:
CACHEABLE_TOOLS = frozenset({
    "file_read",
    "web_fetch",
})
```

No HEAD requests. If the agent needs fresh content before the TTL expires, it uses `force=true`.

### Phase 4: Cache Stats and Observability (~0.5 days)

Log cache hit/miss rates per session. Expose in the optimization dashboard:

```python
@dataclass  
class CacheStats:
    hits: int = 0
    misses: int = 0
    tokens_saved: int = 0
    diff_too_large: int = 0  # times we fell back to full read due to diff size
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
```

## 5. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_CACHE_ENABLED` | `true` | Master switch |
| `TOOL_CACHE_MAX_ENTRIES` | `100` | Max cached results per session (LRU eviction) |
| `TOOL_CACHE_MAX_CONTENT_SIZE` | `50000` | Don't cache results larger than this (chars) — avoids memory bloat |
| `TOOL_CACHE_SHOW_DIFF` | `true` | Show diffs for agent-modified files on re-read |
| `TOOL_CACHE_WEB_TTL_SECONDS` | `300` | TTL for web_fetch cache entries |
| `TOOL_CACHE_MAX_DIFF_LINES` | `50` | Max diff lines before falling back to full read |
| `TOOL_CACHE_MAX_DIFF_CHARS` | `2000` | Max diff chars before falling back to full read |

## 6. Critical Design Decision: `force=true`

The agent can always bypass the cache by passing `force=true` to any tool call. The cache hit response explicitly tells the agent this is available. This ensures the cache is a *transparent optimization* — the agent is never locked out of fresh data.

The system prompt should include a note: "When you re-read a file and get a cache hit showing it's unchanged, trust it. If you need the full content for a specific reason, use force=true."

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Stale cache: file changed externally (another process, git pull) | Medium | mtime+size check catches this — mtime changes on any write. `os.stat()` is authoritative. |
| Agent confused by cache hit format | Medium | Cache hit format is explicit and tells the agent what to do. Test with eval suite. |
| Memory usage from caching large files | Low | `TOOL_CACHE_MAX_CONTENT_SIZE` caps per-entry size. LRU eviction caps total entries. |
| `code_execute` side effects not tracked | Medium | After every `code_execute`, `revalidate_after_execute()` re-stats all cached file paths and drops any that changed. O(n) stat calls where n ≤ 100 — microseconds. |
| Diff generation for large files is expensive | Low | Use Python's `difflib` — fast for typical file sizes. Diff output capped at 50 lines / 2k chars; larger diffs fall back to full re-read. |
| File deleted between reads | Low | `os.stat()` raises `OSError` → cache entry and mutation tracking cleaned up. Next read surfaces the real error from the tool. |

## 8. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| Cache hit rate for file_read | `CacheStats.hit_rate` | >40% within coding sessions |
| Tokens saved per session | `CacheStats.tokens_saved` | >20% reduction in file_read tokens |
| Agent re-read rate | Count of force=true calls | <10% of cached file reads need force |
| Diff fallback rate | `CacheStats.diff_too_large` | <5% of cache hits for modified files |
| Quality | Eval suite (especially edit-test-fix scenarios) | No regression |

## 9. Relationship to Prior Docs

- **Doc 012 (Context Distillation):** Caching reduces context volume *before* distillation kicks in. Complementary.
- **Doc 062 (Headroom):** This addresses the tools that Headroom explicitly excluded (`file_read`). The two approaches cover different parts of the problem. Both can coexist — Headroom compresses cacheable-miss results (execution output, web content), caching eliminates redundant reads entirely.
- **Doc 029 (LLM Call Efficiency):** Caching directly reduces token consumption per turn.
