# Design Doc 062: Headroom Context Compression Integration

**Status:** Draft (v2 — revised after review)  
**Date:** 2026-03-23  
**Depends on:** 012 (Context Distillation Pipeline)  
**Reference:** [chopratejas/headroom](https://github.com/chopratejas/headroom) — `pip install headroom-ai`

---

## 1. Problem

Bond compresses context in two places:

| Module | What it does | Limitation |
|--------|-------------|------------|
| `context_pipeline.py` | Sliding window + LLM-based summarization of old messages | Costs an LLM call; only handles conversation history |
| `tool_result_filter.py` | Rule-based pruning, then utility-model LLM filter for large tool outputs (>6k chars) | LLM filter costs tokens + latency per result; no structural awareness of content type |

The rule-based pruning stage is solid (ANSI stripping, blank-line collapse, grep capping, stdout truncation). The problem is the *second* stage: when rule-based pruning isn't enough, Bond calls a utility model (Sonnet) to extract relevant parts. That's a full inference call per large tool result.

Headroom provides **content-type-aware compression without LLM calls**:

- **SmartCrusher** — statistical analysis of JSON/tabular data, keeps anomalies and boundaries
- **AST-aware code compression** — Python, JS, Go, Rust, Java, C++ (via tree-sitter)
- Core compressors are CPU-only, <50ms, no ML dependencies

## 2. What Bond Gets

**One thing: eliminate the utility-model LLM call for tool result filtering on safe-to-compress tools.**

Bond's existing rule-based pruning stays. Headroom slots in between the rule-based stage and the LLM filter as a second attempt. If Headroom compresses the result below threshold, the LLM call is skipped entirely.

## 3. Critical Constraint: File Content Must Not Be Compressed

**Prior experience:** When code was compressed in earlier experiments, the agent couldn't find specific lines for edits. It had to re-read files — wasting more tokens than compression saved and causing edit loops.

The agent needs **verbatim content with accurate line numbers** for any tool result it may act on with exact-text edits. Compression that removes lines, collapses function bodies, or restructures output breaks the `edit` tool's `oldText` matching.

### Tool Safety Classification

| Tool | Compress? | Reasoning |
|------|-----------|-----------|
| `code_execute` (stdout/stderr) | ✅ Yes | Read-only diagnostic output, never edited |
| `web_fetch` / `web_search` | ✅ Yes | Reference material, never edited |
| `shell_grep` (large results) | ✅ Yes | Used to decide what to read next, not edited directly |
| Build/test output | ✅ Yes | Diagnostic, agent extracts errors and moves on |
| `file_read` | ❌ **Never** | Agent needs exact text + line numbers for edits |
| `shell_cat` / equivalent | ❌ **Never** | Same as file_read |
| `project_search` | ❌ **Never** | Agent may copy-paste identifiers from results |

`file_read` and file-content tools are added to `SKIP_HEADROOM` — a new exclusion set separate from the existing `SKIP_TOOLS` (which skips *all* filtering).

### Honest Scope Assessment

The tools where compression is **safest** (execution output, search results, web fetches) overlap with tools where Bond's existing rule-based pruning already handles the common cases. The tools where compression would save the **most tokens** (large code files) are exactly the ones that break agent workflow when compressed.

The remaining value is real but narrower than a naive analysis suggests:
- Large JSON API responses from `code_execute` — real savings, safe
- Verbose build/test output (hundreds of lines) — real savings, safe
- Skipping the LLM utility-model call for those cases — cost + latency savings

## 4. Integration Architecture

```
    tool result arrives
           │
           ▼
    ┌──────────────┐
    │  SKIP_TOOLS?  │──yes──▶ return as-is (existing behavior)
    │  (too small?) │
    └──────┬───────┘
           │ no
           ▼
    ┌──────────────┐
    │  rule_based   │──modified──▶ check size again
    │  _prune()     │                │
    └──────┬───────┘           small enough? ──yes──▶ return
           │ no change              │ no
           ▼                        ▼
    ┌──────────────────┐    ┌──────────────────┐
    │  SKIP_HEADROOM?  │    │  SKIP_HEADROOM?  │
    │  (file_read etc) │    │  (file_read etc) │
    └──────┬───────────┘    └──────┬───────────┘
           │ no                     │ no
           ▼                        ▼
    ┌──────────────────────────────────┐
    │  Headroom compress()             │
    │  (SmartCrusher / content-aware)  │
    └──────┬───────────────────────────┘
           │
           │ compressed below threshold?
           │
      yes ─┤─── no
           │         │
    return  │    ┌────▼─────────────────┐
    result  │    │  Existing LLM filter  │  ← fallback (unchanged)
            │    │  (utility model call) │
            │    └──────────────────────┘
            ▼
```

The LLM filter code path is **kept intact** as a fallback. Headroom is an optimization layer, not a replacement.

## 5. Implementation

**Scope: ~30 lines of new code in one file.**

**File changed:** `backend/app/agent/tool_result_filter.py`

```python
# New exclusion set: tools where compression would break agent workflow
SKIP_HEADROOM = frozenset({
    "file_read",       # Agent needs verbatim text + line numbers for edits
    "file_write",      # Already in SKIP_TOOLS but belt-and-suspenders
    "project_search",  # Agent may copy-paste identifiers from results
})


def _headroom_compress(raw_json: str, tool_name: str) -> str | None:
    """Attempt Headroom compression. Returns compressed text or None to fall through.
    
    Uses tool-role message format so Headroom's pipeline treats it as tool output.
    """
    if tool_name in SKIP_HEADROOM:
        return None

    try:
        from headroom import compress
        result = compress(
            [{"role": "tool", "content": raw_json}],
            model="claude-sonnet-4-6",  # for token counting only
            optimize=True,
        )
        compressed = result.messages[0]["content"]
        
        # Only use if meaningful compression achieved
        if result.compression_ratio > 0.1 and len(compressed) < len(raw_json):
            logger.info(
                "Headroom [%s]: %d→%d chars (%.0f%% saved), skipping LLM filter",
                tool_name, len(raw_json), len(compressed),
                result.compression_ratio * 100,
            )
            return compressed
        return None  # not worth it, fall through to LLM filter
    except Exception as e:
        logger.debug("Headroom compression failed for %s: %s", tool_name, e)
        return None  # fall through to LLM filter
```

Then in `filter_tool_result()`, insert after `rule_based_prune()` and before the LLM call:

```python
    # Try Headroom compression before expensive LLM filter
    headroom_result = _headroom_compress(raw_json, tool_name)
    if headroom_result is not None:
        return headroom_result, 0.0  # no LLM cost

    # Existing LLM filter below (unchanged)
    ...
```

**What doesn't change:**
- `rule_based_prune()` — stays as first pass
- LLM utility-model filter — stays as final fallback
- `SKIP_TOOLS` — stays as-is
- `context_pipeline.py` — untouched
- `parallel_worker.py` — untouched

## 6. Install

```bash
pip install "headroom-ai[code]"   # core + tree-sitter for AST compression (~50MB)
```

**Not** `[all]` or `[ml]`. The core package includes SmartCrusher (JSON/tabular compression) which is rule-based. The `[code]` extra adds tree-sitter for AST-aware code compression in execution output. No torch, no transformers, no GPU needed.

If Kompress (ModernBERT-based text compression) proves necessary later, `[ml]` can be added — but that pulls torch (~2GB) and should be a deliberate decision, not a default.

## 7. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HEADROOM_ENABLED` | `true` | Kill switch — set to `false` to bypass Headroom entirely |

One env var. The `FILTER_THRESHOLD` (6k chars) and `SKIP_TOOLS` already control when filtering kicks in — no new thresholds needed.

## 8. Measuring Impact

**Before writing any code**, establish baselines from the existing `context_compression_log` table:

```sql
-- Average tokens per filtered tool result
SELECT tool_name, COUNT(*), AVG(original_tokens), AVG(compressed_tokens)
FROM context_compression_log
WHERE stages_applied LIKE '%tool_pruning%'
GROUP BY tool_name;
```

### Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| LLM filter calls avoided | Count when Headroom returns non-None | >50% of large tool results |
| LLM filter cost saved | Sum of avoided utility-model costs | Measurable from existing cost tracking |
| Agent re-read rate | Count `file_read` calls to same path within a session | No increase (compression isn't touching file reads) |
| Quality | Spot-check agent task completion | No regression |

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Headroom compresses something the agent needed verbatim | **High** | `SKIP_HEADROOM` excludes all file-content tools; LLM fallback catches edge cases |
| Headroom `compress()` expects conversation messages, not raw tool output | Medium | Use `role: "tool"` messages; verify behavior in testing; fall back to LLM filter if output is worse |
| Dependency conflict with Bond's existing packages | Low | Core + `[code]` has minimal deps (tiktoken, pydantic, litellm — all already in Bond) |
| Headroom upstream breaking changes | Low | Pin version; wrapped behind `_headroom_compress()` with try/except fallback |
| Narrow scope means modest savings | Low | That's fine — this is a low-risk incremental improvement, not a rewrite |

## 10. What This Doc Intentionally Does NOT Cover

These were in v1 of this doc and removed after review:

| Removed | Why |
|---------|-----|
| **History compression with Headroom** (was Phase 2) | Headroom does structural compression, not semantic summarization. Bond's history compression needs semantic summaries ("what happened in the last 20 messages"). Headroom can't produce those. |
| **Prompt cache prefix stabilization** (was Phase 3) | Bond's system prompt includes dynamic fragments (fragment_router.py) and conversation summaries that change every turn. Prefix stabilization requires restructuring prompt assembly to put dynamic content after a stable prefix — that's an architectural change unrelated to Headroom. |
| **SharedContext for parallel workers** (was Phase 4) | Speculative. Requires verifying whether parallel workers share a process (SharedContext is in-process only). Separate design concern. |
| **`pip install "headroom-ai[all]"`** | Pulls torch (~2GB), transformers, sentence-transformers. Bond runs on a VPS. Core + `[code]` is ~50MB and covers the use case. |

Any of these could become separate design docs if the Phase 1 data justifies them.
