# Design Doc 096: Progress Checkpointing

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

When Bond's agent loop crashes, times out, or exhausts its turn budget mid-task, all progress is lost. The next attempt starts from scratch with no knowledge of:

1. **What was already completed** — files created, edits made, commands run
2. **What was discovered** — file locations, codebase patterns, error messages encountered
3. **What the plan was** — which steps were done and which remain
4. **What failed** — what approaches were tried and didn't work

This leads to duplicate work, repeated failures, and user frustration. Claude Code handles this with agent memory snapshots and command queuing for resumption.

---

## Changes

### 1. Define a Checkpoint Dataclass

**File:** `backend/app/agent/checkpoint.py` (new file)

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import json


@dataclass
class ToolCallRecord:
    """Record of a completed tool call."""
    tool_name: str
    arguments_summary: str
    success: bool
    output_summary: str
    duration_ms: int
    turn_number: int


@dataclass
class Checkpoint:
    """Snapshot of agent loop progress for resumption."""
    conversation_id: str
    task_id: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    turn_number: int = 0
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    completed_actions: list[ToolCallRecord] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    current_plan: Optional[str] = None
    progress_summary: str = ""
    last_error: Optional[str] = None
    failed_approaches: list[str] = field(default_factory=list)
    stop_reason: Optional[str] = None

    def record_tool_call(self, record: ToolCallRecord) -> None:
        self.completed_actions.append(record)
        self.total_tool_calls += 1
        if record.success:
            self.successful_tool_calls += 1
        else:
            self.failed_tool_calls += 1
        self.turn_number = record.turn_number
        if record.tool_name in ("file_write", "file_edit") and record.success:
            path = self._extract_path(record.arguments_summary)
            if path and path not in self.files_modified:
                self.files_modified.append(path)

    def add_failed_approach(self, description: str) -> None:
        if description not in self.failed_approaches:
            self.failed_approaches.append(description)

    def to_resumption_message(self) -> str:
        parts = ["📋 **Resuming from checkpoint** — here's what was already done:\n"]
        if self.progress_summary:
            parts.append(f"**Summary:** {self.progress_summary}\n")
        if self.completed_actions:
            parts.append(f"**Completed:** {self.successful_tool_calls} successful tool calls across {self.turn_number} turns.")
            recent = self.completed_actions[-5:]
            parts.append("Recent actions:")
            for action in recent:
                status = "✅" if action.success else "❌"
                parts.append(f"  {status} {action.tool_name}: {action.output_summary}")
        if self.files_modified:
            parts.append(f"\n**Files modified:** {', '.join(self.files_modified)}")
        if self.failed_approaches:
            parts.append("\n**Approaches that didn't work** (don't retry these):")
            for approach in self.failed_approaches:
                parts.append(f"  ⚠️ {approach}")
        if self.last_error:
            parts.append(f"\n**Last error before stopping:** {self.last_error}")
        if self.stop_reason:
            parts.append(f"\n**Why it stopped:** {self.stop_reason}")
        parts.append("\n**Continue from where this left off.** Don't repeat completed work.")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "turn_number": self.turn_number,
            "total_tool_calls": self.total_tool_calls,
            "successful_tool_calls": self.successful_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "files_modified": self.files_modified,
            "files_created": self.files_created,
            "current_plan": self.current_plan,
            "progress_summary": self.progress_summary,
            "last_error": self.last_error,
            "failed_approaches": self.failed_approaches,
            "stop_reason": self.stop_reason,
            "completed_actions": [
                {"tool_name": a.tool_name, "arguments_summary": a.arguments_summary,
                 "success": a.success, "output_summary": a.output_summary,
                 "duration_ms": a.duration_ms, "turn_number": a.turn_number}
                for a in self.completed_actions
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "Checkpoint":
        cp = Checkpoint(
            conversation_id=data["conversation_id"], task_id=data["task_id"],
            created_at=data.get("created_at", ""), turn_number=data.get("turn_number", 0),
            total_tool_calls=data.get("total_tool_calls", 0),
            successful_tool_calls=data.get("successful_tool_calls", 0),
            failed_tool_calls=data.get("failed_tool_calls", 0),
            files_modified=data.get("files_modified", []),
            files_created=data.get("files_created", []),
            current_plan=data.get("current_plan"),
            progress_summary=data.get("progress_summary", ""),
            last_error=data.get("last_error"),
            failed_approaches=data.get("failed_approaches", []),
            stop_reason=data.get("stop_reason"),
        )
        for a in data.get("completed_actions", []):
            cp.completed_actions.append(ToolCallRecord(**a))
        return cp

    @staticmethod
    def _extract_path(args_summary: str) -> Optional[str]:
        if "path=" in args_summary:
            start = args_summary.index("path=") + 5
            end = args_summary.find(",", start)
            if end == -1:
                end = len(args_summary)
            return args_summary[start:end].strip().strip("'\"")
        return None
```

### 2. Auto-Save Checkpoints During the Loop

**File:** `backend/app/agent/loop.py`

```python
from backend.app.agent.checkpoint import Checkpoint, ToolCallRecord

async def agent_loop(messages, config, **kwargs):
    checkpoint = Checkpoint(conversation_id=config.conversation_id, task_id=config.task_id or "unknown")
    try:
        while not guard.should_stop:
            # ... existing loop ...
            for tool_call in response.tool_calls:
                result = await execute_tool_call(tool_call.name, tool_call.arguments)
                checkpoint.record_tool_call(ToolCallRecord(
                    tool_name=tool_call.name,
                    arguments_summary=_summarize_args(tool_call.arguments),
                    success=result.success,
                    output_summary=result.output[:200] if result.output else "",
                    duration_ms=result.duration_ms, turn_number=guard.current_turn,
                ))
                await save_checkpoint(checkpoint)
    except Exception as e:
        checkpoint.last_error = str(e)
        checkpoint.stop_reason = "crash"
        await save_checkpoint(checkpoint)
        raise
    if guard.is_budget_exhausted():
        checkpoint.stop_reason = "budget_exhausted"
        await save_checkpoint(checkpoint)
    else:
        checkpoint.stop_reason = "completed"
        await expire_checkpoint(checkpoint.conversation_id)
    return messages
```

### 3. Resume from Checkpoint

**File:** `backend/app/agent/continuation.py`

```python
async def maybe_resume_from_checkpoint(messages, conversation_id, context_store):
    checkpoint_data = await context_store.get_checkpoint(conversation_id)
    if not checkpoint_data:
        return messages
    checkpoint = Checkpoint.from_dict(checkpoint_data)
    if checkpoint.stop_reason == "completed":
        await context_store.delete_checkpoint(conversation_id)
        return messages
    resumption_msg = {"role": "system", "content": checkpoint.to_resumption_message()}
    messages.insert(1, resumption_msg)  # After system prompt
    return messages
```

### 4. Checkpoint Storage

**File:** `backend/app/agent/context/context_store.py`

```python
async def save_checkpoint(self, conversation_id, checkpoint_data):
    key = f"checkpoint:{conversation_id}"
    await self._store.set(key, json.dumps(checkpoint_data))
    await self._store.expire(key, 3600)  # 1 hour TTL

async def get_checkpoint(self, conversation_id):
    key = f"checkpoint:{conversation_id}"
    data = await self._store.get(key)
    return json.loads(data) if data else None

async def delete_checkpoint(self, conversation_id):
    await self._store.delete(f"checkpoint:{conversation_id}")
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | Checkpoint dataclass | **Foundation** | 30 min |
| 2 | Auto-save in loop | **Critical** | 30 min |
| 3 | Resume from checkpoint | **Critical** | 30 min |
| 4 | Checkpoint storage | **Required** | 20 min |

---

## Files Affected

- `backend/app/agent/checkpoint.py` — new file
- `backend/app/agent/loop.py` — auto-save after each tool call
- `backend/app/agent/continuation.py` — resume logic
- `backend/app/agent/context/context_store.py` — persistence

---

## Risks

- **Checkpoint staleness** — references files that changed. Mitigation: 1-hour TTL; resumption is advisory.
- **Storage overhead** — saving after every tool call. Mitigation: checkpoints are small (<10KB); async writes.
- **Resumption confusion** — model sees work it didn't do. Mitigation: clear labeling and explicit instructions.

---

## Not Addressed Here

- **Turn budget enforcement** — see doc 093 (Turn Budget & Stuck Detection)
- **Context compaction on resume** — see doc 090 (Token-Aware Context Management)
- **Multi-agent checkpoint coordination** — future work
