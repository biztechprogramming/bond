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

The agent loop runs **inside the sandbox container** as a lightweight HTTP worker. Bond's backend starts it, sends it messages, and receives tool results + responses back.

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
  │  └──────────┘    └──────────┘    │                  │   │
  │                                   │  Turn Manager    │   │
  │                                   │  (dispatch only) │   │
  │                                   └───────┬──────────┘   │
  │                                           │ HTTP         │
  │  ┌────────────────────────────────────────┼──────────┐   │
  │  │  CONTAINER                             │          │   │
  │  │                                        ▼          │   │
  │  │  ┌────────────────────────────────────────────┐   │   │
  │  │  │  Agent Worker (:18791)                     │   │   │
  │  │  │                                            │   │   │
  │  │  │  Agent Loop                                │   │   │
  │  │  │  ├── LLM calls (direct to API)             │   │   │
  │  │  │  ├── file_read  → open("/workspace/...")   │   │   │
  │  │  │  ├── file_write → open("/workspace/...")   │   │   │
  │  │  │  ├── code_exec  → subprocess.run(...)      │   │   │
  │  │  │  ├── shell      → subprocess.run(...)      │   │   │
  │  │  │  └── respond    → POST back to backend     │   │   │
  │  │  │                                            │   │   │
  │  │  │  Bond Agent Library (mounted read-only)    │   │   │
  │  │  └────────────────────────────────────────────┘   │   │
  │  │                                                    │   │
  │  │  /workspace/project ──bind──► /mnt/c/dev/project   │   │
  │  │  /bond (ro)         ──bind──► ~/bond/backend       │   │
  │  └────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────┘
```

### 2.2 What Changes

| Aspect | Current | Proposed |
|--------|---------|----------|
| Agent loop runs on | Host (backend process) | Container (agent worker) |
| File I/O | `docker exec cat/tee` or path translation | Native `open()`, `pathlib` |
| Code execution | `docker exec python3 -c` | `subprocess.run()` |
| Shell commands | `docker exec sh -c` | `subprocess.run()` |
| LLM calls | Backend → LiteLLM | Agent worker → LiteLLM (direct) |
| Path translation | Required (container ↔ host) | **None** — everything is `/workspace/...` |
| Tool context | Needs sandbox_image, mounts, docker IDs | Just local paths |
| Backend role | Runs agent loop + tool dispatch | Turn manager — dispatches to container, receives results |

### 2.3 What Stays the Same

- Frontend, Gateway, WebSocket protocol — **unchanged**
- Database (SQLite) — stays on host, backend owns it
- Conversation persistence — backend writes to DB
- Settings UI, agent config — unchanged
- Memory/knowledge store — backend manages (agent calls back to backend API)

---

## 3. Container Mounts

```
Container filesystem:

/bond/                          ← Bond agent library (read-only mount)
  backend/
    app/
      agent/
        loop.py                 ← The agent loop code
        tools/                  ← Tool handlers
        llm.py                  ← LiteLLM wrapper
      worker.py                 ← FastAPI worker (new)

/workspace/                     ← User workspace mounts (read-write)
  ecoinspector-portal/          ← bind: /mnt/c/dev/ecoinspector/ecoinspector-portal
  another-project/              ← bind: ~/projects/another-project

/tmp/.ssh/                      ← SSH keys (read-only mount)

/config/                        ← Agent config injected at startup
  agent.json                    ← Model, tools, system prompt, API keys
```

### Mount Strategy

| Mount | Source (host) | Target (container) | Mode |
|-------|---------------|---------------------|------|
| Bond library | `~/bond/backend` | `/bond/backend` | `ro` |
| Workspace(s) | Per agent config | `/workspace/{name}` | `rw` |
| SSH keys | `~/.ssh` | `/tmp/.ssh` | `ro` |
| Agent config | Generated at startup | `/config/agent.json` | `ro` |

The Bond library mount means we don't need to rebuild the container image when agent code changes — just restart the worker.

---

## 4. Agent Worker

A lightweight FastAPI app that runs inside the container. It receives turn requests from the backend and executes them.

### 4.1 API

```
POST /turn
  Body: { message, history, conversation_id }
  Response: SSE stream of events (same format as current backend SSE)
    - { event: "status", data: "thinking" }
    - { event: "chunk", data: "response text" }
    - { event: "tool_call", data: { name, arguments, result } }
    - { event: "done", data: { response, tool_calls_made } }

POST /interrupt
  Body: { new_messages: [...] }
  Response: { acknowledged: true }

GET /health
  Response: { status: "ok", agent_id, uptime }
```

### 4.2 Startup

```
Container starts with:
  python -m bond.agent.worker \
    --port 18791 \
    --config /config/agent.json \
    --backend-url http://host.docker.internal:18790
```

The `--backend-url` lets the agent call back to the host backend for:
- Memory search (RAG) — `GET /api/v1/search?q=...`
- Memory save — `POST /api/v1/memories`
- Entity operations — `POST /api/v1/entities`
- Conversation context — `GET /api/v1/conversations/{id}/messages`

### 4.3 Tool Execution (Native)

Inside the container, tools are just local operations:

```python
# file_read — just open the file
async def handle_file_read(arguments, context):
    path = arguments["path"]  # e.g. /workspace/ecoinspector-portal/Makefile
    content = Path(path).read_text()
    return {"content": content}

# file_write — just write the file
async def handle_file_write(arguments, context):
    path = arguments["path"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(arguments["content"])
    return {"status": "written"}

# code_execute — just run it
async def handle_code_execute(arguments, context):
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", arguments["code"],
        stdout=PIPE, stderr=PIPE
    )
    stdout, stderr = await proc.communicate()
    return {"stdout": stdout.decode(), "stderr": stderr.decode()}

# shell — just run it
async def handle_shell(arguments, context):
    proc = await asyncio.create_subprocess_shell(
        arguments["command"],
        stdout=PIPE, stderr=PIPE,
        cwd="/workspace"
    )
    stdout, stderr = await proc.communicate()
    return {"stdout": stdout.decode(), "stderr": stderr.decode()}
```

No path translation. No docker exec. No shell escaping. Just native operations.

### 4.4 Callback Tools (Memory, Search)

Some tools need the host database. These call back to the backend:

```python
# search_memory — call back to backend API
async def handle_search_memory(arguments, context):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{context['backend_url']}/api/v1/search",
            params={"q": arguments["query"], "limit": arguments.get("limit", 10)}
        )
        return resp.json()

# memory_save — call back to backend API
async def handle_memory_save(arguments, context):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{context['backend_url']}/api/v1/memories",
            json=arguments
        )
        return resp.json()
```

---

## 5. Backend Changes

### 5.1 Turn Manager (replaces Agent Loop on host)

The backend no longer runs the agent loop. Instead it:

1. Receives turn request from gateway
2. Ensures container is running (start if needed)
3. Forwards request to agent worker inside container
4. Streams SSE events back to gateway
5. Persists conversation messages to DB

```python
async def dispatch_turn(message: str, history: list, conversation_id: str, agent: dict):
    """Dispatch a turn to the agent worker inside the container."""
    container_id = await sandbox_manager.get_or_create_container(
        agent["id"], agent["sandbox_image"], agent["workspace_mounts"]
    )
    
    # Container exposes port 18791 internally
    worker_url = await sandbox_manager.get_worker_url(container_id)
    
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{worker_url}/turn",
            json={"message": message, "history": history, "conversation_id": conversation_id},
            timeout=300,
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line  # Forward SSE to gateway
```

### 5.2 Container Startup Changes

Current: `docker run ... sleep infinity`  
Proposed: `docker run ... python -m bond.agent.worker --port 18791 --config /config/agent.json`

The container runs the agent worker process instead of sleeping. The worker is the long-running process that keeps the container alive.

### 5.3 Config Injection

Before starting the container, the backend writes `/config/agent.json`:

```json
{
  "agent_id": "01JBOND0000000000000DEFAULT",
  "name": "bond",
  "model": "anthropic/claude-sonnet-4-20250514",
  "api_keys": {
    "anthropic": "sk-ant-...",
    "voyage": "pa-..."
  },
  "system_prompt": "You are Bond...",
  "tools": ["respond", "search_memory", "memory_save", "file_read", "file_write", "code_execute", "shell", "web_search", "web_read"],
  "max_iterations": 25,
  "auto_rag": true,
  "auto_rag_limit": 5,
  "backend_url": "http://host.docker.internal:18790"
}
```

API keys are decrypted from the DB and injected into the config. They exist only in the container's memory after the config file is read and deleted.

---

## 6. Sequence Diagrams

### 6.1 Normal Turn

```
User          Frontend       Gateway        Backend         Container
 │               │              │              │               │
 │  "Fix the     │              │              │               │
 │   bug in..."  │              │              │               │
 │──────────────►│              │              │               │
 │               │──WebSocket──►│              │               │
 │               │              │──POST /turn──►│               │
 │               │              │              │──ensure container running
 │               │              │              │──POST /turn───►│
 │               │              │              │               │──LLM call
 │               │              │              │               │  (direct to API)
 │               │              │              │               │
 │               │              │              │◄──SSE: tool_call (file_read)
 │               │              │◄─SSE forward─┤               │
 │               │◄──WebSocket──│              │               │  open("/workspace/...")
 │               │              │              │               │  (native file I/O)
 │               │              │              │               │
 │               │              │              │               │──LLM call
 │               │              │              │               │
 │               │              │              │◄──SSE: tool_call (file_write)
 │               │              │◄─SSE forward─┤               │
 │               │◄──WebSocket──│              │               │  open("/workspace/...")
 │               │              │              │               │
 │               │              │              │               │──LLM call
 │               │              │              │               │
 │               │              │              │◄──SSE: chunk "I fixed..."
 │               │              │◄─SSE forward─┤               │
 │               │◄──WebSocket──│              │               │
 │               │              │              │◄──SSE: done
 │◄──────────────│              │              │──persist to DB │
 │               │              │              │               │
```

### 6.2 Memory Callback

```
Container                          Backend (host)
    │                                    │
    │  Agent needs context               │
    │  (search_memory tool)              │
    │                                    │
    │──GET /api/v1/search?q=...─────────►│
    │                                    │──query SQLite
    │                                    │──run hybrid search
    │◄──JSON results────────────────────│
    │                                    │
    │  (continues agent loop with        │
    │   memory context)                  │
    │                                    │
```

### 6.3 Container Lifecycle

```
Backend                         Docker
  │                               │
  │  First turn for agent         │
  │                               │
  │  1. Generate /config/agent.json
  │  2. docker run                │
  │     -v ~/bond/backend:/bond/backend:ro
  │     -v /mnt/c/dev/project:/workspace/project
  │     -v ~/.ssh:/tmp/.ssh:ro
  │     -v /tmp/agent-config:/config:ro
  │     -p 18791                  │
  │     bond-sandbox-image        │
  │     python -m bond.agent.worker
  │──────────────────────────────►│
  │                               │──start worker
  │  3. Wait for health check     │
  │◄──GET /health → 200──────────│
  │                               │
  │  4. Dispatch turn             │
  │──POST /turn──────────────────►│
  │                               │
  │  ... (container stays alive)  │
  │                               │
  │  Idle timeout (1hr)           │
  │──docker stop─────────────────►│
  │                               │
```

---

## 7. Host Mode (No Sandbox)

When `sandbox_image` is null, the agent loop runs on the host exactly as it does today. This is the "trusted" mode for the main session.

```
sandbox_image = null  →  Agent loop runs in backend process (current behavior)
sandbox_image = "..."  →  Agent loop runs in container (new behavior)
```

This means we keep backward compatibility. The existing host-mode code stays untouched.

---

## 8. Security Considerations

### 8.1 API Key Handling

API keys are decrypted on the host and injected into the container config at startup. The config file is mounted read-only and can be deleted from the host after the worker reads it (the worker loads config into memory on startup).

Alternative: Pass keys via environment variables at container start. Avoids writing to disk entirely.

### 8.2 Backend Callback Authentication

The agent worker calls back to the backend for memory operations. These callbacks need authentication to prevent other containers or processes from accessing the API:

- Generate a one-time token per container start
- Pass it in the agent config
- Backend validates it on callback endpoints
- Token is scoped to the specific agent ID

### 8.3 Filesystem Isolation

The container only sees:
- `/workspace/...` — explicitly mounted directories (configured per agent)
- `/bond/backend` — agent library (read-only)
- `/config/` — agent config (read-only)
- `/tmp/.ssh` — SSH keys (read-only)

No access to host filesystem outside these mounts.

### 8.4 Network

The container needs outbound access for:
- LLM API calls (Anthropic, OpenAI, etc.)
- `host.docker.internal:18790` — backend callbacks
- Web search / web read tools
- Git operations (clone, push, pull)

This is the same as current behavior (network is already enabled).

---

## 9. Implementation Plan

### Phase 1: Agent Worker (MVP)

1. **Create `backend/app/worker.py`** — FastAPI app with `/turn`, `/health`, `/interrupt` endpoints
2. **Extract agent loop** — Move core loop logic into a shared module used by both host-mode and worker-mode
3. **Native tool handlers** — Rewrite file/code/shell tools for native execution (no docker exec)
4. **Callback client** — HTTP client for memory/search/entity operations back to host
5. **Container startup** — Update `SandboxManager` to run worker instead of `sleep infinity`
6. **Backend dispatch** — New `TurnManager` that forwards turns to container worker

### Phase 2: Config & Security

7. **Config injection** — Generate and mount `agent.json` with decrypted API keys
8. **Callback auth** — One-time token per container, validated on backend
9. **Health checks** — Backend waits for worker health before dispatching

### Phase 3: Polish

10. **Graceful shutdown** — Worker finishes current turn before container stops
11. **Hot reload** — Worker detects agent config changes (via backend notification)
12. **Logging** — Worker logs forwarded to host (docker logs or mounted log file)
13. **Error recovery** — Backend detects dead worker, restarts container

### Stories

| ID | Story | Phase | Depends |
|----|-------|-------|---------|
| C1 | Create agent worker FastAPI app with `/turn` SSE, `/health`, `/interrupt` | 1 | — |
| C2 | Extract shared agent loop module (used by host-mode and worker-mode) | 1 | C1 |
| C3 | Native tool handlers (file, code, shell — no docker exec) | 1 | C2 |
| C4 | Memory/search callback client (worker → backend HTTP) | 1 | C2 |
| C5 | Update SandboxManager: run worker process, port mapping, health wait | 1 | C1 |
| C6 | TurnManager: dispatch turns to container, stream SSE back | 1 | C5 |
| C7 | Config injection: generate agent.json, mount into container | 2 | C5 |
| C8 | Callback authentication (one-time token) | 2 | C4, C7 |
| C9 | Graceful shutdown + error recovery | 3 | C6 |
| C10 | Logging, hot reload, cleanup | 3 | C9 |

---

## 10. Migration Path

The current host-side agent loop continues to work for `sandbox_image = null`. This means:

1. **No breaking changes** — existing host-mode agents keep working
2. **Incremental rollout** — switch individual agents to containerized mode by setting `sandbox_image`
3. **Easy rollback** — set `sandbox_image = null` to go back to host mode

The backend detects which mode to use:

```python
if agent["sandbox_image"]:
    # Containerized mode — dispatch to worker
    yield from turn_manager.dispatch(message, history, agent)
else:
    # Host mode — run loop in-process
    yield from agent_turn(message, history, db=db, agent_id=agent["id"])
```

---

## 11. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Agent worker is a FastAPI app | Same stack as backend, reuses existing code. SSE streaming already proven. |
| Mount Bond library read-only | No container rebuild needed for code changes. Just restart worker. |
| Config injected as JSON file | Simple, debuggable, no complex env var marshaling. |
| Callback to backend for DB ops | SQLite can't be shared across processes/containers. Backend owns the DB. |
| One-time auth token per container | Minimal security overhead, prevents rogue access to backend API. |
| Host mode preserved | Backward compatible. Power users who trust their environment skip Docker overhead. |
| Worker runs on port 18791 | Avoids conflict with backend (18790). Each container gets its own port via Docker port mapping. |
| API keys passed in config, not env | Easier to manage multiple keys. Config file is read-only and can be purged after startup. |

---

## 12. What This Eliminates

- ❌ `_translate_container_to_host()` — gone
- ❌ `_translate_host_to_container()` — gone
- ❌ `docker exec` for file operations — gone
- ❌ Shell escaping for content piping — gone
- ❌ `workspace_dirs` allowlist (container-mode) — the mount IS the allowlist
- ❌ Path confusion between host and container — gone
- ❌ `sleep infinity` as container entrypoint — replaced by worker process

---

## 13. Open Questions

1. **Multiple agents, multiple containers** — Each agent with a `sandbox_image` gets its own container + worker. Port allocation strategy? (Docker random port mapping + inspect?)

2. **Worker crash recovery** — If the worker crashes mid-turn, does the backend retry? Or just report the error?

3. **Shared container optimization** — Could multiple agents share a container if they have the same sandbox image? (Probably not worth the complexity.)

4. **LLM streaming** — Should the worker stream LLM tokens to the backend as they arrive? Or batch per-tool-call? (Stream for UX, batch is simpler.)

5. **Container image requirements** — The sandbox image needs Python + uvicorn + litellm + httpx. Should we build a base image (`bond-agent-base`) that all sandbox images inherit from?
