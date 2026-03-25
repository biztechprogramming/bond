# 074 — Context Mode Integration

**Status:** Draft  
**Author:** Bond Agent  
**Date:** 2025-07-07  
**Relates to:** [context-mode](https://github.com/mksglu/context-mode), Design Docs 003 (Agent Tools & Sandbox), 004 (Conversation Persistence)

---

## 1. Problem Statement

Bond agents routinely encounter two context-window pressure points:

| Scenario | What Happens | Impact |
|---|---|---|
| **Log file monitoring** | Agents tail/grep large log files (build output, app logs, system journals). A single `code_execute` can return 50–300 KB of raw text. | Blows the context window in 1–2 turns. Subsequent reasoning degrades. |
| **Large code files** | `file_read` on a 2,000+ line file returns the full content. Even with `outline: true`, follow-up targeted reads accumulate. | Context fills with stale code blocks that `context_decay` can only partially mitigate. |
| **Session continuity** | When conversations compact or restart, agents lose track of prior decisions, file edits, and error history. | Agents repeat work, re-read files, and ask questions they already resolved. |

### What Bond already does

Bond has two layers of defense today:

1. **`tool_result_filter.py`** — A utility-model filter that passes large tool results (>6,000 chars) through a cheap LLM to extract only relevant parts. Effective but adds latency and cost per filtered call.
2. **`context_decay.py`** — Progressive decay that compresses tool results based on turn age (full → head/tail → one-line summary → name+args only). Effective for aging out old results, but doesn't help with *fresh* large results on turn 0.

Neither layer addresses **session continuity** — when a conversation compacts or a new session starts, all prior context is lost.

### What context-mode solves

[context-mode](https://github.com/mksglu/context-mode) is an MCP server that attacks both problems:

1. **Context Saving (Sandbox Execution)** — Commands and file reads execute in a sandbox; only a compact summary enters the context window. The project claims 98% reduction (315 KB → 5.4 KB).
2. **Session Continuity (FTS5 Knowledge Base)** — Every file edit, git operation, task, error, and user decision is tracked in SQLite with FTS5 full-text indexing. On session resume, only BM25-relevant events are retrieved — not a full dump.

### The 6 MCP tools it exposes

| Tool | Purpose |
|---|---|
| `ctx_execute` | Run a shell command in sandbox; return structured summary, not raw stdout |
| `ctx_batch_execute` | Run multiple commands; single summary response |
| `ctx_execute_file` | Run a script file in sandbox |
| `ctx_index` | Index content (markdown, code, docs) into the FTS5 knowledge base |
| `ctx_search` | BM25-ranked search over indexed content |
| `ctx_fetch_and_index` | Fetch a URL, convert to markdown, index it |

---

## 2. Integration Goals

1. **Reduce context consumption** for log monitoring and large file reads by 80%+ without adding utility-model latency.
2. **Preserve session knowledge** across conversation compactions and restarts.
3. **Reusable across all agent types** — main Bond agent, sandbox worker agents, and future subordinate agents.
4. **Non-breaking** — existing tool behavior is unchanged; context-mode is additive.
5. **Leverage existing MCP infrastructure** — Bond already has an MCP proxy client and broker; context-mode slots in as another MCP server.

---

## 3. Architecture

### 3.1 Where context-mode lives

```
┌──────────────────────────────────────────────────────┐
│                    Gateway (TS)                       │
│  ┌────────────┐   ┌──────────────┐   ┌────────────┐ │
│  │ MCP Broker │───│ context-mode │   │ Other MCP  │ │
│  │ (existing) │   │ MCP Server   │   │ Servers    │ │
│  └─────┬──────┘   └──────┬───────┘   └────────────┘ │
│        │                 │                           │
│        │    stdio/HTTP   │                           │
└────────┼─────────────────┼───────────────────────────┘
         │                 │
    ┌────┴─────┐     ┌────┴──────┐
    │ Worker   │     │ SQLite    │
    │ (Python) │     │ FTS5 DB   │
    │          │     │ (per-agent│
    │ mcp_proxy│     │  or shared│
    │ .py      │     │  instance)│
    └──────────┘     └───────────┘
```

**context-mode runs as an MCP server managed by the Gateway's MCP broker.** This is the most reusable integration point because:

- The Gateway broker already handles MCP server lifecycle, authentication, policy, and audit logging.
- All agent types (main worker, sandbox agents, subordinates) already access MCP tools through `MCPProxyClient` → Gateway broker → MCP server.
- No changes needed to the worker's core loop — context-mode tools appear as regular MCP tools.

### 3.2 Registration

In the Gateway's MCP server configuration (managed via the existing `broker/` infrastructure):

```json
{
  "context-mode": {
    "command": "npx",
    "args": ["-y", "context-mode"],
    "env": {
      "CONTEXT_MODE_DATA_DIR": "/data/context-mode"
    }
  }
}
```

The broker spawns context-mode as a child process using stdio transport. Tools are discovered via `listTools()` and exposed to workers with the `mcp_context-mode_` prefix (following Bond's existing MCP naming convention).

### 3.3 Data flow for a log monitoring task

```
User: "Monitor the nginx error log for 502s"

Agent calls: mcp_context-mode_ctx_execute
  args: { command: "tail -n 500 /var/log/nginx/error.log | grep 502" }

  ┌─ context-mode sandbox ─────────────────────┐
  │ Executes command                            │
  │ Raw output: 47 KB of log lines              │
  │ Structured summary: 1.2 KB                  │
  │   - exit_code: 0                            │
  │   - line_count: 312                         │
  │   - key findings (pattern-matched)          │
  │   - first/last timestamps                   │
  └─────────────────────────────────────────────┘

  Returns to agent context: 1.2 KB (not 47 KB)
```

### 3.4 Data flow for session continuity

```
Turn 1-50: Agent works on a feature
  → context-mode's PostToolUse hook indexes each tool result
  → File edits, git ops, errors, decisions → FTS5 DB

[Conversation compacts or new session starts]

Turn 51: Agent needs to resume
  Agent calls: mcp_context-mode_ctx_search
    args: { query: "what errors did we fix in auth module" }
  Returns: BM25-ranked relevant events from prior session
```

---

## 4. Integration Points (Detailed)

### 4.1 Gateway: MCP Server Registration (Primary — Most Reusable)

**File:** `gateway/src/broker/router.ts` (existing MCP broker)

**What:** Register context-mode as a managed MCP server in the broker's server registry. This is the **single integration point** that makes context-mode available to all agents.

**Why most reusable:** Every agent type in Bond accesses MCP tools through the Gateway broker. By registering context-mode here, it's instantly available to:
- The main Bond worker agent
- Sandbox worker agents (via `MCPProxyClient`)
- Future subordinate/specialized agents
- Any new agent type that uses the broker

**Changes needed:**
- Add context-mode to the MCP server config (likely `gateway/data/` or environment-based config)
- No code changes to the broker itself — it already handles arbitrary MCP servers

### 4.2 Worker: Smart Routing in tool_result_filter.py (Enhancement)

**File:** `backend/app/agent/tools/tool_result_filter.py`

**What:** When context-mode is available, route large results through `ctx_index` instead of the utility-model filter for applicable tool types. This replaces the LLM-based filtering with deterministic sandbox-based summarization.

**Current flow:**
```
tool result > 6000 chars → utility model → filtered result (~3000 chars)
```

**Proposed flow:**
```
tool result > 6000 chars
  → if context-mode available AND tool is [code_execute, file_read, shell_grep]:
      → ctx_index the raw result (stored in FTS5, searchable later)
      → return compact summary to context
  → else:
      → existing utility model filter (fallback)
```

**Benefits:**
- No LLM cost for filtering
- Raw data is preserved and searchable (not discarded)
- Faster than a utility-model round-trip

**Changes needed:**
- Add a `context_mode_available()` check (query broker for tool availability)
- Add routing logic for applicable tools
- Preserve existing fallback path

### 4.3 Worker: context_decay.py Awareness (Enhancement)

**File:** `backend/app/agent/context_decay.py`

**What:** When a tool result has been indexed by context-mode, decay can be more aggressive — the data is recoverable via `ctx_search`, so we can drop to summary form faster.

**Current decay schedule:**
```
Turn 0: Full (capped at 1500 tokens)
Turn 1-2: Head/tail
Turn 3-5: One-line summary
Turn 6+: Name + args only
```

**Proposed decay schedule when context-mode indexed:**
```
Turn 0: Full (capped at 1500 tokens)
Turn 1: One-line summary + "[indexed, searchable via ctx_search]"
Turn 2+: Name + args only + "[indexed]"
```

**Changes needed:**
- Tag tool results that were indexed by context-mode (metadata flag)
- Adjust decay tiers for tagged results

### 4.4 Prompt Fragment: Agent Routing Instructions

**File:** `prompts/tools/context-mode.md` (new)

**What:** A prompt fragment that teaches agents when and how to use context-mode tools. Loaded when context-mode MCP tools are available.

**Content guidance:**
```markdown
## Context Mode Tools

When available, prefer these tools for operations that produce large output:

- **Log analysis**: Use `ctx_execute` instead of `code_execute` for tail, grep, journalctl
- **Large file reads**: Use `ctx_execute` with `cat` for files >500 lines, then `ctx_search` for specific sections
- **Indexing references**: Use `ctx_index` to store API docs, config references, error catalogs
- **Resuming work**: Use `ctx_search` to find prior decisions, errors, and file changes

### When NOT to use context-mode
- Small commands (<50 lines expected output) — use `code_execute` directly
- File writes — use `file_write` directly
- Interactive/streaming commands — not supported in sandbox
```

### 4.5 Docker Configuration

**File:** `docker-compose.dev.yml` or `Dockerfile.agent`

**What:** Ensure `npx context-mode` is available in the Gateway container (or as a sidecar).

**Options:**
1. **In-process (recommended):** Install `context-mode` as an npm dependency in the Gateway's `package.json`. The broker spawns it via stdio.
2. **Sidecar:** Run context-mode as a separate container with HTTP transport. More isolated but more complex.

**Recommendation:** Option 1 (in-process). The Gateway already runs Node.js and manages MCP servers as child processes. Adding one more is the path of least resistance.

---

## 5. Per-Agent vs Shared FTS5 Database

### Option A: Shared database (recommended)

One context-mode instance serves all agents through the Gateway broker. The FTS5 database is shared, with content tagged by agent ID and session ID.

**Pros:** Single source of truth. One agent's indexed log analysis is searchable by another agent working on the same project. Lower resource usage.

**Cons:** Requires content isolation if agents work on unrelated projects (solvable with namespaced queries).

### Option B: Per-agent instances

Each sandbox worker spawns its own context-mode instance with a separate database.

**Pros:** Complete isolation. No cross-contamination.

**Cons:** Duplicated indexing. No knowledge sharing. More processes to manage.

**Recommendation:** Option A with agent-scoped namespacing. The Gateway broker already tracks agent identity — pass `agent_id` as context to context-mode for scoped search.

---

## 6. Implementation Plan

### Phase 1: MCP Registration (Low effort, high value)
1. Add `context-mode` to Gateway MCP server config
2. Install `context-mode` npm package in Gateway
3. Verify tools appear via `MCPProxyClient.list_tools()`
4. Add prompt fragment `prompts/tools/context-mode.md`
5. **Agents can now manually use context-mode tools**

### Phase 2: Smart Routing (Medium effort, high value)
1. Add context-mode availability check to `tool_result_filter.py`
2. Route large `code_execute` results through `ctx_index` + summary
3. Route large `file_read` results through `ctx_index` + summary
4. Preserve utility-model fallback when context-mode unavailable
5. **Automatic context savings for all agents**

### Phase 3: Accelerated Decay (Low effort, medium value)
1. Tag indexed results in message metadata
2. Adjust `context_decay.py` tiers for tagged results
3. **More aggressive context reclamation**

### Phase 4: Session Continuity (Medium effort, high value)
1. Hook into conversation lifecycle events (compact, new session)
2. Auto-index key events (file edits, git ops, errors) via `ctx_index`
3. On session resume, inject relevant prior context via `ctx_search`
4. **Agents resume work without re-reading everything**

---

## 7. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| context-mode process crashes | Agents lose sandbox execution; fall back to direct tools | Broker health checks + automatic restart. Existing tools remain functional as fallback. |
| FTS5 database grows unbounded | Disk usage on long-running agents | Implement TTL-based cleanup. context-mode already deletes non-continued session data. |
| Sandbox execution adds latency | Slower than direct `code_execute` | Only route large-output commands. Small commands bypass context-mode entirely. |
| Security: sandbox escape | Arbitrary code execution in context-mode sandbox | context-mode has its own security policies (command deny lists, file path evaluation). Bond's broker policy layer adds a second gate. |
| npm dependency supply chain | context-mode is a third-party package | Pin version. Review updates before upgrading. Consider vendoring if stability is critical. |

---

## 8. Success Metrics

| Metric | Baseline (today) | Target |
|---|---|---|
| Context tokens consumed per log monitoring turn | ~4,000–12,000 | <1,500 |
| Utility-model filter calls per conversation | ~5–15 | <3 (only for non-routable tools) |
| Agent re-reads of same file after compaction | 2–4x | 0–1x (searchable in FTS5) |
| Session resume accuracy (can agent continue without re-asking) | ~30% | >80% |

---

## 9. Files Changed (Estimated)

| File | Change Type | Phase |
|---|---|---|
| `gateway/package.json` | Add `context-mode` dependency | 1 |
| `gateway/data/mcp-servers.json` (or equivalent config) | Register context-mode server | 1 |
| `prompts/tools/context-mode.md` | New prompt fragment | 1 |
| `backend/app/agent/tools/tool_result_filter.py` | Add context-mode routing | 2 |
| `backend/app/agent/context_decay.py` | Add indexed-result decay tiers | 3 |
| `backend/app/worker.py` | Add session event indexing hooks | 4 |
| `docker-compose.dev.yml` | Ensure Node.js available for npx | 1 |

---

## 10. Open Questions

1. **Hook support:** context-mode's full power comes from lifecycle hooks (PreToolUse, PostToolUse, PreCompact, SessionStart). Bond's worker loop is custom Python, not Claude Code/Gemini CLI. Should we implement equivalent hooks in the worker, or rely on MCP tools only?
   - **Recommendation:** Start with MCP tools only (Phase 1–2). Add worker-side hooks in Phase 4 if session continuity requires them.

2. **Licensing:** context-mode uses the Elastic License 2.0, which prohibits offering it as a managed service. Bond is a personal assistant, not a SaaS product, so this should be fine — but worth noting.

3. **Version pinning:** Should we pin to a specific context-mode version or track latest?
   - **Recommendation:** Pin to a specific version in `package.json`. Update deliberately.
