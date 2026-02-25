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

## 2. Complete ER Diagram

This diagram shows the full Bond database schema — all tables across all migrations (000001–000006), including the new conversation tables.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            BOND DATABASE SCHEMA                             │
│                                                                             │
│  PK = Primary Key    FK = Foreign Key    NN = NOT NULL    UQ = Unique       │
│  TS = TIMESTAMP      CHK = CHECK         DF = DEFAULT     JSON = json_valid │
│  All PKs are ULIDs (time-sortable, globally unique)                         │
│  All tables use soft deletes where applicable (deleted_at)                  │
│  All mutable tables have created_at + updated_at with auto-update triggers  │
└─────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
  FOUNDATION LAYER (migrations 000001, 000005)
═══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────┐
  │      settings            │  000001
  ├─────────────────────────┤
  │ key        TEXT PK       │  Key-value config store
  │ value      TEXT NN       │  API keys stored encrypted (enc: prefix)
  │ created_at TS NN         │
  │ updated_at TS NN         │  Auto-trigger
  └─────────────────────────┘

  ┌─────────────────────────┐       ┌─────────────────────────┐
  │      agents              │  005  │  agent_workspace_mounts  │  005
  ├─────────────────────────┤       ├─────────────────────────┤
  │ id           TEXT PK     │◀──FK─┤ id           TEXT PK     │
  │ name         TEXT NN UQ  │       │ agent_id     TEXT NN FK  │
  │ display_name TEXT NN     │       │ host_path    TEXT NN     │
  │ system_prompt TEXT NN    │       │ mount_name   TEXT NN     │  → /workspace/{name}
  │ model        TEXT NN     │       │ readonly     INT NN DF 0 │
  │ sandbox_image TEXT       │       │ created_at   TS NN       │
  │ tools        JSON NN     │       ├─────────────────────────┤
  │ max_iterations INT NN    │       │ UQ(agent_id, mount_name) │
  │  DF 25                   │       └─────────────────────────┘
  │ auto_rag     INT NN DF 1 │
  │ auto_rag_limit INT NN   │       ┌─────────────────────────┐
  │  DF 5                    │       │   agent_channels         │  005
  │ is_default   INT NN DF 0 │       ├─────────────────────────┤
  │ is_active    INT NN DF 1 │◀──FK─┤ id           TEXT PK     │
  │ created_at   TS NN       │       │ agent_id     TEXT NN FK  │
  │ updated_at   TS NN       │       │ channel      TEXT NN     │
  └──────────┬──────────────┘       │ sandbox_override TEXT    │
             │                       │ enabled      INT NN DF 1 │
             │                       │ created_at   TS NN       │
             │                       ├─────────────────────────┤
             │                       │ UQ(agent_id, channel)    │
             │                       └─────────────────────────┘
             │
             │ FK: conversations.agent_id
             ▼
═══════════════════════════════════════════════════════════════════════════════
  CONVERSATION LAYER (migration 000006)
═══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────┐       ┌─────────────────────────┐
  │    conversations         │  006  │  conversation_messages   │  006
  ├─────────────────────────┤       ├─────────────────────────┤
  │ id           TEXT PK     │◀──FK─┤ id           TEXT PK     │
  │ agent_id     TEXT NN FK ─┼──▶ agents.id     │ conversation_id TEXT NN FK│
  │ channel      TEXT NN     │       │ role         TEXT NN CHK │
  │  DF 'webchat'            │       │  ∊{user,assistant,      │
  │ title        TEXT        │       │    system,tool}          │
  │ is_active    INT NN DF 1 │       │ content      TEXT NN     │
  │ message_count INT NN DF 0│       │ tool_calls   JSON        │
  │ summary_id   TEXT FK ────┼──▶ session_summaries.id      │ tool_call_id TEXT        │
  │ created_at   TS NN       │       │ token_count  INT         │
  │ updated_at   TS NN       │       │ created_at   TS NN       │
  └─────────────────────────┘       └─────────────────────────┘
                                     IDX: (conversation_id, created_at)


═══════════════════════════════════════════════════════════════════════════════
  KNOWLEDGE STORE LAYER (migration 000002)
═══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────┐             ┌─────────────────────────┐
  │   embedding_configs      │  002       │     content_chunks       │  002
  ├─────────────────────────┤             ├─────────────────────────┤
  │ model_name   TEXT PK     │             │ id           TEXT PK     │
  │ family       TEXT NN     │             │ source_type  TEXT NN     │  conversation|file|
  │ provider     TEXT NN     │             │ source_id    TEXT        │  email|web
  │ max_dimension INT NN     │             │ text         TEXT NN     │
  │ supported_dims JSON NN   │             │ summary      TEXT        │
  │ supports_local INT NN    │             │ chunk_index  INT NN DF 0 │
  │ supports_api   INT NN    │             │ parent_id    TEXT FK ────┼──self ref
  │ is_default   INT NN DF 0 │             │ sensitivity  TEXT NN CHK │
  │ created_at   TS NN       │             │  ∊{normal,personal,     │
  └─────────────────────────┘             │    secret}               │
                                           │ metadata     JSON CHK    │
                                           │ embedding_model TEXT     │
  ┌─────────────────────────┐             │ processed_at TS          │  NULL = needs embedding
  │  content_chunks_vec      │             │ created_at   TS NN       │
  │  (vec0 virtual, runtime) │             │ updated_at   TS NN       │
  ├─────────────────────────┤             └────────┬────────────────┘
  │ id        TEXT PK        │                      │
  │ embedding FLOAT[N]       │             ┌────────┴────────────────┐
  └─────────────────────────┘             │  content_chunks_fts      │
                                           │  (FTS5 virtual)          │
  * N = embedding.output_dimension         ├─────────────────────────┤
    from settings (default 1024)           │ id      TEXT UNINDEXED   │
    Created at runtime by                  │ text    TEXT             │
    ensure_vec_tables()                    │ summary TEXT             │
                                           └─────────────────────────┘
                                           Sync triggers: INSERT/UPDATE/DELETE


  ┌─────────────────────────┐             ┌─────────────────────────┐
  │        memories          │  002       │    memories_vec          │
  ├─────────────────────────┤             │    (vec0 virtual)        │
  │ id           TEXT PK     │             ├─────────────────────────┤
  │ type         TEXT NN CHK │             │ id        TEXT PK        │
  │  ∊{fact,solution,       │             │ embedding FLOAT[N]       │
  │    instruction,         │             └─────────────────────────┘
  │    preference}          │
  │ content      TEXT NN     │             ┌─────────────────────────┐
  │ summary      TEXT        │             │    memories_fts          │
  │ source_type  TEXT        │             │    (FTS5 virtual)        │
  │ source_id    TEXT        │             ├─────────────────────────┤
  │ sensitivity  TEXT NN CHK │             │ id      TEXT UNINDEXED   │
  │  ∊{normal,personal,     │             │ content TEXT             │
  │    secret}               │             │ summary TEXT             │
  │ metadata     JSON CHK    │             └─────────────────────────┘
  │ embedding_model TEXT     │
  │ importance   REAL NN CHK │
  │  BETWEEN 0.0 AND 1.0    │             ┌─────────────────────────┐
  │ access_count INT NN DF 0 │             │    memory_versions       │  002
  │ last_accessed_at TS      │             │    (append-only)         │
  │ processed_at TS          │             ├─────────────────────────┤
  │ deleted_at   TS          │  soft del   │ id           TEXT PK     │
  │ created_at   TS NN       │             │ memory_id    TEXT NN FK ─┼──▶ memories.id (CASCADE)
  │ updated_at   TS NN       │             │ version      INT NN      │
  └─────────────────────────┘             │ previous_content TEXT    │
                                           │ new_content  TEXT NN     │
                                           │ previous_type TEXT       │
  ┌─────────────────────────┐             │ new_type     TEXT NN     │
  │   session_summaries      │  002       │ changed_by   TEXT NN     │
  ├─────────────────────────┤             │  ∊{agent,user,system}    │
  │ id           TEXT PK     │             │ change_reason TEXT       │
  │ session_key  TEXT NN UQ  │             │ created_at   TS NN       │
  │ summary      TEXT NN     │             └─────────────────────────┘
  │ key_decisions JSON CHK   │
  │ message_count INT NN DF 0│
  │ embedding_model TEXT     │             ┌─────────────────────────┐
  │ processed_at TS          │             │ session_summaries_vec    │
  │ created_at   TS NN       │             │ (vec0 virtual)           │
  │ updated_at   TS NN       │             ├─────────────────────────┤
  └─────────────────────────┘             │ id        TEXT PK        │
                                           │ embedding FLOAT[N]       │
  ┌─────────────────────────┐             └─────────────────────────┘
  │ session_summaries_fts    │
  │ (FTS5 virtual)           │
  ├─────────────────────────┤
  │ id      TEXT UNINDEXED   │
  │ summary TEXT             │
  │ key_decisions TEXT       │
  └─────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
  ENTITY GRAPH LAYER (migration 000003)
═══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────┐             ┌─────────────────────────┐
  │      entities            │  003       │   entities_vec           │
  ├─────────────────────────┤             │   (vec0 virtual)         │
  │ id           TEXT PK     │             ├─────────────────────────┤
  │ type         TEXT NN CHK │             │ id        TEXT PK        │
  │  ∊{person,project,task, │             │ embedding FLOAT[N]       │
  │    decision,meeting,     │             └─────────────────────────┘
  │    document,event}       │
  │ name         TEXT NN     │
  │ metadata     JSON CHK    │
  │ embedding_model TEXT     │
  │ processed_at TS          │
  │ created_at   TS NN       │
  │ updated_at   TS NN       │
  └──┬───────────┬──────────┘
     │           │
     │           │ FK: entity_mentions.entity_id
     │           ▼
     │    ┌─────────────────────────┐
     │    │   entity_mentions        │  003
     │    ├─────────────────────────┤
     │    │ id           TEXT PK     │
     │    │ entity_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
     │    │ source_type  TEXT NN     │
     │    │ source_id    TEXT NN     │
     │    │ created_at   TS NN       │
     │    └─────────────────────────┘
     │
     │ FK: relationships.source_id / target_id
     ▼
  ┌─────────────────────────┐
  │    relationships         │  003
  ├─────────────────────────┤
  │ id           TEXT PK     │
  │ source_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
  │ target_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
  │ type         TEXT NN     │
  │ weight       REAL NN CHK │
  │  BETWEEN 0.0 AND 1.0    │
  │ context      TEXT        │
  │ created_at   TS NN       │
  │ updated_at   TS NN       │
  ├─────────────────────────┤
  │ UQ(source_id,target_id, │
  │    type)                 │
  └─────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
  OBSERVABILITY LAYER (migration 000004)
═══════════════════════════════════════════════════════════════════════════════

  ┌─────────────────────────┐
  │      audit_log           │  004
  ├─────────────────────────┤
  │ id           TEXT PK     │
  │ timestamp    TS NN       │
  │ command      TEXT NN     │  Mediator command name
  │ actor        TEXT        │  agent|user|system
  │ capability   TEXT        │  Which tool/feature
  │ context      JSON CHK    │  Request params (sensitive redacted)
  │ result       TEXT        │  success|error|...
  │ duration_ms  INT         │
  │ created_at   TS NN       │
  └─────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
  RELATIONSHIP SUMMARY
═══════════════════════════════════════════════════════════════════════════════

  agents           1──*        agent_workspace_mounts     (host dir mappings)
  agents           1──*        agent_channels             (enabled channels)
  agents           1──*        conversations              (agent handles convos)
  conversations    1──*        conversation_messages      (ordered messages, CASCADE)
  conversations    *──1        session_summaries          (optional summary FK)
  content_chunks   1──vec──1   content_chunks_vec         (embedding storage)
  content_chunks   1──fts──1   content_chunks_fts         (full-text index)
  content_chunks   *──self──1  content_chunks.parent_id   (multi-chunk docs)
  memories         1──vec──1   memories_vec               (embedding storage)
  memories         1──fts──1   memories_fts               (full-text index)
  memories         1──*        memory_versions            (immutable change log, CASCADE)
  session_summaries 1──vec──1  session_summaries_vec      (embedding storage)
  session_summaries 1──fts──1  session_summaries_fts      (full-text index)
  entities         1──vec──1   entities_vec               (embedding storage)
  entities         1──*        entity_mentions            (where entity appears, CASCADE)
  entities         *──rel──*   entities                   (via relationships, CASCADE)
  embedding_configs            (standalone reference, seeded)
  settings                     (standalone key-value config)
  audit_log                    (standalone append-only log)
```

### Enterprise Standards Checklist

| Standard | Status | Notes |
|----------|--------|-------|
| **ULID primary keys** | ✅ All tables | Time-sortable, globally unique, no auto-increment |
| **Foreign key constraints** | ✅ All FKs | ON DELETE CASCADE where parent owns children |
| **CHECK constraints** | ✅ Enums, ranges | role, type, sensitivity, weight, importance |
| **NOT NULL enforcement** | ✅ All required fields | Only truly optional fields allow NULL |
| **Unique constraints** | ✅ Business keys | agent name, session_key, composite keys |
| **Indexes** | ✅ All FK columns + query patterns | Partial indexes where applicable (WHERE clauses) |
| **Timestamps** | ✅ created_at + updated_at | Auto-update triggers on all mutable tables |
| **Soft deletes** | ✅ Where needed | memories.deleted_at; conversations use hard delete (CASCADE) |
| **Append-only audit** | ✅ memory_versions, audit_log | Immutable history for compliance |
| **JSON validation** | ✅ All JSON columns | json_valid() CHECK constraints |
| **Encryption at rest** | ✅ API keys | Fernet encryption with enc: prefix in settings table |
| **Cascade deletes** | ✅ Parent-child | Deleting agent cascades to mounts/channels; conversation cascades to messages |
| **Migration versioning** | ✅ golang-migrate | Sequential numbered migrations with up/down, schema_migrations tracking |
| **Graceful degradation** | ✅ vec0 tables | Created at runtime, not in migrations; system works without sqlite-vec |
| **Trigger-based consistency** | ✅ FTS sync, updated_at | Auto-maintained indexes and timestamps |
| **Configurable dimensions** | ✅ Runtime vec0 | User changes dimension → vec0 tables recreated |

### Gap Analysis

| Issue | Severity | Resolution |
|-------|----------|------------|
| `conversations.summary_id` FK to session_summaries — summaries may not exist yet | Low | FK is nullable, populated asynchronously after conversation ends |
| `conversation_messages` has no max size limit | Medium | Add `agent.max_context_messages` setting to cap history loaded per turn (e.g., last 100 messages). Old messages still stored, just not sent to LLM. |
| No `conversation_messages_fts` for searching within conversations | Medium | Not needed for MVP — conversations are indexed as content_chunks. Add later if direct message search is needed. |
| No TTL/archival on conversations | Low | Add `conversations.archived_at` in a future migration for conversation archival |
| `audit_log` has no retention policy | Low | Add a cleanup cron that prunes entries older than configurable retention (default 90 days) |

---

## 3. Data Model — New Tables

### 3.1 Migration 000006: Conversations

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,                     -- ULID
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'webchat',
    title TEXT,                              -- Auto-generated or user-set
    is_active INTEGER NOT NULL DEFAULT 1,    -- is this the current conversation?
    message_count INTEGER NOT NULL DEFAULT 0,
    summary_id TEXT REFERENCES session_summaries(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_conv_agent ON conversations(agent_id);
CREATE INDEX idx_conv_channel ON conversations(channel);
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
    tool_call_id TEXT,                       -- for role='tool', which call this responds to
    token_count INTEGER,                     -- approximate token count
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_cm_conv ON conversation_messages(conversation_id, created_at);
CREATE INDEX idx_cm_role ON conversation_messages(conversation_id, role);
```

### 3.2 Auto-Title

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
