# Design Doc 096: Progress Checkpointing (Revised)

**Status:** Revised  
**Date:** 2026-04-02 (original) · 2026-04-07 (revised)  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements  
**Revision note:** Updated to unify with existing `continuation.py` infrastructure, use SpacetimeDB for persistence (sqlite-vec only for embeddings), target `loop.py` as the integration point, and align with `IterationBudget` / `LoopState` / `ToolMetrics`.

---

## Problem

When Bond's agent loop crashes, times out, or exhausts its turn budget mid-task, all progress is lost. The next attempt starts from scratch with no knowledge of:

1. **What was already completed** — files created, edits made, commands run
2. **What was discovered** — file locations, codebase patterns, error messages encountered
3. **What the plan was** — which steps were done and which remain
4. **What failed** — what approaches were tried and didn't work

This leads to duplicate work, repeated failures, and user frustration. Claude Code handles this with agent memory snapshots and command queuing for resumption.

### What Already Exists

Bond has partial infrastructure for this problem, but the pieces aren't connected:

| Component | File | What it does | Gap |
|-----------|------|-------------|-----|
| `LightweightCheckpoint` | `continuation.py:356` | Captures last request/action, uncommitted changes, decisions, TODOs | Not persisted — lives only in memory during a single turn |
| `build_checkpoint_from_history()` | `continuation.py:366` | Scans history + git state to build a checkpoint | Only runs on explicit "continue" — not triggered on crash/timeout |
| `format_checkpoint_context()` | `continuation.py:419` | Formats checkpoint as ~500-token recovery context | Works, but only for `LightweightCheckpoint` — no tool call records |
| `IterationBudget` | `continuation.py:471` | Tracks budget with thresholds (50% checkpoint, 65% nudge, 80% wrap-up, 95% stop) | Emits advisory messages but doesn't actually save state |
| `ToolMetrics` | `loop_state.py:18` | Tracks total/successful/failed/timeout calls and durations | Aggregate counters only — no per-call records for resumption |
| `build_continuation_context()` | `continuation.py:242` | Builds plan-aware recovery context from work plan + git state | Requires an active work plan — doesn't help for ad-hoc tasks |

**The core gap:** None of these persist checkpoint data across turns. If the process dies, everything in `LoopState`, `IterationBudget`, and `LightweightCheckpoint` is gone.

---

## Design Principles

1. **Extend, don't duplicate.** Build on `LightweightCheckpoint`, `ToolMetrics`, and `IterationBudget` — do not create parallel data structures.
2. **SpacetimeDB for persistence.** All non-embedding state goes to SpacetimeDB. No phantom key-value stores, no SQLite for structured data.
3. **Save at thresholds, not every call.** Use the existing `IterationBudget` thresholds to trigger saves — not after every tool call.
4. **Resumption is advisory.** Checkpoints inform the model; they don't replay actions. The model always verifies current state before acting.

---

## Changes

### 1. Extend `LightweightCheckpoint` with Tool Call Records

**File:** `backend/app/agent/continuation.py` (modify existing)

Add tool call history and failure tracking to the existing dataclass:

```python
@dataclass
class ToolCallRecord:
    """Record of a completed tool call for resumption context."""
    tool_name: str
    arguments_summary: str  # Truncated to ~100 chars
    success: bool
    output_summary: str     # Truncated to ~200 chars
    turn_number: int


@dataclass
class LightweightCheckpoint:
    """Minimal state for continuation — now with persistence support."""
    last_user_request: str = ""
    last_assistant_action: str = ""
    uncommitted_changes: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_todos: list[str] = field(default_factory=list)
    exact_identifiers: dict[str, str] = field(default_factory=dict)
    # --- New fields ---
    completed_actions: list[ToolCallRecord] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    progress_summary: str = ""
    turn_number: int = 0
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0

    def record_tool_call(self, record: ToolCallRecord) -> None:
        """Record a tool call, updating counters and file tracking."""
        self.completed_actions.append(record)
        self.total_tool_calls += 1
        if record.success:
            self.successful_tool_calls += 1
        else:
            self.failed_tool_calls += 1
        self.turn_number = record.turn_number
        if record.tool_name in ("file_write", "file_edit", "file_smart_edit") and record.success:
            path = self._extract_path(record.arguments_summary)
            if path and path not in self.files_modified:
                self.files_modified.append(path)

    def add_failed_approach(self, description: str) -> None:
        if description not in self.failed_approaches:
            self.failed_approaches.append(description)

    def to_dict(self) -> dict:
        """Serialize for SpacetimeDB storage."""
        import json
        return {
            "last_user_request": self.last_user_request,
            "last_assistant_action": self.last_assistant_action,
            "uncommitted_changes": self.uncommitted_changes,
            "decisions": self.decisions,
            "open_todos": self.open_todos,
            "exact_identifiers": self.exact_identifiers,
            "completed_actions": [
                {
                    "tool_name": a.tool_name,
                    "arguments_summary": a.arguments_summary,
                    "success": a.success,
                    "output_summary": a.output_summary,
                    "turn_number": a.turn_number,
                }
                for a in self.completed_actions[-10:]  # Keep last 10 only
            ],
            "files_modified": self.files_modified,
            "files_created": self.files_created,
            "failed_approaches": self.failed_approaches,
            "stop_reason": self.stop_reason,
            "progress_summary": self.progress_summary,
            "turn_number": self.turn_number,
            "total_tool_calls": self.total_tool_calls,
            "successful_tool_calls": self.successful_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LightweightCheckpoint":
        """Deserialize from SpacetimeDB storage."""
        cp = cls(
            last_user_request=data.get("last_user_request", ""),
            last_assistant_action=data.get("last_assistant_action", ""),
            uncommitted_changes=data.get("uncommitted_changes", []),
            decisions=data.get("decisions", []),
            open_todos=data.get("open_todos", []),
            exact_identifiers=data.get("exact_identifiers", {}),
            files_modified=data.get("files_modified", []),
            files_created=data.get("files_created", []),
            failed_approaches=data.get("failed_approaches", []),
            stop_reason=data.get("stop_reason"),
            progress_summary=data.get("progress_summary", ""),
            turn_number=data.get("turn_number", 0),
            total_tool_calls=data.get("total_tool_calls", 0),
            successful_tool_calls=data.get("successful_tool_calls", 0),
            failed_tool_calls=data.get("failed_tool_calls", 0),
        )
        for a in data.get("completed_actions", []):
            cp.completed_actions.append(ToolCallRecord(**a))
        return cp

    @staticmethod
    def _extract_path(args_summary: str) -> str | None:
        """Best-effort path extraction from truncated args summary."""
        import re
        m = re.search(r'path["\s:=]+([^\s,"]+)', args_summary)
        return m.group(1) if m else None
```

**Why:** This unifies the proposed `Checkpoint` dataclass with the existing `LightweightCheckpoint`. No new file needed. The existing `build_checkpoint_from_history()` and `format_checkpoint_context()` continue to work — they just gain access to richer data when it's available.

### 2. Enhance `format_checkpoint_context()` for Rich Checkpoints

**File:** `backend/app/agent/continuation.py` (modify existing)

Extend the existing formatter to include the new fields when present:

```python
def format_checkpoint_context(checkpoint: LightweightCheckpoint) -> str:
    """Format a checkpoint as context for a continuation turn.

    Target: ~500-2000 tokens depending on richness.
    """
    lines = ["# Resuming Previous Work", ""]

    if checkpoint.progress_summary:
        lines.append("## Summary")
        lines.append(checkpoint.progress_summary)
        lines.append("")

    if checkpoint.last_user_request:
        lines.append("## Last Request")
        lines.append(checkpoint.last_user_request)
        lines.append("")

    if checkpoint.last_assistant_action:
        lines.append("## Last Action")
        lines.append(checkpoint.last_assistant_action)
        lines.append("")

    if checkpoint.completed_actions:
        lines.append(f"## Progress: {checkpoint.successful_tool_calls} successful / "
                      f"{checkpoint.failed_tool_calls} failed across {checkpoint.turn_number} turns")
        recent = checkpoint.completed_actions[-5:]
        for action in recent:
            status = "✅" if action.success else "❌"
            lines.append(f"  {status} {action.tool_name}: {action.output_summary}")
        lines.append("")

    if checkpoint.files_modified:
        lines.append("## Files Modified")
        for f in checkpoint.files_modified:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.uncommitted_changes:
        lines.append("## Uncommitted Changes (git)")
        for f in checkpoint.uncommitted_changes:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.failed_approaches:
        lines.append("## Approaches That Failed (don't retry)")
        for approach in checkpoint.failed_approaches:
            lines.append(f"  ⚠️ {approach}")
        lines.append("")

    if checkpoint.decisions:
        lines.append("## Decisions Made")
        for d in checkpoint.decisions:
            lines.append(f"- {d}")
        lines.append("")

    if checkpoint.open_todos:
        lines.append("## Open TODOs")
        for t in checkpoint.open_todos:
            lines.append(f"- {t}")
        lines.append("")

    if checkpoint.stop_reason:
        lines.append(f"## Stop Reason: {checkpoint.stop_reason}")
        lines.append("")

    lines.append("## Instructions")
    lines.append("Pick up where you left off. Check the current file/git state before making changes.")

    return "\n".join(lines)
```

### 3. SpacetimeDB Table and Reducers for Checkpoint Persistence

**SpacetimeDB module change** — add a `checkpoints` table and associated reducers.

#### Table Schema

```
checkpoints
├── id: String (primary key, = conversation_id)
├── agent_id: String (indexed)
├── conversation_id: String (indexed)
├── data: String (JSON-serialized LightweightCheckpoint.to_dict())
├── stop_reason: String
├── created_at: String (ISO 8601)
├── updated_at: String (ISO 8601)
└── expires_at: String (ISO 8601, created_at + 1 hour)
```

#### Reducers

```
upsert_checkpoint {id, agent_id, conversation_id, data, stop_reason, created_at, updated_at, expires_at}
delete_checkpoint {id}
delete_expired_checkpoints {}  // scheduled reducer, repeat = "15m"
```

**Why SpacetimeDB and not SQLite:**
- Bond's primary database is SpacetimeDB. SQLite is only used for `ContextStore` FTS5 (full-text search for embeddings/semantic retrieval via sqlite-vec). Checkpoints are structured relational data — they belong in SpacetimeDB.
- SpacetimeDB subscriptions mean the frontend can observe checkpoint state in real-time (e.g., show a "resumable" indicator on conversations).
- The `delete_expired_checkpoints` scheduled reducer handles TTL cleanup without application-side cron.

#### Backend Client Methods

**File:** `backend/app/agent/continuation.py` (add to existing)

```python
async def save_checkpoint(
    conversation_id: str,
    agent_id: str,
    checkpoint: LightweightCheckpoint,
) -> None:
    """Persist checkpoint to SpacetimeDB."""
    from datetime import datetime, timezone, timedelta
    from backend.app.core.spacetimedb import get_stdb
    import json

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)

    await get_stdb().call_reducer("upsert_checkpoint", [
        conversation_id,           # id (= conversation_id, one checkpoint per conversation)
        agent_id,
        conversation_id,
        json.dumps(checkpoint.to_dict()),
        checkpoint.stop_reason or "",
        now.isoformat(),
        now.isoformat(),
        expires.isoformat(),
    ])


async def load_checkpoint(conversation_id: str) -> LightweightCheckpoint | None:
    """Load checkpoint from SpacetimeDB if it exists and hasn't expired."""
    from datetime import datetime, timezone
    from backend.app.core.spacetimedb import get_stdb
    import json

    rows = await get_stdb().query(
        f"SELECT data, expires_at FROM checkpoints WHERE id = '{conversation_id}'"
    )
    if not rows:
        return None

    row = rows[0]
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        # Expired — clean up
        await get_stdb().call_reducer("delete_checkpoint", [conversation_id])
        return None

    return LightweightCheckpoint.from_dict(json.loads(row["data"]))


async def delete_checkpoint(conversation_id: str) -> None:
    """Remove checkpoint after successful resumption or task completion."""
    from backend.app.core.spacetimedb import get_stdb
    await get_stdb().call_reducer("delete_checkpoint", [conversation_id])
```

### 4. Auto-Save at Budget Thresholds in the Agent Loop

**File:** `backend/app/agent/loop.py` (modify existing tool-use loop, starting ~line 420)

Instead of saving after every tool call (wasteful), hook into the existing `IterationBudget` thresholds:

```python
# Inside the tool-use loop, after each tool execution:

# Update the in-memory checkpoint (cheap — always do this)
loop_checkpoint.record_tool_call(ToolCallRecord(
    tool_name=tool_name,
    arguments_summary=str(tool_args)[:100],
    success=tool_result.success,
    output_summary=str(tool_result.output)[:200],
    turn_number=loop_state.iteration,
))

# Persist to SpacetimeDB only at budget thresholds
if budget.should_checkpoint and not checkpoint_saved_this_threshold:
    loop_checkpoint.progress_summary = _summarize_progress(loop_state, loop_checkpoint)
    await save_checkpoint(conversation_id, agent_id, loop_checkpoint)
    checkpoint_saved_this_threshold = True
    logger.info("Checkpoint saved at %s budget", f"{budget.pct_used:.0%}")
```

**On loop exit (any reason):**

```python
# After the loop ends — whether normally, crash, timeout, or budget exhaustion
try:
    loop_checkpoint.stop_reason = _classify_stop_reason(loop_state, budget, error)
    loop_checkpoint.progress_summary = _summarize_progress(loop_state, loop_checkpoint)
    # Merge git state
    checkpoint_from_git = build_checkpoint_from_history(messages, workspace_dir)
    loop_checkpoint.uncommitted_changes = checkpoint_from_git.uncommitted_changes
    await save_checkpoint(conversation_id, agent_id, loop_checkpoint)
except Exception:
    logger.warning("Failed to save exit checkpoint", exc_info=True)
```

**Save triggers (summary):**

| Trigger | When | Why |
|---------|------|-----|
| Budget 50% | `IterationBudget.should_checkpoint` | Early safety net |
| Loop exit — normal | Task completed, `respond` tool called | Record success for potential follow-up |
| Loop exit — budget exhausted | `IterationBudget.should_stop` | Primary crash recovery case |
| Loop exit — error/timeout | Exception caught in loop wrapper | Crash recovery |
| Auto-delegation | Before calling `coding_agent` | Hand off context to sub-agent |

**What we do NOT do:** Save after every single tool call. The in-memory `loop_checkpoint` object accumulates tool records cheaply. SpacetimeDB writes happen only at thresholds.

### 5. Resume from Checkpoint on Continuation

**File:** `backend/app/agent/continuation.py` (modify existing `build_continuation_context`)

Integrate checkpoint loading into the existing continuation flow:

```python
async def build_continuation_context(
    plan_position: PlanPosition | None,
    checkpoint: LightweightCheckpoint | None,  # from build_checkpoint_from_history
    conversation_id: str | None = None,        # NEW: for SpacetimeDB lookup
) -> str:
    """Build minimal recovery context for a continuation turn.

    Priority:
    1. Persisted checkpoint from SpacetimeDB (richest — has tool records, failed approaches)
    2. Plan position context (if active work plan exists)
    3. History-derived checkpoint (fallback — last request/action + git state)
    """
    # Try loading persisted checkpoint first
    persisted = None
    if conversation_id:
        persisted = await load_checkpoint(conversation_id)

    if persisted:
        # Merge: persisted checkpoint has tool records; history checkpoint has fresh git state
        if checkpoint:
            persisted.uncommitted_changes = checkpoint.uncommitted_changes
        context = format_checkpoint_context(persisted)
        # Delete after loading — one-shot use to prevent stale resumption
        await delete_checkpoint(conversation_id)
        return context

    # Fall through to existing plan-based and history-based logic...
    # (existing code unchanged)
```

### 6. Checkpoint Invalidation

Checkpoints should be invalidated (deleted) in these cases:

| Condition | Where to check | Action |
|-----------|---------------|--------|
| User sends a `NEW_TASK` intent | `classify_intent()` returns `NEW_TASK` | `delete_checkpoint(conversation_id)` |
| Work plan marked complete | `update_work_plan_status` reducer (status = "done") | `delete_checkpoint(conversation_id)` |
| Successful resumption | `build_continuation_context()` loads a checkpoint | Delete after formatting (one-shot) |
| TTL expiry (1 hour) | `delete_expired_checkpoints` scheduled reducer | Automatic cleanup |
| Git branch divergence | On resume, compare `git rev-parse HEAD` with checkpoint's state | Discard if branch changed |

**Implementation in the loop entry point:**

```python
# At the start of agent_turn / _run_agent_loop:
intent = classify_intent(user_message, has_active_plan)
if intent == ContinuationIntent.NEW_TASK:
    await delete_checkpoint(conversation_id)
```

---

## Interaction with Related Design Docs

### Doc 093 — Turn Budget & Stuck Detection

`IterationBudget` is the **primary trigger** for checkpoint saves. This doc does not change the budget thresholds — it hooks into them:

- **50% (`should_checkpoint`):** First persist to SpacetimeDB
- **80% (`should_wrap_up`):** Update checkpoint with wrap-up reason
- **95% (`should_stop`):** Final checkpoint save before forced exit

The budget message at 50% ("`Consider checkpointing your current progress`") becomes actionable — the system now actually checkpoints, rather than just advising the model to.

### Doc 090 — Token-Aware Context Management

Checkpoint injection must respect token budgets. The `format_checkpoint_context()` output targets **500–2000 tokens** depending on richness:

- Minimal (last request + git state): ~500 tokens
- Rich (tool records + failed approaches + files): ~1500 tokens
- Maximum (all fields populated): ~2000 tokens

If the context pipeline (doc 090) determines the conversation is near the token limit, checkpoint context should be truncated to the minimal form:

```python
def format_checkpoint_context(checkpoint, max_tokens: int = 2000) -> str:
    # If tight on space, only include: summary + failed approaches + open TODOs
    # Drop: individual tool records, file lists, decisions
```

This truncation logic lives in `format_checkpoint_context()` and is controlled by a `max_tokens` parameter passed from the context pipeline.

---

## Priority & Ordering

| # | Change | Severity | Effort | Dependencies |
|---|--------|----------|--------|-------------|
| 1 | Extend `LightweightCheckpoint` | **Foundation** | 30 min | None |
| 2 | SpacetimeDB table + reducers | **Foundation** | 45 min | SpacetimeDB module publish |
| 3 | `save_checkpoint` / `load_checkpoint` client methods | **Required** | 20 min | #1, #2 |
| 4 | Auto-save at budget thresholds in `loop.py` | **Critical** | 45 min | #1, #3 |
| 5 | Resume integration in `build_continuation_context` | **Critical** | 30 min | #3 |
| 6 | Checkpoint invalidation hooks | **Required** | 20 min | #3 |
| 7 | `format_checkpoint_context` token-aware truncation | **Nice-to-have** | 20 min | #1 |

---

## Files Affected

- `backend/app/agent/continuation.py` — extend `LightweightCheckpoint`, add persistence methods, enhance `format_checkpoint_context`, integrate into `build_continuation_context`
- `backend/app/agent/loop.py` — auto-save at budget thresholds, checkpoint on loop exit
- `backend/app/agent/loop_state.py` — no changes (existing `ToolMetrics` stays as-is for aggregate tracking; `ToolCallRecord` in checkpoint is for per-call resumption context — different purpose)
- SpacetimeDB module — new `checkpoints` table, `upsert_checkpoint` / `delete_checkpoint` / `delete_expired_checkpoints` reducers

**Files NOT affected (avoiding duplication):**
- ~~`backend/app/agent/checkpoint.py`~~ — no new file; everything goes in `continuation.py`
- ~~`backend/app/agent/context/context_store.py`~~ — path doesn't exist; `ContextStore` (`context_store.py`) is for FTS5/sqlite-vec semantic search, not structured checkpoint data
- ~~`backend/app/worker.py`~~ — the container worker proxies to `loop.py`; checkpoint logic belongs in the loop

---

## Risks

- **Checkpoint staleness** — references files that changed between turns. Mitigation: 1-hour TTL via scheduled reducer; one-shot deletion after resumption; git state is re-checked on resume.
- **SpacetimeDB write latency** — checkpoint saves add a reducer call. Mitigation: saves happen at most 2-3 times per turn (budget thresholds + exit), not after every tool call. Async fire-and-forget is acceptable — if the save fails, the loop continues.
- **Resumption confusion** — model sees work it didn't do. Mitigation: clear labeling ("Resuming Previous Work"), explicit instruction to verify state before acting, and one-shot deletion prevents double-resumption.
- **Schema migration** — adding the `checkpoints` table requires a SpacetimeDB module publish. Mitigation: new table with no foreign keys — additive change, no impact on existing tables.

---

## Not Addressed Here

- **Multi-agent checkpoint coordination** — when agent A delegates to agent B, should B inherit A's checkpoint? Future work.
- **Checkpoint diffing** — comparing two checkpoints to detect regression. Future work.
- **Frontend checkpoint UI** — showing "resumable" state on conversations. The SpacetimeDB subscription makes this trivial, but the UI component is out of scope.
