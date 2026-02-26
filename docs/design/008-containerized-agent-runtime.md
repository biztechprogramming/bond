# Design Doc 008: Containerized Agent Runtime

**Status:** Draft — awaiting review  
**Depends on:** 003 (Agent Tools & Sandbox), 005 (Message Queue & Interrupts)  
**Architecture refs:** [03 — Agent Runtime](../architecture/03-agent-runtime.html), [08 — Sandbox System](../architecture/08-sandbox.html)

---

## 1. The Problem

The current architecture runs the agent loop on the **host**, then reaches into the container via `docker exec` for file and code operations. This creates a mess:

- **Path translation hell** — container paths (`/workspace/project/file.txt`) must be mapped back to host paths (`/mnt/c/dev/project/file.txt`). Fragile, especially on WSL.
- **Two execution contexts** — some tools run on host, some via `docker exec`. Every tool must know which context it's in.
- **Shell escaping nightmares** — piping content through `docker exec sh -c` means escaping quotes, heredocs, special chars. Broke writes in practice.
- **No native tool execution** — the agent can't just run `git status` or `npm test`. It has to go through a `code_execute` tool that shells into the container.
- **Workspace state is remote** — the agent can't `os.listdir()` or `pathlib.Path()` because the files aren't local to its process.

Agent Zero solved this years ago: **put the agent inside the container**.

---

## 2. Proposed Architecture

The agent loop runs **inside the sandbox container** as a lightweight worker. The host runs only the UI layer (frontend + gateway) and manages shared state. Communication is minimal — the gateway sends turns in and receives SSE events out.

### 2.1 Current vs. Proposed

```
CURRENT (host-side agent):

  ┌─────────────────────────────────────────────────────────┐
  │  HOST                                                    │
  │                                                          │
  │  ┌──────────┐    ┌──────────┐    ┌──────────────────┐   │
  │  │ Frontend │◄──►│ Gateway  │◄──►│ Backend          │   │
  │  │ :18788   │    │ :18789   │    │ :18790           │   │
  │  └──────────┘    └──────────┘    │                  │   │
  │                                   │  Agent Loop      │   │
  │                                   │  ┌────────────┐  │   │
  │                                   │  │ LLM Call   │  │   │
  │                                   │  │ Tool Exec ─┼──┼───┼──► docker exec
  │                                   │  │ file_read ─┼──┼───┼──► docker exec
  │                                   │  │ file_write─┼──┼───┼──► docker exec
  │                                   │  └────────────┘  │   │
  │                                   └──────────────────┘   │
  │                                                          │
  │  ┌──────────────────────────────────────────────────┐    │
  │  │  CONTAINER                                        │    │
  │  │  /workspace/project ──bind──► /mnt/c/dev/project  │    │
  │  │                                                    │    │
  │  │  (just sits there running `sleep infinity`)        │    │
  │  └──────────────────────────────────────────────────┘    │
  └─────────────────────────────────────────────────────────┘


PROPOSED (container-side agent):

  ┌─────────────────────────────────────────────────────────┐
  │  HOST                                                    │
  │                                                          │
  │  ┌──────────┐    ┌──────────┐    ┌──────────────────┐   │
  │  │ Frontend │◄──►│ Gateway  │◄──►│ Backend          │   │
  │  │ :18788   │    │ :18789   │    │ :18790           │   │
  │  └──────────┘    └──────────┘    │ Settings, config │   │
  │                                   │ Shared memory DB │   │
  │                       ┌───────── │ Message persist  │   │
  │                       │ SSE      └──────────────────┘   │
  │                       │                                  │
  │  ┌────────────────────┼─────────────────────────────┐   │
  │  │  CONTAINER         │                              │   │
  │  │                    ▼                              │   │
  │  │  ┌────────────────────────────────────────────┐   │   │
  │  │  │  Agent Worker (:18791)                     │   │   │
  │  │  │                                            │   │   │
  │  │  │  Agent Loop + LLM calls (direct to API)    │   │   │
  │  │  │  ├── file_read  → open("/workspace/...")   │   │   │
  │  │  │  ├── file_write → open("/workspace/...")   │   │   │
  │  │  │  ├── code_exec  → subprocess.run(...)      │   │   │
  │  │  │  ├── shell      → subprocess.run(...)      │   │   │
  │  │  │  ├── search_memory → local agent DB        │   │   │
  │  │  │  ├── memory_save   → local agent DB        │   │   │
  │  │  │  └── respond    → SSE event back to host   │   │   │
  │  │  │                                            │   │   │
  │  │  │  Agent DB (/data/agent.db)                 │   │   │
  │  │  │  Bond Agent Library (/bond, read-only)     │   │   │
  │  │  │  Shared Memory Snapshot (/data/shared, ro) │   │   │
  │  │  └────────────────────────────────────────────┘   │   │
  │  │                                                    │   │
  │  │  /workspace/project ──bind──► /mnt/c/dev/project   │   │
  │  └────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────┘
```

### 2.2 Communication Model

The host and container communicate through **one channel**: the SSE stream from worker to gateway. That's it.

```
Gateway ──► Container Worker
  │              │
  │  POST /turn  │  (everything happens locally inside container)
  │  POST /intr  │  (interrupt with new messages)
  │  GET /health │
  │              │
  │  ◄── SSE ── │  Events:
  │              │    status    — "thinking", "tool_use"
  │              │    chunk     — response text
  │              │    tool_call — tool name + result (for UI display)
  │              │    memory    — new memories to sync to shared DB
  │              │    done      — turn complete
  │              │
  └──────────────┘
```

**No callbacks.** The agent never calls back to the host during a turn. Everything it needs is local.

### 2.3 What Lives Where

| Component | Location | Why |
|-----------|----------|-----|
| Frontend + Gateway | Host | Serves UI, manages WebSocket connections |
| Backend (settings, config) | Host | Agent config, API key management, shared DB |
| Agent loop | Container | Native file/code access, isolated execution |
| Agent's own memories | Container (`/data/agent.db`) | Fast local access, no network round-trip |
| Shared memories | Host (`bond.db`) + snapshot in container | All agents need common context |
| LLM API calls | Container (direct) | No proxy needed, agent has API keys |
| File I/O | Container (native) | `/workspace/...` via bind mounts |
| Code/shell execution | Container (native) | `subprocess.run()` |
| Conversation messages | Host (gateway persists from SSE stream) | UI needs to display them |
| Web search / web read | Container (native) | Agent makes HTTP requests directly |

---

## 3. Memory Architecture

### 3.1 Two-Tier Memory

```
┌─────────────────────────────────────────────────────────────┐
│  SHARED MEMORY (host — bond.db)                              │
│                                                              │
│  Who you are, preferences, people, projects, recurring       │
│  context that ALL agents need.                               │
│                                                              │
│  Written by: gateway (from agent SSE memory events)          │
│  Read by: agents (snapshot at startup / periodic sync)       │
│                                                              │
│  Examples:                                                   │
│  - "User prefers dark mode and concise responses"            │
│  - "Andrew works on Bond and EcoInspector projects"          │
│  - Entity: Andrew → works_at → BizTech                      │
│  - "Use uv for Python, pnpm for Node"                       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                     snapshot at startup
                     + periodic sync
                               │
┌──────────────────────────────┼──────────────────────────────┐
│  AGENT MEMORY (container — /data/agent.db)                   │
│                                                              │
│  What THIS agent learned during its tasks. Working context,  │
│  session-specific knowledge, tool results.                   │
│                                                              │
│  Written by: agent (locally, no network)                     │
│  Read by: this agent only                                    │
│                                                              │
│  Examples:                                                   │
│  - "ecoinspector-portal uses Next.js 15 with app router"     │
│  - "The auth module is in src/lib/auth.ts"                   │
│  - "Last test run had 3 failures in api.test.ts"             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Memory Sync Flow

Agents produce memories locally. Some get promoted to shared.

```
Agent Turn (inside container)
    │
    │  Agent saves memory locally
    │  memory_save("User prefers TypeScript over JavaScript")
    │     → writes to /data/agent.db (instant, local)
    │     → if memory is "promotable" (user pref, fact, entity):
    │         emit SSE event: { event: "memory", data: { ... } }
    │
    ▼
Gateway (host)
    │
    │  Receives SSE memory event
    │  Writes to shared bond.db
    │
    ▼
Shared Memory (bond.db)
    │
    │  Available to all agents on next startup/sync
    │
    ▼
Other Agent Containers
    │
    │  On startup: mount /data/shared/ with snapshot
    │  Periodic: gateway pushes updates (optional)
    │
    ▼
  Agent has shared context without any callback
```

### 3.3 What Gets Promoted to Shared

Not everything an agent learns should be shared. Promotion criteria:

| Memory Type | Promote? | Example |
|-------------|----------|---------|
| `preference` | ✅ Always | "User prefers concise responses" |
| `fact` (about user) | ✅ Always | "Andrew's timezone is EST" |
| `fact` (about project) | ✅ If general | "Bond uses SQLite + FastAPI" |
| `fact` (about code) | ❌ Agent-local | "Line 42 of auth.ts has a race condition" |
| `solution` | ⚠️ Maybe | "Use `--legacy-peer-deps` for npm conflicts" — useful if general |
| `instruction` | ✅ If from user | "Always run tests before committing" |
| `entity` | ✅ Always | People, projects, relationships |
| Working context | ❌ Never | "Current file open: src/main.py" |

The agent marks memories as `promote: true/false` when saving. The SSE stream only includes promotable memories.

### 3.4 Shared Memory Snapshot

On container startup, shared memories are available as a read-only SQLite file:

```
/data/
  agent.db          ← agent's own DB (read-write, persisted volume)
  shared/
    shared.db       ← snapshot of shared memories (read-only mount)
```

The agent's search_memory tool queries **both** databases:
1. Local agent.db — agent-specific context
2. shared.db — cross-agent shared knowledge

Results are merged by relevance (same RRF merge we already built).

### 3.5 Sync Schedule

| Trigger | Direction | What |
|---------|-----------|------|
| Container startup | Host → Container | Full shared memory snapshot |
| Agent emits `memory` SSE event | Container → Host | New promotable memory |
| Scheduled job (every 15 min) | Host → Container | Incremental shared updates |
| Agent emits `entity` SSE event | Container → Host | New entity / relationship |
| Offline consolidation (hourly) | Host only | Deduplicate, merge, summarize shared memories |

---

## 4. Container Mounts

```
Container filesystem:

/bond/                          ← Bond agent library (read-only mount)
  backend/
    app/
      agent/
        loop.py                 ← The agent loop code
        tools/                  ← Tool handlers (native)
        llm.py                  ← LiteLLM wrapper
      worker.py                 ← FastAPI worker

/workspace/                     ← User workspace mounts (read-write)
  ecoinspector-portal/          ← bind: /mnt/c/dev/ecoinspector/ecoinspector-portal
  another-project/              ← bind: ~/projects/another-project

/data/                          ← Persistent agent data (Docker volume)
  agent.db                      ← Agent's own SQLite database
  shared/
    shared.db                   ← Read-only snapshot of shared memories

/tmp/.ssh/                      ← SSH keys (read-only mount)

/config/
  agent.json                    ← Agent config (model, tools, system prompt, API keys)
```

### Mount Strategy

| Mount | Source (host) | Target (container) | Mode |
|-------|---------------|---------------------|------|
| Bond library | `~/bond/backend` | `/bond/backend` | `ro` |
| Workspace(s) | Per agent config | `/workspace/{name}` | `rw` |
| Agent data | Docker volume `bond-agent-{id}` | `/data` | `rw` |
| Shared memory | `~/bond/data/shared/` | `/data/shared` | `ro` |
| SSH keys | `~/.ssh` | `/tmp/.ssh` | `ro` |
| Agent config | Generated at startup | `/config/agent.json` | `ro` |

The Bond library mount means we don't need to rebuild the container image when agent code changes — just restart the worker.

---

## 5. Agent Worker

A lightweight FastAPI app that runs inside the container. Fully self-contained — no callbacks to the host.

### 5.1 API

```
POST /turn
  Body: { message, history, conversation_id }
  Response: SSE stream of events
    - { event: "status", data: "thinking" }
    - { event: "chunk",  data: "response text" }
    - { event: "tool_call", data: { name, arguments, result } }
    - { event: "memory", data: { type, content, promote, entities } }
    - { event: "entity", data: { name, type, relationships } }
    - { event: "done",   data: { response, tool_calls_made } }

POST /interrupt
  Body: { new_messages: [...] }
  Response: { acknowledged: true }

GET /health
  Response: { status: "ok", agent_id, uptime }
```

### 5.2 Startup

```bash
# Container entrypoint
python -m bond.agent.worker \
  --port 18791 \
  --config /config/agent.json \
  --data-dir /data
```

On startup the worker:
1. Reads `/config/agent.json` for model, tools, system prompt, API keys
2. Opens `/data/agent.db` (creates if first run, runs migrations)
3. Attaches `/data/shared/shared.db` as read-only for cross-agent search
4. Starts FastAPI on port 18791
5. Ready to receive turns

### 5.3 Tool Execution (Native)

Inside the container, tools are just local operations:

```python
# file_read — just open the file
async def handle_file_read(arguments, context):
    path = arguments["path"]  # /workspace/ecoinspector-portal/Makefile
    content = Path(path).read_text()
    return {"content": content, "path": path, "size": len(content)}

# file_write — just write the file
async def handle_file_write(arguments, context):
    path = Path(arguments["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(arguments["content"])
    return {"status": "written", "path": str(path)}

# code_execute — just run it
async def handle_code_execute(arguments, context):
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", arguments["code"],
        stdout=PIPE, stderr=PIPE
    )
    stdout, stderr = await proc.communicate()
    return {"stdout": stdout.decode(), "stderr": stderr.decode(), "exit_code": proc.returncode}

# shell — just run it
async def handle_shell(arguments, context):
    proc = await asyncio.create_subprocess_shell(
        arguments["command"],
        stdout=PIPE, stderr=PIPE, cwd="/workspace"
    )
    stdout, stderr = await proc.communicate()
    return {"stdout": stdout.decode(), "stderr": stderr.decode(), "exit_code": proc.returncode}

# search_memory — query both local + shared DBs
async def handle_search_memory(arguments, context):
    local_results = await searcher.search(context["agent_db"], arguments["query"])
    shared_results = await searcher.search(context["shared_db"], arguments["query"])
    merged = rrf_merge(local_results, shared_results, k=60)
    return {"results": merged[:arguments.get("limit", 10)]}

# memory_save — write to local DB + optionally emit for promotion
async def handle_memory_save(arguments, context):
    memory = await memory_repo.create(context["agent_db"], arguments)
    result = {"id": memory.id, "status": "saved"}

    # Check if this should be promoted to shared memory
    if should_promote(arguments):
        result["_promote"] = {
            "type": arguments["type"],
            "content": arguments["content"],
            "entities": arguments.get("entities", []),
        }
    return result
```

No path translation. No docker exec. No shell escaping. No callbacks.

### 5.4 Memory Promotion in SSE Stream

The agent loop checks tool results for `_promote` flags and emits SSE events:

```python
# Inside the agent loop, after executing a tool:
result = await registry.execute(tool_name, tool_args, tool_context)

# If the tool produced a promotable memory, emit it
if "_promote" in result:
    yield sse_event("memory", result["_promote"])
    del result["_promote"]  # Don't send back to LLM
```

The gateway receives these events alongside the normal response stream and writes them to the shared DB on the host. Zero extra communication — it's just another event type in the existing SSE stream.

---

## 6. Gateway Changes

The gateway's role expands slightly. It already consumes SSE from the backend — now it consumes SSE from the container worker (or the backend proxies it).

### 6.1 Option A: Gateway talks directly to container

```
Frontend ◄──WebSocket──► Gateway ◄──SSE──► Container Worker (:18791)
                            │
                            └──► Host DB (persist messages + shared memories)
```

Simpler. Gateway needs to know the container's port. Backend tells it during container startup.

### 6.2 Option B: Backend proxies SSE

```
Frontend ◄──WebSocket──► Gateway ◄──SSE──► Backend ◄──SSE──► Container Worker
                                              │
                                              └──► Host DB
```

More indirection but keeps the gateway simple. Backend handles container lifecycle + SSE proxying.

### 6.3 Recommendation: Option A for simplicity

The gateway already handles WebSocket ↔ SSE translation. Having it talk directly to the container worker removes a hop. The backend's role becomes:

- Container lifecycle (start/stop/health)
- Settings API (agents, API keys, models)
- Shared memory writes (gateway calls a simple endpoint)
- Shared memory snapshot generation

### 6.4 Gateway SSE Event Handling

```typescript
// Gateway processes SSE events from container worker
function handleWorkerEvent(event: SSEEvent) {
  switch (event.type) {
    case "status":
    case "chunk":
    case "tool_call":
      // Forward to frontend via WebSocket (existing behavior)
      ws.send(JSON.stringify(event));
      break;

    case "memory":
      // Write to shared DB on host
      await backend.post("/api/v1/shared-memories", event.data);
      break;

    case "entity":
      // Write to shared entity graph on host
      await backend.post("/api/v1/shared-entities", event.data);
      break;

    case "done":
      // Persist conversation messages to host DB
      await backend.post("/api/v1/conversations/{id}/messages", {
        role: "assistant",
        content: event.data.response,
        tool_calls: event.data.tool_calls_made,
      });
      ws.send(JSON.stringify(event));
      break;
  }
}
```

---

## 7. Sequence Diagrams

### 7.1 Normal Turn (Zero Callbacks)

```
User          Frontend       Gateway        Container Worker
 │               │              │               │
 │  "Fix bug"    │              │               │
 │──────────────►│              │               │
 │               │──WebSocket──►│               │
 │               │              │──POST /turn───►│
 │               │              │               │
 │               │              │               │── LLM call (direct to Anthropic)
 │               │              │               │
 │               │              │◄──SSE: tool_call (file_read)
 │               │◄──WebSocket──│               │── open("/workspace/project/bug.py")
 │               │              │               │   (native file I/O — instant)
 │               │              │               │
 │               │              │               │── LLM call
 │               │              │               │
 │               │              │◄──SSE: tool_call (file_write)
 │               │◄──WebSocket──│               │── open("/workspace/project/bug.py", "w")
 │               │              │               │   (bind mount syncs to host automatically)
 │               │              │               │
 │               │              │◄──SSE: memory (promote)
 │               │              │──► backend.post("/shared-memories")
 │               │              │               │
 │               │              │               │── LLM call
 │               │              │               │
 │               │              │◄──SSE: chunk "I fixed the bug..."
 │               │◄──WebSocket──│               │
 │               │              │◄──SSE: done
 │◄──────────────│              │──► persist messages to host DB
 │               │              │               │
```

Note: **zero calls from container back to host** during the entire turn.

### 7.2 Memory Sync Lifecycle

```
                    STARTUP
                       │
Agent Container        │         Host
     │                 │           │
     │  Mount /data/shared/shared.db (read-only snapshot)
     │◄────────────────┼───────────│
     │                 │           │
     │  Agent has full shared context
     │  No network call needed     │
     │                 │           │

                   DURING TURN
                       │
     │  Agent saves memory locally │
     │  → /data/agent.db           │
     │                 │           │
     │  If promotable:             │
     │  ──SSE: memory─────────────►│
     │                 │           │── gateway writes to bond.db
     │                 │           │

                PERIODIC SYNC (every 15 min)
                       │
     │                 │           │
     │                 │           │── backend regenerates shared.db snapshot
     │  (new snapshot mounted on   │
     │   next container restart    │
     │   or hot-reloaded)          │
     │                 │           │
```

### 7.3 Container Lifecycle

```
Backend                         Docker
  │                               │
  │  First turn for agent         │
  │                               │
  │  1. Generate /config/agent.json
  │  2. Export shared.db snapshot  │
  │  3. docker run                │
  │     -v ~/bond/backend:/bond/backend:ro
  │     -v /mnt/c/dev/project:/workspace/project
  │     -v bond-agent-{id}:/data
  │     -v ~/bond/data/shared:/data/shared:ro
  │     -v ~/.ssh:/tmp/.ssh:ro
  │     -v /tmp/agent-config:/config:ro
  │     -p 18791
  │     bond-sandbox-image        │
  │     python -m bond.agent.worker
  │──────────────────────────────►│
  │                               │──start worker
  │  4. Wait for health check     │
  │◄──GET /health → 200──────────│
  │                               │
  │  5. Tell gateway the port     │
  │──► gateway.register(agent_id, port)
  │                               │
  │  ... (container stays alive)  │
  │                               │
  │  Idle timeout (1hr)           │
  │──docker stop─────────────────►│
  │  (volume persists — agent.db  │
  │   survives restart)           │
```

---

## 8. Host Mode (No Sandbox)

When `sandbox_image` is null, the agent loop runs on the host exactly as it does today. This is the "trusted" mode for the main session.

```
sandbox_image = null   →  Agent loop runs in backend process (current behavior)
sandbox_image = "..."  →  Agent loop runs in container (new behavior)
```

Backward compatible. Existing host-mode code stays untouched.

---

## 9. Database Schema

### 9.1 Host DB (bond.db) — Shared State

```sql
-- Existing tables stay as-is:
-- conversations, conversation_messages, agents, agent_workspace_mounts,
-- agent_channels, settings, audit_log

-- New/modified tables for shared memory:

CREATE TABLE shared_memories (
    id TEXT PRIMARY KEY,                  -- ULID
    type TEXT NOT NULL,                   -- fact, preference, instruction, entity
    content TEXT NOT NULL,
    source_agent_id TEXT,                 -- which agent produced this
    source_memory_id TEXT,                -- original memory ID in agent's local DB
    confidence REAL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT,                   -- for versioning
    UNIQUE(source_agent_id, source_memory_id)  -- prevent duplicate promotions
);

CREATE TABLE shared_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,            -- person, project, org, tool, concept
    attributes TEXT,                       -- JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE shared_entity_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES shared_entities(id),
    target_id TEXT NOT NULL REFERENCES shared_entities(id),
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    source_agent_id TEXT,
    created_at TEXT NOT NULL
);

-- FTS for shared memories
CREATE VIRTUAL TABLE shared_memories_fts USING fts5(
    content,
    content_rowid='rowid'
);
```

### 9.2 Agent DB (/data/agent.db) — Per-Agent State

```sql
-- Agent's own memories (same schema as current memories table)
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    promoted INTEGER DEFAULT 0,           -- 1 = sent to shared DB
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT
);

-- Agent's own entities (lightweight, for working context)
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    attributes TEXT,
    created_at TEXT NOT NULL
);

-- Content chunks for agent-local RAG
CREATE TABLE content_chunks (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);

-- FTS + vec0 tables for local search
CREATE VIRTUAL TABLE memories_fts USING fts5(content);
-- vec0 tables created at runtime based on embedding config
```

### 9.3 Shared Memory Snapshot

The backend periodically exports shared state to a read-only SQLite file:

```python
async def export_shared_snapshot():
    """Export shared memories + entities to a snapshot DB for containers."""
    snapshot_path = Path("~/bond/data/shared/shared.db")

    async with aiosqlite.connect(snapshot_path) as snap:
        # Copy shared_memories
        await snap.execute("CREATE TABLE IF NOT EXISTS memories (...)")
        rows = await host_db.execute("SELECT * FROM shared_memories WHERE superseded_by IS NULL")
        await snap.executemany("INSERT INTO memories VALUES (...)", rows)

        # Copy shared_entities + edges
        await snap.execute("CREATE TABLE IF NOT EXISTS entities (...)")
        await snap.execute("CREATE TABLE IF NOT EXISTS entity_edges (...)")
        # ... copy data ...

        # Build FTS index
        await snap.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")
        # ... populate ...

        await snap.commit()
```

---

## 10. Security Considerations

### 10.1 API Key Handling

API keys are decrypted on the host and written to `/config/agent.json`. The config is mounted read-only. The worker reads keys into memory on startup.

Alternative: Pass keys via environment variables at `docker run`. Avoids writing to disk entirely.

```bash
docker run ... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e VOYAGE_API_KEY=pa-... \
  bond-sandbox-image python -m bond.agent.worker
```

### 10.2 No Callback Authentication Needed

Since the agent never calls back to the host, there's no callback API to secure. The gateway calls into the container (POST /turn), and the container streams events back on the same connection. No inbound ports on the host needed.

### 10.3 Filesystem Isolation

The container only sees:
- `/workspace/...` — explicitly mounted directories (configured per agent)
- `/bond/backend` — agent library (read-only)
- `/data/agent.db` — agent's own database (persisted volume)
- `/data/shared/` — shared memory snapshot (read-only)
- `/config/` — agent config (read-only)
- `/tmp/.ssh` — SSH keys (read-only)

No access to host filesystem outside these mounts.

### 10.4 Network

The container needs outbound access for:
- LLM API calls (Anthropic, OpenAI, etc.)
- Web search / web read tools
- Git operations (clone, push, pull)

It does **not** need access to `host.docker.internal` — no callbacks.

---

## 11. Implementation Plan

### Phase 1: Container Worker (MVP)

| ID | Story | Effort |
|----|-------|--------|
| C1 | Create `backend/app/worker.py` — FastAPI app with `/turn` SSE, `/health`, `/interrupt` | M |
| C2 | Native tool handlers (file, code, shell — no docker exec, no path translation) | S |
| C3 | Local memory store in agent DB (search + save, both DBs) | M |
| C4 | Update `SandboxManager`: run worker, mount volumes, port mapping, health wait | M |
| C5 | Gateway: talk directly to container worker, handle memory/entity SSE events | M |
| C6 | Backend: shared memory write endpoints (called by gateway) | S |

### Phase 2: Shared Memory

| ID | Story | Effort |
|----|-------|--------|
| C7 | Migration: `shared_memories` + `shared_entities` + FTS tables on host DB | S |
| C8 | Shared memory snapshot export (backend scheduled task) | M |
| C9 | Agent worker: attach + query shared.db alongside agent.db | S |
| C10 | Memory promotion logic: which memories get promoted, SSE events | S |

### Phase 3: Polish

| ID | Story | Effort |
|----|-------|--------|
| C11 | Graceful shutdown — worker finishes current turn before container stops | S |
| C12 | Periodic shared memory sync (incremental updates without restart) | M |
| C13 | Offline memory consolidation job (deduplicate, merge, summarize) | M |
| C14 | Logging — worker logs forwarded to host (docker logs) | S |
| C15 | Error recovery — backend detects dead worker, restarts container | S |

---

## 12. Migration Path

1. **No breaking changes** — `sandbox_image = null` keeps current host-mode behavior
2. **Incremental rollout** — set `sandbox_image` on an agent to move it to containerized mode
3. **Easy rollback** — set `sandbox_image = null` to go back to host mode
4. **Agent data persists** — Docker volumes survive container restarts

```python
# Backend dispatch logic
if agent["sandbox_image"]:
    # Containerized: gateway talks to container worker
    container = await sandbox_manager.ensure_running(agent)
    return {"worker_url": container.worker_url}
else:
    # Host mode: run loop in-process (existing code)
    return agent_turn(message, history, db=db, agent_id=agent["id"])
```

---

## 13. What This Eliminates

- ❌ `_translate_container_to_host()` — gone
- ❌ `_translate_host_to_container()` — gone  
- ❌ `docker exec` for file operations — gone
- ❌ Shell escaping for content piping — gone
- ❌ `workspace_dirs` allowlist (container-mode) — the mount IS the allowlist
- ❌ Path confusion between host and container — gone
- ❌ `sleep infinity` as container entrypoint — replaced by worker process
- ❌ Host-to-container callbacks during turns — gone
- ❌ Backend as SSE proxy — gateway talks direct to container

---

## 14. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Agent is fully self-contained in container | No callbacks = no latency, no auth complexity, no failure modes from network issues |
| Two-tier memory (agent-local + shared) | Agents need their own working context AND common knowledge about the user |
| SSE stream carries memory events | Reuses existing communication channel — no new protocols or endpoints |
| Gateway talks directly to container | Removes a proxy hop. Backend stays focused on config + shared state. |
| Shared memory via read-only snapshot | No concurrent DB writes. Agents read a point-in-time copy. Simple, safe. |
| Promotion is agent-decided | The agent knows which memories are generalizable vs. task-specific |
| Docker volumes for agent DB | Data persists across container restarts. No export/import needed. |
| Bond library mounted read-only | Code changes don't require image rebuild. Just restart the worker. |
| Host mode preserved | Backward compatible. Trusted sessions skip Docker overhead. |
| Offline consolidation job | Dedup, merge, summarize shared memories without blocking agent turns |

---

## 15. Resolved Questions

1. **Port allocation** — ✅ Allocate from a fixed range (18791–18890). Backend checks port availability before assigning. Predictable and simple.

2. **Shared memory updates** — ✅ No push mechanism needed. The shared.db snapshot is a file on the host that the backend periodically re-exports. The container mounts the directory (`/data/shared/`), and the worker periodically detaches and re-attaches the shared DB (every 30 seconds). New shared memories propagate within 30 seconds of the snapshot being updated.

```python
# Worker: periodic shared DB re-attach
async def _refresh_shared_db(self):
    """Re-attach shared.db to pick up host-side updates."""
    while True:
        await asyncio.sleep(30)
        try:
            await self._agent_db.execute("DETACH DATABASE shared")
            await self._agent_db.execute(
                "ATTACH DATABASE '/data/shared/shared.db' AS shared"
            )
            logger.debug("Re-attached shared.db")
        except Exception as e:
            logger.warning("Failed to re-attach shared.db: %s", e)
```

## 16. Open Questions

1. **Embedding model in container** — If agents use local embeddings (voyage-4-nano), the model needs to be in the container. Mount it? Include in image? Download on first run?

2. **Container image base** — The sandbox image needs Python + uvicorn + litellm + httpx + aiosqlite. Build a `bond-agent-base` image that all sandbox images inherit from?

3. **Multiple workspaces, one container** — Current model is one container per agent. If an agent has 5 workspace mounts, they're all in one container. Is that always right?
