# Review: Design Doc 065 — Tool Result Caching with Content Hashing

**Reviewer:** Bond AI  
**Date:** 2026-03-24  
**Verdict:** ✅ **YES — Implement with modifications**

---

## 1. Executive Summary

This is a **well-motivated, practically useful design** that addresses a real and measurable problem: redundant file reads burning tokens during edit-test-fix loops. The core idea — cache tool results keyed by tool+args, validate freshness via `os.stat()`, and return compact references or diffs on cache hits — is sound and should deliver meaningful token savings (likely 15-30% on file_read tokens in typical coding sessions).

**Recommendation: Implement**, but with several modifications to address correctness edge cases, agent behavior risks, and a simpler phasing strategy.

---

## 2. Strengths

### 2.1 Well-Scoped Problem Statement
The opening example (8-turn edit-test-fix loop with 17k tokens on file reads) is concrete and quantifiable. The doc correctly identifies that Doc 062 (Headroom) explicitly excluded `file_read` — the biggest token consumer — making this a natural complement.

### 2.2 Smart Staleness Strategy
Using `mtime + size` as the fingerprint for file freshness is the right call. It's:
- **Fast** — `os.stat()` is <1ms, no I/O beyond a single syscall
- **Reliable** — catches all writes (agent or external)
- **Simple** — no content hashing needed (avoids re-reading the file just to check if it changed)

### 2.3 Change-Aware Re-Reads with Diffs
The diff-on-cache-hit behavior for agent-modified files is genuinely clever. Instead of "unchanged, trust me" or "here's the whole file again," the agent gets a compact diff showing exactly what changed. This is the sweet spot between saving tokens and maintaining agent situational awareness.

### 2.4 Graceful Fallbacks
- `force=true` escape hatch ensures the agent is never locked out
- Large diffs (>50 lines / 2k chars) fall back to full re-read — avoids sending a diff that's bigger than the file
- `revalidate_after_execute()` handles `code_execute` side effects without nuking the entire cache

### 2.5 Clear Configuration Surface
The config table with sensible defaults (`TOOL_CACHE_MAX_ENTRIES=100`, `MAX_CONTENT_SIZE=50000`) shows the author thought about operational concerns.

---

## 3. Concerns & Weaknesses

### 3.1 🔴 Critical: Partial File Reads Are Not Addressed

**Problem:** `file_read` supports `line_start` and `line_end` parameters for reading specific ranges. The cache key is `hash(tool_name, normalized_args)`, which means:
- `file_read("worker.py", line_start=1, line_end=50)` and `file_read("worker.py")` are **different cache keys**
- A full-file read won't serve a subsequent range read (cache miss), and vice versa
- Worse: the agent reads lines 1-50, then later reads the full file — the full-file result is cached, but if the agent then reads lines 1-50 again, it's a miss because the args differ

**Fix:** Cache should be keyed by `(tool_name, resolved_path)` for file tools, storing the **full file content**. Range reads should check if a full-file cache entry exists and extract the requested range from it. If only a partial read is cached and a full read is requested, that's a miss. This is simpler and more effective than per-args caching for file operations.

### 3.2 🔴 Critical: Race Condition in mtime Check

**Problem:** The freshness check reads `os.stat()`, but between the stat check and the cache hit being returned to the agent, another process (or `code_execute` in a parallel tool call) could modify the file. The window is small but real, especially with parallel tool execution.

**Fix:** This is inherent to any cache and the window is microseconds. Acknowledge it in the doc and note that `force=true` is the escape hatch. The practical risk is low — but the doc should explicitly call out that this is an accepted tradeoff, not an oversight.

### 3.3 🟡 Major: Agent Behavior with Cache Hits Is Untested and Risky

**Problem:** The design assumes the LLM will correctly interpret cache hit messages like `📋 Cache hit: file_read("src/worker.py") ... UNCHANGED`. But:
- The agent may not trust the cache and spam `force=true` on every read, negating all savings
- The agent may hallucinate file content based on stale memory when told "unchanged" instead of re-reading
- The diff format may confuse the agent into making incorrect edits (applying edits to the wrong version)

This is the **single biggest risk** to the feature's success.

**Fix:**
1. **Run an eval suite BEFORE full rollout** — specifically edit-test-fix scenarios where cache hits will fire. Measure: does the agent make correct edits? Does it over-use `force=true`?
2. **Start with a shadow mode** — compute cache hits but still return full content. Log what *would have been* saved. This validates the hit rate and token savings claims without risking agent quality.
3. **Tune the system prompt carefully** — the doc mentions adding a prompt note but doesn't specify exact wording. This needs to be treated as a first-class design decision, not an afterthought.

### 3.4 🟡 Major: `_file_mutations` Tracks Only Path, Not Content

**Problem:** `record_mutation()` records that a file was mutated at a given turn, but doesn't record *what* changed. The diff is computed at cache-hit time by comparing `cached.content` to the current file on disk. If the file was mutated multiple times between reads, the diff shows the cumulative change — which is correct. But if an **external process** (not tracked in `_file_mutations`) modifies the file, the mtime check will correctly detect staleness, but the code path goes to `_format_unchanged_response` (no mutation recorded) and returns "UNCHANGED" — which is wrong. The file changed, just not by the agent.

**Fix:** When `_is_fresh()` returns `True` (mtime matches), the file genuinely hasn't changed — this is correct. When `_is_fresh()` returns `False`, the entry is deleted and it's a cache miss — also correct. So actually, on closer analysis, this concern is **not a bug** — the mtime check is the ground truth, and `_file_mutations` is only used to provide richer messaging (showing which turn the agent edited it). The doc should make this clearer to avoid confusion during code review.

### 3.5 🟡 Major: No Cache Warming or Preloading Strategy

**Problem:** The cache only helps on the *second* read of a file. In many sessions, the agent reads 10-15 different files but only re-reads 3-4 of them. The hit rate may be lower than the projected 40%.

**Fix:** 
- Track actual re-read patterns across real sessions before committing to the 40% hit rate target
- Consider **conversation-resumption warming**: when a session is resumed, pre-populate the cache from the last N file reads in the conversation history (if files haven't changed)

### 3.6 🟢 Minor: Token Counting Function Not Specified

**Problem:** `_count_tokens(result)` is called but never defined. Token counting is model-dependent (cl100k_base for GPT-4, different for Claude). An inaccurate count makes `tokens_saved` metrics unreliable.

**Fix:** Use a simple heuristic (`len(text) / 4`) for the cache's internal metrics. Exact counts aren't needed for cache decisions — they're only used for reporting.

### 3.7 🟢 Minor: LRU Eviction Not Implemented in the Code

**Problem:** The doc mentions `TOOL_CACHE_MAX_ENTRIES=100` with LRU eviction, but the implementation code uses a plain `dict` with no eviction logic.

**Fix:** Use `functools.lru_cache` or implement a simple `OrderedDict`-based LRU. Alternatively, since 100 entries of cached file content (capped at 50k chars each) could use up to 5MB of memory, consider whether a smaller default (e.g., 50) or size-based eviction (total cached bytes) would be more appropriate.

### 3.8 🟢 Minor: `web_fetch` Uses `datetime.utcnow()` (Deprecated)

**Problem:** `datetime.utcnow()` is deprecated in Python 3.12+. Should use `datetime.now(timezone.utc)`.

**Fix:** Use `datetime.now(timezone.utc)` throughout.

---

## 4. Specific Improvements & Recommendations

### 4.1 Add Shadow Mode as Phase 0

Before any agent-visible changes, add a shadow/logging-only phase:

```python
class ToolResultCache:
    def __init__(self, shadow_mode: bool = True):
        self._shadow_mode = shadow_mode
    
    def check(self, tool_name, args, turn):
        cached = self._lookup(tool_name, args)
        if cached and self._is_fresh(cached):
            self._stats.hits += 1
            self._stats.tokens_saved += cached.token_count
            if self._shadow_mode:
                return None  # Don't actually return cache hit — just log it
            return cached
        self._stats.misses += 1
        return None
```

Run shadow mode for 1-2 weeks, collect data on hit rates and token savings, then enable real caching with confidence.

### 4.2 Key by Resolved Path for File Tools, Not by Full Args

```python
def _make_key(self, tool_name: str, args: dict) -> str:
    if tool_name in ("file_read", "file_write", "file_edit"):
        # Key by path only — we cache the full file content
        # and serve range requests from the cached content
        return f"{tool_name}:{self._resolve_path(tool_name, args)}"
    filtered = {k: v for k, v in sorted(args.items()) if k != "force"}
    return f"{tool_name}:{json.dumps(filtered, sort_keys=True)}"
```

### 4.3 Include First/Last N Lines in "Unchanged" Cache Hits

A bare "UNCHANGED" message gives the agent zero content to work with. Consider including a brief excerpt:

```
📋 Cache hit: file_read("src/worker.py")
   Last read: turn 3
   Status: UNCHANGED
   Content: 523 lines, 4,102 tokens
   
   First 5 lines:
   | 1: import os
   | 2: from pathlib import Path
   | 3: 
   | 4: class Worker:
   | 5:     def __init__(self, config):
   
   To re-read the full file, call file_read with force=true.
```

This helps the agent confirm it's thinking of the right file without needing `force=true`.

### 4.4 Add Cache Stats to Conversation Metadata

Expose `CacheStats` in the conversation metadata/debug panel so developers can see cache performance in real time. This is essential for validating the feature post-launch.

### 4.5 Consider Content Hashing as a Secondary Check

`mtime + size` can have false positives in rare cases (file rewritten with identical size at the same mtime granularity). Adding a fast content hash (e.g., `xxhash` of the first and last 1KB) as a secondary check would eliminate this edge case with negligible cost.

---

## 5. Alternative Approaches Considered

### 5.1 Context Window Deduplication (LLM-Side)
Instead of caching at the tool layer, deduplicate at the context assembly layer — detect when the same file content appears multiple times in the conversation and replace earlier occurrences with "[file content shown above]".

**Tradeoff:** Simpler to implement, but doesn't reduce API token costs (the content was already sent in earlier turns). Caching prevents the tokens from being generated at all. **Caching is better.**

### 5.2 File Watcher (inotify) Instead of Stat-Based Checking
Use `inotify` (Linux) or `watchdog` to get push notifications when files change, instead of polling with `os.stat()`.

**Tradeoff:** More complex, platform-dependent, and doesn't improve correctness since `os.stat()` is already authoritative and fast. **Stat-based is the right choice.**

### 5.3 Semantic Caching (Embedding-Based)
Cache based on semantic similarity of the request rather than exact args match. E.g., "read the worker file" and "show me worker.py" would be cache hits.

**Tradeoff:** Massive complexity for marginal benefit. The agent already normalizes tool calls to exact paths. **Not worth it.**

### 5.4 Do Nothing — Rely on Prompt Engineering
Just tell the agent "don't re-read files you've already read" in the system prompt.

**Tradeoff:** Unreliable. LLMs frequently re-read files even when instructed not to, especially in long sessions. The current Bond system prompt already has guidance about this and agents still re-read. **Caching is necessary.**

---

## 6. Implementation Risk Assessment

### 6.1 Complexity vs. Estimates
The doc estimates **Phase 1 at 1.5 days** and **Phase 2 at 0.5 days**. These are **optimistic but achievable** if:
- The tool execution pipeline has clean hook points (pre/post execution)
- No major refactoring is needed to thread the cache through

If the tool dispatch is deeply nested or spread across multiple files, add 1 day for integration work.

### 6.2 Key Integration Points
- **Tool execution loop** — needs pre-check (cache lookup) and post-execution (cache store) hooks
- **System prompt** — needs cache-awareness instructions
- **`file_edit` / `file_write` handlers** — need to call `record_mutation()`
- **`code_execute` handler** — needs to call `revalidate_after_execute()`

### 6.3 Testing Strategy Gaps
The doc mentions an "eval suite" for quality validation but doesn't specify:
- **Unit tests** for the cache itself (hit/miss/eviction/staleness)
- **Integration tests** for the tool execution hooks
- **Regression tests** for edge cases (file deleted between reads, symlinks, binary files, very large files)

**Recommendation:** Write the unit tests for `ToolResultCache` as part of Phase 1. They're straightforward and will catch bugs early.

### 6.4 Rollback Plan
Not mentioned in the doc. The `TOOL_CACHE_ENABLED=false` config switch is the rollback mechanism — this should be explicitly called out as the rollback plan.

---

## 7. Verdict

### ✅ Implement — with these prerequisites:

1. **Start with Shadow Mode (Phase 0)** — Log cache hits/misses for 1-2 weeks without changing agent behavior. Validate hit rate and token savings projections.

2. **Fix the partial-read cache key issue** (Section 3.1) — Key file tool cache entries by resolved path, not full args. Serve range requests from cached full-file content.

3. **Run agent behavior evals before enabling** (Section 3.3) — The biggest risk isn't the cache logic, it's whether the LLM handles cache hit messages correctly. Test this explicitly.

4. **Add LRU eviction to the implementation** (Section 3.7) — The code as written will grow unbounded.

5. **Write unit tests as part of Phase 1** (Section 6.3) — The cache is a correctness-critical component; it needs tests.

### Why This Is Worth Doing

The problem is real (redundant reads waste 15-30% of file_read tokens), the solution is architecturally clean (transparent caching layer with escape hatch), and the implementation is modest (~2-3 days). The main risk is agent behavior, which shadow mode mitigates. This is a high-value, low-risk improvement to Bond's token efficiency.
