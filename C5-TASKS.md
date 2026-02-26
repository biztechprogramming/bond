# C5: Gateway Talks Directly to Container Worker

**Reference:** Design Doc 008, §6 (Turn Flow), §7 (Lifecycle), §12 (Migration Path)

**Goal:** When an agent has `sandbox_image` set, the gateway routes turns to the container worker's `/turn` SSE endpoint instead of the backend's `/api/v1/agent/turn`. The gateway becomes the SSE bridge between frontend WebSocket and container worker HTTP/SSE. Memory promotion events from the worker are forwarded to the backend for persistence.

---

## Architecture Change

```
BEFORE (host mode — unchanged):
  Frontend ──WS──► Gateway ──HTTP/SSE──► Backend (agent_turn in-process)

AFTER (container mode — new):
  Frontend ──WS──► Gateway ──HTTP/SSE──► Container Worker (:18791+)
                       │
                       └──HTTP POST──► Backend (shared memory writes, message persistence)
```

The backend still owns:
- Conversation CRUD (create, load history, save messages)
- Agent config (which agent, what model, sandbox_image)
- Shared memory persistence (promoted memories from SSE events)

The gateway adds:
- Route resolution: ask backend which agent handles this conversation → get worker_url if containerized
- SSE proxy: POST to worker `/turn`, relay events to frontend WebSocket
- Memory promotion: intercept `memory` SSE events from worker, POST to backend shared memory endpoint
- Message persistence: after turn completes, POST assistant message to backend for storage

---

## Task 1: Backend — Agent Resolution Endpoint

**File:** `backend/app/api/v1/agent.py` (new endpoint)

New endpoint for the gateway to resolve how to route a turn:

```
GET /api/v1/agent/resolve?conversation_id={id}
```

Response (containerized agent):
```json
{
  "mode": "container",
  "worker_url": "http://localhost:18793",
  "agent_id": "agent-abc123",
  "conversation_id": "conv-xyz"
}
```

Response (host-mode agent):
```json
{
  "mode": "host",
  "agent_id": "agent-abc123",
  "conversation_id": "conv-xyz"
}
```

Implementation:
- Look up conversation → agent → check `sandbox_image`
- If containerized: call `sandbox_manager.ensure_running(agent)` to get `worker_url`
- If host: return `mode: "host"` (gateway uses existing backend SSE path)
- If conversation doesn't exist yet: create it, resolve agent, return mode
- Accept optional `agent_id` query param for explicit agent selection

Error cases:
- Conversation not found + no agent_id → 400
- Agent not found → 404
- Container failed to start → 503 with error detail
- Include correlation ID in all responses for tracing

---

## Task 2: Backend — Save Assistant Message Endpoint

**File:** `backend/app/api/v1/conversations.py` (new endpoint)

When the gateway proxies a turn to the container worker, the backend doesn't see the response. The gateway needs to persist the assistant message afterward:

```
POST /api/v1/conversations/{conversation_id}/messages
```

Request:
```json
{
  "role": "assistant",
  "content": "I fixed the bug in line 42...",
  "tool_calls_made": 3
}
```

Response:
```json
{
  "message_id": "01KJD...",
  "conversation_id": "conv-xyz"
}
```

Implementation:
- Reuse existing `_save_message()` helper from `agent.py`
- Increment `message_count` on conversation
- Update conversation `updated_at`
- Validate role is `"assistant"` (gateway shouldn't save user messages this way)
- Return 404 if conversation doesn't exist

Note: this endpoint already partially exists for queuing user messages. Extend it to support assistant messages from the gateway.

---

## Task 3: Backend — Shared Memory Write Endpoint (Stub)

**File:** `backend/app/api/v1/memory.py` (new file)

Stub for C6 (shared memory), but needed now so the gateway has something to call:

```
POST /api/v1/shared-memories
```

Request:
```json
{
  "agent_id": "agent-abc123",
  "memory_id": "01KJC...",
  "type": "fact",
  "content": "User prefers dark mode",
  "summary": "User prefers dark mode",
  "source_type": "agent",
  "entities": []
}
```

Response (stub):
```json
{
  "status": "accepted",
  "shared_memory_id": "01KJD..."
}
```

For C5, this is a stub that:
- Validates the request body
- Logs the promotion event
- Returns 202 Accepted
- Does NOT actually persist to shared DB yet (that's C6)

Include a `# TODO(C6): persist to shared_memories table` comment.

---

## Task 4: Gateway — Worker Client

**File:** `gateway/src/backend/worker-client.ts` (new file)

New client class for talking to container workers:

```typescript
export class WorkerClient {
  constructor(private workerUrl: string) {}

  async healthCheck(): Promise<boolean> { ... }

  async *turnStream(req: WorkerTurnRequest): AsyncGenerator<WorkerSSEEvent> { ... }

  async interrupt(newMessages: Array<{ role: string; content: string }>): Promise<void> { ... }
}
```

`WorkerTurnRequest`:
```typescript
{
  messages: Array<{ role: string; content: string }>;
  conversation_id: string;
}
```

`WorkerSSEEvent` types (matching worker.py output):
- `status` — `{ state: "thinking" }`
- `chunk` — `{ content: "..." }`
- `tool_call` — `{ tool: "file_read", arguments: {...} }`
- `memory` — `{ type: "fact", content: "...", memory_id: "...", entities: [...] }` (promotion event)
- `done` — `{ response: "...", tool_calls_made: 3 }`
- `error` — `{ message: "..." }`

SSE parsing:
- Reuse the same `event:/data:` parsing logic from `BackendClient.agentTurnStream()`
- Extract into a shared utility: `gateway/src/backend/sse-parser.ts`
- Handle connection errors with clear error messages (worker unreachable, connection reset)

Timeout:
- Per-request timeout of 300s (agent turns can be long)
- Abort controller for cancellation on interrupt

---

## Task 5: Gateway — Route Resolution

**File:** `gateway/src/backend/client.ts` (add method)

Add `resolveAgent()` to `BackendClient`:

```typescript
interface AgentResolution {
  mode: "container" | "host";
  worker_url?: string;
  agent_id: string;
  conversation_id: string;
}

async resolveAgent(conversationId?: string, agentId?: string): Promise<AgentResolution> {
  const params = new URLSearchParams();
  if (conversationId) params.set("conversation_id", conversationId);
  if (agentId) params.set("agent_id", agentId);

  const res = await fetch(`${this.baseUrl}/api/v1/agent/resolve?${params}`);
  ...
}
```

Also add `saveAssistantMessage()` and `promoteMemory()`:

```typescript
async saveAssistantMessage(conversationId: string, content: string, toolCallsMade: number): Promise<{ message_id: string }> { ... }

async promoteMemory(data: MemoryPromotionEvent): Promise<void> { ... }
```

---

## Task 6: Gateway — Dual-Mode Turn Routing

**File:** `gateway/src/channels/webchat.ts`

Modify `startStreamingTurn()` to resolve the agent first, then route:

```typescript
private async startStreamingTurn(...): Promise<void> {
  // 1. Resolve agent mode
  const resolution = await this.backendClient.resolveAgent(conversationId);

  if (resolution.mode === "container") {
    await this.startContainerTurn(socket, sessionId, message, resolution);
  } else {
    await this.startHostTurn(socket, sessionId, message, resolution.conversation_id);
  }
}
```

Rename existing `startStreamingTurn()` logic to `startHostTurn()` (no changes to host flow).

New `startContainerTurn()`:
1. Create `WorkerClient` with `resolution.worker_url`
2. Load conversation history from backend
3. POST to worker `/turn` with messages + conversation_id
4. Relay SSE events to frontend WebSocket:
   - `status` → forward as-is
   - `chunk` → forward + accumulate response text
   - `tool_call` → forward to frontend (for UI display)
   - `memory` → **intercept**: call `backendClient.promoteMemory()`, do NOT forward to frontend
   - `done` → save assistant message to backend, then forward to frontend
   - `error` → forward to frontend
5. Update session state (agentBusy, conversationId)
6. Handle queued messages (auto-continue if `queued_count > 0`)

Error handling:
- Worker unreachable → send error to frontend, log with conversation_id
- Worker SSE stream interrupted → send error, mark agent idle
- Backend save fails after turn → log error (don't fail the turn — user already saw the response)
- Memory promotion fails → log warning (non-fatal, memory stays local)

---

## Task 7: Gateway — Interrupt Routing

**File:** `gateway/src/channels/webchat.ts`

Update `handleInterrupt()` to route to the correct target:

```typescript
private async handleInterrupt(...): Promise<void> {
  const resolution = await this.backendClient.resolveAgent(conversationId);

  if (resolution.mode === "container") {
    const worker = new WorkerClient(resolution.worker_url!);
    await worker.interrupt(newMessages);
  } else {
    await this.backendClient.interrupt(conversationId);
  }
}
```

---

## Task 8: Shared SSE Parser

**File:** `gateway/src/backend/sse-parser.ts` (new file)

Extract SSE parsing from `BackendClient.agentTurnStream()` into a reusable async generator:

```typescript
export async function* parseSSEStream(
  response: Response,
  options?: { signal?: AbortSignal }
): AsyncGenerator<SSEEvent> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  ...
}
```

Both `BackendClient.agentTurnStream()` and `WorkerClient.turnStream()` use this.

Requirements:
- Handle partial chunks (data split across reads)
- Handle multi-line data fields
- Handle unnamed events (default to "message")
- Clean up reader on generator return/throw
- AbortSignal support for cancellation

---

## Task 9: Worker Client Caching

**File:** `gateway/src/channels/webchat.ts` or new `gateway/src/backend/worker-pool.ts`

Don't create a new `WorkerClient` per turn — cache by worker_url:

```typescript
class WorkerPool {
  private clients = new Map<string, WorkerClient>();

  get(workerUrl: string): WorkerClient {
    let client = this.clients.get(workerUrl);
    if (!client) {
      client = new WorkerClient(workerUrl);
      this.clients.set(workerUrl, client);
    }
    return client;
  }

  remove(workerUrl: string): void {
    this.clients.delete(workerUrl);
  }
}
```

Why: avoids re-creating HTTP clients, enables future connection pooling, provides a single place to handle worker death (remove from pool).

---

## Task 10: Observability

Structured logging throughout the gateway for container turns:

```
[gateway] Resolving agent for conversation conv-xyz → container (worker http://localhost:18793)
[gateway] Starting container turn: conversation=conv-xyz worker=http://localhost:18793
[gateway] Container turn SSE event: status thinking
[gateway] Container turn SSE event: chunk (42 chars)
[gateway] Container turn SSE event: memory promote type=fact
[gateway] Memory promotion sent to backend: agent=agent-abc123 type=fact
[gateway] Container turn complete: conversation=conv-xyz tool_calls=3 response_length=847 elapsed=12.4s
[gateway] Assistant message saved: conversation=conv-xyz message_id=01KJD...
[gateway] ERROR Container turn failed: conversation=conv-xyz error=ECONNREFUSED
[gateway] WARN Memory promotion failed (non-fatal): agent=agent-abc123 error=500
```

Requirements:
- Log at turn boundaries (start, complete, error) — not every chunk
- Include conversation_id in all turn-related logs
- Include elapsed time on turn completion
- Memory promotion failures are WARN, not ERROR (non-fatal)
- Worker connection failures are ERROR

---

## Task 11: Tests — Gateway (vitest)

**Setup:** Add vitest to gateway:
```bash
cd gateway && pnpm add -D vitest
```

Add to `gateway/package.json` scripts:
```json
"test": "vitest run",
"test:watch": "vitest"
```

Update `gateway/tsconfig.json` include to add test files if needed.

**File:** `gateway/src/__tests__/sse-parser.test.ts`

SSE parser tests:
- test_parses_single_event
- test_parses_multiple_events
- test_handles_partial_chunks (data split across reads)
- test_handles_unnamed_events (default to "message")
- test_handles_empty_lines_between_events
- test_skips_malformed_json_data
- test_handles_multi_line_data
- test_aborts_on_signal

**File:** `gateway/src/__tests__/worker-client.test.ts`

Worker client tests (mock fetch):
- test_health_check_success
- test_health_check_failure
- test_turn_stream_parses_all_event_types
- test_turn_stream_connection_error
- test_interrupt_sends_correct_payload

**File:** `gateway/src/__tests__/worker-pool.test.ts`

Worker pool tests:
- test_caches_client_by_url
- test_returns_same_client_for_same_url
- test_remove_clears_client

**File:** `gateway/src/__tests__/routing.test.ts`

Routing logic tests (mock BackendClient + WorkerClient):
- test_host_mode_uses_backend_stream
- test_container_mode_uses_worker_stream
- test_container_mode_saves_assistant_message_after_turn
- test_container_mode_promotes_memory_events
- test_container_mode_memory_promotion_failure_non_fatal
- test_container_mode_worker_error_sent_to_frontend
- test_interrupt_routes_to_container_worker
- test_interrupt_routes_to_backend_for_host_mode

---

## Task 12: Tests — Backend Endpoints (pytest)

**File:** `backend/tests/test_agent_resolve.py` (new)

- test_resolve_host_mode_agent
- test_resolve_container_mode_agent
- test_resolve_creates_conversation_if_needed
- test_resolve_nonexistent_conversation_returns_400
- test_resolve_nonexistent_agent_returns_404

**File:** `backend/tests/test_conversations_api.py` (extend or new)

- test_save_assistant_message
- test_save_assistant_message_increments_count
- test_save_assistant_message_nonexistent_conversation_404
- test_save_non_assistant_role_rejected

**File:** `backend/tests/test_shared_memory_stub.py` (new)

- test_promote_memory_returns_202
- test_promote_memory_validates_required_fields
- test_promote_memory_logs_event

---

## Task 13: TypeScript Build Verification

After all changes:
```bash
cd gateway && pnpm run lint    # tsc --noEmit (type checking)
cd gateway && pnpm run build   # tsc (compile)
cd gateway && pnpm test        # vitest run
```

All must pass with zero errors.

---

## Definition of Done

- [ ] All 13 tasks implemented
- [ ] Backend: resolve endpoint, assistant message save, shared memory stub
- [ ] Gateway: WorkerClient, SSE parser, dual-mode routing, interrupt routing, worker pool
- [ ] Gateway: `pnpm run lint` passes (zero type errors)
- [ ] Gateway: `pnpm run build` passes
- [ ] Gateway: `pnpm test` passes (target: 20+ tests)
- [ ] Backend: `pytest` passes (target: 12+ new tests, all existing pass)
- [ ] Host-mode flow completely unchanged (regression-safe)
- [ ] Container-mode flow: gateway → worker → SSE → frontend works end-to-end
- [ ] Memory promotion events intercepted and forwarded to backend
- [ ] Assistant messages persisted to backend after container turns
- [ ] Structured logging at turn boundaries with timing
- [ ] No TODOs except the explicit C6 stub comment
- [ ] Committed: `feat: C5 — gateway routes turns to container worker`
