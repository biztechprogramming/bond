# Design Doc: Coding Agent Completion Loop

## Status: Implemented
## Author: Bond AI
## Date: 2026-03-11
## Branch: feature/system-events-subscription

---

## 1. Problem Statement

When Bond spawns a coding agent (via the `coding_agent` tool), the agent loop returns a message like *"I'll report back with the results once it completes"* — but **nothing actually follows up**. The coding agent runs in the background, the git diff watcher monitors changes and pushes SSE events to the frontend, but when the subprocess finishes:

- **No LLM turn is triggered.** The agent never re-engages to summarize results, report failures, or suggest next steps.
- **The user sees raw SSE events** (diffs, status) in the UI but gets no conversational follow-up.
- **The "I'll report back" promise is a lie.** The LLM said it would follow up because that's the natural thing to say, but there's no mechanism to deliver on it.
- **Failures are silent.** If the coding agent crashes, times out, or produces bad output, the user has to notice on their own.

### How OpenClaw solves this

OpenClaw uses an in-memory system events queue + heartbeat wake mechanism:

1. **System Events Queue** (`system-events.ts`) — An in-memory, session-scoped queue where components enqueue human-readable event strings.
2. **Heartbeat Wake** (`heartbeat-wake.ts`) — `requestHeartbeatNow()` schedules an immediate heartbeat that drains events and injects them into the next agent turn.

This works for OpenClaw's single-process gateway architecture. Bond's architecture is different — workers run in separate Docker containers, communicating with the gateway over HTTP. An in-memory queue inside a worker container can't wake the gateway.

---

## 2. Gap Analysis: Bond vs OpenClaw (pre-implementation)

| Capability | OpenClaw | Bond (before) |
|---|---|---|
| Background process monitoring | `exec` tool + `process` management | `CodingAgentProcess` + `GitDiffWatcher` |
| Incremental progress to UI | System events → heartbeat → agent reply | SSE events → frontend (no LLM involvement) |
| Completion notification | `enqueueSystemEvent` → `requestHeartbeatNow` → agent turn | `event_queue.put({"type": "done"})` → SSE only |
| Agent re-engagement on finish | ✅ Heartbeat wakes agent, LLM summarizes | ❌ No mechanism |
| Failure notification | ✅ Agent sees error event, responds | ❌ Silent — user must check UI |
| SpacetimeDB usage | N/A (uses in-memory queue) | HTTP-only (tables for storage, no subscriptions) |

### The key insight: Bond already has SpacetimeDB

Bond's frontend already uses SpacetimeDB's real-time WebSocket subscription system for conversations, messages, and work plans. The generated TypeScript SDK with `DbConnection`, `onInsert`/`onUpdate` callbacks, and subscription queries is fully operational in `frontend/src/lib/spacetimedb-client.ts`.

However, **only the frontend subscribes**. The gateway and workers treat SpacetimeDB as a dumb REST database — `callReducer()` and `sqlQuery()` over HTTP. This means:

```
Frontend  ──WebSocket──▶  SpacetimeDB  ◀──HTTP REST──  Gateway/Workers
   (real-time push)                       (request/response only)
```

The solution: extend SpacetimeDB subscriptions to the gateway. When a worker writes a completion event to SpacetimeDB, the gateway sees it instantly via WebSocket push and triggers an agent turn.

---

## 3. Architecture

### Data flow

```
                              SpacetimeDB
                            ┌─────────────┐
Worker (Python)             │             │          Gateway (TypeScript)
┌──────────────┐  HTTP      │ system_     │  WebSocket  ┌──────────────┐
│ coding_agent │──reducer──▶│ events      │──onInsert──▶│ subscription │
│ _monitor()   │            │ table       │             │ .ts          │
└──────────────┘            └─────────────┘             └──────┬───────┘
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │ completion/  │
                                                        │ handler.ts   │
                                                        └──────┬───────┘
                                                               │
                                              conversationTurnStream()
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │ BackendClient│──▶ Worker /turn
                                                        └──────┬───────┘
                                                               │
                                                          WebSocket
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │ Frontend     │
                                                        │ (user sees   │
                                                        │  the reply)  │
                                                        └──────────────┘
```

### Why SpacetimeDB instead of in-memory queues

| Concern | In-memory dict | SpacetimeDB table |
|---|---|---|
| Cross-process | ❌ Worker and gateway are separate containers | ✅ Both can read/write the same table |
| Durability | ❌ Lost on restart | ✅ Events survive restarts; gateway drains on reconnect |
| Frontend visibility | ❌ Requires separate notification channel | ✅ Frontend can subscribe to same table for toasts |
| Observability | ❌ No audit trail | ✅ Every event is a queryable row |
| Latency | ~0ms (same process) | ~sub-millisecond (WebSocket push) |
| Complexity | Lower (but only works single-process) | Uses existing SDK already in `package.json` |

---

## 4. Implementation

### 4.1 SpacetimeDB Schema

**File:** `spacetimedb/spacetimedb/src/index.ts`

New table:

```typescript
system_events: table(
  { public: true },
  {
    id: t.string().primaryKey(),
    conversationId: t.string(),
    agentId: t.string(),
    eventType: t.string(),     // "coding_agent_done", "coding_agent_failed"
    summary: t.string(),       // human-readable summary
    metadata: t.string(),      // JSON string with structured data
    consumed: t.bool(),
    createdAt: t.u64(),
  }
),
```

New reducers:

- `enqueueSystemEvent` — inserts a row with `consumed: false`
- `consumeSystemEvent` — deletes the row by ID (cleanup after processing)

### 4.2 Gateway: SpacetimeDB WebSocket Subscription

**New file:** `gateway/src/spacetimedb/subscription.ts`

- Opens a WebSocket connection using the existing `DbConnection` from the generated SDK
- Subscribes to `SELECT * FROM system_events`
- Registers `onInsert` callback on the `system_events` table
- On insert of unconsumed event → calls the provided handler
- On connect: drains any existing unconsumed events (recovery after gateway restart)
- Auto-reconnects with exponential backoff (5s → 60s max)

The WebSocket URL is derived from the existing `config.spacetimedbUrl` by replacing `http://` with `ws://`.

### 4.3 Gateway: Completion Turn Handler

**New file:** `gateway/src/completion/handler.ts`

`CompletionHandler` class:

1. **Receives** a `SystemEventRow` from the subscription callback
2. **Rate-limits**: max 3 auto-turns per conversation per 60-second window
3. **Deduplicates**: tracks in-flight event IDs to prevent double-processing
4. **Builds** a system-context message based on event type:
   - `coding_agent_done` → "[System: Background coding agent completed successfully]" + summary + git stat + instruction to summarize
   - `coding_agent_failed` → "[System: Background coding agent failed]" + exit code + error + instruction to explain
   - Both include: "Do NOT spawn another coding agent in this response."
5. **Triggers** an agent turn via `backendClient.conversationTurnStream()`
6. **Streams** response chunks to WebSocket clients via `webchat.sendToConversation()`
7. **Consumes** the event via HTTP `callReducer("consume_system_event", [id])`

All broadcasts include `isCompletionTurn: true` so the frontend can distinguish auto-turns from user-initiated turns.

### 4.4 Gateway: Server Wiring

**File:** `gateway/src/server.ts`

After `httpServer.listen()`, if SpacetimeDB is configured:
- Creates a `CompletionHandler` with the backend client and webchat broadcast function
- Calls `initSubscription(config, handler)` 
- Logs success or warns on failure (non-fatal — gateway works without subscriptions)

### 4.5 Python Worker: System Event Enqueue

**File:** `backend/app/agent/tools/coding_agent.py`

New method `CodingAgentSession._enqueue_system_event()`:
- Called from `_monitor()` after building the summary and pushing the SSE "done" event
- Uses the existing `StdbClient.call_reducer()` (HTTP) to call `enqueue_system_event`
- Passes: UUID, conversation_id, agent_id, event_type, summary, metadata JSON
- Wrapped in try/except — SpacetimeDB failure is logged but doesn't break the existing SSE flow

### 4.6 WebSocket Channel Visibility

**File:** `gateway/src/channels/webchat.ts`

Changed `sendToConversation` from `private` to `public` so the completion handler can broadcast to conversation subscribers.

---

## 4.7 Iteration-Exhaustion Handoff to coding_agent

When the main agent exhausts its iteration budget, three code paths attempt to
delegate remaining work to `coding_agent`:

1. **Budget escalation** (`iteration_handlers.py:handle_budget_escalation`) — at
   80% of adaptive budget, injects a system message nudging the agent to spawn
   `coding_agent`. At 4+ iterations overbudget, restricts available tools to
   `coding_agent + respond + say`.

2. **Worker fallback** (`worker.py` post-loop) — after the loop exits, if the
   task is a coding task and `coding_agent` was never called, auto-spawns it
   with a handoff context summary.

3. **loop.py fallback** — same as (2) but for the simpler `agent_turn` path.

### What counts as "meaningful work" for handoff

Prior to the fix in `fix/iteration-exhaustion-coding-agent-handoff`, handoff
only triggered when the agent had made **consequential calls** (file edits,
writes). This missed a common failure mode: the agent spends its entire budget
on discovery (reading files, searching code, exploring the repo) and never
reaches the implementation phase.

**Current rule**: handoff triggers when the agent has done *either*:
- Made consequential calls (file_edit, file_write, code_execute), **OR**
- Made **3 or more discovery tool calls** (file_read, search_memory, web_search,
  file_search, git_info, shell_find, shell_ls, shell_tree, project_search, etc.)

The threshold of 3 discovery calls avoids spurious handoff when the agent made
only trivial progress (e.g., one failed search).

### Constraints

- The handoff message includes files already read and edits already made
  (via `build_handoff_context`) so the coding agent doesn't repeat work.
- `coding_agent` is never spawned if it was already called in the session.
- Tasks with genuinely no progress (<3 discovery calls, no edits) still force
  a report-back to the user instead of blindly delegating.

---

## 5. Guard Rails

| Guard | Implementation |
|---|---|
| **Rate limit: 3 auto-turns/min/conversation** | `CompletionHandler.checkRateLimit()` — per-conversation sliding window |
| **Event deduplication** | `CompletionHandler.processing` Set — prevents concurrent handling of same event ID |
| **No recursive coding agent spawns** | Completion message includes "Do NOT spawn another coding agent in this response" |
| **Graceful degradation** | SpacetimeDB subscription failure is non-fatal; gateway logs warning and continues without completion turns |
| **Event cleanup** | Events are always consumed (deleted) after processing, even on error |
| **Reconnection** | Subscription auto-reconnects with exponential backoff (5s → 60s) |
| **Recovery after downtime** | On reconnect, gateway drains all existing unconsumed events |

---

## 6. Deployment

After merging `feature/system-events-subscription`:

1. **Deploy schema:** `cd spacetimedb/spacetimedb && spacetime build && spacetime publish`
2. **Regenerate SDK bindings:** `spacetime generate` — creates the `system_events` table accessor that `subscription.ts` references (`conn.db.system_events`)
3. **Restart gateway** — picks up new subscription code
4. **Rebuild worker containers** — picks up the `_enqueue_system_event` changes in `coding_agent.py`

---

## 7. Future Extensions

The `system_events` table is generic — not limited to coding agent completions. Future uses:

- **CI/CD webhooks** — GitHub Actions completes → insert system event → agent reports results
- **Scheduled tasks** — Cron job finishes → system event → agent summarizes
- **Multi-agent coordination** — Agent A finishes work → system event → Agent B picks up next step
- **External service notifications** — Any webhook → system event → agent responds
- **Frontend toasts** — Frontend subscribes to `system_events` for real-time notification UI

---

## 8. Test Coverage

### Gateway (`gateway/src/__tests__/completion-handler.test.ts`) — 10 tests
- Message building: done, failed, and generic event types
- Rate limiting: allows 3, blocks 4th, independent per conversation
- Event deduplication: same ID not processed twice concurrently
- Broadcasting: correct message types and `isCompletionTurn` flag
- Error handling: event consumed even when backend fails

### Python (`backend/tests/test_system_events.py`) — 4 tests
- Successful completion enqueues `coding_agent_done` with correct metadata
- Failed completion enqueues `coding_agent_failed`
- SpacetimeDB connection failure is graceful (no crash)
- Reducer returning false is graceful (no crash)

---

## 9. Success Criteria

- [x] SpacetimeDB `system_events` table and reducers defined
- [x] Gateway subscribes to SpacetimeDB via WebSocket
- [x] Completion handler triggers agent turn on coding agent finish
- [x] Worker enqueues system event when coding agent exits
- [x] Rate limiting and deduplication guard rails
- [x] 14 tests passing (10 gateway + 4 Python)
- [ ] Schema deployed and SDK bindings regenerated
- [ ] End-to-end verification: spawn coding agent → completes → user receives conversational summary
