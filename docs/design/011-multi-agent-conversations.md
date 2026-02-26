# Design Doc 011: Multi-Agent Conversations

**Status:** Draft  
**Author:** Developer Agent  
**Created:** 2026-02-26  
**Depends on:** 005 (Agents), 006 (Conversations), 008 (Containerized Runtime), 010 (Prompt Management)

---

## 1. Overview

Enable conversations where multiple agents participate simultaneously. Users can select one or more agents per conversation, and each agent contributes based on its role, system prompt, tools, and attached prompt fragments. Agents can see each other's responses, enabling collaboration, debate, and division of labor within a single conversation thread.

## 2. Motivation

- **Division of expertise** — A planner agent breaks down work while a developer agent implements, each with their own tools and prompts.
- **Review workflows** — One agent writes code, another reviews it, in the same conversation.
- **Redundancy and verification** — Multiple agents tackle the same question, user picks the best answer.
- **Orchestrated pipelines** — Chain agent responses: Agent A analyzes → Agent B plans → Agent C implements.

## 3. Requirements

### 3.1 Functional

| ID | Requirement | Priority |
|----|------------|----------|
| F-1 | User can select multiple agents for a conversation | Must |
| F-2 | Each message is attributed to a specific agent | Must |
| F-3 | Agents can see other agents' messages in the conversation history | Must |
| F-4 | User can control turn order (parallel, sequential, round-robin, or manual) | Must |
| F-5 | User can @-mention a specific agent to direct a message | Should |
| F-6 | Each agent maintains its own system prompt, tools, and fragment set | Must |
| F-7 | Agents can be added/removed mid-conversation | Should |
| F-8 | Each agent runs in its own container (if containerized) | Must |
| F-9 | Conversation history clearly shows which agent said what | Must |
| F-10 | User can set a "lead" agent that responds by default | Should |
| F-11 | Turn timeout — if an agent doesn't respond in N seconds, skip or escalate | Should |
| F-12 | Agent-to-agent delegation — one agent can explicitly hand off to another | Could |

### 3.2 Non-Functional

| ID | Requirement | Priority |
|----|------------|----------|
| NF-1 | No performance degradation for single-agent conversations | Must |
| NF-2 | Multi-agent turns execute within 2x the single-agent latency for parallel mode | Should |
| NF-3 | Full audit trail — every agent turn is logged with agent ID, model, token usage | Must |
| NF-4 | Graceful degradation — if one agent's container is unhealthy, others continue | Must |
| NF-5 | Rate limiting — per-agent and per-conversation rate limits | Should |

## 4. Architecture

### 4.1 Data Model Changes

#### `conversations` table — new columns

```sql
ALTER TABLE conversations ADD COLUMN turn_mode TEXT NOT NULL DEFAULT 'single'
    CHECK(turn_mode IN ('single', 'parallel', 'sequential', 'round_robin', 'manual'));
ALTER TABLE conversations ADD COLUMN lead_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL;
```

#### New: `conversation_agents` — agents participating in a conversation

```sql
CREATE TABLE conversation_agents (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'participant'
        CHECK(role IN ('lead', 'participant', 'observer')),
    turn_order INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(conversation_id, agent_id)
);

CREATE INDEX idx_ca_conversation ON conversation_agents(conversation_id, turn_order);
```

#### `conversation_messages` table — new column

```sql
ALTER TABLE conversation_messages ADD COLUMN agent_id TEXT REFERENCES agents(id);
ALTER TABLE conversation_messages ADD COLUMN agent_name TEXT;
```

### 4.2 Turn Modes

| Mode | Behavior |
|------|----------|
| `single` | Legacy — one agent per conversation (backward compatible) |
| `parallel` | All selected agents receive the user message simultaneously and respond concurrently |
| `sequential` | Agents respond one at a time in `turn_order`. Each sees the previous agent's response. |
| `round_robin` | Like sequential, but cycles automatically. User message → Agent A → Agent B → Agent A → ... until a stop condition. |
| `manual` | User explicitly @-mentions which agent should respond. No automatic turns. |

### 4.3 Message Flow

```
User sends message
    │
    ├─ turn_mode = 'single'
    │   └─ Route to conversation's agent (existing behavior)
    │
    ├─ turn_mode = 'parallel'
    │   └─ Fan out to all active agents simultaneously
    │       ├─ Agent A starts turn (own container/host)
    │       ├─ Agent B starts turn (own container/host)
    │       └─ Responses stream back independently, labeled by agent
    │
    ├─ turn_mode = 'sequential'
    │   └─ Route to agent with turn_order = 0
    │       └─ On completion, auto-trigger next agent (turn_order = 1)
    │           └─ On completion, auto-trigger next (turn_order = 2)
    │               └─ ... until all agents have responded
    │
    ├─ turn_mode = 'round_robin'
    │   └─ Like sequential, but wraps around
    │       └─ Stop conditions: max_rounds reached, agent signals "done",
    │          or user interrupts
    │
    └─ turn_mode = 'manual'
        └─ Parse @-mention from user message
            ├─ @agent-name found → route to that agent only
            └─ No @-mention → route to lead agent (or error if no lead)
```

### 4.4 Gateway Changes

The gateway currently resolves a single agent per conversation. For multi-agent:

1. **Resolve phase** returns a list of `AgentResolution[]` instead of a single resolution.
2. **Parallel mode:** Gateway spawns concurrent SSE streams, one per agent. Each stream is tagged with `agent_id` and `agent_name` in the events.
3. **Sequential mode:** Gateway chains turns — waits for agent A's `done` event, then starts agent B's turn with the updated history.
4. **Client protocol:** New message fields:
   - `agentId` — which agent sent this chunk/response
   - `agentName` — display name for the agent
   - `turnIndex` — position in the sequential chain (0, 1, 2...)

```typescript
// Updated OutgoingMessage
interface OutgoingMessage {
  // ... existing fields ...
  agentId?: string;
  agentName?: string;
  turnIndex?: number;
}
```

### 4.5 Frontend Changes

#### Agent Selection
- Replace single agent dropdown with multi-select pill/tag component
- First selected agent becomes `lead` by default
- Drag to reorder for sequential/round-robin turn order
- Turn mode selector (parallel | sequential | round-robin | manual)

#### Message Display
- Each message bubble shows the agent name/avatar
- Color-coded by agent for quick visual distinction
- Parallel responses appear side-by-side or stacked with clear agent labels
- Sequential responses appear in order with a visual connector

#### @-mention Support
- Type `@` in the input field to show agent autocomplete
- Selected agent name appears as a highlighted pill in the input
- In manual mode, @-mention is required

### 4.6 Backend API Changes

#### New Endpoints

```
POST   /api/v1/conversations/{id}/agents          — Add agent(s) to conversation
DELETE /api/v1/conversations/{id}/agents/{agent_id} — Remove agent from conversation
PUT    /api/v1/conversations/{id}/agents/{agent_id} — Update role/turn_order
GET    /api/v1/conversations/{id}/agents            — List conversation agents
PUT    /api/v1/conversations/{id}/turn-mode         — Change turn mode
```

#### Updated Endpoints

```
POST /api/v1/agent/resolve — Returns AgentResolution[] for multi-agent conversations
GET  /api/v1/conversations/{id} — Includes agents[] and turn_mode in response
```

### 4.7 Container Orchestration

Each agent in a multi-agent conversation runs in its own container (if containerized). The sandbox manager already supports multiple concurrent containers. Key considerations:

- **Port allocation** — Each agent gets its own port (already handled by port pool)
- **Shared workspace** — If agents need to collaborate on files, they can share workspace mounts. The same host directory can be mounted into multiple containers.
- **Isolation** — Agents cannot directly communicate with each other. All coordination goes through the conversation history.
- **Resource limits** — Per-agent resource limits apply independently. A conversation with 3 agents uses 3x the container resources.

## 5. Security & Authorization

| Concern | Mitigation |
|---------|-----------|
| Agent impersonation | Messages are tagged server-side with the originating agent ID. Agents cannot forge the `agent_id` field. |
| Prompt injection between agents | Each agent's system prompt includes only its own fragments. One agent's output is treated as conversation history, not system instructions, for other agents. |
| Resource exhaustion | Per-conversation agent limit (configurable, default: 5). Per-agent rate limits. Total conversation token budget. |
| Data leakage | Agents in the same conversation share the same conversation history by design. If agent isolation is needed, use separate conversations. |
| Runaway round-robin | Max rounds limit (configurable, default: 10). Total turn timeout per conversation. |

## 6. Audit & Observability

Every agent turn in a multi-agent conversation is logged to `audit_log`:

```json
{
  "event": "agent_turn",
  "conversation_id": "...",
  "agent_id": "...",
  "agent_name": "...",
  "turn_mode": "parallel",
  "turn_index": 0,
  "model": "anthropic/claude-sonnet-4-20250514",
  "input_tokens": 1234,
  "output_tokens": 567,
  "tool_calls": ["file_read", "code_execute"],
  "duration_ms": 3400,
  "status": "completed"
}
```

Dashboard metrics:
- Turns per conversation (by mode)
- Agent response time distribution
- Token usage per agent per conversation
- Error rate per agent in multi-agent conversations

## 7. Migration Strategy

### Phase 1: Foundation (Non-Breaking)
1. Add `conversation_agents` table
2. Add `turn_mode` and `lead_agent_id` to `conversations`
3. Add `agent_id` and `agent_name` to `conversation_messages`
4. Backfill: For existing conversations, insert a `conversation_agents` row with the current `agent_id` as `lead`
5. Backfill: Tag existing messages with the conversation's `agent_id`

### Phase 2: Backend
1. Update resolve endpoint to return agent list
2. Implement parallel turn execution in gateway
3. Implement sequential turn chaining
4. Add conversation agent management endpoints

### Phase 3: Frontend
1. Multi-select agent component
2. Turn mode selector
3. Agent-labeled message bubbles
4. @-mention autocomplete

### Phase 4: Advanced
1. Round-robin mode with stop conditions
2. Agent-to-agent delegation protocol
3. Shared workspace coordination
4. Conversation-level token budgets

## 8. Backward Compatibility

- Single-agent conversations continue to work exactly as before (`turn_mode = 'single'`)
- Existing API contracts are preserved — new fields are additive
- The `conversations.agent_id` column is retained as `lead_agent_id` for backward compatibility
- Gateway falls back to single-agent behavior if `conversation_agents` table has exactly one entry

## 9. Configuration

```json
{
  "multi_agent": {
    "max_agents_per_conversation": 5,
    "max_round_robin_rounds": 10,
    "parallel_turn_timeout_seconds": 120,
    "sequential_turn_timeout_seconds": 60,
    "total_conversation_token_budget": 500000,
    "enable_at_mentions": true,
    "enable_agent_delegation": false
  }
}
```

## 10. Open Questions

1. **Should agents see each other's tool calls?** Currently tool calls are internal to a turn. In multi-agent mode, should Agent B see that Agent A called `file_write`? Recommendation: Yes, include tool calls in shared history for transparency.

2. **How to handle conflicting file writes?** If two agents in parallel mode both write to the same file, the last write wins. Should we add file locking? Recommendation: Defer to Phase 4. Document the risk. Users should use sequential mode for file-heavy collaboration.

3. **Should round-robin have a "consensus" stop condition?** e.g., stop when two agents agree on an answer. Recommendation: Interesting but complex. Defer to Phase 4.

4. **Per-agent conversation memory?** Should each agent maintain its own memory of the conversation, or share a single memory? Recommendation: Each agent uses its own memory system (already scoped by agent). Shared conversation summary is available to all.

## 11. Alternatives Considered

| Alternative | Why Rejected |
|------------|-------------|
| Separate conversations per agent with a "bridge" | Too complex for users. Loses the benefit of shared context. |
| Single container with multiple agent processes | Breaks isolation. Can't use different images/tools per agent. |
| Agent-to-agent direct communication (pub/sub) | Introduces complexity. Conversation history as the communication channel is simpler, auditable, and transparent. |
| Fixed agent roles (planner/developer/reviewer) | Too rigid. Users should compose their own agent teams. |
