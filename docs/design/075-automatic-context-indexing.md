# 075 — Automatic Context Indexing

**Status:** Draft  
**Author:** Bond Agent  
**Date:** 2025-07-08  
**Supersedes:** 073 (context-mode Analysis), 074 (context-mode Integration)  
**Depends on:** 012 (Context Distillation Pipeline), 062 (Headroom Context Compression)  

---

## 1. Problem Statement

Large tool outputs are the single biggest source of context window waste in Bond's agent loop. A single `tail -n 500` on a log file can dump 50KB into context. A `file_read` on a 2,000-line file fills context with code the agent may never reference again. Test failures, build output, and grep results all exhibit the same pattern: the agent asked for information, got 10x more than it needed, and now that bloat persists across every subsequent LLM call until context decay eventually prunes it.

Bond already has two defenses:

1. **`tool_result_filter.py`** — passes large results through a cheap utility model to extract relevant parts. Works well but is lossy: the original content is gone forever.
2. **`context_pipeline.py`** — progressive decay that prunes older tool results. Works well but is time-based: a massive result still occupies full context for several turns before decay kicks in.

Neither defense handles the core issue: **large outputs should be searchable later without occupying context now.**

### Why Not context-mode (Docs 073/074)?

Docs 073 and 074 analyzed integrating context-mode as an MCP server. The conclusion: context-mode's hook-based routing system (PreToolUse/PostToolUse) is designed for client-side CLI tools like Claude Code and Cursor. Bond's agent loop is server-side Python. Without hooks, the agent would need to *choose* between normal tools and `ctx_*` tools — and agents can't reliably predict output size before running a command.

**This doc takes a different approach: the system intercepts tool results automatically based on measured output size. The agent never has to choose anything.**

---

## 2. Design Principles

1. **Measure, don't predict.** Decisions are based on the actual byte size of tool output, never on file extensions, command names, or agent guesses.
2. **The agent doesn't choose.** No new tools for the agent to learn (except `ctx_search`). The system handles interception transparently.
3. **Nothing is lost.** Large outputs are indexed into FTS5, not discarded. The agent can retrieve specific parts via search.
4. **Small outputs are untouched.** Below the threshold, behavior is identical to today. Zero overhead for normal operations.
5. **The agent can override.** An escape hatch exists for when the agent genuinely needs raw output.

---

## 3. Architecture

### 3.1 Data Flow

```
Agent calls tool (code_execute, file_read, shell_grep, etc.)
         │
         ▼
   Tool executes normally
         │
         ▼
   Measure output size (bytes)
         │
         ├─── < 4KB ──────────► Pass through unchanged (today's behavior)
         │
         ├─── 4KB–16KB ───────► Pass through unchanged + index in background
         │
         ├─── 16KB–64KB ──────► Summarize + index → return summary to agent
         │
         └─── > 64KB ─────────► Summarize + index → return summary + warning
```

### 3.2 Component Map

```
┌─────────────────────────────────────────────────────┐
│                  Agent Loop (loop.py)                │
│  Calls tools normally. Receives filtered results.   │
├─────────────────────────────────────────────────────┤
│           result_interceptor.py (NEW)               │
│  Sits between tool execution and result delivery.   │
│  Measures size → routes to appropriate handler.     │
├──────────┬──────────────────┬───────────────────────┤
│ Passthru │  Background      │  Summarize + Index    │
│ (< 4KB)  │  Index (4-16KB)  │  (> 16KB)             │
│          │                  │                       │
│          │  ┌───────────────┴───────────────┐       │
│          │  │     context_store.py (NEW)     │       │
│          │  │  FTS5 knowledge base per conv  │       │
│          │  │  Chunking, indexing, search    │       │
│          │  └───────────────────────────────┘       │
├─────────────────────────────────────────────────────┤
│  tool_result_filter.py (EXISTING — modified)        │
│  Utility-model summarization for 16KB+ results.     │
│  Now also stores original in context_store.         │
├─────────────────────────────────────────────────────┤
│  ctx_search tool (NEW — exposed to agent)           │
│  Agent can search indexed content by keyword.       │
└─────────────────────────────────────────────────────┘
```

### 3.3 Where It Plugs In

The interceptor runs **after** `rule_based_prune()` and **before** `filter_tool_result()` in the existing pipeline. This means:

1. `rule_based_prune()` strips ANSI codes, collapses blank lines (cheap, safe cleanup)
2. **`result_interceptor`** measures the cleaned output, indexes if needed
3. `filter_tool_result()` summarizes large outputs via utility model (only for 16KB+ tier)

The interceptor does NOT replace the existing pipeline — it adds the indexing layer between the two existing stages.

---

## 4. FTS5 Knowledge Base

### 4.1 Storage

One SQLite database per conversation, stored alongside conversation data:

```
data/context_index/{conversation_id}.db
```

WAL mode enabled. Database is created lazily on first index operation. Deleted when conversation is deleted.

### 4.2 Schema

```sql
-- Indexed content chunks
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    title,           -- tool name + args summary, or section heading
    content,         -- the actual text chunk
    source_id UNINDEXED,  -- FK to sources table
    content_type UNINDEXED,  -- 'tool_output', 'file', 'log', 'build'
    tokenize = 'porter unicode61'
);

-- Source tracking (one per tool invocation that gets indexed)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    tool_args TEXT,          -- JSON of tool args (for display)
    turn_number INTEGER,     -- which agent loop turn produced this
    original_bytes INTEGER,  -- size of original output
    chunk_count INTEGER DEFAULT 0,
    indexed_at TEXT DEFAULT (datetime('now'))
);

-- Vocabulary for fuzzy correction
CREATE TABLE IF NOT EXISTS vocabulary (
    word TEXT PRIMARY KEY
);
```

**Why no trigram table?** context-mode uses a dual Porter + trigram approach. For Bond's use case (searching tool outputs within a single conversation), Porter stemming with fuzzy correction is sufficient. Trigram tables roughly double storage and index time. We can add them later if search quality is insufficient.

### 4.3 Chunking Strategy

Content is chunked based on its structure, not its source tool:

| Content Shape | Detection | Chunk Strategy |
|---|---|---|
| **Log-shaped** | >80% of lines match `^\d{4}[-/]\d{2}[-/]\d{2}` or `^\[?(INFO\|WARN\|ERROR\|DEBUG)` | Group by log level; extract unique error messages; keep timestamps |
| **Code-shaped** | Source tool is `file_read`, `file_open`, `file_view`; or content has consistent indentation + syntax markers | Chunk by function/class boundaries (blank-line heuristic); keep line numbers |
| **Structured output** | Valid JSON or consistent columnar format | JSON: chunk by top-level keys. Columnar: keep header + chunk rows |
| **Plain text** | Default fallback | 20-line chunks with 2-line overlap |

Max chunk size: 4096 bytes. Chunks include a title derived from the tool call (e.g., `"code_execute: tail -n 500 /var/log/app.log — ERROR lines"`).

### 4.4 Search

Single-tool search exposed to the agent:

```
ctx_search(queries: list[str], limit: int = 5) -> list[SearchResult]
```

Search cascade:
1. Porter BM25 (title weight 5.0, content weight 1.0)
2. If no results: fuzzy correction (Levenshtein ≤ 2) → re-run BM25
3. Results include: source tool name, turn number, chunk title, content snippet (max 1KB per result)

**No throttling.** context-mode throttles search after 3 calls in 60s because it's an external MCP server with abuse concerns. Bond's index is local and per-conversation — no reason to throttle.

---

## 5. Size Tiers — Detailed Behavior

### 5.1 Tier 1: Small (< 4KB, ~100 lines)

**Behavior:** Pass through unchanged. No indexing, no summarization.

**Rationale:** This is the common case. Most tool calls return small results. Adding any overhead here would slow down every agent turn for no benefit.

**Examples:** `ls`, `git status`, small `file_read`, `grep` with few matches, successful `code_execute` with brief output.

### 5.2 Tier 2: Medium (4KB–16KB, ~100–400 lines)

**Behavior:** Pass through unchanged to the agent AND index into FTS5 in the background.

**Rationale:** The agent gets the full result now (it might need it immediately), but the content is also searchable later. If context decay eventually prunes this message, the agent can still retrieve it via `ctx_search`. This is pure upside — the agent's experience is unchanged, but a safety net exists.

**Implementation:** Indexing runs as an `asyncio.create_task()` — fire-and-forget, doesn't block the agent loop. If indexing fails, nothing changes for the agent.

**Examples:** Medium `file_read`, `grep` with 20-50 matches, test output with a few failures.

### 5.3 Tier 3: Large (16KB–64KB, ~400–1600 lines)

**Behavior:** Index into FTS5, then summarize via utility model. Return summary to agent with a note explaining what happened.

**Agent sees:**
```
📋 Indexed: code_execute("tail -n 500 /var/log/app.log") — 947 lines, 42KB
Summary:
  - 312 INFO lines (normal startup sequence)  
  - 23 WARN lines (connection pool near limit, 14:01–14:05 UTC)
  - 4 ERROR lines (TimeoutException in PaymentService)
  - Errors cluster between 14:02–14:07 UTC
  - Last line: 2025-07-08T14:12:33Z INFO service healthy

Use ctx_search(["TimeoutException", "PaymentService"]) to see full error details.
```

**Rationale:** 16KB+ in context is actively harmful — it pushes out other content and costs real money on every subsequent LLM call. The summary preserves the agent's ability to reason about the output, and `ctx_search` provides drill-down access.

**Implementation:** This replaces the current `filter_tool_result()` flow for large results. Instead of the utility model summarizing and discarding, it summarizes and indexes.

### 5.4 Tier 4: Very Large (> 64KB)

**Behavior:** Same as Tier 3, but the summary includes a warning:

```
⚠️ Very large output indexed: code_execute("cat database_dump.sql") — 4,200 lines, 312KB
Summary: [...]
Warning: This output was very large. Use specific ctx_search queries to find what you need.
Do NOT re-run this command — the full output is already indexed.
```

**Rationale:** At this size, the agent is likely doing something inefficient (reading an entire database dump, catting a huge file). The warning nudges it toward more targeted approaches.

---

## 6. The `ctx_search` Tool

### 6.1 Tool Definition

```python
{
    "name": "ctx_search",
    "description": "Search previously indexed tool outputs from this conversation. "
                   "Use when you need to find specific details from large outputs that "
                   "were automatically summarized. Returns matching text chunks with "
                   "source context.",
    "parameters": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Search queries (1-3 recommended). Use specific terms: "
                               "error messages, function names, variable names, timestamps."
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": "Max results per query (1-10)"
            }
        },
        "required": ["queries"]
    }
}
```

### 6.2 When the Agent Knows to Use It

This is the critical design question from docs 073/074. The answer: **the system tells the agent exactly when to use it.**

Every Tier 3/4 summary ends with an explicit instruction:
```
Use ctx_search(["specific query"]) to see full details.
```

The agent doesn't need to independently decide "I should search the index." The summary hands it the tool name, example queries, and a clear reason to use it. This is fundamentally different from the context-mode approach where the agent had to predict output size and choose tools proactively.

Additionally, the system prompt includes a brief note:

```
## Indexed Content
When tool outputs are large, they are automatically indexed and you receive a summary.
Use `ctx_search(queries=["your query"])` to retrieve specific details from indexed content.
You do NOT need to re-run commands — the full output is already searchable.
```

### 6.3 What the Agent Does NOT Need to Know

- How indexing works
- What the size thresholds are
- Which tier a result fell into
- Whether FTS5 or some other technology is involved
- How to choose between normal tools and indexed tools

The agent uses normal tools. The system handles the rest.

---

## 7. Override Escape Hatch

### 7.1 The `raw` Parameter

For tools that support it (`code_execute`, `file_read`), add an optional `raw: boolean` parameter:

```python
code_execute(language="shell", code="cat big_file.py", raw=True)
```

When `raw=True`:
- Tier 2/3/4 interception is skipped
- The full output is returned to the agent as-is
- The output is still indexed (for later search), but not summarized

**When would the agent use this?** When it's editing a file and needs every line. When it's doing a precise diff. When it explicitly tells the user "I need to see the full output." The system prompt documents this:

```
Pass `raw=True` to code_execute or file_read to bypass automatic summarization
when you need the complete output (e.g., for editing or precise analysis).
Use sparingly — large raw outputs consume context.
```

### 7.2 Why Not Just Always Use Raw?

The agent could theoretically pass `raw=True` on every call, defeating the system. But agents are trained to follow system prompt guidance, and the prompt is clear: "use sparingly." In practice, agents default to the path of least resistance (not passing extra parameters), so the automatic behavior will dominate.

If an agent consistently overuses `raw`, that's a prompt tuning issue, not an architecture issue.

---

## 8. Integration with Existing Systems

### 8.1 `tool_result_filter.py` Changes

**Minimal.** The interceptor sits *before* `filter_tool_result()`:

- Tier 1 (< 4KB): No change. Falls through to existing `SKIP_TOOLS` / `FILTER_THRESHOLD` logic.
- Tier 2 (4-16KB): No change to agent-visible result. Background indexing is invisible.
- Tier 3/4 (> 16KB): The interceptor indexes the content, then passes it to `filter_tool_result()` for summarization. The summary is augmented with the `ctx_search` hint. The utility model call still happens — it's good at extracting relevant parts — but now the original isn't lost.

**Key change:** `filter_tool_result()` gains an optional `indexed: bool` parameter. When `True`, the summary template includes the `ctx_search` hint. When `False` (default), behavior is unchanged.

### 8.2 `context_pipeline.py` Changes

**One addition:** When context decay prunes a tool result message, if that message was in Tier 2 (full content + background indexed), the decay can be more aggressive because the content is recoverable via search. Add a flag `_indexed: true` to Tier 2 results so the decay system knows.

```python
# In _prune_tool_result():
if msg.get("_indexed"):
    # Content is in FTS5 — safe to prune aggressively
    return {"_pruned": True, "_note": "Content indexed. Use ctx_search to retrieve."}
```

### 8.3 `context_decay.py` Changes

**One addition:** Messages with `_indexed: true` get accelerated decay. Instead of surviving `VERBATIM_MESSAGE_COUNT` turns at full size, they decay after `VERBATIM_MESSAGE_COUNT // 2` turns. The content is recoverable, so keeping it in context longer than necessary is pure waste.

### 8.4 Session Continuity

When a conversation is resumed (continuation.py), the FTS5 index persists because it's stored per-conversation. The agent can `ctx_search` content from previous sessions without re-running commands. The continuation summary should mention this:

```
Previous session indexed 12 tool outputs (47 search chunks available).
Use ctx_search to find specific details from prior work.
```

---

## 9. Log-Shaped Output Detection

### 9.1 Why Special-Case Logs?

Log output is the most common source of context bloat, and it has a unique property: 95% of lines are noise (INFO-level repetition), and the agent almost always wants the 5% that are errors or warnings. Generic summarization works, but log-aware summarization works much better.

### 9.2 Detection Heuristic

```python
def is_log_shaped(content: str, sample_size: int = 20) -> bool:
    """Check if content looks like log output."""
    lines = content.strip().splitlines()[:sample_size]
    if len(lines) < 5:
        return False
    
    log_patterns = [
        r'^\d{4}[-/]\d{2}[-/]\d{2}',           # ISO date prefix
        r'^\[?\d{2}:\d{2}:\d{2}',               # Time prefix
        r'^\[?(INFO|WARN|ERROR|DEBUG|TRACE)\b',  # Log level prefix
        r'^\w{3}\s+\d{1,2}\s+\d{2}:\d{2}',     # Syslog format
    ]
    
    matches = sum(
        1 for line in lines
        if any(re.match(p, line.strip()) for p in log_patterns)
    )
    return matches / len(lines) > 0.6
```

### 9.3 Log-Aware Chunking

When log-shaped content is detected, chunking changes:

1. **Group by level:** Separate ERROR, WARN, INFO, DEBUG lines
2. **Deduplicate INFO:** If the same message template appears 50+ times, collapse to `"[repeated 312 times] INFO Starting health check"`
3. **Keep all ERROR/WARN lines verbatim** with timestamps
4. **Extract unique error messages** as separate high-priority chunks (title = error class name)
5. **Time range summary:** First and last timestamp, duration

This means `ctx_search("TimeoutException")` returns the exact error lines with context, not a generic chunk that happens to contain the word.

---

## 10. Implementation Plan

### Phase 1: Core Indexing (1-2 days)

1. **`context_store.py`** — FTS5 knowledge base: create/open DB, chunk content, index, search, cleanup.
2. **`result_interceptor.py`** — Size measurement, tier routing, background indexing task.
3. **`ctx_search` tool** — Tool definition, registration in tool manifest, search execution.
4. **Integration** — Wire interceptor into the tool result pipeline between `rule_based_prune()` and `filter_tool_result()`.
5. **System prompt update** — Add the "Indexed Content" section and `ctx_search` documentation.

### Phase 2: Smart Summarization (1 day)

6. **Augment `filter_tool_result()`** — Add `indexed` parameter, `ctx_search` hint in summary template.
7. **Log detection** — `is_log_shaped()` heuristic + log-aware chunking in `context_store.py`.
8. **Accelerated decay** — `_indexed` flag support in `context_pipeline.py` and `context_decay.py`.

### Phase 3: Polish (1 day)

9. **`raw` parameter** — Add to `code_execute` and `file_read` tool definitions.
10. **Continuation support** — Include index stats in continuation summaries.
11. **Cleanup** — Delete index DB when conversation is deleted. Age out stale sources (>7 days).
12. **Metrics** — Log indexing stats (bytes indexed, search hit rate, tier distribution) to Langfuse.

### Phase 4: Evaluation (ongoing)

13. **Measure context savings** — Compare context window usage before/after on real conversations.
14. **Search quality** — Track how often `ctx_search` returns useful results vs. empty results.
15. **Agent behavior** — Monitor whether agents actually use `ctx_search` when prompted, or re-run commands.
16. **Threshold tuning** — Adjust 4KB/16KB/64KB boundaries based on real data.

---

## 11. What We're NOT Building

| Excluded Feature | Why |
|---|---|
| **MCP server** | Bond's agent loop is server-side Python. An MCP server adds network hops, process management, and a TypeScript dependency for no benefit. The FTS5 logic is ported directly into Python. |
| **Code executor** | Bond already has `code_execute` with Docker sandboxing. context-mode's executor is redundant and less secure. |
| **Hook-based routing** | Hooks are for client-side CLI tools. Bond's interceptor achieves the same goal server-side, automatically. |
| **Session event tracking** | context-mode tracks 15 categories of session events. Bond already has conversation persistence (doc 004) and continuation (continuation.py). Duplicating this adds complexity for marginal benefit. |
| **Trigram FTS5 table** | Doubles storage/index time. Porter + fuzzy correction covers Bond's use case. Add later if search quality data justifies it. |
| **URL fetching/indexing** | Bond agents rarely fetch URLs. Can be added as a separate feature if needed. |
| **Multi-conversation search** | Each conversation gets its own index. Cross-conversation search is a memory system concern (doc 001), not a context indexing concern. |
| **Rules based on file extension or command name** | Bad proxy for output size. Will misfire. Measure the actual output instead. |

---

## 12. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **FTS5 indexing adds latency to tool calls** | Agent loop slows down | Tier 2 indexes in background (fire-and-forget). Tier 3/4 indexing runs in parallel with utility model summarization. Target: <100ms for indexing a 64KB output. |
| **Agent ignores `ctx_search` hints and re-runs commands** | Wasted compute, no context savings | System prompt explicitly says "Do NOT re-run this command." Monitor and tune prompt if needed. |
| **Summarization loses critical details** | Agent makes wrong decisions | `ctx_search` provides drill-down. `raw=True` provides escape hatch. Log-aware chunking preserves all errors/warnings. |
| **Index DB grows unbounded** | Disk usage | Age out sources older than 7 days. Delete DB with conversation. Typical conversation indexes <10MB. |
| **Threshold boundaries are wrong** | Too aggressive (useful content summarized) or too lenient (bloat still enters context) | Start conservative (4KB/16KB/64KB). Log tier distribution. Adjust based on real data in Phase 4. |
| **`raw=True` overuse defeats the system** | Context bloat returns | Prompt guidance + monitoring. If systematic, can add per-conversation raw budget (e.g., max 3 raw calls per turn). |

---

## 13. Success Metrics

| Metric | Target | How to Measure |
|---|---|---|
| **Context window savings** | 30%+ reduction in avg tool result tokens per conversation | Compare `_estimate_messages_tokens()` before/after |
| **Search hit rate** | >70% of `ctx_search` calls return ≥1 relevant result | Log search result counts in Langfuse |
| **Agent compliance** | <10% of Tier 3/4 outputs followed by re-running the same command | Detect duplicate commands in agent loop telemetry |
| **Latency overhead** | <200ms p95 added to tool call round-trip for Tier 2/3 | Measure interceptor duration in Langfuse |
| **Index size** | <20MB per conversation p95 | Monitor DB file sizes |

---

## 14. Relationship to Other Design Docs

| Doc | Relationship |
|---|---|
| **012 (Context Distillation)** | This doc extends 012's pipeline with an indexing stage. The compression and sliding window logic in `context_pipeline.py` is unchanged. |
| **062 (Headroom Compression)** | Headroom operates on the full message history. This doc operates on individual tool results. They're complementary — Headroom compresses what's in context, this doc prevents bloat from entering context in the first place. |
| **073 (context-mode Analysis)** | Superseded. The analysis of context-mode's FTS5 store and chunking strategies informed this design. The MCP server, executor, hooks, and session tracking are excluded. |
| **074 (context-mode Integration)** | Superseded. The phased integration plan assumed an MCP server approach. This doc replaces it with server-side automatic interception. |
| **004 (Conversation Persistence)** | Index DBs are stored per-conversation and follow the same lifecycle (created, persisted, deleted). |
