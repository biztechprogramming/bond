# Design Doc 012: Context Distillation Pipeline

## Status: Draft
## Author: Bond Team
## Date: 2026-02-26

---

## 1. Problem Statement

Agent conversations accumulate context rapidly. A 20-turn conversation with file reads,
tool calls, and iterative debugging can easily consume 100K+ tokens per turn sent to
the primary model. Most of this context is noise — resolved errors, superseded file reads,
abandoned approaches, verbose tool outputs.

**Current state:** Every turn sends the full conversation history verbatim to the primary
model (e.g., Opus at ~$15/MTok input). A single long session can cost $5-10+ in wasted
context tokens.

**Desired state:** A utility model (e.g., Sonnet at ~$3/MTok) pre-processes the context
before each turn, producing a distilled version that preserves all decision-relevant
information while dramatically reducing token count.

## 2. Architecture Overview

The Context Distillation Pipeline (CDP) runs **before every primary model call** inside
the worker. It uses the agent's configured `utility_model` to curate what the primary
model sees.

```
User Message
    │
    ▼
┌─────────────────────────────────────────────┐
│         Context Distillation Pipeline        │
│                                              │
│  Stage 1: Fragment Selection (DONE)          │
│  Stage 2: History Compression                │
│  Stage 3: Tool Output Pruning               │
│  Stage 4: Context Budget Allocation          │
│                                              │
└─────────────────────────────────────────────┘
    │
    ▼
Primary Model (with distilled context)
```

### 2.1 Design Principles

1. **Never lose critical information** — summarize rather than drop
2. **Fail safe** — if any stage fails, pass through unmodified context
3. **Configurable aggressiveness** — per-agent context budget controls compression level
4. **Auditable** — log what was compressed/dropped for debugging
5. **Cost-aware** — the pipeline itself must cost less than it saves
6. **Idempotent** — re-running on already-compressed context doesn't corrupt it

## 3. Pipeline Stages

### Stage 1: Fragment Selection ✅ (Implemented)

Utility model selects which prompt fragments are relevant to this turn.
See migration 010 and `_select_relevant_fragments()` in worker.py.

### Stage 2: History Compression

The core of the pipeline. Converts raw conversation history into a compressed
representation with three tiers:

#### Tier Structure (inspired by Agent Zero)

```
┌─────────────────────────────────────┐
│  Tier 1: BULK SUMMARIES             │  ← Oldest history, heavily compressed
│  "Earlier, the agent set up the     │     ~100 words per bulk (covers many turns)
│   project, installed deps, and      │
│   created initial file structure."  │
├─────────────────────────────────────┤
│  Tier 2: TOPIC SUMMARIES            │  ← Middle history, moderately compressed
│  "User asked to refactor auth.      │     ~100 words per topic (covers 3-5 turns)
│   Agent read 3 files, proposed      │
│   changes. User approved approach." │
├─────────────────────────────────────┤
│  Tier 3: VERBATIM RECENT            │  ← Recent messages, untouched
│  [Last N messages kept as-is]       │     Full detail preserved
│  (configurable, default 6 msgs)     │
└─────────────────────────────────────┘
```

#### Topic Detection

Messages are grouped into **topics** based on:
- User initiating a new request/direction
- Significant time gap between messages
- Shift in tool usage pattern (e.g., from file editing to testing)

The utility model handles topic boundary detection as part of the compression call.

#### Compression Algorithm

```python
def compress_history(messages, context_budget, utility_model):
    total_tokens = count_tokens(messages)

    if total_tokens <= context_budget:
        return messages  # fits, no compression needed

    # 1. Protect the last N messages (verbatim tier)
    verbatim = messages[-VERBATIM_COUNT:]
    compressible = messages[:-VERBATIM_COUNT]

    # 2. Check for existing summaries (from previous turns)
    # Summaries are cached and only regenerated when new messages
    # are added to a topic.

    # 3. Compress oldest messages into topic summaries
    # 4. Compress old topic summaries into bulk summaries
    # 5. Drop oldest bulk summaries if still over budget
```

#### Summary Caching

Summaries are **cached in the agent's local DB** (agent.db) to avoid
re-summarizing the same messages every turn:

```sql
CREATE TABLE context_summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('topic', 'bulk')),
    covers_from INTEGER NOT NULL,  -- message index start
    covers_to INTEGER NOT NULL,    -- message index end
    summary TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cs_conv ON context_summaries(conversation_id, tier, covers_from);
```

On each turn:
1. Load existing summaries for this conversation
2. Check if new messages have been added since last summary
3. Only summarize the *new* unsummarized portion
4. Assemble: cached summaries + new summaries + verbatim recent

This means the utility model only processes a few new messages per turn,
not the entire history. **Cost per turn stays constant** regardless of
conversation length.

### Stage 3: Tool Output Pruning

Tool call/result pairs are the biggest token consumers. A single `file_read`
can inject 5K+ tokens of file content into history.

#### Pruning Rules

| Age | Tool Type | Action |
|-----|-----------|--------|
| Current topic | Any | Keep verbatim |
| Previous topic | file_read | Keep first/last 10 lines + line count |
| Previous topic | web_search/web_read | Keep title + 3-sentence summary |
| Previous topic | bash_exec | Keep command + exit code + last 5 lines |
| Older | Any | Replace with one-line summary |

#### Implementation

Tool results are tagged with metadata when stored:
```json
{
  "role": "tool",
  "tool_call_id": "call_abc",
  "content": "...",
  "_meta": {
    "tool_name": "file_read",
    "token_count": 5200,
    "file_path": "/workspace/src/auth.py",
    "result_lines": 340
  }
}
```

The pruning stage uses these tags to apply rules without needing to
re-parse tool results. The utility model is NOT called for this stage —
it's pure rule-based for speed.

For ambiguous cases (is this file still relevant?), the utility model
can be consulted, but only for files above a token threshold (e.g., 2K tokens).

### Stage 4: Context Budget Allocation

The final stage enforces a hard token budget. After stages 1-3, if the
assembled context still exceeds the budget:

1. Calculate token usage per section:
   - System prompt (fragments): X tokens
   - Bulk summaries: Y tokens
   - Topic summaries: Z tokens
   - Verbatim recent: W tokens
   - Current user message: V tokens

2. Apply proportional compression:
   - System prompt: never compressed (already filtered by Stage 1)
   - Verbatim recent: never compressed (protected)
   - Topic summaries: compress attention window (keep first/last msg, summarize middle)
   - Bulk summaries: merge oldest bulks into single summary
   - Last resort: drop oldest bulk summaries

3. Hard limit: `context_budget = model_context_window * context_ratio`
   - Default `context_ratio`: 0.7 (reserve 30% for output)
   - Configurable per agent

## 4. Configuration

New agent-level settings (stored in agents table or agent_settings):

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `context_budget_ratio` | float | 0.7 | Fraction of model context to use for input |
| `verbatim_message_count` | int | 6 | Messages to keep verbatim (recent) |
| `compression_enabled` | bool | true | Enable/disable the entire pipeline |
| `tool_pruning_enabled` | bool | true | Enable/disable tool output pruning |
| `max_topic_messages` | int | 8 | Messages before forcing topic boundary |
| `summary_max_words` | int | 100 | Max words per topic/bulk summary |

These can start as constants and be promoted to DB-configurable settings
when the Container Configuration UI (Design Doc 009) is built.

## 5. Database Changes

### Agent DB (agent.db — inside container)

```sql
-- Migration: Add context summary caching
CREATE TABLE IF NOT EXISTS context_summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('topic', 'bulk')),
    covers_from INTEGER NOT NULL,
    covers_to INTEGER NOT NULL,
    original_token_count INTEGER NOT NULL,
    summary TEXT NOT NULL,
    summary_token_count INTEGER NOT NULL,
    utility_model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cs_conv
    ON context_summaries(conversation_id, tier, covers_from);

-- Track which messages have been summarized
CREATE TABLE IF NOT EXISTS context_compression_log (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    original_tokens INTEGER NOT NULL,
    compressed_tokens INTEGER NOT NULL,
    stages_applied TEXT NOT NULL,  -- JSON: ["fragment_selection", "history_compression", "tool_pruning"]
    fragments_selected INTEGER,
    fragments_total INTEGER,
    topics_summarized INTEGER,
    tools_pruned INTEGER,
    processing_time_ms INTEGER NOT NULL,
    utility_model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ccl_conv
    ON context_compression_log(conversation_id, turn_number);
```

### Host DB (knowledge.db) — No changes

All compression state lives in the agent's local DB since it's
conversation-specific and container-local.

## 6. Observability & Audit Trail

Every turn logs a compression record:

```json
{
  "turn": 15,
  "original_tokens": 85420,
  "compressed_tokens": 12800,
  "savings_pct": 85.0,
  "stages": {
    "fragment_selection": {"selected": 4, "total": 12},
    "history_compression": {
      "verbatim_messages": 6,
      "topic_summaries": 3,
      "bulk_summaries": 1,
      "tokens_saved": 52000
    },
    "tool_pruning": {
      "tools_pruned": 8,
      "tokens_saved": 18000
    },
    "budget_enforcement": "within_budget"
  },
  "utility_cost_tokens": 3200,
  "processing_time_ms": 1400
}
```

This is logged to:
1. `context_compression_log` table (agent DB)
2. Worker stdout (structured JSON for log aggregation)
3. SSE event to frontend (for the tool activity feed)

## 7. Cost Analysis

### Assumptions
- Primary model: Claude Opus 4 (~$15/MTok input)
- Utility model: Claude Sonnet 4.6 (~$3/MTok input, ~$15/MTok output)
- Average conversation: 20 turns, 100K tokens history by turn 20

### Without CDP
- Turn 20: 100K input tokens × $15/MTok = **$1.50 per turn**
- 20-turn conversation: ~$15 total (growing per turn)

### With CDP
- Utility model per turn: ~3K input + ~500 output = ~$0.02
- Primary model per turn: ~15K input tokens × $15/MTok = **$0.23 per turn**
- 20-turn conversation: ~$2.50 total (constant per turn after warmup)

### Savings: ~83% cost reduction on long conversations

The pipeline pays for itself after 2-3 turns of compression.

## 8. Implementation Plan

### Phase 1: History Compression (this sprint)
- [ ] Add `context_summaries` table to agent DB schema in worker.py
- [ ] Implement `_compress_history()` with topic detection + summarization
- [ ] Summary caching in agent DB
- [ ] Wire into the turn pipeline (after fragment selection, before LLM call)
- [ ] Logging + SSE events for compression stats

### Phase 2: Tool Output Pruning
- [ ] Tag tool results with `_meta` during tool execution
- [ ] Implement rule-based pruning (no LLM needed)
- [ ] Add utility model fallback for ambiguous cases

### Phase 3: Context Budget Enforcement
- [ ] Token counting integration (tiktoken or litellm tokenizer)
- [ ] Budget allocation algorithm
- [ ] Progressive compression when over budget

### Phase 4: Configuration UI
- [ ] Compression settings in agent editor
- [ ] Compression stats dashboard (tokens saved, cost savings)
- [ ] Per-conversation compression log viewer

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Summary loses critical detail | Agent makes wrong decisions | Conservative summarization; keep file paths, error codes, decisions verbatim |
| Utility model hallucination | Fabricated history | Summaries are informational context, not instructions; primary model still has recent verbatim turns |
| Latency increase | Slower turn response | Summary caching means only new messages are processed; parallel execution where possible |
| Summary cost exceeds savings | Net negative ROI | Only activate when history exceeds threshold (e.g., 20K tokens) |
| Circular compression | Summaries get re-summarized poorly | Mark summaries with `[SUMMARY]` tag; never re-summarize the same content |

## 10. References

- Agent Zero history.py: Topic/Bulk/Message hierarchy with attention-window compression
- Agent Zero memory_consolidation.py: LLM-based memory deduplication with similarity thresholds
- Anthropic contextual retrieval: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- LLMLingua: Token compression research (Microsoft)
