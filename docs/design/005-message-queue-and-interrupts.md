# Design Doc 005: Message Queue & Interrupt System

**Status**: Draft — awaiting review  
**Author**: Developer Agent  
**Date**: 2026-02-25

## Problem

Currently, the chat UI blocks while the agent is processing. Users cannot:
1. Send follow-up messages while the agent is working
2. Correct or redirect the agent mid-turn
3. Queue up context that the agent should consider

This creates a frustrating experience — especially during long tool-use loops where the agent may be heading in the wrong direction.

## Goals

- Users can send messages at any time, regardless of agent state
- Messages queue up and are visible immediately in the UI
- Agent processes queued messages after completing its current turn
- Users can interrupt ("stop and listen") to force the agent to read new input mid-turn
- Architecture supports streaming responses alongside queued input

## Non-Goals

- Multi-agent orchestration (separate concern)
- Message editing/deletion after send
- Priority queuing (all messages are FIFO)

## Architecture

### Message Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Frontend │────▶│ Gateway  │────▶│ Backend  │
│          │     │          │     │          │
│ Send msg │     │ Queue +  │     │ Agent    │
│ anytime  │     │ persist  │     │ Loop     │
│          │     │          │     │          │
│ Show in  │     │ Forward  │     │ Check    │
│ UI       │     │ when     │     │ queue    │
│ instant  │     │ ready    │     │ between  │
│          │     │          │     │ iters    │
└──────────┘     └──────────┘     └──────────┘
```

### Three Message States

| State | Description |
|-------|-------------|
| `queued` | Saved to DB, visible in UI, not yet seen by agent |
| `delivered` | Injected into agent context |
| `processed` | Agent has responded |

### Agent States

```
idle ──▶ thinking ──▶ tool_calling ──▶ responding ──▶ idle
                          │                              ▲
                          ▼                              │
                    check_queue ─── (new msgs?) ─────────┘
                          │              │
                     (no msgs)      (has msgs)
                          │              │
                          ▼              ▼
                      continue      inject + respond
```

## Design

### 1. Frontend Changes

**Always-active input**: The message input is never disabled. Sent messages appear immediately in the chat with a "sending" indicator.

**Stop button**: While agent is processing, show a stop button (⏹) next to the input. Clicking sends an `interrupt` signal via WebSocket.

**Message status indicators**:
- 💬 Sending (optimistic, pre-confirmation)
- ✓ Queued (persisted, awaiting agent)
- ✓✓ Delivered (agent has seen it)

**Streaming**: Agent responses stream in via WebSocket as they do today. New user messages can be sent during streaming.

```typescript
// WebSocket message types
interface OutgoingMessage {
  type: "message" | "interrupt" | "cancel";
  content?: string;
  conversationId?: string;
}

interface IncomingMessage {
  type: "chunk" | "done" | "queued" | "status" | "error";
  content?: string;
  messageId?: string;
  agentStatus?: "idle" | "thinking" | "tool_calling" | "responding";
}
```

### 2. Gateway Changes

**Session state**: Track agent processing state per session.

```typescript
interface Session {
  id: string;
  conversationId: string | null;
  agentBusy: boolean;       // true while turn is in-flight
  pendingMessages: string[];  // queued message IDs (DB is source of truth)
}
```

**Message handling**:
- On message received: Always save to `conversation_messages` via backend API immediately (status: `queued`). If agent is idle, start a new turn. If agent is busy, just queue it.
- On interrupt: Set an interrupt flag on the active turn. Backend checks this between iterations.
- On turn complete: Check for queued messages. If any exist, start a new turn automatically.

**New gateway endpoints** (internal, backend → gateway push):
- None needed — gateway polls or the turn response includes queue status.

### 3. Backend Changes

#### 3a. Message Queue Endpoint

```
POST /api/v1/conversations/{id}/messages
Body: { "content": "...", "role": "user" }
Response: { "message_id": "...", "status": "queued", "queue_position": 2 }
```

Saves message to DB immediately. Does NOT trigger an agent turn — the gateway decides when to start turns.

#### 3b. Agent Turn Refactor

The turn endpoint changes from synchronous request-response to a stateful process:

```
POST /api/v1/agent/turn
Body: {
  "conversation_id": "...",
  "agent_id": "..."      // optional, resolved from conversation
}
Response: streamed (SSE or chunked)
```

The agent loop changes:

```python
async def run_agent_turn(conversation_id: str, db: AsyncSession):
    agent = await load_agent(conversation_id, db)
    
    # Load ALL unprocessed messages (not just one)
    messages = await get_unprocessed_messages(conversation_id, db)
    
    # Build context from conversation history + new messages
    history = await get_conversation_history(conversation_id, db)
    
    for iteration in range(agent["max_iterations"]):
        # Check for interrupt
        if await check_interrupt(conversation_id):
            # Load any new messages that arrived
            new_msgs = await get_new_queued_messages(conversation_id, db)
            if new_msgs:
                # Inject into context
                history.extend(new_msgs)
                yield status_event("interrupted", "Reading new messages...")
                # Mark as delivered
                await mark_delivered(new_msgs, db)
                continue  # Let the LLM process the new context
            else:
                # Interrupt with no new messages = stop
                yield status_event("stopped", "Stopped by user")
                break
        
        # Normal LLM call
        response = await chat_completion(agent, history)
        
        if response.has_tool_calls:
            # Execute tools, add results to history
            ...
            
            # Check queue between tool calls
            new_msgs = await get_new_queued_messages(conversation_id, db)
            if new_msgs:
                history.extend(new_msgs)
                await mark_delivered(new_msgs, db)
                yield status_event("new_input", f"{len(new_msgs)} new message(s)")
                # Continue loop — LLM will see new messages on next iteration
        else:
            # Final response
            yield response_event(response.content)
            await save_assistant_message(conversation_id, response, db)
            break
    
    # After turn completes, check for more queued messages
    remaining = await get_new_queued_messages(conversation_id, db)
    yield done_event(queued_count=len(remaining))
```

#### 3c. Interrupt Mechanism

Use a lightweight in-memory flag (not DB — needs to be fast):

```python
# In-memory interrupt store (process-level)
_interrupts: dict[str, asyncio.Event] = {}

async def set_interrupt(conversation_id: str):
    if conversation_id in _interrupts:
        _interrupts[conversation_id].set()

async def check_interrupt(conversation_id: str) -> bool:
    event = _interrupts.get(conversation_id)
    if event and event.is_set():
        event.clear()
        return True
    return False

# Endpoint
POST /api/v1/conversations/{id}/interrupt
Response: { "status": "interrupt_sent" }
```

#### 3d. Database Changes

Add status tracking to `conversation_messages`:

```sql
-- Migration 000008
ALTER TABLE conversation_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'delivered';
-- status: 'queued' | 'delivered' | 'processed'

CREATE INDEX idx_cm_status ON conversation_messages(conversation_id, status)
  WHERE status = 'queued';
```

### 4. Streaming (SSE)

Replace the current JSON request-response with Server-Sent Events for the turn endpoint:

```
POST /api/v1/agent/turn → SSE stream

event: status
data: {"state": "thinking"}

event: chunk  
data: {"content": "Let me "}

event: chunk
data: {"content": "check that..."}

event: tool_call
data: {"tool": "search_memory", "args": {...}}

event: tool_result
data: {"tool": "search_memory", "result": "..."}

event: new_input
data: {"count": 1, "message": "actually, try X instead"}

event: chunk
data: {"content": "Good point, switching to X..."}

event: done
data: {"message_id": "...", "queued_count": 0}
```

The gateway translates SSE → WebSocket frames for the frontend.

### 5. Conversation Auto-Continue

When a turn completes and `queued_count > 0`, the gateway automatically starts a new turn:

```typescript
// Gateway: on turn complete
if (result.queued_count > 0) {
  // Small delay to batch rapid messages
  setTimeout(() => startNewTurn(session), 500);
}
```

This creates a natural loop: user sends multiple messages → agent processes them one turn at a time → user can keep adding to the queue.

## Sequence Diagrams

### Normal Queue Flow

```
User          Frontend       Gateway        Backend         DB
 │               │              │              │              │
 ├─"do X"───────▶│──message───▶│──POST msg──▶│──INSERT────▶│
 │               │◀─queued─────│              │              │
 │               │              │──POST turn─▶│              │
 │               │              │◀─SSE stream─│              │
 ├─"also Y"─────▶│──message───▶│──POST msg──▶│──INSERT────▶│
 │               │◀─queued─────│   (busy)     │              │
 │               │◀─chunk──────│◀─chunk───────│              │
 │               │◀─done───────│◀─done(q=1)──│              │
 │               │              │──POST turn─▶│  (auto)      │
 │               │◀─chunk──────│◀─chunk───────│  (sees "Y")  │
 │               │◀─done───────│◀─done(q=0)──│              │
```

### Interrupt Flow

```
User          Frontend       Gateway        Backend
 │               │              │              │
 │               │              │──POST turn─▶│ (working...)
 ├─"stop, do Z"─▶│──message───▶│──POST msg──▶│──save──
 │               │──interrupt──▶│──POST int──▶│
 │               │              │              │──check flag──
 │               │              │              │──load new msgs──
 │               │◀─new_input──│◀─new_input──│
 │               │◀─chunk──────│◀─chunk──────│ (responds to Z)
 │               │◀─done───────│◀─done───────│
```

## Migration Path

This can be built incrementally:

1. **Phase 1: Queue only** — Frontend always-active input, messages save to DB immediately, gateway auto-starts new turn after previous completes. No interrupt yet.
2. **Phase 2: SSE streaming** — Replace request-response with SSE. Agent status events.
3. **Phase 3: Interrupts** — Between-iteration queue checking, interrupt endpoint, stop button.

Phase 1 alone gives 80% of the value. Users can type freely, messages queue, agent processes them in order.

## Stories

### Story Q1: Always-Active Input + Message Queue (Phase 1)
- Frontend: Never disable input, show messages immediately
- Gateway: Track busy state, queue messages, auto-continue
- Backend: `POST /conversations/{id}/messages` for immediate persistence
- Migration 000008: Add `status` column to `conversation_messages`

### Story Q2: SSE Streaming (Phase 2)
- Backend: Convert `/agent/turn` to SSE stream
- Gateway: SSE → WebSocket translation
- Frontend: Handle streaming chunks + status events

### Story Q3: Interrupt System (Phase 3)
- Backend: In-memory interrupt flags, between-iteration checking
- Backend: `POST /conversations/{id}/interrupt` endpoint
- Gateway: Forward interrupt signals
- Frontend: Stop button, "new input" indicators

### Story Q4: Agent Status UI
- Frontend: Show agent state (thinking/tool_calling/responding)
- Frontend: Message status indicators (queued/delivered/processed)
- Tool call visibility (show what the agent is doing)

## Open Questions

1. **Batch delay**: When multiple messages arrive rapidly, should we batch them into one turn or process each separately? Recommendation: batch with a 500ms delay.

2. **Interrupt semantics**: Should interrupt mean "stop everything" or "pause and read"? Recommendation: "pause and read" — the agent sees new messages and decides how to proceed. A separate "cancel" signal means "stop everything."

3. **Max queue depth**: Should we limit how many messages can queue? Recommendation: no hard limit, but warn in UI after 10 queued messages.

4. **Tool call interruption**: If the agent is mid-tool-call (e.g., running code), should interrupt wait for the tool to finish? Recommendation: yes — interrupting mid-execution could leave state inconsistent. Check queue after each tool completes.
