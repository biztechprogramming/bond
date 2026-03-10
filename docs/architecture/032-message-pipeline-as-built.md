# 032 — Message Pipeline Architecture (As-Built)

**Implemented:** 2026-03-10  
**Branch:** `feature/032-message-pipeline`  
**Commits:** `91ac621`, `54dff59`, `07cde26`, `6673a20`  
**Design Doc:** [`docs/design/032-message-pipeline-architecture.md`](../design/032-message-pipeline-architecture.md)

---

## Overview

Every message — regardless of source channel — now flows through a **single ordered pipeline** of handlers. This replaces the previous architecture where WebChat, Telegram, and WhatsApp each had separate routing logic, leading to duplicate code and inconsistent behavior (e.g., API keys loading in one path but not another).

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   WebChat    │     │  Telegram   │     │  WhatsApp   │
│  (WebSocket) │     │  (grammY)   │     │ (Baileys)   │
└──────┬───────┘     └──────┬──────┘     └──────┬──────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                    ┌───────▼───────┐
                    │ PipelineMessage│
                    └───────┬───────┘
                            │
              ┌─────────────▼─────────────┐
              │     MessagePipeline        │
              │                            │
              │  1. RateLimitHandler        │
              │  2. AuthHandler             │
              │  3. AllowListHandler        │
              │  4. AgentResolver           │
              │  5. ContextLoader           │
              │  6. TurnExecutor            │
              │  7. Persister               │
              │  8. ResponseFanOut          │
              └─────────────┬──────────────┘
                            │
                    ┌───────▼───────┐
                    │   Response     │
                    │ (stream/push)  │
                    └───────────────┘
```

---

## Core Types

### `PipelineMessage` — `gateway/src/pipeline/types.ts`

The canonical message representation that flows through the pipeline:

| Field | Type | Set By | Description |
|---|---|---|---|
| `id` | `string` | Adapter | ULID |
| `channelType` | `string` | Adapter | `"webchat"` / `"telegram"` / `"whatsapp"` |
| `channelId` | `string` | Adapter | Session ID (webchat) or chat ID (telegram/whatsapp) |
| `content` | `string` | Adapter | User's message text |
| `userId` | `string?` | AuthHandler | Resolved user identity (currently always `"owner"`) |
| `agentId` | `string?` | AgentResolver | Selected agent |
| `conversationId` | `string?` | AgentResolver | Resolved conversation |
| `response` | `string?` | TurnExecutor | Accumulated agent response |
| `agentName` | `string?` | TurnExecutor | Agent display name |
| `timestamp` | `number` | Adapter | Epoch ms |
| `metadata` | `Record<string, any>` | Adapter | Channel-specific data (planId, agentId, conversationId) |

### `PipelineHandler` — `gateway/src/pipeline/types.ts`

```typescript
interface PipelineHandler {
  name: string;
  handle(
    message: PipelineMessage,
    context: PipelineContext,
    next: () => Promise<void>,
  ): Promise<void>;
}
```

Handlers call `next()` to continue the chain, or return early to short-circuit (e.g., rate limit rejection, allow-list block).

### `PipelineContext` — `gateway/src/pipeline/types.ts`

Each channel adapter creates its own PipelineContext that wires pipeline events back to the channel's transport:

| Method | WebChat Implementation | Telegram/WhatsApp Implementation |
|---|---|---|
| `respond(text)` | Sends error message to conversation sockets | Sends text to chat |
| `broadcast(text)` | (unused — ResponseFanOut handles) | (unused) |
| `streamChunk(chunk)` | Sends `"chunk"` WS message to all conversation sockets | No-op (accumulated in `message.response`) |
| `abort(reason)` | Sets `aborted=true`, sends error to sockets | Sets `aborted=true`, sends error to chat |
| `emit(event, data)` | Translates to WS messages (`status`, `tool_call`, `plan_*`) | No-op |

### `MessagePipeline` — `gateway/src/pipeline/pipeline.ts`

Minimal middleware chain. Handlers are registered with `.use()` and executed in order via recursive `next()` calls. If `context.aborted` is set, remaining handlers are skipped.

---

## Handler Implementations

All handlers in `gateway/src/pipeline/handlers/`.

### 1. RateLimitHandler — `rate-limit.ts`

Per-user sliding window. Default: 20/minute, 200/hour. Configurable via constructor.

- Uses `userId` if available, falls back to `channelType:channelId`
- Short-circuits with a user-facing message on limit hit
- Windows are pruned on each check

### 2. AuthHandler — `auth.ts`

Maps all channel users to `"owner"`. Placeholder for future multi-user support.

### 3. AllowListHandler — `allow-list.ts`

- WebChat: always allowed (bypass)
- Telegram/WhatsApp: checked against per-channel allow-list via `AllowListProvider`
- Unknown senders are **silently rejected** (no response sent)

### 4. AgentResolver — `agent-resolver.ts`

Resolves `agentId` and `conversationId` on the message:

1. Check `message.metadata.agentId` (pre-set by adapter)
2. Fall back to `deps.getSelectedAgentId()` (channel session's current agent)
3. For conversationId: check metadata → deps lookup → generate new ULID

Both WebChat and Telegram/WhatsApp pre-resolve these in their adapters before calling the pipeline, so the AgentResolver's deps are currently stubs that return `null`. The handler ensures the fields are always populated.

### 5. ContextLoader — `context-loader.ts`

Pass-through. The backend handles API key loading, history, system prompt. This handler exists as an extension point for future gateway-side context needs.

### 6. TurnExecutor — `turn-executor.ts`

The core handler. Calls `BackendClient.conversationTurnStream()` and dispatches SSE events:

| SSE Event | Pipeline Action |
|---|---|
| `status` | `context.emit("status", ...)` — agent state changes (thinking/tool_calling/responding) |
| `chunk` | `context.streamChunk(content)` — accumulates in `message.response` |
| `tool_call` | `context.emit("tool_call", ...)` |
| `plan_created` | `context.emit("plan_created", ...)` |
| `item_created/updated` | `context.emit("item_updated", ...)` |
| `plan_completed` | `context.emit("plan_completed", ...)` |
| `done` | Stores `message_id` in metadata |
| `error` | `context.emit("error", ...)` |

Respects `context.aborted` — breaks out of SSE loop if pipeline is aborted.

### 7. Persister — `persister.ts`

Pass-through. The backend persists messages during the turn. Extension point for gateway-side audit logging.

### 8. ResponseFanOut — `response-fan-out.ts`

After the turn completes, pushes the full response to all channels watching the conversation **except** the originating channel (which already received the stream/response).

Watchers are resolved via `FanOutDeps`:
- Telegram/WhatsApp bindings from `ChannelManager.getChannelBinding()`
- WebChat sockets from `SessionManager.getSocketsForConversation()`

**Skip logic:** Skips any watcher whose `channelType` matches the originating message's `channelType`. This prevents double-delivery (e.g., webchat already got chunks via streaming, so ResponseFanOut only pushes to Telegram/WhatsApp).

---

## Channel Adapters

### WebChat — `gateway/src/channels/webchat.ts`

**Pipeline path:** `executePipeline()` — creates `PipelineContext` that wires to WebSocket `sendToConversation()`.

**Direct-handled events** (not through pipeline):
- `switch_conversation` — load history, catch up on active stream buffer
- `list_conversations` — query backend
- `delete_conversation` — delete via backend
- `new_conversation` — reset session state
- `interrupt` / `pause` — abort active turn via backend

**Cross-channel push:** `setCrossChannelPush()` callback pushes user messages to Telegram/WhatsApp watchers. Agent responses are pushed by ResponseFanOut.

### Cross-Channel User Message Echo

When a Telegram/WhatsApp user sends a message, `ChannelManager.setCrossChannelUserEcho()` pushes the user's message text to any webchat sockets watching the same conversation (as a `"user_message"` WS event with sender label). This is wired in `server.ts`.

**Stream buffering:** Active streams are buffered in `streamBuffers` so clients joining mid-stream (via `switch_conversation`) see accumulated content.

### Telegram/WhatsApp — `gateway/src/channels/manager.ts`

**Pipeline path:** `routeViaPipeline()` — creates `PipelineContext` where `streamChunk` is a no-op (chunks accumulate in `message.response`, sent as complete message after pipeline finishes).

**Command handling** stays in the manager (not pipelined):
- `/help`, `/agents`, `/agent`, `/all`, `/new`, `/reset`, `/status`

**Channel bindings:** `channelBindings` map tracks which Telegram/WhatsApp chats are watching which conversations, enabling cross-channel push via ResponseFanOut.

---

## Wiring — `gateway/src/server.ts`

```typescript
const pipeline = new MessagePipeline();
pipeline.use(new RateLimitHandler());
pipeline.use(new AuthHandler());
pipeline.use(new AllowListHandler(allowListProvider));
pipeline.use(new AgentResolver({ /* stub deps — adapters pre-resolve */ }));
pipeline.use(new ContextLoader());
pipeline.use(new TurnExecutor(backendClient));
pipeline.use(new Persister());
pipeline.use(new ResponseFanOut({ getWatchers, sendToChannel }));

webchat.setPipeline(pipeline);
webchat.setCrossChannelPush((convId, msg, label) => {
  channelManager.pushToChannel(convId, msg, label);
});
channelManager.setPipeline(pipeline);
```

---

## What Changed from Design Doc

| Design Doc | As-Built | Reason |
|---|---|---|
| ContextLoader loads API keys, history, prompt | Pass-through — backend handles this | Gateway doesn't need these; backend already loads them in the turn endpoint |
| Persister saves to SpacetimeDB | Pass-through — backend handles this | Backend persists during `conversationTurnStream` |
| Channel adapters implement `ChannelAdapter` interface | Adapters are refactored in-place | Minimized risk; WebChat and ChannelManager kept their existing structure but delegate to pipeline |
| `webchat.setChannelManager()` for cross-channel | `webchat.setCrossChannelPush()` callback | Decouples webchat from ChannelManager; pipeline handles response fan-out |
| Shadow mode (run old + new in parallel) | Legacy `startTurn()` kept as fallback | Pipeline is used when set; legacy path runs if pipeline is null |

---

## Testing

18 new tests in `gateway/src/__tests__/pipeline.test.ts`:

- Pipeline executes handlers in order
- Short-circuit: handler that doesn't call `next()` stops the chain
- `context.aborted` stops pipeline execution
- RateLimitHandler: blocks after per-minute threshold
- RateLimitHandler: allows messages within limits
- RateLimitHandler: hourly window enforcement
- AllowListHandler: webchat always passes
- AllowListHandler: rejects unknown Telegram sender (silent)
- AllowListHandler: allows listed Telegram sender
- AuthHandler: sets userId to "owner"
- AgentResolver: preserves pre-set conversationId
- AgentResolver: generates new conversationId when missing
- Full pipeline integration with mock BackendClient

All 82 gateway tests pass (18 new + 64 existing).

---

## Files Changed

| File | Change |
|---|---|
| `gateway/src/pipeline/types.ts` | **New** — PipelineMessage, PipelineHandler, PipelineContext |
| `gateway/src/pipeline/pipeline.ts` | **New** — MessagePipeline class |
| `gateway/src/pipeline/handlers/*.ts` | **New** — 8 handler implementations |
| `gateway/src/pipeline/handlers/index.ts` | **New** — re-exports |
| `gateway/src/pipeline/index.ts` | **New** — re-exports |
| `gateway/src/channels/webchat.ts` | **Modified** — delegates chat messages to pipeline, added `setPipeline()` + `setCrossChannelPush()` |
| `gateway/src/channels/manager.ts` | **Modified** — delegates non-command messages to pipeline, added `setPipeline()` + `getAllowListForChannel()` + public `sendToChannel()` |
| `gateway/src/server.ts` | **Modified** — creates pipeline, wires handlers, connects to adapters |
| `gateway/src/__tests__/pipeline.test.ts` | **New** — 18 tests |

---

## What This Enables Next

| Feature | How |
|---|---|
| Discord channel | Write adapter only — pipeline handles everything |
| Message logging / audit | Add handler before TurnExecutor |
| Content filtering | Add handler after AuthHandler |
| Multi-user auth | Expand AuthHandler to map channel IDs to user table |
| Per-agent rate limits | Extend RateLimitHandler with agent-specific config |
| A/B testing | Add handler that modifies agent selection |
