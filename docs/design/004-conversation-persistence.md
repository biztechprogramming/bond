# Design Doc 004: Conversation Persistence

**Status:** Draft — awaiting review
**Depends on:** 001 (Knowledge Store), 003 (Agent Tools)

---

## 1. Overview

Conversations are currently ephemeral — the gateway holds history in memory, and the frontend holds messages in React state. Refreshing the page or restarting the gateway loses everything.

This design doc adds proper conversation persistence:

- **Messages saved to the database** as they happen
- **History survives refresh** — frontend reconnects and loads from server
- **Conversations indexed for RAG** — past conversations searchable via knowledge store
- **Multi-conversation support** — user can have multiple conversations, switch between them

---

## 2. Data Model

### 2.1 Tables

```sql
-- Migration 000006: Conversations

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,                     -- ULID
    title TEXT,                              -- Auto-generated or user-set
    agent_id TEXT NOT NULL REFERENCES agents(id),
    channel TEXT NOT NULL DEFAULT 'webchat',
    is_active INTEGER NOT NULL DEFAULT 1,    -- is this the current conversation?
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_conv_agent ON conversations(agent_id);
CREATE INDEX idx_conv_active ON conversations(is_active) WHERE is_active = 1;
CREATE INDEX idx_conv_updated ON conversations(updated_at DESC);

CREATE TRIGGER conversations_updated_at
    AFTER UPDATE ON conversations FOR EACH ROW
BEGIN
    UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,                     -- ULID
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tool_calls JSON,                         -- tool calls made by assistant (if any)
    tool_call_id TEXT,                       -- for role='tool', which call this is responding to
    token_count INTEGER,                     -- approximate token count
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_cm_conv ON conversation_messages(conversation_id, created_at);
```

### 2.2 Auto-Title

After the first assistant response, the backend generates a short title from the user's first message (truncate to ~50 chars or use the LLM to generate a 3-5 word summary). This shows in the conversation list sidebar.

---

## 3. Backend API

### 3.1 Conversation Endpoints

```
POST   /api/v1/conversations                    — create new conversation
GET    /api/v1/conversations                    — list conversations (paginated, newest first)
GET    /api/v1/conversations/{id}               — get conversation with messages
GET    /api/v1/conversations/{id}/messages      — get messages (paginated, for long convos)
PUT    /api/v1/conversations/{id}               — update title
DELETE /api/v1/conversations/{id}               — delete conversation and messages
```

### 3.2 Agent Turn Changes

The `/api/v1/agent/turn` endpoint changes:

```python
class AgentTurnRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # existing conversation, or None to create new
    stream: bool = False

class AgentTurnResponse(BaseModel):
    response: str
    conversation_id: str               # always returned
    message_id: str                    # the assistant message ID
```

**Flow:**
1. If `conversation_id` is null → create new conversation, use default agent
2. Load conversation's agent config from DB
3. Load message history from `conversation_messages` (not from the request body)
4. Run agent turn (auto-RAG + tool loop)
5. Save user message + assistant response (+ tool call messages) to `conversation_messages`
6. Update `conversations.message_count` and `updated_at`
7. Return response + conversation_id

The `history` field in the request is **removed** — history always comes from the database. This is the single source of truth.

### 3.3 Knowledge Store Indexing

After each conversation turn, the backend:
1. Saves the user+assistant messages to `conversation_messages`
2. Creates a `content_chunk` with `source_type='conversation'` and `source_id=conversation_id` for the exchange
3. The chunk gets queued for embedding (processed_at = NULL)
4. Background worker embeds it → available for RAG on future turns

This means past conversations become searchable context automatically.

---

## 4. Gateway Changes

### 4.1 Session → Conversation Mapping

The gateway's `SessionManager` maps WebSocket connections to conversation IDs (not its own history):

```typescript
interface Session {
  id: string;                    // WebSocket session ID
  conversationId: string | null; // Backend conversation ID
  createdAt: Date;
  // history[] removed — backend owns this now
}
```

### 4.2 Message Flow

```
Frontend                    Gateway                     Backend
   │                          │                            │
   ├─ connect ───────────────▶│                            │
   │                          ├─ create session            │
   │◀── connected(sessionId) ─┤                            │
   │                          │                            │
   ├─ message(text) ─────────▶│                            │
   │                          ├─ POST /agent/turn ────────▶│
   │                          │   {message, conv_id}       ├─ load history from DB
   │                          │                            ├─ run agent turn
   │                          │                            ├─ save messages to DB
   │                          │◀── {response, conv_id} ───┤
   │◀── response(text,        │                            │
   │     conv_id) ────────────┤                            │
   │                          │                            │
   ├─ reconnect(conv_id) ────▶│                            │
   │                          ├─ GET /conversations/{id} ─▶│
   │                          │◀── {messages} ─────────────┤
   │◀── history(messages) ────┤                            │
```

### 4.3 Reconnection

When the frontend connects with an existing `conversationId`:
1. Gateway sends it to the backend: `GET /conversations/{id}/messages`
2. Backend returns the message history
3. Gateway sends it to the frontend as a `history` event
4. Frontend renders the messages

---

## 5. Frontend Changes

### 5.1 Conversation State

```typescript
// Stored in localStorage: just the active conversation ID
const [conversationId, setConversationId] = useState<string | null>(() => {
  return localStorage.getItem("bond-conversation-id");
});
```

On connect, the frontend sends its `conversationId` to the gateway. If the gateway can load the history, it sends it back. If not (deleted, invalid), a new conversation starts.

### 5.2 Conversation Sidebar

A collapsible sidebar showing past conversations:
- List of conversations with title, date, message count
- Click to switch conversations
- "New Conversation" button
- Delete conversation (swipe or button)

### 5.3 Message Rendering

Messages come from the server on reconnect, and are appended in real-time during the conversation. The frontend does NOT maintain its own history as source of truth — it's purely a render cache.

---

## 6. Implementation Stories

### Story 12a: Migration 000006 — Conversations
- Create `conversations` and `conversation_messages` tables
- Tests: migration up/down

### Story 12b: Conversation API
- Backend CRUD endpoints for conversations
- Tests: create, list, get with messages, delete

### Story 12c: Agent Turn with Persistence
- Update `/agent/turn` to accept `conversation_id`, load history from DB, save messages
- Remove `history` field from request
- Auto-create conversation if none provided
- Auto-title from first message
- Tests: turn creates conversation, messages persisted, history loaded

### Story 12d: Gateway Session Refactor
- Remove in-memory history from SessionManager
- Forward `conversation_id` between frontend and backend
- Handle reconnection: load history from backend, send to frontend
- Add `history` event type to protocol

### Story 12e: Frontend Reconnection
- Store `conversationId` in localStorage
- Send on connect, receive history, render
- Remove React state as source of truth

### Story 12f: Conversation Sidebar
- Sidebar component listing past conversations
- New conversation button
- Switch between conversations
- Delete conversation

### Story 12g: Knowledge Store Indexing
- After each turn, create content_chunk from the exchange
- Queue for embedding
- Tests: conversation turn creates searchable content

---

## 7. Migration Path

This is backward-compatible:
1. Deploy migration 000006
2. Deploy backend changes — old clients that send `history` still work (it's ignored, DB is source of truth)
3. Deploy gateway changes — sessions without conversation_id create new conversations
4. Deploy frontend changes — starts sending/receiving conversation_id
