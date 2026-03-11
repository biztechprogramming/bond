# Design Doc: Coding Agent Completion Loop

## Status: Proposed
## Author: Bond AI
## Date: 2026-03-11

---

## 1. Problem Statement

When Bond spawns a coding agent (via the `coding_agent` tool), the agent loop returns a message like *"I'll report back with the results once it completes"* — but **nothing actually follows up**. The coding agent runs in the background, the git diff watcher monitors changes and pushes SSE events to the frontend, but when the subprocess finishes:

- **No LLM turn is triggered.** The agent never re-engages to summarize results, report failures, or suggest next steps.
- **The user sees raw SSE events** (diffs, status) in the UI but gets no conversational follow-up.
- **The "I'll report back" promise is a lie.** The LLM said it would follow up because that's the natural thing to say, but there's no mechanism to deliver on it.
- **Failures are silent.** If the coding agent crashes, times out, or produces bad output, the user has to notice on their own.

### How OpenClaw solves this

OpenClaw has a fundamentally different architecture for background task completion:

1. **System Events Queue** (`system-events.ts`) — An in-memory, session-scoped queue where components can enqueue human-readable event strings (e.g., `"Exec finished (node=merlin, code 0)\n<output snippet>"`).

2. **Heartbeat Wake** (`heartbeat-wake.ts`) — When a system event is enqueued, `requestHeartbeatNow()` is called, which schedules an immediate heartbeat with coalescing (default 250ms). This wakes the agent loop.

3. **Heartbeat → Agent Turn** — The heartbeat runner drains pending system events and injects them as context into the next agent turn. The LLM sees *"System: Exec completed (code 0): ..."* and can respond conversationally.

4. **Session scoping** — Events are keyed to specific sessions, so multi-session setups route completions to the right conversation.

The result: when a background `exec` finishes in OpenClaw, the agent wakes up within seconds, sees the completion event, and replies to the user with a summary. Bond has none of this.

---

## 2. Gap Analysis: Bond vs OpenClaw

| Capability | OpenClaw | Bond |
|---|---|---|
| Background process monitoring | `exec` tool + `process` management | `CodingAgentProcess` + `GitDiffWatcher` |
| Incremental progress to UI | System events → heartbeat → agent reply | SSE events → frontend (no LLM involvement) |
| Completion notification | `enqueueSystemEvent` → `requestHeartbeatNow` → agent turn | `event_queue.put({"type": "done"})` → SSE only |
| Agent re-engagement on finish | ✅ Heartbeat wakes agent, LLM summarizes | ❌ No mechanism |
| Failure notification | ✅ Agent sees error event, responds | ❌ Silent — user must check UI |
| "I'll follow up" accuracy | ✅ It actually follows up | ❌ Empty promise |
| Multi-agent orchestration | Sub-agent completion events route to parent | Single coding agent per conversation, no callback |

### Bond's current flow

```
User: "Build feature X"
  → LLM calls coding_agent tool
  → CodingAgentProcess spawns subprocess
  → Tool returns immediately: {"status": "started", ...}
  → LLM says: "I've started the coding agent, I'll report back..."
  → [Background: GitDiffWatcher pushes SSE diffs to frontend]
  → [Background: Process finishes, "done" event sent via SSE]
  → [Nothing happens in the LLM. User is on their own.]
```

### What it should look like

```
User: "Build feature X"
  → LLM calls coding_agent tool
  → CodingAgentProcess spawns subprocess
  → Tool returns immediately: {"status": "started", ...}
  → LLM says: "I've started the coding agent, I'll report back..."
  → [Background: GitDiffWatcher pushes SSE diffs to frontend]
  → [Background: Process finishes]
  → Completion event injected into next agent turn
  → LLM sees: "Coding agent (claude) completed in 142s. 8 files changed..."
  → LLM responds: "The coding agent finished. Here's what it did: [summary].
     Tests pass. Want me to push to a branch?"
```

---

## 3. Proposed Design

### 3.1 Completion Callback System

Add a lightweight callback mechanism in the worker that allows background tasks to trigger a new agent turn when they complete.

```python
# New file: backend/app/agent/completion_events.py

class CompletionEvent:
    """A background task completion that should trigger an agent turn."""
    def __init__(self, conversation_id: str, event_type: str, summary: str, 
                 metadata: dict | None = None):
        self.conversation_id = conversation_id
        self.event_type = event_type  # "coding_agent_done", "coding_agent_failed", etc.
        self.summary = summary
        self.metadata = metadata or {}
        self.timestamp = time.time()

# Session-scoped queue (mirrors OpenClaw's system-events.ts)
_pending_events: dict[str, list[CompletionEvent]] = {}  # conversation_id -> events

def enqueue_completion(event: CompletionEvent) -> None:
    """Queue a completion event for the next agent turn."""
    events = _pending_events.setdefault(event.conversation_id, [])
    events.append(event)
    # Cap at 20 events per conversation
    if len(events) > 20:
        events.pop(0)

def drain_completions(conversation_id: str) -> list[CompletionEvent]:
    """Drain all pending completion events for a conversation."""
    return _pending_events.pop(conversation_id, [])
```

### 3.2 CodingAgentSession Monitor Enhancement

Modify the existing `_monitor()` method in `CodingAgentSession` to enqueue a completion event when the process finishes:

```python
# In coding_agent.py, CodingAgentSession._monitor()

async def _monitor(self) -> None:
    try:
        # ... existing diff polling loop ...
        
        # Process finished — build summary (existing code)
        self.exit_code = self.process.process.returncode
        self.finished = True
        # ... existing summary building ...
        
        # NEW: Enqueue completion event for agent re-engagement
        from backend.app.agent.completion_events import enqueue_completion, CompletionEvent
        enqueue_completion(CompletionEvent(
            conversation_id=self.conversation_id,
            event_type="coding_agent_done" if self.exit_code == 0 else "coding_agent_failed",
            summary=self.final_summary,
            metadata={
                "agent_type": self.agent_type,
                "exit_code": self.exit_code,
                "elapsed_seconds": round(self.process.elapsed, 1),
                "git_stat": stat,
                "baseline_commit": self.baseline_commit,
                "branch": self.branch,
                "working_directory": self.process.working_directory,
            },
        ))
        
        # ... existing SSE event push ...
```

### 3.3 Worker Wake Mechanism

Bond doesn't have OpenClaw's heartbeat system. Two options:

#### Option A: Polling endpoint (simpler)

Add an endpoint the frontend/gateway can poll, and trigger a synthetic agent turn when completions are pending:

```python
# In worker.py

@app.post("/check-completions/{conversation_id}")
async def check_completions(conversation_id: str) -> dict:
    """Check for pending background task completions."""
    from backend.app.agent.completion_events import drain_completions
    events = drain_completions(conversation_id)
    if not events:
        return {"pending": False}
    return {
        "pending": True,
        "events": [
            {"type": e.event_type, "summary": e.summary, "metadata": e.metadata}
            for e in events
        ],
    }
```

The frontend would poll this every ~10s when a coding agent is active, and auto-trigger a `/turn` with the completion context injected as a system message.

#### Option B: Internal wake (preferred, mirrors OpenClaw)

The monitor task directly triggers a new agent turn internally:

```python
# In coding_agent.py or a new completion_handler.py

async def _trigger_completion_turn(
    conversation_id: str, 
    completion_summary: str,
    metadata: dict,
) -> None:
    """Trigger a new agent turn with completion context.
    
    Injects the completion as a system event and runs a lightweight
    agent loop iteration that can respond to the user.
    """
    # Build a synthetic "system" message with the completion info
    system_context = (
        f"[System: Background task completed]\n\n"
        f"{completion_summary}\n\n"
        f"Summarize what happened for the user. If the task succeeded, "
        f"describe the changes and suggest next steps. If it failed, "
        f"explain what went wrong and offer to help debug."
    )
    
    # Trigger a new turn via the worker's internal API
    # This reuses the existing _run_agent_loop with the completion
    # injected as the "user message" (but marked as system-originated)
    response_text, _ = await _run_agent_loop(
        user_message=system_context,
        history=await _load_recent_history(conversation_id),
        conversation_id=conversation_id,
        is_system_initiated=True,  # New flag: don't wait for user input
    )
    
    # Push the response to the frontend via SSE or WebSocket
    await _push_assistant_message(conversation_id, response_text)
```

### 3.4 Frontend Integration

The frontend needs to handle unsolicited assistant messages (agent-initiated turns). Two delivery options:

1. **Extend existing SSE stream** — Keep the coding agent SSE connection open after the process finishes. When the completion turn runs, push the agent's response through the same stream.

2. **WebSocket push** — If the frontend has a WebSocket connection (or can open one), push completion responses as a new message type.

3. **Poll + fetch** — Frontend polls `/check-completions`, and when completions are found, fetches the latest messages for the conversation.

### 3.5 Message Persistence

Completion-triggered turns must be persisted the same way user-initiated turns are:

- The completion context (system message) is stored in history so the LLM has context in future turns
- The agent's response is stored as a normal assistant message
- The frontend renders it as a regular assistant message (perhaps with a subtle "auto-generated" indicator)

---

## 4. Implementation Plan

### Phase 1: Core completion events (backend only)
1. Create `backend/app/agent/completion_events.py` with the event queue
2. Modify `CodingAgentSession._monitor()` to enqueue completion events
3. Add `/check-completions/{conversation_id}` polling endpoint
4. Add integration tests

**Effort:** ~1 day  
**Risk:** Low — additive, no changes to existing agent loop

### Phase 2: Internal wake + auto-turn
1. Implement `_trigger_completion_turn()` in the worker
2. Add `is_system_initiated` flag to `_run_agent_loop` to distinguish auto-turns
3. Handle message persistence for system-initiated turns
4. Add guard rails: max 1 auto-turn per completion, timeout on auto-turn, no recursive spawning

**Effort:** ~2 days  
**Risk:** Medium — modifying the agent loop requires care to avoid infinite loops or unexpected behavior

### Phase 3: Frontend delivery
1. Extend SSE or add WebSocket push for unsolicited messages
2. Frontend renders completion-triggered messages with appropriate UX
3. Add "coding agent completed" notification/toast in the UI

**Effort:** ~1-2 days  
**Risk:** Low-medium — frontend changes, but well-scoped

### Phase 4: Enhanced UX
1. Progress summaries during long runs (periodic, not just on completion)
2. Failure diagnostics — when the coding agent fails, auto-read its output and provide actionable error info
3. Auto-suggest next steps (run tests, create PR, review diffs)
4. Multi-agent support — handle multiple concurrent coding agents with separate completion events

**Effort:** ~2-3 days  
**Risk:** Low — incremental improvements on solid foundation

---

## 5. Guard Rails

Background-triggered agent turns are powerful but need constraints:

| Guard | Why |
|---|---|
| **Max 1 auto-turn per completion** | Prevent runaway loops |
| **No tool calls in auto-turns** (Phase 2 only) | Keep auto-turns cheap and safe; relax later |
| **Timeout on auto-turns: 30s** | Don't let a completion summary consume expensive model time |
| **No recursive coding agent spawns** | A completion turn must not spawn another coding agent |
| **Rate limit: max 3 auto-turns per minute per conversation** | Prevent rapid-fire completions from overwhelming the user |
| **Cooldown after user message** | If the user sends a message while a completion turn is pending, cancel the auto-turn (user takes priority) |

---

## 6. Future: Convergence with OpenClaw

Bond's architecture is converging toward OpenClaw's event-driven model. Long-term, Bond should adopt:

- **A general system events queue** (not just for coding agent completions) — any background task (file watchers, CI webhooks, scheduled jobs) can enqueue events
- **A heartbeat/wake mechanism** — periodic polling for pending events, with immediate wake on high-priority events
- **Session-scoped event routing** — events are delivered to the right conversation/session

This design doc addresses the immediate pain point (coding agent follow-up) while laying groundwork for the broader event-driven architecture.

---

## 7. Success Criteria

- [ ] When a coding agent finishes, the user receives a conversational summary within 30 seconds
- [ ] When a coding agent fails, the user receives a clear error explanation
- [ ] The LLM never says "I'll report back" without a mechanism to actually report back
- [ ] No regressions in existing coding agent functionality (SSE diffs, kill, etc.)
- [ ] Auto-turns are distinguishable from user-initiated turns in the UI
