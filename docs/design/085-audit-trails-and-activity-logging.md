# Design Doc 085: Audit Trails & Activity Logging

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-30  
**Depends on:** 004 (Conversation Persistence), 081 (Cost Tracking), 084 (Multi-Agent Coordination)  
**Inspired by:** Paperclip's immutable tool-call tracing and replay capability

---

## 1. Problem Statement

Bond executes complex multi-step workflows — reading files, running code, editing code, making git commits, spawning sub-agents — but there is **no structured record** of what happened during a task beyond the conversation messages themselves. This means:

- **No reviewability**: Users can't audit what tools were called, what arguments were passed, what results came back, and what decisions the agent made.
- **No debugging**: When something goes wrong (wrong file edited, bad commit, failed deployment), there's no trace to understand the sequence of events.
- **No replay**: Users can't re-run a successful workflow on a different repo or project.
- **No compliance**: For teams or enterprise use, there's no tamper-evident log of agent actions.

Paperclip maintains immutable activity logs with full tool-call tracing per task, enabling both debugging and governance review. Bond needs similar capabilities.

---

## 2. Goals

1. **Immutable activity log** — Every tool call, agent decision, and state change is recorded in append-only storage with timestamps and correlation IDs.
2. **Tool-call tracing** — For each tool invocation: the tool name, arguments (sanitized), result (truncated), duration, and cost.
3. **Decision logging** — When the agent makes a routing decision (which model, which agent, which approach), log the reasoning.
4. **Queryable history** — Users can search and filter activity by conversation, time range, tool type, agent, or outcome.
5. **Replay foundation** — Activity logs contain enough detail to reconstruct what happened and potentially replay workflows.

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = activity_event, public)]
pub struct ActivityEvent {
    #[primary_key]
    pub id: String,
    pub correlation_id: String,         // groups related events across a single user request
    pub conversation_id: String,
    pub task_id: Option<String>,        // link to delegated_task (084) if applicable
    pub agent_id: String,
    pub event_type: String,             // "tool_call", "tool_result", "decision", "error", "state_change"
    pub category: String,               // "file_op", "code_exec", "git", "llm_call", "agent_spawn", "system"
    pub summary: String,                // human-readable one-liner: "Edited backend/worker.py lines 45-60"
    pub detail: String,                 // JSON: full structured data (args, result, reasoning)
    pub duration_ms: Option<u64>,
    pub cost_usd: Option<f64>,
    pub severity: String,               // "info", "warning", "error"
    pub created_at: Timestamp,
}

#[table(name = activity_snapshot, public)]
pub struct ActivitySnapshot {
    #[primary_key]
    pub id: String,
    pub correlation_id: String,
    pub snapshot_type: String,          // "file_before", "file_after", "git_diff", "state"
    pub reference: String,              // file path, git ref, etc.
    pub content_hash: String,           // SHA-256 of content for integrity verification
    pub content: String,                // the actual content (truncated for large files)
    pub created_at: Timestamp,
}

#[table(name = activity_index, public)]
pub struct ActivityIndex {
    #[primary_key]
    pub id: String,
    #[index(btree)]
    pub conversation_id: String,
    #[index(btree)]
    pub event_type: String,
    #[index(btree)]
    pub category: String,
    #[index(btree)]
    pub created_at: Timestamp,
    pub activity_event_id: String,
}
```

### 3.2 Reducers

- `record_activity {id, correlationId, conversationId, taskId, agentId, eventType, category, summary, detail, durationMs, costUsd, severity}` — Append an activity event.
- `record_snapshot {id, correlationId, snapshotType, reference, contentHash, content}` — Capture a before/after state snapshot.
- `query_activity {conversationId, eventType, category, startTime, endTime, limit}` — Filtered retrieval of activity events.

---

## 4. Architecture

### 4.1 Event Recording Pipeline

Tool calls are instrumented at the tool execution layer, not in individual tools:

```python
class AuditedToolExecutor:
    """Wraps tool execution with automatic activity logging."""
    
    async def execute(self, tool_name: str, args: dict, correlation_id: str) -> Any:
        sanitized_args = self._sanitize(args)  # strip secrets, truncate large values
        
        event_id = str(uuid4())
        await record_activity(
            id=event_id,
            correlation_id=correlation_id,
            event_type="tool_call",
            category=self._categorize(tool_name),
            summary=f"Calling {tool_name}",
            detail=json.dumps({"tool": tool_name, "args": sanitized_args}),
            severity="info",
        )
        
        start = time.monotonic()
        try:
            result = await self._inner_execute(tool_name, args)
            duration_ms = int((time.monotonic() - start) * 1000)
            
            await record_activity(
                id=str(uuid4()),
                correlation_id=correlation_id,
                event_type="tool_result",
                category=self._categorize(tool_name),
                summary=self._summarize_result(tool_name, result),
                detail=json.dumps({"tool": tool_name, "result": self._truncate(result)}),
                duration_ms=duration_ms,
                severity="info",
            )
            return result
            
        except Exception as ex:
            duration_ms = int((time.monotonic() - start) * 1000)
            await record_activity(
                id=str(uuid4()),
                correlation_id=correlation_id,
                event_type="error",
                category=self._categorize(tool_name),
                summary=f"{tool_name} failed: {type(ex).__name__}: {ex}",
                detail=json.dumps({"tool": tool_name, "error": str(ex), "traceback": traceback.format_exc()}),
                duration_ms=duration_ms,
                severity="error",
            )
            raise
    
    def _sanitize(self, args: dict) -> dict:
        """Remove secrets, API keys, and PII from tool arguments."""
        sensitive_keys = {"api_key", "token", "password", "secret", "credential"}
        return {
            k: "***REDACTED***" if k.lower() in sensitive_keys else v
            for k, v in args.items()
        }
    
    def _categorize(self, tool_name: str) -> str:
        categories = {
            "file_read": "file_op", "file_write": "file_op", "file_edit": "file_op",
            "code_execute": "code_exec", "shell_grep": "file_op",
            "git_commit": "git", "git_push": "git",
            "coding_agent": "agent_spawn",
        }
        return categories.get(tool_name, "system")
```

### 4.2 File Change Snapshots

For file-modifying operations, capture before/after state:

```python
async def snapshot_file_change(correlation_id: str, file_path: str, before: str, after: str):
    """Record file state before and after an edit for audit trail."""
    await record_snapshot(
        id=str(uuid4()),
        correlation_id=correlation_id,
        snapshot_type="file_before",
        reference=file_path,
        content_hash=hashlib.sha256(before.encode()).hexdigest(),
        content=before[:10000],  # truncate very large files
    )
    await record_snapshot(
        id=str(uuid4()),
        correlation_id=correlation_id,
        snapshot_type="file_after",
        reference=file_path,
        content_hash=hashlib.sha256(after.encode()).hexdigest(),
        content=after[:10000],
    )
```

### 4.3 Frontend Activity View

A new "Activity" panel accessible per conversation or globally:

```
┌─────────────────────────────────────────────────┐
│ Activity Log — Conversation: "Fix auth bug"     │
│ Filter: [All Types ▼] [All Categories ▼] 🔍    │
├─────────────────────────────────────────────────┤
│ 14:23:01  🔍 file_read  backend/auth.py         │
│           Read 245 lines (outline mode)          │
│ 14:23:03  🔍 shell_grep "validate_token"        │
│           Found 3 matches in 2 files             │
│ 14:23:05  🧠 decision   Route to claude agent   │
│           "Bug is in token validation logic,     │
│            needs careful reasoning"               │
│ 14:23:08  ✏️  file_edit  backend/auth.py:67-82   │
│           Fixed token expiry comparison          │
│ 14:23:10  ▶️  code_exec  pytest tests/auth/      │
│           12 passed, 0 failed (2.3s)             │
│ 14:23:15  📦 git_commit "fix: token expiry..."   │
│           3 files changed, +12 -8                │
│                                                   │
│ Total: 6 events | Duration: 14s | Cost: $0.03   │
└─────────────────────────────────────────────────┘
```

---

## 5. Interaction with Existing Systems

| System | Integration |
|--------|------------|
| Conversation persistence (004) | Activity events are linked to conversations but stored separately — conversations hold messages, activity holds tool traces |
| Cost tracking (081) | `cost_usd` on activity events provides per-action cost; aggregated in cost dashboard |
| Multi-agent coordination (084) | `task_id` links activity to delegated tasks; each agent's work is independently traced |
| Circuit breakers (070) | Circuit breaker triggers are logged as `severity: "warning"` activity events |
| Context distillation (012) | Activity summaries can feed into context distillation for long-running tasks |

---

## 6. Migration Path

1. **Phase 1**: Instrument the tool execution layer with `AuditedToolExecutor`. All tool calls start generating `activity_event` records. No UI yet.
2. **Phase 2**: Add file change snapshots for `file_edit`, `file_write`, and `code_execute` operations.
3. **Phase 3**: Frontend activity panel — per-conversation timeline view with filtering.
4. **Phase 4**: Decision logging — instrument the agent loop to record routing and decomposition decisions.
5. **Phase 5**: Replay prototype — use activity logs to reconstruct and re-execute workflows.

---

## 7. Open Questions

- How long should activity logs be retained? Indefinitely is safest for audit but grows storage. Should there be a configurable TTL with archival to file?
- Should activity events be written synchronously (guarantees completeness but adds latency) or asynchronously (faster but risks losing events on crash)?
- How do we handle activity from coding sub-agents that run in separate containers? They'd need to report back via the delegation protocol (084).
- What level of detail should be exposed in the UI vs. only available in raw logs? Users probably don't want to see every `file_read` but do want to see edits and decisions.
- Should the content hash on snapshots be used for tamper detection? If so, we'd need a hash chain (each event references the previous event's hash).
