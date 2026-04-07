# Design Doc 096: Progress Checkpointing (Revised)

**Status:** Revised  
**Date:** 2026-04-02 (original) · 2026-04-07 (revised) · 2026-04-07 (second revision — full continuation coverage)  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements  
**Revision note:** Updated to unify with existing `continuation.py` infrastructure, use SpacetimeDB for persistence (sqlite-vec only for embeddings), target `loop.py` as the integration point, and align with `IterationBudget` / `LoopState` / `ToolMetrics`. Second revision adds comprehensive continuation scenario coverage for all loop exit paths including interrupts, container kills, auto-delegation handoffs, race conditions, and work plan / checkpoint merging.

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
| `interrupts.py` | `interrupts.py` | `set_interrupt()` / `check_interrupt()` / `is_interrupted()` for turn cancellation | Not wired into `loop.py` tool-use loop — interrupts are not checked during iteration |
| Auto-delegation | `loop.py:757-809` | On budget exhaustion, delegates to `coding_agent` via `build_handoff_context()` | Handoff context doesn't include checkpoint data (failed approaches, tool records) |

**The core gap:** None of these persist checkpoint data across turns. If the process dies, everything in `LoopState`, `IterationBudget`, and `LightweightCheckpoint` is gone.

---

## Design Principles

1. **Extend, don't duplicate.** Build on `LightweightCheckpoint`, `ToolMetrics`, and `IterationBudget` — do not create parallel data structures.
2. **SpacetimeDB for persistence.** All non-embedding state goes to SpacetimeDB. No phantom key-value stores, no SQLite for structured data.
3. **Save at thresholds, not every call.** Use the existing `IterationBudget` thresholds to trigger saves — not after every tool call.
4. **Resumption is advisory.** Checkpoints inform the model; they don't replay actions. The model always verifies current state before acting.
5. **Every exit path saves.** Interrupts, crashes, budget exhaustion, container kills — all must produce a checkpoint or acknowledge they can't.

---

## Continuation Scenario Coverage

This section enumerates every combination of how a conversation can pause and resume, and how checkpointing handles each one.

### Complete Scenario Matrix

| # | Scenario | How the loop exits | Checkpoint saved? | How it resumes | Section |
|---|----------|--------------------|-------------------|----------------|---------|
| 1 | Budget exhaustion (normal) | `IterationBudget.should_stop` → loop ends | ✅ At 50% threshold + on exit | `classify_intent()` → CONTINUE → load from SpacetimeDB | §4, §5 |
| 2 | User says "continue" | N/A — new turn starts | N/A | Load persisted checkpoint → inject via `format_checkpoint_context` | §5 |
| 3 | User says "continue" with adjustment | N/A — new turn starts | N/A | Load checkpoint + user's adjustment modifies plan | §5 |
| 4 | User sends new task | N/A — new turn starts | N/A | `classify_intent()` → NEW_TASK → `delete_checkpoint()` | §6 |
| 5 | Crash/exception in loop | `except Exception` in loop wrapper | ✅ In `finally` block | Load checkpoint on next turn | §4, §8.1 |
| 6 | Normal completion (`respond` tool) | `_terminal` flag → return | ✅ With `stop_reason="completed"` | Available for follow-up questions | §4 |
| 7 | TTL expiry | N/A | Scheduled reducer cleans up | No checkpoint available — fresh start | §6 |
| 8 | **User interrupt (cancel mid-turn)** | `check_interrupt()` → break | ✅ In interrupt handler (NEW) | Load partial checkpoint on next turn | §8.2 |
| 9 | **Auto-delegation to `coding_agent`** | Budget exhausted + edits made → delegate | ✅ Checkpoint feeds into handoff (NEW) | Coding agent gets enriched context | §8.3 |
| 10 | **`coding_agent` completes/fails** | Sub-agent finishes → tool result returned | Parent checkpoint invalidated (NEW) | Parent re-evaluates from fresh state | §8.4 |
| 11 | **Container kill / OOM / SIGKILL** | Process killed — no cleanup runs | ⚠️ Best-effort via backend shadow (NEW) | Backend's last-known-state used as fallback | §8.5 |
| 12 | **LLM provider error / rate limit** | Exception from `chat_completion` | ✅ Only for fatal errors (NEW) | Transient errors → no checkpoint, just retry | §8.6 |
| 13 | **Token overflow on resume** | Context compaction triggers | N/A | Checkpoint truncated to minimal form (NEW) | §8.7 |
| 14 | **User sends new message while turn running** | Interrupt + new turn starts | ✅ With race condition guard (NEW) | Atomic save-then-evaluate on new turn | §8.8 |
| 15 | **Work plan exists + checkpoint exists** | N/A — both available on resume | N/A | Merge strategy: checkpoint enriches plan (NEW) | §8.9 |
| 16 | **Cross-channel continuation** | User starts on web, continues on Telegram | N/A | Same `conversation_id` → same checkpoint | §8.10 |

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
    git_head_sha: str | None = None       # NEW: for divergence detection
    git_branch: str | None = None         # NEW: for divergence detection

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

    def capture_git_state(self, workspace_dir: str) -> None:
        """Snapshot current git HEAD and branch for divergence detection on resume."""
        import subprocess
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if sha.returncode == 0:
                self.git_head_sha = sha.stdout.strip()
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if branch.returncode == 0:
                self.git_branch = branch.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def to_dict(self) -> dict:
        """Serialize for SpacetimeDB storage."""
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
            "git_head_sha": self.git_head_sha,
            "git_branch": self.git_branch,
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
            git_head_sha=data.get("git_head_sha"),
            git_branch=data.get("git_branch"),
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

**Why:** This unifies the proposed `Checkpoint` dataclass with the existing `LightweightCheckpoint`. No new file needed. The existing `build_checkpoint_from_history()` and `format_checkpoint_context()` continue to work — they just gain access to richer data when it's available. The new `git_head_sha` and `git_branch` fields enable divergence detection on resume (§8.5, §6).

### 2. Enhance `format_checkpoint_context()` for Rich Checkpoints

**File:** `backend/app/agent/continuation.py` (modify existing)

Extend the existing formatter to include the new fields when present, with token-aware truncation:

```python
def format_checkpoint_context(
    checkpoint: LightweightCheckpoint,
    max_tokens: int = 2000,
) -> str:
    """Format a checkpoint as context for a continuation turn.

    Target: ~500-2000 tokens depending on richness and available budget.
    
    Args:
        checkpoint: The checkpoint to format.
        max_tokens: Maximum token budget for checkpoint context.
            Passed from the context pipeline (Doc 090) based on
            available headroom in the conversation's token budget.
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

    # Estimate tokens so far (~4 chars per token)
    _current_len = sum(len(line) for line in lines) // 4

    # Rich fields — include only if we have token budget
    if checkpoint.completed_actions and _current_len < max_tokens * 0.6:
        lines.append(f"## Progress: {checkpoint.successful_tool_calls} successful / "
                      f"{checkpoint.failed_tool_calls} failed across {checkpoint.turn_number} turns")
        recent = checkpoint.completed_actions[-5:]
        for action in recent:
            status = "✅" if action.success else "❌"
            lines.append(f"  {status} {action.tool_name}: {action.output_summary}")
        lines.append("")

    if checkpoint.files_modified and _current_len < max_tokens * 0.7:
        lines.append("## Files Modified")
        for f in checkpoint.files_modified:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.uncommitted_changes and _current_len < max_tokens * 0.75:
        lines.append("## Uncommitted Changes (git)")
        for f in checkpoint.uncommitted_changes:
            lines.append(f"- {f}")
        lines.append("")

    # These are high-value even when tight on space — always include
    if checkpoint.failed_approaches:
        lines.append("## Approaches That Failed (don't retry)")
        for approach in checkpoint.failed_approaches:
            lines.append(f"  ⚠️ {approach}")
        lines.append("")

    if checkpoint.decisions and _current_len < max_tokens * 0.85:
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

**Token budget integration (Doc 090):** The caller determines available headroom and passes `max_tokens`. When tight on space, the function prioritizes: summary → failed approaches → open TODOs → everything else. Failed approaches are always included regardless of budget because they prevent wasted iterations.

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

**File:** `backend/app/agent/loop.py` (modify existing tool-use loop, starting ~line 470)

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
    loop_checkpoint.capture_git_state(workspace_dir)
    await save_checkpoint(conversation_id, agent_id, loop_checkpoint)
    checkpoint_saved_this_threshold = True
    logger.info("Checkpoint saved at %s budget", f"{budget.pct_used:.0%}")
```

**On loop exit (any reason) — wrapped in try/finally:**

```python
# After the loop ends — whether normally, crash, timeout, or budget exhaustion
try:
    loop_checkpoint.stop_reason = _classify_stop_reason(loop_state, budget, error)
    loop_checkpoint.progress_summary = _summarize_progress(loop_state, loop_checkpoint)
    # Merge git state
    checkpoint_from_git = build_checkpoint_from_history(messages, workspace_dir)
    loop_checkpoint.uncommitted_changes = checkpoint_from_git.uncommitted_changes
    loop_checkpoint.capture_git_state(workspace_dir)
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
| Loop exit — interrupt | `check_interrupt()` returns True (§8.2) | User cancelled mid-turn |
| Auto-delegation | Before calling `coding_agent` (§8.3) | Hand off enriched context to sub-agent |

**What we do NOT do:** Save after every single tool call. The in-memory `loop_checkpoint` object accumulates tool records cheaply. SpacetimeDB writes happen only at thresholds.

### 5. Resume from Checkpoint on Continuation

**File:** `backend/app/agent/continuation.py` (modify existing `build_continuation_context`)

Integrate checkpoint loading into the existing continuation flow:

```python
async def build_continuation_context(
    plan_position: PlanPosition | None,
    checkpoint: LightweightCheckpoint | None,  # from build_checkpoint_from_history
    conversation_id: str | None = None,        # NEW: for SpacetimeDB lookup
    workspace_dir: str | None = None,          # NEW: for git divergence check
    max_tokens: int = 2000,                    # NEW: token budget from Doc 090
) -> str:
    """Build minimal recovery context for a continuation turn.

    Priority:
    1. Persisted checkpoint from SpacetimeDB (richest — has tool records, failed approaches)
       — merged with plan position if both exist (§8.9)
    2. Plan position context (if active work plan exists)
    3. History-derived checkpoint (fallback — last request/action + git state)
    """
    # Try loading persisted checkpoint first
    persisted = None
    if conversation_id:
        persisted = await load_checkpoint(conversation_id)

    if persisted:
        # Check for git divergence (§8.5, §6)
        if workspace_dir and persisted.git_branch:
            diverged = _check_git_divergence(persisted, workspace_dir)
            if diverged:
                logger.warning("Checkpoint git state diverged — discarding")
                await delete_checkpoint(conversation_id)
                persisted = None

    if persisted:
        # Merge: persisted checkpoint has tool records; history checkpoint has fresh git state
        if checkpoint:
            persisted.uncommitted_changes = checkpoint.uncommitted_changes

        # §8.9: If work plan also exists, merge plan items into checkpoint context
        if plan_position:
            persisted = _merge_plan_into_checkpoint(persisted, plan_position)

        context = format_checkpoint_context(persisted, max_tokens=max_tokens)
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
| Git branch divergence | On resume, compare HEAD SHA and branch name | Discard if branch changed or HEAD diverged significantly |
| Coding agent modified checkpoint files | On `coding_agent` tool result (§8.4) | Invalidate parent checkpoint |

**Implementation in the loop entry point:**

```python
# At the start of agent_turn:
intent = classify_intent(user_message, has_active_plan)
if intent == ContinuationIntent.NEW_TASK:
    await delete_checkpoint(conversation_id)
```

**Git divergence check:**

```python
def _check_git_divergence(
    checkpoint: LightweightCheckpoint,
    workspace_dir: str,
) -> bool:
    """Return True if git state has diverged enough to invalidate the checkpoint."""
    import subprocess
    try:
        # Check branch
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=workspace_dir, timeout=5,
        )
        if branch.returncode == 0 and checkpoint.git_branch:
            if branch.stdout.strip() != checkpoint.git_branch:
                return True  # Different branch — checkpoint is stale

        # Check HEAD divergence
        if checkpoint.git_head_sha:
            # Is the checkpoint's HEAD still an ancestor of current HEAD?
            merge_base = subprocess.run(
                ["git", "merge-base", "--is-ancestor", checkpoint.git_head_sha, "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if merge_base.returncode != 0:
                return True  # HEAD has diverged (force push, reset, etc.)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Can't check — assume not diverged
    return False
```

---

## §8. Additional Continuation Scenarios

These scenarios were identified as gaps in the original design and are now fully addressed.

### 8.1. Crash / Exception in Loop

**How it happens:** An unhandled exception propagates out of the tool-use loop — a bug in tool execution, an unexpected API response shape, or a Python runtime error.

**Current behavior:** The exception propagates to `agent_turn()`'s caller. No state is saved.

**With checkpointing:**

The tool-use loop is wrapped in `try/finally`. The `finally` block always attempts to save:

```python
# In loop.py agent_turn(), wrapping the tool-use for loop:
loop_checkpoint = LightweightCheckpoint()
_exit_error = None

try:
    for iteration in range(max_iterations):
        # ... existing tool-use loop ...
        pass
except Exception as exc:
    _exit_error = exc
    raise  # Re-raise after checkpoint save
finally:
    # Save checkpoint regardless of how we exited
    try:
        loop_checkpoint.stop_reason = (
            f"error: {type(_exit_error).__name__}: {_exit_error}"
            if _exit_error
            else _classify_stop_reason(loop_state, budget, None)
        )
        loop_checkpoint.progress_summary = _summarize_progress(loop_state, loop_checkpoint)
        if workspace_dir:
            checkpoint_from_git = build_checkpoint_from_history(messages, workspace_dir)
            loop_checkpoint.uncommitted_changes = checkpoint_from_git.uncommitted_changes
            loop_checkpoint.capture_git_state(workspace_dir)
        await save_checkpoint(conversation_id, agent_id, loop_checkpoint)
    except Exception:
        logger.warning("Failed to save exit checkpoint", exc_info=True)
```

**On resume:** The checkpoint's `stop_reason` will be `"error: ..."`, which the model can use to understand why the previous attempt failed and adjust its approach.

### 8.2. User Interrupt (Cancel Mid-Turn)

**How it happens:** The user clicks "Stop" or sends a new message while a turn is running. The gateway calls `set_interrupt(conversation_id)` in `interrupts.py`. Currently, `check_interrupt()` exists but is **not called** inside `loop.py`'s tool-use loop.

**Current behavior:** The interrupt flag is set but the loop doesn't check it. The turn runs to completion or budget exhaustion. This is a bug independent of checkpointing.

**With checkpointing — two changes required:**

**Change 1: Wire `check_interrupt()` into the tool-use loop:**

```python
# In loop.py, at the top of each iteration:
from backend.app.agent.interrupts import check_interrupt

for iteration in range(max_iterations):
    # Check for user interrupt before each LLM call
    if check_interrupt(conversation_id):
        logger.info("Turn interrupted by user at iteration %d", iteration)
        loop_checkpoint.stop_reason = "interrupted"
        break

    # ... rest of iteration (LLM call, tool execution) ...

    # Also check after tool execution (long tools may take minutes)
    if check_interrupt(conversation_id):
        logger.info("Turn interrupted by user after tool execution at iteration %d", iteration)
        loop_checkpoint.stop_reason = "interrupted"
        break
```

**Change 2: The `finally` block (§8.1) handles saving.** No additional code needed — the interrupt causes a `break`, the loop ends, and the `finally` block saves the checkpoint with `stop_reason="interrupted"`.

**On resume:** The checkpoint shows partial progress. The model sees what was done before the user cancelled and can continue or take a different direction based on the user's new message.

### 8.3. Auto-Delegation to `coding_agent` on Budget Exhaustion

**How it happens:** At `loop.py:757-809`, when the budget is exhausted AND the agent made file edits AND `coding_agent` is available, the loop builds a `handoff_context` via `build_handoff_context(messages)` and spawns a coding agent.

**Current behavior:** The handoff context only includes `files_read` and `edits_made` extracted from message history. It does not include failed approaches, discovered patterns, or tool call records.

**With checkpointing — enrich the handoff:**

```python
# In loop.py, replacing the existing auto-delegation block (~line 757):

if _has_edits and not _called_coding_agent and "coding_agent" in all_enabled_tools:
    try:
        from backend.app.agent.pre_gather import build_handoff_context
        handoff_ctx = build_handoff_context(messages)
        _user_msg = user_message[:2000]
        _working_dir = ...  # existing workspace resolution

        # NEW: Enrich handoff with checkpoint data
        _checkpoint_section = ""
        if loop_checkpoint.failed_approaches:
            _checkpoint_section += (
                "\n## Approaches That Failed (don't retry)\n"
                + "\n".join(f"- {a}" for a in loop_checkpoint.failed_approaches)
                + "\n"
            )
        if loop_checkpoint.decisions:
            _checkpoint_section += (
                "\n## Decisions Already Made\n"
                + "\n".join(f"- {d}" for d in loop_checkpoint.decisions)
                + "\n"
            )

        _handoff_task = (
            f"CONTINUE AND COMPLETE this task that ran out of iteration budget.\n\n"
            f"## Original User Request\n{_user_msg}\n\n"
            f"## Files Already Read\n{handoff_ctx['files_read']}\n\n"
            f"## Changes Already Made\n{handoff_ctx['edits_made']}\n\n"
            f"{_checkpoint_section}"
            f"## Instructions\n"
            f"Pick up where the previous agent left off. Complete the remaining work."
        )

        # Save checkpoint BEFORE delegation so it's available if the coding agent also fails
        loop_checkpoint.stop_reason = "auto_delegated"
        loop_checkpoint.capture_git_state(_working_dir)
        await save_checkpoint(conversation_id, agent_id, loop_checkpoint)

        _ca_result = await registry.execute("coding_agent", {
            "task": _handoff_task,
            "working_directory": _working_dir,
            "agent_type": "claude",
            "timeout_minutes": 30,
        }, tool_context)

        if not _ca_result.get("error"):
            logger.info("Auto-delegated to coding_agent from loop.py")
            # Invalidate checkpoint — coding agent took over
            await delete_checkpoint(conversation_id)
            return (
                "I used all my iterations exploring the codebase, so I've handed off "
                "the remaining work to a coding agent running in the background."
            )
    except Exception as e:
        logger.error("Auto-delegation failed in loop.py: %s", e)
        # Checkpoint remains — user can manually continue
```

**Key behaviors:**
- Checkpoint is saved **before** delegation (safety net if coding agent also fails)
- Failed approaches and decisions are injected into the handoff task
- Checkpoint is **deleted** after successful delegation (coding agent owns the work now)
- If delegation fails, the checkpoint persists for manual user continuation

### 8.4. Coding Agent Completes or Fails

**How it happens:** The `coding_agent` tool runs as a sub-process. When it finishes, the tool returns a result to the parent loop. The coding agent may have modified files that the parent's checkpoint references.

**Current behavior:** No coordination. The parent's checkpoint (if any) may reference stale file state.

**With checkpointing:**

When a `coding_agent` tool result is received in the parent loop, invalidate the parent's checkpoint if the coding agent modified files:

```python
# In the tool result handler, after coding_agent returns:
if tool_name == "coding_agent" and tool_result.get("success"):
    # The coding agent has modified files — our checkpoint's file references are stale
    await delete_checkpoint(conversation_id)
    logger.info("Deleted parent checkpoint after successful coding_agent execution")
```

**If the coding agent fails:** The parent's checkpoint remains valid. The user can say "continue" and the parent agent resumes with full context of what was attempted.

**Future work (not in this doc):** True multi-agent checkpoint coordination where the coding agent's progress is merged back into the parent checkpoint. For now, the simple rule is: successful sub-agent → invalidate parent checkpoint.

### 8.5. Container Kill / OOM / SIGKILL

**How it happens:** The sandbox container is killed by Docker (idle timeout via `SandboxManager`, OOM killer, `docker stop`, host restart). The process receives `SIGKILL` — no signal handlers run, no `finally` blocks execute, no cleanup happens.

**Current behavior:** All in-memory state is lost. No checkpoint is saved.

**With checkpointing — defense in depth:**

**Layer 1: The 50% budget threshold checkpoint (already covered in §4).** If the loop reached 50% before being killed, a checkpoint exists in SpacetimeDB. This is the primary defense.

**Layer 2: Backend shadow checkpoint (NEW).** The backend already receives SSE events from the agent loop (tool calls, results, text chunks). It can maintain a lightweight "last known state" without any changes to the container:

```python
# In the backend's SSE event handler (where it proxies agent responses):

class TurnShadowState:
    """Backend-side shadow of the agent's progress, updated from SSE events."""
    def __init__(self, conversation_id: str, agent_id: str):
        self.conversation_id = conversation_id
        self.agent_id = agent_id
        self.tool_calls: list[dict] = []
        self.last_text: str = ""
        self.started_at: datetime = datetime.now(timezone.utc)

    def on_tool_call(self, tool_name: str, args_summary: str) -> None:
        self.tool_calls.append({"tool": tool_name, "args": args_summary[:100]})

    def on_tool_result(self, tool_name: str, success: bool, summary: str) -> None:
        if self.tool_calls and self.tool_calls[-1]["tool"] == tool_name:
            self.tool_calls[-1]["success"] = success
            self.tool_calls[-1]["output"] = summary[:200]

    def on_text(self, text: str) -> None:
        self.last_text = text[-500:]  # Keep last 500 chars

    async def save_as_fallback(self) -> None:
        """Save shadow state as a checkpoint if no real checkpoint exists."""
        existing = await load_checkpoint(self.conversation_id)
        if existing:
            return  # Real checkpoint exists — don't overwrite

        fallback = LightweightCheckpoint(
            last_assistant_action=self.last_text,
            stop_reason="container_killed",
            progress_summary=f"Container was killed after {len(self.tool_calls)} tool calls. "
                           f"This is a best-effort recovery from SSE events.",
            total_tool_calls=len(self.tool_calls),
        )
        for tc in self.tool_calls[-10:]:
            fallback.completed_actions.append(ToolCallRecord(
                tool_name=tc["tool"],
                arguments_summary=tc.get("args", ""),
                success=tc.get("success", False),
                output_summary=tc.get("output", ""),
                turn_number=0,
            ))
        await save_checkpoint(self.conversation_id, self.agent_id, fallback)
```

**When to trigger `save_as_fallback()`:** When the backend detects the container is gone (SSE stream drops, health check fails, `SandboxManager` reports container missing) AND the turn was still active.

**Limitations acknowledged:**
- Shadow checkpoints are less rich than real checkpoints (no git state, no file lists, no failed approaches)
- There's a window between the last SSE event and the kill where tool calls are lost
- This is explicitly a **best-effort fallback**, not a guarantee

### 8.6. LLM Provider Error / Rate Limit

**How it happens:** The LLM API returns a 429 (rate limit), 500 (server error), 503 (overloaded), or times out. `_llm_call_with_overflow_recovery()` attempts retries and model fallback.

**Current behavior:** If all retries fail, an exception propagates.

**With checkpointing — distinguish transient vs. fatal:**

```python
def _classify_stop_reason(loop_state, budget, error) -> str:
    """Classify why the loop stopped for checkpoint metadata."""
    if error is None:
        if budget.should_stop:
            return "budget_exhausted"
        return "completed"

    error_str = str(error).lower()

    # Transient errors — don't checkpoint, the next turn should just retry
    TRANSIENT_PATTERNS = ["rate_limit", "429", "503", "overloaded", "timeout", "connection"]
    if any(p in error_str for p in TRANSIENT_PATTERNS):
        return "transient_error"

    # Fatal errors — checkpoint with error details
    return f"error: {type(error).__name__}: {error}"
```

**Behavior by error type:**

| Error type | Checkpoint? | Why |
|-----------|------------|-----|
| Rate limit (429) | ✅ Save but mark as `transient_error` | Checkpoint is valid; next turn should resume, not restart |
| Server error (500/503) | ✅ Save but mark as `transient_error` | Same — work is valid, provider is temporarily down |
| Timeout | ✅ Save but mark as `transient_error` | Same |
| Auth error (401/403) | ✅ Save with `error: AuthenticationError` | Work is valid but can't continue until key is fixed |
| Malformed response | ✅ Save with `error: ...` | May indicate a model issue; checkpoint preserves progress |
| Python runtime error | ✅ Save with `error: ...` | Bug in our code; checkpoint preserves progress for retry after fix |

**On resume from transient error:** The checkpoint is loaded normally. The model sees `stop_reason: transient_error` and knows the previous approach was working — it should continue, not pivot.

**On resume from fatal error:** The model sees the specific error and can adjust (e.g., if an auth error, it might tell the user to check their API key).

### 8.7. Token Overflow on Resume (Circular Budget Problem)

**How it happens:** A conversation is already near the token limit. The user says "continue". We load a checkpoint and inject it into context. The injected checkpoint pushes the context over the limit, triggering `_check_token_budget()` which aggressively compacts messages — potentially removing the checkpoint context we just added.

**Current behavior:** Not applicable (no checkpoints injected today).

**With checkpointing — prevent the circular problem:**

**Step 1: Measure available headroom BEFORE injecting checkpoint:**

```python
# In the continuation flow, before calling format_checkpoint_context:
from backend.app.agent.loop import _estimate_token_count

current_tokens = _estimate_token_count(messages)
model_limit = _get_model_context_limit(model_string)
available_headroom = model_limit - current_tokens - 1000  # 1000 token safety margin

# Cap checkpoint context to available headroom
checkpoint_max_tokens = min(2000, max(300, available_headroom))
checkpoint_context = format_checkpoint_context(persisted, max_tokens=checkpoint_max_tokens)
```

**Step 2: `format_checkpoint_context` truncation tiers (already shown in §2):**

| Available tokens | What's included |
|-----------------|-----------------|
| ≥ 1500 | Full checkpoint: summary, tool records, files, failed approaches, decisions, TODOs |
| 800–1500 | Medium: summary, failed approaches, TODOs, files modified |
| 300–800 | Minimal: summary, failed approaches, open TODOs only |
| < 300 | Ultra-minimal: one-line summary + failed approaches only |

**Step 3: Inject checkpoint as a system message, not a user message.** System messages are compacted last by `_check_token_budget()` and `_aggressive_compact()`, so checkpoint context survives longer:

```python
# Inject as a system message appended after the main system prompt
messages.insert(1, {
    "role": "system",
    "content": checkpoint_context,
})
```

### 8.8. Race Condition: User Sends New Message While Turn Running

**How it happens:** User sends message A. Turn A starts. While turn A is running, user sends message B. The gateway calls `set_interrupt()` for turn A, then starts turn B. Now two things can happen concurrently:
1. Turn A's `finally` block tries to save a checkpoint
2. Turn B's entry point tries to delete the checkpoint (if it's a NEW_TASK) or load it (if it's a CONTINUE)

**Current behavior:** No checkpoints exist, so no race. But with checkpointing, this becomes a real concern.

**Solution: Sequence guarantee via interrupt → wait → new turn:**

The gateway already serializes turns per conversation (one active turn at a time). The race condition is between turn A's cleanup and turn B's startup. The fix:

```python
# In the backend's turn handler (where it receives the gateway's request):

async def handle_turn(conversation_id: str, message: str, ...):
    # Wait for any interrupted turn to finish its cleanup
    # interrupts.py already tracks active turns via register_turn/unregister_turn
    if is_turn_active(conversation_id):
        set_interrupt(conversation_id)
        # Wait up to 5 seconds for the interrupted turn to finish
        for _ in range(50):
            if not is_turn_active(conversation_id):
                break
            await asyncio.sleep(0.1)
        else:
            logger.warning("Interrupted turn didn't finish cleanup in 5s — proceeding anyway")

    # Now safe to start the new turn
    register_turn(conversation_id)
    try:
        # Checkpoint operations here are safe — old turn is done
        intent = classify_intent(message, has_active_plan)
        if intent == ContinuationIntent.NEW_TASK:
            await delete_checkpoint(conversation_id)
        # ... proceed with turn ...
    finally:
        unregister_turn(conversation_id)
```

**Key guarantee:** Turn B never starts its checkpoint operations until turn A has finished its `finally` block (including checkpoint save). The 5-second timeout is a safety valve — if turn A is truly stuck, we proceed anyway and accept potential checkpoint inconsistency (which is no worse than the current no-checkpoint behavior).

### 8.9. Work Plan Exists + Checkpoint Exists (Merge Strategy)

**How it happens:** The agent was working on a task with an active work plan. The turn exhausted its budget. A checkpoint was saved. On resume, both the work plan (in SpacetimeDB) and the checkpoint are available.

**Current behavior:** `build_continuation_context()` uses plan position only. No checkpoint exists to conflict.

**With checkpointing — merge, don't replace:**

The original design said "persisted checkpoint wins (priority 1) over plan position (priority 2)." This is wrong. A work plan has richer structure (item-level progress, dependencies, titles). The correct strategy is:

**Checkpoint enriches the plan-based context:**

```python
def _merge_plan_into_checkpoint(
    checkpoint: LightweightCheckpoint,
    plan_position: PlanPosition,
) -> LightweightCheckpoint:
    """Merge work plan progress into checkpoint for unified resumption context.

    The plan provides structure (what items exist, which are done).
    The checkpoint provides details (what was tried, what failed, what files changed).
    """
    # Add plan items as TODOs (replacing checkpoint's open_todos with plan-aware ones)
    plan_todos = []
    for item in plan_position.pending_items:
        plan_todos.append(f"[Plan item {item.ordinal}] {item.title}")
    if plan_position.current_item:
        plan_todos.insert(0, f"[IN PROGRESS] {plan_position.current_item.title}")

    # Plan TODOs take precedence over checkpoint TODOs (more structured)
    if plan_todos:
        checkpoint.open_todos = plan_todos

    # Add plan progress to summary
    plan_summary = (
        f"Work plan: {plan_position.completed_count}/{plan_position.total_items} items complete "
        f"({plan_position.progress_pct:.0%})"
    )
    if checkpoint.progress_summary:
        checkpoint.progress_summary = f"{plan_summary}\n{checkpoint.progress_summary}"
    else:
        checkpoint.progress_summary = plan_summary

    return checkpoint
```

**Result:** The formatted context includes:
- Plan structure (which items are done, which remain) — from the work plan
- Failed approaches (what didn't work) — from the checkpoint
- Tool call history (what was executed) — from the checkpoint
- File modifications (what changed) — from the checkpoint
- Git state (uncommitted changes) — from the checkpoint

### 8.10. Cross-Channel Continuation

**How it happens:** A user starts a conversation on the web UI, then sends "continue" from Telegram (or Discord, Slack, WhatsApp). The message arrives via a different channel but targets the same `conversation_id`.

**Current behavior:** `classify_intent()` works on message text regardless of channel. The conversation ID is resolved by the gateway's channel handler.

**With checkpointing — no special handling needed, but document the assumption:**

Checkpoints are keyed by `conversation_id`, not by channel. As long as the gateway correctly maps the incoming channel message to the right conversation, checkpoint load/save works identically.

**Assumption:** The gateway's channel handlers (Telegram, Discord, Slack, WhatsApp, WebChat) all resolve to the same `conversation_id` for the same logical conversation. This is already true — each channel maps a user+thread to a conversation.

**Edge case:** If a user has separate conversations per channel (not linked), then "continue" on Telegram creates a new conversation with no checkpoint. This is correct behavior — the checkpoint belongs to the web conversation, not the Telegram one. The user would need to explicitly reference the same conversation.

**No code changes required for this scenario.**

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

The caller measures available headroom (§8.7) and passes `max_tokens` to `format_checkpoint_context()`. Failed approaches are always included regardless of budget because they prevent wasted iterations.

Checkpoint context is injected as a **system message** (not user message) so it survives aggressive compaction longer.

---

## Priority & Ordering

| # | Change | Severity | Effort | Dependencies |
|---|--------|----------|--------|-------------|
| 1 | Extend `LightweightCheckpoint` (§1) | **Foundation** | 30 min | None |
| 2 | SpacetimeDB table + reducers (§3) | **Foundation** | 45 min | SpacetimeDB module publish |
| 3 | `save_checkpoint` / `load_checkpoint` client methods (§3) | **Required** | 20 min | #1, #2 |
| 4 | Auto-save at budget thresholds in `loop.py` (§4) | **Critical** | 45 min | #1, #3 |
| 5 | Resume integration in `build_continuation_context` (§5) | **Critical** | 30 min | #3 |
| 6 | Checkpoint invalidation hooks (§6) | **Required** | 20 min | #3 |
| 7 | Wire `check_interrupt()` into loop.py (§8.2) | **Critical** | 15 min | None (independent fix) |
| 8 | Enrich auto-delegation handoff (§8.3) | **Important** | 30 min | #1, #4 |
| 9 | Coding agent checkpoint invalidation (§8.4) | **Important** | 15 min | #3 |
| 10 | Backend shadow checkpoint (§8.5) | **Nice-to-have** | 60 min | #3 |
| 11 | LLM error classification (§8.6) | **Important** | 20 min | #4 |
| 12 | Token overflow prevention (§8.7) | **Required** | 30 min | #2 (format_checkpoint_context) |
| 13 | Race condition guard (§8.8) | **Required** | 30 min | #7 |
| 14 | Work plan + checkpoint merge (§8.9) | **Important** | 20 min | #5 |
| 15 | `format_checkpoint_context` token-aware truncation (§2) | **Required** | 20 min | #1 |

**Recommended implementation order:** 1 → 2 → 3 → 7 → 4 → 15 → 5 → 6 → 13 → 8 → 11 → 9 → 14 → 12 → 10

Note: §7 (wire `check_interrupt`) is prioritized early because it's an independent bug fix that improves the system regardless of checkpointing.

---

## Files Affected

- `backend/app/agent/continuation.py` — extend `LightweightCheckpoint`, add persistence methods, enhance `format_checkpoint_context`, integrate into `build_continuation_context`, add `_merge_plan_into_checkpoint`, add `_check_git_divergence`
- `backend/app/agent/loop.py` — auto-save at budget thresholds, checkpoint on loop exit, wire `check_interrupt()` into tool-use loop, enrich auto-delegation handoff, invalidate checkpoint on `coding_agent` result
- `backend/app/agent/interrupts.py` — no changes (existing API is sufficient; `check_interrupt` just needs to be called)
- `backend/app/agent/loop_state.py` — no changes (existing `ToolMetrics` stays as-is for aggregate tracking; `ToolCallRecord` in checkpoint is for per-call resumption context — different purpose)
- Backend SSE handler — add `TurnShadowState` for container kill fallback (§8.5)
- Backend turn handler — add interrupt-wait-then-start sequence (§8.8)
- SpacetimeDB module — new `checkpoints` table, `upsert_checkpoint` / `delete_checkpoint` / `delete_expired_checkpoints` reducers

**Files NOT affected (avoiding duplication):**
- ~~`backend/app/agent/checkpoint.py`~~ — no new file; everything goes in `continuation.py`
- ~~`backend/app/agent/context/context_store.py`~~ — path doesn't exist; `ContextStore` (`context_store.py`) is for FTS5/sqlite-vec semantic search, not structured checkpoint data

---

## Risks

- **Checkpoint staleness** — references files that changed between turns. Mitigation: 1-hour TTL via scheduled reducer; one-shot deletion after resumption; git divergence detection (§6); coding agent invalidation (§8.4).
- **SpacetimeDB write latency** — checkpoint saves add a reducer call. Mitigation: saves happen at most 2-3 times per turn (budget thresholds + exit), not after every tool call. Async fire-and-forget is acceptable — if the save fails, the loop continues.
- **Resumption confusion** — model sees work it didn't do. Mitigation: clear labeling ("Resuming Previous Work"), explicit instruction to verify state before acting, and one-shot deletion prevents double-resumption.
- **Schema migration** — adding the `checkpoints` table requires a SpacetimeDB module publish. Mitigation: new table with no foreign keys — additive change, no impact on existing tables.
- **Container kill gap** — between 0% and 50% budget, a container kill loses all progress. Mitigation: backend shadow checkpoint (§8.5) provides best-effort recovery. Accepted risk: shadow checkpoints are less rich than real checkpoints.
- **Race conditions** — concurrent turn cleanup and startup. Mitigation: interrupt-wait-then-start sequence (§8.8) with 5-second timeout. Worst case (timeout): no worse than current no-checkpoint behavior.
- **Token budget pressure** — checkpoint injection competes with conversation history for context space. Mitigation: token-aware truncation (§2, §8.7) with tiered detail levels. Failed approaches always survive truncation.

---

## Not Addressed Here (Future Work)

- **Multi-agent checkpoint coordination** — when agent A delegates to agent B, should B inherit A's checkpoint? Currently: A's checkpoint is invalidated on B's success (§8.4). Future: merge B's results back into A's checkpoint for richer handoff chains.
- **Checkpoint diffing** — comparing two checkpoints to detect regression. Useful for debugging "the agent keeps trying the same thing."
- **Frontend checkpoint UI** — showing "resumable" state on conversations. The SpacetimeDB subscription makes this trivial, but the UI component is out of scope.
- **Checkpoint-based replay** — using checkpoint history to replay a task for debugging or training. Requires storing checkpoint history rather than upserting.
- **Sub-agent checkpoint inheritance** — coding agent inheriting parent's checkpoint as initial context rather than just the handoff task. Requires the coding agent to understand checkpoint format.
