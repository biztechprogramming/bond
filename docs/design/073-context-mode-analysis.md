# 073 — context-mode Integration Analysis

> Comprehensive analysis of integrating [context-mode](https://github.com/context-mode) into Bond for intelligent context window management.

---

## 1. context-mode Architecture

### 1.1 Overview

context-mode is an MCP server (v1.0.53, Elastic-2.0 license) that acts as a **context window optimizer** for AI coding agents. It provides:

- **FTS5 knowledge base** — indexes content (code output, docs, fetched URLs) into SQLite with full-text search, so agents can retrieve relevant snippets instead of keeping everything in context.
- **Polyglot code executor** — runs code in 11 languages with sandboxing, output capture, and smart truncation.
- **Session continuity** — tracks tool calls and user messages across conversation turns, builds resume snapshots for context compaction/continuation.
- **Hook-based routing** — intercepts tool calls (PreToolUse/PostToolUse) to redirect agents toward context-mode tools when appropriate.

### 1.2 Component Map

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Server (server.ts)                │
│  8 tools: ctx_execute, ctx_execute_file, ctx_index,     │
│  ctx_search, ctx_fetch_and_index, ctx_batch_execute,    │
│  ctx_stats, ctx_doctor                                  │
├───────────┬───────────────┬─────────────────────────────┤
│ Executor  │ ContentStore  │ Session Layer               │
│ (11 langs)│ (FTS5+SQLite) │ (DB + Snapshots + Extract)  │
├───────────┤               ├─────────────────────────────┤
│ Security  │               │ Adapters (12 platforms)      │
│ (policies)│               │ Hooks (routing enforcement)  │
└───────────┴───────────────┴─────────────────────────────┘
```

### 1.3 Data Storage

- **Content DB**: `~/.context-mode/content/<sha256-of-project-path>.db` — per-project FTS5 knowledge base (WAL mode).
- **Session DB**: `~/.claude/context-mode/sessions/<project-hash>.db` — per-project session events.
- **Session events**: JSON files in `~/.claude/context-mode/sessions/` for cross-process sharing.

---

## 2. Tool Inventory

### 2.1 `ctx_execute`

**Purpose:** Execute code in a sandboxed subprocess, optionally indexing large output for later search.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `language` | enum | required | javascript, typescript, python, shell, ruby, go, rust, php, perl, r, elixir |
| `code` | string | required | Code to execute |
| `timeout` | number | 30000 | Timeout in ms |
| `background` | boolean | false | Detach after timeout, return partial output |
| `intent` | string | — | If provided and output >5KB, indexes output and returns search results against intent instead of raw output |

**Flow:** Security check → write temp file → spawn process → capture stdout/stderr (100KB soft cap, 100MB hard kill) → if intent provided and output large: index into FTS5, search against intent, return titles + previews + distinctive terms.

### 2.2 `ctx_execute_file`

**Purpose:** Read a file into a `FILE_CONTENT` variable, then execute code that processes it.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | required | File to read |
| `language` | enum | required | Language for processing code |
| `code` | string | required | Code that can reference `FILE_CONTENT` |
| `timeout` | number | 30000 | Timeout in ms |
| `intent` | string | — | Intent-driven search on large output |

**Flow:** Check file path against deny patterns → inject file content via language-specific preamble → execute → optional intent search.

### 2.3 `ctx_index`

**Purpose:** Index markdown/documentation content into the FTS5 knowledge base.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | — | Content to index (markdown, plain text, JSON) |
| `path` | string | — | File path to read and index |
| `source` | string | — | Label for the source (used in search filtering) |

**Flow:** Chunk by headings (markdown) or line groups (plain text) or key paths (JSON) → deduplicate by source label → insert into FTS5 porter + trigram tables → extract vocabulary.

### 2.4 `ctx_search`

**Purpose:** Search the FTS5 knowledge base with multi-layer fallback.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `queries` | string[] | required | Search queries |
| `limit` | number | 3 | Results per query |
| `source` | string | — | Filter by source label |
| `contentType` | enum | — | "code" or "prose" |

**Search cascade:** Porter stemming (BM25, title=5x weight) → Trigram → RRF fusion (K=60) → Proximity reranking → Fuzzy correction (Levenshtein) → Re-run RRF.

**Throttling:** After 3 calls in 60s → 1 result/query. After 8 calls → blocked entirely.

### 2.5 `ctx_fetch_and_index`

**Purpose:** Fetch URL content, convert to markdown, index into FTS5.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | required | URL to fetch |
| `source` | string | — | Source label |
| `force` | boolean | false | Bypass 24h TTL cache |

**Flow:** Subprocess fetch → HTML→Turndown markdown / JSON→key-path chunking / plain text→line groups → index → return ~3KB preview. 24h cache by source label.

### 2.6 `ctx_batch_execute`

**Purpose:** The "primary" tool. Execute multiple commands, index all output, search against queries.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `commands` | `{label, command}[]` | required | Shell commands to run sequentially |
| `queries` | string[] | required | Search queries to run against indexed output |
| `timeout` | number | 60000 | Total timeout |

**Flow:** For each command: deny-check → execute → smartTruncate → accumulate. Index all output into FTS5. Run queries with 3-tier fallback (scoped → boosted → global). Return: section inventory, search results (3 per query, 3KB snippets), distinctive terms. 80KB total output cap.

### 2.7 `ctx_stats`

Returns session statistics: bytes processed vs bytes in context, savings ratio, per-tool breakdown, session continuity stats.

### 2.8 `ctx_doctor`

Diagnostics: runtime detection (11 languages), Bun detection, JS/FTS5 health checks, hook script existence, version.

---

## 3. ContentStore Deep Dive (store.ts)

### 3.1 Schema

```sql
-- Sources table
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  chunk_count INTEGER DEFAULT 0,
  code_chunk_count INTEGER DEFAULT 0,
  indexed_at TEXT DEFAULT (datetime('now'))
);

-- Porter stemming FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  title, content, source_id UNINDEXED, content_type UNINDEXED,
  tokenize = 'porter unicode61'
);

-- Trigram FTS5 (for substring/partial matching)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram USING fts5(
  title, content, source_id UNINDEXED, content_type UNINDEXED,
  tokenize = 'trigram'
);

-- Vocabulary for fuzzy correction
CREATE TABLE IF NOT EXISTS vocabulary (
  word TEXT PRIMARY KEY
);
```

### 3.2 Chunking Strategies

| Content Type | Strategy | Chunk Size | Details |
|---|---|---|---|
| Markdown | Heading-based | ≤4096 bytes | Splits on H1-H4, hierarchical title stack, code blocks kept intact |
| Plain text | Blank-line or fixed-size | 20 lines | 2-line overlap, blank-line splitting preferred |
| JSON | Key-path walking | Varies | Objects recurse by key, arrays batch with identity-field-aware titles |

### 3.3 Search Cascade

```
Query
  ├─ Porter BM25 (title weight 5.0, content 1.0)
  ├─ Trigram BM25
  ├─ RRF Fusion (K=60, merges porter OR + trigram OR)
  ├─ Proximity Reranking (multi-term: minimum span boost)
  └─ If empty: Fuzzy Correction (Levenshtein ≤1/2/3) → re-run RRF
```

### 3.4 Deduplication & TTL

- **Source-level dedup**: Before inserting, deletes all chunks with same source label (atomic transaction).
- **TTL**: `cleanupStaleSources(maxAgeDays)` removes old sources + chunks. `cleanupStaleContentDBs()` removes old DB files by mtime.

---

## 4. Executor (executor.ts)

### 4.1 Language Support

11 languages: JavaScript, TypeScript, Python, Shell, Ruby, Go, Rust, PHP, Perl, R, Elixir.

### 4.2 Sandboxing

- **Environment sanitization**: Strips ~60 dangerous env vars (BASH_ENV, NODE_OPTIONS, PYTHONSTARTUP, LD_PRELOAD, RUBYOPT, etc.).
- **Forced overrides**: TMPDIR, HOME, LANG, PYTHONDONTWRITEBYTECODE, NO_COLOR.
- **Output limits**: 100KB soft cap (smartTruncate: 60% head + 40% tail), 100MB hard kill.
- **Process group kill**: `-pid` on Unix for all children.

### 4.3 Language-Specific Setup

- **Go**: Auto-wraps in `package main` + `import "fmt"` if missing.
- **Rust**: Compile-then-run via `rustc`.
- **PHP**: Prepends `<?php`.
- **Elixir**: BEAM paths for Mix projects.

---

## 5. Security (security.ts)

### 5.1 Policy System

Three-tier precedence (most local first):
1. `.claude/settings.local.json` (project-local)
2. `.claude/settings.json` (project-shared)
3. `~/.claude/settings.json` (global)

### 5.2 Command Evaluation

- Splits chained commands (`;`, `&&`, `||`, `|`) respecting quotes.
- Each segment checked against deny patterns.
- Non-shell code scanned for embedded shell escapes (os.system, subprocess, execSync, etc.).

---

## 6. Session Continuity

### 6.1 SessionDB (session/db.ts)

Per-project SQLite with 3 tables:
- `session_events`: id, session_id, type, category (15 categories), priority (1-4), data, data_hash.
- `session_meta`: session_id, project_dir, event_count, compact_count.
- `session_resume`: snapshot blob, consumed flag.

**Constants:** MAX_EVENTS = 1000/session, DEDUP_WINDOW = 5 (checks last 5 events for same type+hash).

**FIFO eviction:** When at 1000 events, evicts lowest priority first, then oldest.

### 6.2 Event Extraction (session/extract.ts)

Extracts structured events from tool calls. 15 categories:

| Category | Source | Priority |
|----------|--------|----------|
| file | Read, Edit, Write, Glob, Grep | 1-3 |
| rule | CLAUDE.md reads | 1 |
| task | TodoWrite, TaskCreate/Update | 1 |
| plan | EnterPlanMode, plan files | 1-2 |
| cwd | Bash with `cd` | 2 |
| error | Bash errors, isError flag | 2 |
| git | Bash with git commands | 2 |
| env | venv, export, nvm, pip | 2 |
| decision | AskUserQuestion / user patterns | 2 |
| subagent | Agent tool | 2-3 |
| skill | Skill tool | 3 |
| mcp | mcp__ prefixed tools | 3 |
| role | User persona patterns | 3 |
| intent | User intent classification | 4 |
| data | User messages >1KB | 4 |

### 6.3 Resume Snapshots (session/snapshot.ts)

Converts events into XML snapshots with budget allocation:
- P1 (50%): active_files, task_state, rules
- P2 (35%): decisions, environment, errors, subagents
- P3-P4 (15%): intent, mcp_tools, launched subagents

Default budget: 2048 bytes. Progressive section dropping until under budget.

---

## 7. Bond Integration Points

### 7.1 MCP Server Registration

**Where:** SpacetimeDB `mcp_servers` table via `POST /api/v1/mcp/servers`

**Files:**
- `backend/app/mcp/manager.py` — `MCPManager.ensure_servers_loaded()` (line 186) loads from DB
- `backend/app/api/v1/mcp.py` — REST CRUD for server configs
- `gateway/src/broker/router.ts` — Proxies MCP tool calls with policy enforcement

**Action:** Add context-mode as an MCP server entry:
```json
{
  "name": "context-mode",
  "command": "node",
  "args": ["/path/to/context-mode/server.bundle.mjs"],
  "env": {"CONTEXT_MODE_PROJECT_DIR": "/workspace"},
  "enabled": true,
  "agent_id": null
}
```

Tool names become `mcp_context-mode_ctx_execute`, etc. in Bond's registry.

### 7.2 Tool Result Filtering — Smart Indexing

**File:** `backend/app/agent/tool_result_filter.py`

**Current behavior:** Results >6000 chars go through a utility model for summarization. Rule-based pruning first (code_execute stdout >4K → first/last 1K, file_read >200 lines → first/last 50).

**Integration:** When context-mode is available, route large results through `ctx_index` + `ctx_search` instead of the utility model:

```python
# In filter_tool_result(), after rule_based_prune():
if len(result) > FILTER_THRESHOLD and mcp_proxy and mcp_proxy.has_tool("ctx_index"):
    # Index the full result
    await mcp_proxy.call_tool("mcp_context-mode_ctx_index", {
        "content": full_result,
        "source": f"{tool_name}:{turn_number}"
    })
    # Search with user intent
    search_results = await mcp_proxy.call_tool("mcp_context-mode_ctx_search", {
        "queries": [user_goal_summary],
        "limit": 3
    })
    return format_indexed_result(search_results)
```

### 7.3 Progressive Context Decay — Accelerated for Indexed Content

**File:** `backend/app/agent/context_decay.py`

**Current tiers:** Turn 0=full (1500 token cap), Turn 1-2=head/tail, Turn 3-5=summary, Turn 6+=name+args.

**Integration:** When a tool result has been indexed into context-mode, apply accelerated decay:

| Turn Age | Current | With context-mode |
|----------|---------|-------------------|
| 0 | Full (1500 tokens) | Full |
| 1 | Head/tail | Summary + "[indexed in knowledge base, use ctx_search]" |
| 2+ | Summary → name only | Name + args only (searchable via ctx_search) |

**File change:** In `apply_progressive_decay()`, check for `[indexed]` marker on tool results.

### 7.4 Context Pipeline — Search Injection

**File:** `backend/app/agent/context_pipeline.py`

**Current:** `_compress_history()` uses tiered summarization. `_apply_sliding_window()` keeps last 20 messages, summarizes overflow.

**Integration:** Before sliding window truncation, index overflow messages into context-mode. On context rebuild, inject relevant `ctx_search` results as a system-level "Previously indexed knowledge" section.

### 7.5 Context Builder — MCP Proxy Wiring

**File:** `backend/app/agent/context_builder.py`

`build_agent_context()` already accepts `mcp_proxy` parameter (line 49). This is where context-mode search results would be injected into the `ContextBundle`.

### 7.6 Worker Agent Loop — Lifecycle Hooks

**File:** `backend/app/worker.py`

- **Turn start** (line 577): Initialize context-mode session, potentially call `ctx_stats` for diagnostics.
- **Tool execution** (line 708-761): MCP proxy tools already registered via `register_proxy_handlers()`.
- **Turn end**: No changes needed; context-mode's PostToolUse hooks handle event extraction.

### 7.7 Sandbox Manager — context-mode as Sidecar

**File:** `backend/app/sandbox/manager.py`

Two deployment options:
1. **Host-side MCP server**: context-mode runs on the host, workers access via Gateway MCP proxy. FTS5 DB shared across agent sessions.
2. **Container sidecar**: context-mode runs inside each worker container. Isolated DB per container. Requires adding to Docker image.

**Recommendation:** Host-side. Avoids per-container overhead, enables cross-session knowledge reuse, and leverages existing MCP proxy infrastructure.

---

## 8. Data Flow Diagrams

### 8.1 Current Bond Tool Execution Flow

```
User Message
  │
  ▼
Worker (_run_agent_loop)
  │
  ├─ build_agent_context() ──► ContextBundle
  │     ├─ sliding_window (last 20 msgs)
  │     ├─ progressive_decay (by turn age)
  │     └─ compress_history (tiered summarization)
  │
  ├─ LLM Call ──► tool_use response
  │
  ├─ execute_tool_call()
  │     ├─ Native tools (file_read, code_execute, etc.)
  │     └─ MCP proxy tools (mcp_{server}_{tool})
  │
  ├─ tool_result_filter() ──► truncated result
  │     ├─ rule_based_prune() (>4K stdout → head/tail)
  │     └─ utility model filter (>6K chars)
  │
  └─ Append to history ──► next iteration
```

### 8.2 Proposed Flow with context-mode

```
User Message
  │
  ▼
Worker (_run_agent_loop)
  │
  ├─ build_agent_context()
  │     ├─ sliding_window (last 20 msgs)
  │     ├─ progressive_decay (ACCELERATED for indexed results)
  │     ├─ compress_history (tiered summarization)
  │     └─ ctx_search("current task context") ──► inject relevant knowledge
  │
  ├─ LLM Call ──► tool_use response
  │
  ├─ execute_tool_call()
  │     ├─ Native tools
  │     ├─ MCP proxy ──► context-mode tools (ctx_execute, ctx_search, etc.)
  │     └─ Agent can directly use ctx_batch_execute for log analysis
  │
  ├─ tool_result_filter()
  │     ├─ rule_based_prune()
  │     ├─ IF context-mode available AND result > threshold:
  │     │     ctx_index(result) ──► FTS5
  │     │     ctx_search(intent) ──► relevant snippets
  │     │     return snippets + "[indexed]" marker
  │     └─ ELSE: utility model filter (fallback)
  │
  └─ Append to history (with [indexed] markers) ──► next iteration
```

### 8.3 Log Monitoring Use Case

```
Agent monitoring /var/log/app.log (continuous):

  ctx_batch_execute({
    commands: [
      {label: "recent-errors", command: "tail -1000 /var/log/app.log | grep -i error"},
      {label: "warnings", command: "tail -1000 /var/log/app.log | grep -i warn"},
      {label: "metrics", command: "tail -100 /var/log/app.log | grep metric"}
    ],
    queries: ["connection timeout errors", "memory warnings"]
  })

  Returns:
  - Section inventory (3 sections indexed)
  - Search results: 3 relevant snippets per query
  - Distinctive terms for follow-up queries

  Context impact: ~2KB in context instead of ~50KB raw log output
```

### 8.4 Large Code File Use Case

```
Agent working with 5000-line file:

  ctx_execute_file({
    path: "src/giant_module.py",
    language: "python",
    code: "import ast; tree = ast.parse(FILE_CONTENT); print([n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))])",
    intent: "find the PaymentProcessor class and its retry logic"
  })

  Returns:
  - Indexed all 5000 lines into FTS5
  - Search results: 3 snippets around PaymentProcessor and retry
  - Agent gets targeted context, not entire file
```

---

## 9. Reusability Analysis

### 9.1 What to Reuse

| Component | Reuse Location | Value |
|-----------|---------------|-------|
| **ContentStore (FTS5)** | Host-side service | High — replaces utility model for large result filtering, enables cross-turn knowledge retrieval |
| **ctx_batch_execute** | Agent tool | High — purpose-built for log monitoring, multi-command workflows |
| **ctx_search** | context_builder.py | High — inject relevant prior knowledge on context rebuild |
| **ctx_index** | tool_result_filter.py | High — index large results instead of discarding |
| **Session continuity** | context_pipeline.py | Medium — Bond has its own session management; value is in the event extraction + snapshot format |
| **Executor** | — | Low — Bond already has sandboxed execution via Docker containers + Gateway broker |
| **Security policies** | — | Low — Bond has its own policy engine in the Gateway |
| **Hook routing** | — | None — Bond's agent loop is server-side, not hook-driven |

### 9.2 Where Integration Gives Maximum Value

**Gateway level (MCP registration only):** Minimal code changes. Agents can use context-mode tools directly. Best for Phase 1.

**Worker level (tool_result_filter + context_decay):** Automatic context optimization. Agents don't need to know about context-mode. Best for Phase 2.

**Context pipeline level (context_builder + context_pipeline):** Knowledge injection on context rebuild. Cross-session knowledge reuse. Best for Phase 3.

---

## 10. Risk Assessment

### 10.1 Dependencies

| Dependency | Risk | Mitigation |
|------------|------|------------|
| `better-sqlite3` (native addon) | Build failures on some platforms | Also supports `bun:sqlite`; Bond already uses SQLite |
| `@modelcontextprotocol/sdk` | Version drift | Pin version; Bond already depends on MCP protocol |
| `turndown` + `domino` | HTML parsing edge cases | Only used for `ctx_fetch_and_index`; not critical path |

### 10.2 Performance

| Concern | Impact | Mitigation |
|---------|--------|------------|
| SQLite FTS5 indexing latency | ~10-50ms per index operation | WAL mode; async; non-blocking to agent loop |
| FTS5 search latency (4-layer cascade) | ~5-20ms for porter, up to ~100ms with fuzzy | Throttling built-in (3 calls/60s limit) |
| Per-project DB size | Grows with indexed content | TTL cleanup built-in; max chunk size 4096 bytes |
| Memory usage | SQLite in-process | WAL mode limits memory; NORMAL synchronous |

### 10.3 SQLite Concurrency

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Multiple agents same project | WAL mode allows concurrent reads, serialized writes | Per-agent DB namespacing via session suffix |
| Host-side shared DB | Write contention under load | Connection pooling in MCPManager (pool_size=2) handles serialization |
| Crash during write | Possible corruption | WAL mode provides crash recovery; NORMAL synchronous is safe |

### 10.4 Security

| Concern | Risk | Mitigation |
|---------|------|------------|
| Code execution in context-mode | Bypasses Bond's Docker sandbox | Disable `ctx_execute`/`ctx_execute_file` in Bond; use only `ctx_index`/`ctx_search`/`ctx_batch_execute` routed through Bond's sandbox |
| File path access | context-mode reads files directly | Rely on Bond's Gateway policy engine for deny patterns |
| Shell command injection | Commands in `ctx_batch_execute` | context-mode has its own deny policies; additionally enforce via MCP policy engine |

---

## 11. Recommended Integration Strategy

### Phase 1: MCP Registration (No Code Changes)

**Goal:** Make context-mode tools available to agents.

**Steps:**
1. Add context-mode as an MCP server in SpacetimeDB:
   ```sql
   INSERT INTO mcp_servers (name, command, args, env, enabled, agent_id)
   VALUES ('context-mode', 'node', '["server.bundle.mjs"]',
           '{"CONTEXT_MODE_PROJECT_DIR": "/workspace"}', true, NULL);
   ```
2. Add MCP policy rules to restrict dangerous tools:
   ```json
   {"tools": ["mcp_context-mode_ctx_execute", "mcp_context-mode_ctx_execute_file"],
    "decision": "deny"}
   ```
3. Allow safe tools: `ctx_index`, `ctx_search`, `ctx_batch_execute`, `ctx_fetch_and_index`, `ctx_stats`.
4. Update agent system prompts to mention available context-mode tools.

**Files changed:** None (configuration only).

### Phase 2: Automatic Context Optimization

**Goal:** Large tool results automatically indexed; accelerated decay for indexed content.

**Files to change:**

1. **`backend/app/agent/tool_result_filter.py`**
   - Add `ctx_index` + `ctx_search` path for results > threshold when context-mode is available.
   - Mark indexed results with `[indexed:source_label]` metadata.
   - Fallback to utility model when context-mode unavailable.

2. **`backend/app/agent/context_decay.py`**
   - Detect `[indexed]` marker on tool results.
   - Apply accelerated decay: Turn 1 → summary + search hint, Turn 2+ → name only.

3. **`backend/app/worker.py`**
   - Pass `mcp_proxy` availability flag to `tool_result_filter`.
   - Add context-mode health check on turn start.

### Phase 3: Knowledge-Augmented Context Building

**Goal:** Inject relevant prior knowledge into context on rebuild.

**Files to change:**

1. **`backend/app/agent/context_builder.py`**
   - Before returning `ContextBundle`, call `ctx_search` with current task/intent.
   - Inject results as a "Prior Knowledge" section in system prompt.

2. **`backend/app/agent/context_pipeline.py`**
   - In `_apply_sliding_window()`: before discarding overflow messages, index them via `ctx_index`.
   - In `_compress_history()`: use `ctx_search` to enrich compressed summaries.

### Phase 4: Session Continuity Integration

**Goal:** Leverage context-mode's session events for smarter conversation continuation.

**Files to change:**

1. **`backend/app/agent/context_pipeline.py`**
   - Use context-mode's resume snapshots as an additional context source on conversation resume.

2. **`backend/app/worker.py`**
   - On `/turn` start with `conversation_id`: check for context-mode session events, inject snapshot.

**Note:** This phase has lower priority since Bond already has conversation persistence. Value is primarily in the structured event extraction (15 categories) which is richer than Bond's current approach.

---

## 12. Key Architectural Decisions

### 12.1 Host-side vs Container Sidecar

**Recommendation: Host-side MCP server.**

- Leverages existing MCP proxy infrastructure (`MCPProxyClient` → Gateway → `MCPManager`).
- FTS5 database persists across container restarts.
- Cross-session knowledge reuse (agent learns from prior conversations).
- No Docker image changes needed.

### 12.2 Shared vs Per-Agent FTS5 Database

**Recommendation: Per-agent databases with optional shared namespace.**

- context-mode already uses per-project DBs (`sha256(project_path)`).
- For Bond: scope by `agent_id` in the env var: `CONTEXT_MODE_PROJECT_DIR=/workspace/agents/{agent_id}`.
- Shared knowledge (docs, architecture) can be indexed once and symlinked.

### 12.3 Executor: Use or Skip?

**Recommendation: Skip context-mode's executor for code execution; use only for `ctx_batch_execute` log analysis.**

- Bond's Docker sandbox is more secure (network isolation, resource limits, filesystem isolation).
- context-mode's executor adds value for **read-only analysis** (grep, awk, jq on command output) where spinning up a Docker container is overkill.
- Deny `ctx_execute` and `ctx_execute_file` via MCP policy; allow `ctx_batch_execute` with command restrictions.

### 12.4 Hook System: Use or Skip?

**Recommendation: Skip hooks entirely.**

- Bond's agent loop is server-side (Python), not client-side (hooks are for Claude Code CLI, Cursor, etc.).
- Routing enforcement should happen in `tool_result_filter.py` and agent system prompts.
- Session event extraction logic (`session/extract.ts`) could be ported to Python for Bond's context pipeline, but this is Phase 4 work.
