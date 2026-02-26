# Bond Observability Guide

How to monitor, debug, and understand what Bond is doing at runtime.

---

## Architecture Overview

Bond has three layers, each with its own observability surface:

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│  Frontend    │────▶│  Gateway    │────▶│  Agent Worker     │
│  (Next.js)  │     │ (TypeScript)│ SSE │  (Python/FastAPI) │
│  :18788     │     │  :18789     │◀────│  :18791           │
└─────────────┘     └─────────────┘     └──────────────────┘
```

Each layer emits structured logs to stdout. In containerized mode, the agent worker runs inside a sandbox container; in host mode, it runs as a local process.

---

## 1. Agent Worker Observability

The agent worker is the most important layer to observe — it runs tool calls, manages memory, and executes the agent loop.

### Health Check

```bash
curl http://localhost:18791/health
```

Returns:
```json
{
  "status": "ok",
  "agent_id": "agent-abc123",
  "uptime": 342.51
}
```

Use this for liveness probes, uptime monitoring, or quick sanity checks.

### SSE Event Stream

Every `/turn` request returns a Server-Sent Events stream. Events you'll see:

| Event      | When                                | Payload                                         |
|------------|-------------------------------------|--------------------------------------------------|
| `status`   | Turn starts                         | `{"state": "thinking", "conversation_id": "..."}` |
| `chunk`    | Agent produces text                 | `{"content": "..."}`                             |
| `done`     | Turn completes                      | `{"response": "...", "tool_calls_made": 3}`      |
| `error`    | Turn fails                          | `{"message": "..."}`                             |

You can observe the raw SSE stream with curl:

```bash
curl -N -X POST http://localhost:18791/turn \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "hello"}], "conversation_id": "test"}'
```

### Log Output

The worker uses Python's `logging` module with this format:

```
2026-02-26 06:30:00 backend.app.worker INFO Agent worker initialized: agent_id=agent-123
2026-02-26 06:30:05 backend.app.worker INFO Tool call [1]: memory_save
2026-02-26 06:30:05 backend.app.agent.tools.native INFO memory_save id=01KJC... type=fact promote=True
2026-02-26 06:30:06 backend.app.agent.tools.native INFO search_memory query='project setup' results=3 (local=2 shared=1) elapsed=4.2ms
```

#### What Gets Logged

**Agent loop** (`backend.app.worker`):
- Worker startup/shutdown with agent ID
- Each tool call with sequence number and tool name
- Shared DB attachment success/failure
- Agent loop errors (full stack trace)

**Memory operations** (`backend.app.agent.tools.native`):
- `memory_save` — ID, type, promotion flag
- `memory_update` — ID, version number
- `memory_delete` — ID
- `search_memory` — query, result count (local vs shared split), elapsed time in ms
- All failures with `WARNING` level + stack trace

**Tool execution** (`backend.app.agent.tools.native`):
- `file_read` / `file_write` — file paths
- `code_execute` — command, exit code
- All tools log errors with context

#### Adjusting Log Level

The worker starts at `INFO` by default. To get debug output (including FTS query details and shared DB search diagnostics):

```bash
# When running the worker directly
LOG_LEVEL=DEBUG python -m backend.app.worker --port 18791

# Or set in the container environment
docker run -e LOG_LEVEL=DEBUG bond-agent-worker
```

At `DEBUG` level you'll also see:
- FTS search failures (expected when no shared.db is attached)
- Detailed parameter logging for each tool call

---

## 2. Host Backend Observability

The host backend (FastAPI on `:18790`) uses the mediator pipeline with built-in observability behaviors.

### Mediator Pipeline Logging

Every command and query flows through `LoggingBehavior`, which logs:

```
2026-02-26 06:30:00 | INFO  | mediator       | [a1b2c3d4] CMD CreateConversation POST /api/v1/conversations params={'title': 'New chat'}
2026-02-26 06:30:01 | INFO  | mediator       | [a1b2c3d4] CreateConversation 42.3ms
```

Key features:
- **Correlation IDs** — every request gets an 8-char hex ID (`[a1b2c3d4]`) logged on entry and exit. Use this to trace a request through the pipeline.
- **Timing** — elapsed time in milliseconds for every handler
- **Parameter capture** — request parameters are logged (truncated at 200 chars)
- **Error logging** — exceptions include the correlation ID, handler name, elapsed time, and exception type

#### Accessing Correlation IDs in Code

If you're writing a handler and want to include the correlation ID in your own logs:

```python
from backend.app.mediator.behaviors.logging import get_correlation_id

logger.info("[%s] My custom log message", get_correlation_id())
```

### Exception Handling

`ExceptionBehavior` catches unhandled exceptions and:
1. Logs the full stack trace at `ERROR` level with the handler name
2. Returns a consistent 500 response: `{"error": "internal_error", "message": "..."}`

No unhandled exceptions escape to the client without being logged.

### API Endpoints for Monitoring

```bash
# Health check
curl http://localhost:18790/api/v1/health

# List agents and their status
curl http://localhost:18790/api/v1/agents

# Get a specific agent's state
curl http://localhost:18790/api/v1/agents/{agent_id}
```

---

## 3. Gateway Observability

The TypeScript gateway (`:18789`) sits between the frontend and agent workers.

Check gateway logs in the terminal where it's running, or:

```bash
# If running via pnpm
pnpm --filter gateway dev 2>&1 | tee gateway.log
```

---

## 4. Database Observability

### Agent DB (SQLite)

Each agent has its own SQLite database at `/data/agent.db` (containerized) or a local temp path (host mode).

**Inspect the agent's memory directly:**

```bash
sqlite3 /data/agent.db

-- List all memories
SELECT id, type, content, created_at, deleted_at FROM memories ORDER BY created_at DESC;

-- Check memory versions (audit trail)
SELECT mv.memory_id, mv.version, mv.previous_content, mv.new_content, mv.changed_by, mv.created_at
FROM memory_versions mv ORDER BY mv.created_at DESC;

-- Verify FTS index is in sync
SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL;
SELECT COUNT(*) FROM memories_fts;
-- These should match

-- Search FTS directly
SELECT id, content, rank FROM memories_fts WHERE memories_fts MATCH 'your search term';

-- Check for orphaned FTS entries (should return 0)
SELECT COUNT(*) FROM memories_fts WHERE id NOT IN (SELECT id FROM memories WHERE deleted_at IS NULL);

-- Entity graph
SELECT * FROM entities ORDER BY created_at DESC;
```

### Shared DB (Read-Only Snapshot)

The shared database is mounted at `/data/shared/shared.db` and contains promoted memories from the host. To check what's been promoted:

```bash
sqlite3 /data/shared/shared.db "SELECT id, type, content FROM memories LIMIT 20;"
```

### Host DB (SQLAlchemy)

The host database uses SQLAlchemy with async SQLite. Connection events are logged by the mediator pipeline. For direct inspection:

```bash
sqlite3 data/bond.db

-- Recent memories
SELECT id, type, content, importance, sensitivity FROM memories ORDER BY created_at DESC LIMIT 20;

-- Memory version history
SELECT * FROM memory_versions WHERE memory_id = 'some-id' ORDER BY version;
```

---

## 5. Debugging Playbook

### "The agent isn't responding"

1. Check health: `curl http://localhost:18791/health`
2. Check logs for errors: look for `ERROR` or `WARNING` in worker stdout
3. Verify the agent DB exists and is writable
4. Check if the agent loop hit an exception (look for "Agent loop failed" in logs)

### "Memory search returns nothing"

1. Verify memories exist: `SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL;`
2. Check FTS is populated: `SELECT COUNT(*) FROM memories_fts;`
3. Test FTS directly: `SELECT * FROM memories_fts WHERE memories_fts MATCH 'your term';`
4. Check if shared.db is attached: look for "Attached shared.db" in startup logs
5. Run search at DEBUG level to see timing and local/shared split

### "Memory was saved but doesn't show in search"

1. Check if it was soft-deleted: `SELECT deleted_at FROM memories WHERE id = 'the-id';`
2. Verify FTS trigger fired: `SELECT * FROM memories_fts WHERE id = 'the-id';`
3. Check the FTS content matches: the FTS index stores `content` and `summary`

### "Tool call failed silently"

All tool failures are logged at `WARNING` level with stack traces. Check the worker logs. If you see nothing, the tool may have returned an error dict without raising — search for `"error"` in the tool response.

### "Shared memory isn't showing up"

1. Check shared.db exists at the expected path
2. Verify it was attached on startup (look for "Attached shared.db" log)
3. Query shared.db directly to confirm data exists
4. Check FTS in shared.db: `SELECT * FROM memories_fts;` (triggers must have fired during insert)

---

## 6. Structured Log Fields Reference

All agent-side log messages use a consistent key=value format for easy parsing:

| Field | Example | Where |
|-------|---------|-------|
| `agent_id` | `agent-abc123` | Worker startup |
| `id` | `01KJC...` | Memory save/update/delete |
| `type` | `fact` | Memory save |
| `promote` | `True` | Memory save (promotable types) |
| `version` | `2` | Memory update |
| `query` | `'project setup'` | Search |
| `results` | `3` | Search |
| `local` | `2` | Search (local result count) |
| `shared` | `1` | Search (shared result count) |
| `elapsed` | `4.2ms` | Search timing |

These fields can be parsed by log aggregators (Loki, CloudWatch, etc.) using simple regex or key=value extraction.

---

## 7. Future Enhancements

The following are not yet implemented but are planned:

- **OpenTelemetry traces** — distributed tracing across gateway → worker → tool calls
- **Prometheus metrics endpoint** — `/metrics` on each service for request rates, latencies, error rates
- **Memory operation counters** — save/update/delete/search counts per agent
- **SSE event metrics** — events emitted per turn, promotion rate
- **Structured JSON logging** — optional JSON format for log aggregation pipelines
