"""Agent Worker — standalone FastAPI app that runs inside a container.

Provides /turn (SSE), /interrupt, and /health endpoints.  Runs the agent
loop locally with native tool handlers and a local aiosqlite database.

Usage::

    python -m bond.agent.worker --port 18791 --config /config/agent.json --data-dir /data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite
import litellm
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("bond.agent.worker")

# ---------------------------------------------------------------------------
# Agent DB schema (applied on startup)
# ---------------------------------------------------------------------------

_AGENT_DB_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    source_type TEXT,
    source_id TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK(sensitivity IN ('normal', 'personal', 'secret')),
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    importance REAL NOT NULL DEFAULT 0.5
        CHECK(importance BETWEEN 0.0 AND 1.0),
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMP,
    embedding_model TEXT,
    processed_at TIMESTAMP,
    deleted_at TIMESTAMP,
    confidence REAL DEFAULT 1.0,
    promoted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_mem_active ON memories(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance DESC);

CREATE TRIGGER IF NOT EXISTS memories_updated_at
    AFTER UPDATE ON memories FOR EACH ROW
BEGIN
    UPDATE memories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED,
    content,
    summary
);

CREATE TRIGGER IF NOT EXISTS mem_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_update AFTER UPDATE OF content, summary ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_delete AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
END;

CREATE TABLE IF NOT EXISTS memory_versions (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    previous_content TEXT,
    new_content TEXT NOT NULL,
    previous_type TEXT,
    new_type TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    change_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mv_memory ON memory_versions(memory_id, version);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    attributes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_chunks (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS content_chunks_fts USING fts5(
    content,
    content='content_chunks',
    content_rowid='rowid'
);
"""

# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------


class WorkerState:
    """Holds the worker's runtime state."""

    def __init__(self) -> None:
        self.agent_db: aiosqlite.Connection | None = None
        self.config: dict[str, Any] = {}
        self.agent_id: str = "unknown"
        self.start_time: float = 0.0
        self.data_dir: Path = Path("/data")
        self.interrupt_event: asyncio.Event = asyncio.Event()
        self.pending_messages: list[dict] = []


_state = WorkerState()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Lifespan handler — startup and shutdown."""
    config_path = os.environ.get("BOND_WORKER_CONFIG", "/config/agent.json")
    data_dir = os.environ.get("BOND_WORKER_DATA_DIR", "/data")
    await _startup(config_path, data_dir)
    yield
    await _shutdown()

app = FastAPI(title="Bond Agent Worker", lifespan=_lifespan)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    uptime = time.time() - _state.start_time if _state.start_time else 0.0
    return {
        "status": "ok",
        "agent_id": _state.agent_id,
        "uptime": round(uptime, 2),
    }


@app.post("/interrupt")
async def interrupt(request: Request) -> dict:
    """Interrupt the current turn with new messages."""
    body = await request.json()
    new_messages = body.get("new_messages", [])
    _state.pending_messages.extend(new_messages)
    _state.interrupt_event.set()
    return {"acknowledged": True}


def _sse_event(event: str, data: Any) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/turn")
async def turn(request: Request) -> StreamingResponse:
    """Execute an agent turn and stream SSE events."""
    body = await request.json()
    message = body.get("message", "")
    history = body.get("history", [])
    conversation_id = body.get("conversation_id", "")

    async def event_stream():
        yield _sse_event("status", {"state": "thinking", "conversation_id": conversation_id})

        try:
            response_text, tool_calls_made = await _run_agent_loop(
                message, history, conversation_id, event_stream_callback=None,
            )
        except Exception as e:
            logger.exception("Agent loop failed")
            yield _sse_event("error", {"message": str(e)})
            yield _sse_event("done", {"response": "", "tool_calls_made": 0, "error": str(e)})
            return

        yield _sse_event("chunk", {"content": response_text})
        yield _sse_event("done", {"response": response_text, "tool_calls_made": tool_calls_made})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Agent loop (container-local, adapted from backend.app.agent.loop)
# ---------------------------------------------------------------------------


async def _run_agent_loop(
    user_message: str,
    history: list[dict],
    conversation_id: str,
    *,
    event_stream_callback: Any = None,
) -> tuple[str, int]:
    """Run the agent tool-use loop locally.

    Returns (response_text, tool_calls_count).
    Emits SSE events for tool_call and memory via the ``_state.sse_queue``
    which the /turn endpoint generator reads from.
    """
    from backend.app.agent.tools import TOOL_MAP
    from backend.app.agent.tools.native_registry import build_native_registry

    config = _state.config
    model = config.get("model", "anthropic/claude-sonnet-4-20250514")
    system_prompt = config.get("system_prompt", "You are Bond, a helpful AI assistant.")
    agent_tools = config.get("tools", ["respond", "search_memory", "memory_save"])
    max_iterations = config.get("max_iterations", 25)

    # Resolve API key: agent DB (encrypted) → env var fallback
    provider = model.split("/")[0] if "/" in model else "anthropic"
    api_key = None

    # 1. Agent DB settings (encrypted at rest, decrypt on demand)
    if _state.agent_db:
        try:
            cursor = await _state.agent_db.execute(
                "SELECT value FROM settings WHERE key = ?",
                (f"llm.api_key.{provider}",),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                from backend.app.core.crypto import decrypt_value
                api_key = decrypt_value(row[0])
        except Exception as e:
            logger.debug("Could not read API key from agent DB for %s: %s", provider, e)

    # 2. Environment variable fallback
    if not api_key:
        api_key = os.environ.get(f"{provider.upper()}_API_KEY")

    extra_kwargs: dict = {}
    if api_key:
        extra_kwargs["api_key"] = api_key

    # Build messages
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # Build tool definitions + registry
    registry = build_native_registry()
    tool_defs = [TOOL_MAP[name] for name in agent_tools if name in TOOL_MAP]

    # Tool context: local agent_db instead of host SQLAlchemy session
    tool_context: dict[str, Any] = {
        "agent_db": _state.agent_db,
        "agent_id": _state.agent_id,
    }

    tool_calls_made = 0
    sse_events: list[str] = []  # collected for the SSE stream

    for _iteration in range(max_iterations):
        # Check interrupt
        if _state.interrupt_event.is_set():
            _state.interrupt_event.clear()
            # Inject pending messages
            for msg in _state.pending_messages:
                messages.append(msg)
            _state.pending_messages.clear()

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tool_defs if tool_defs else None,
            temperature=0.7,
            max_tokens=4096,
            **extra_kwargs,
        )

        choice = response.choices[0]
        llm_message = choice.message

        if llm_message.tool_calls:
            messages.append(llm_message.model_dump())

            for tool_call in llm_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                tool_calls_made += 1
                logger.info("Tool call [%d]: %s", tool_calls_made, tool_name)

                if tool_name not in agent_tools:
                    result = {"error": f"Tool '{tool_name}' is not enabled."}
                else:
                    result = await registry.execute(tool_name, tool_args, tool_context)

                # Check for promotable memory -> emit SSE memory event
                if "_promote" in result:
                    _state._last_sse_events = getattr(_state, "_last_sse_events", [])
                    _state._last_sse_events.append(("memory", result["_promote"]))
                    del result["_promote"]

                # Check terminal tool
                if result.get("_terminal"):
                    return result.get("message", ""), tool_calls_made

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })
        else:
            return llm_message.content or "", tool_calls_made

    return (
        "I've reached my maximum number of steps. Please try rephrasing.",
        tool_calls_made,
    )


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


async def _init_agent_db(data_dir: Path) -> aiosqlite.Connection:
    """Open (or create) the agent's local SQLite database and run migrations."""
    db_path = data_dir / "agent.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(_AGENT_DB_SCHEMA)
    await db.commit()

    # Attach shared.db if it exists
    shared_path = data_dir / "shared" / "shared.db"
    if shared_path.exists():
        try:
            await db.execute(
                f"ATTACH DATABASE '{shared_path}' AS shared"
            )
            logger.info("Attached shared.db from %s", shared_path)
        except Exception as e:
            logger.warning("Failed to attach shared.db: %s", e)

    return db


async def _startup(config_path: str, data_dir: str) -> None:
    """Initialize the worker on startup."""
    _state.start_time = time.time()
    _state.data_dir = Path(data_dir)

    # Load config
    config_file = Path(config_path)
    if config_file.exists():
        _state.config = json.loads(config_file.read_text())
    else:
        logger.warning("Config file not found at %s, using defaults", config_path)
        _state.config = {}

    _state.agent_id = _state.config.get("agent_id", "default")

    # Point BOND_HOME to the mounted vault key location for decryption
    if not os.environ.get("BOND_HOME"):
        os.environ["BOND_HOME"] = "/bond-home"

    # Verify encrypted keys are available in agent DB (seeded by host init container)
    if _state.agent_db:
        try:
            cursor = await _state.agent_db.execute(
                "SELECT COUNT(*) FROM settings WHERE key LIKE 'llm.api_key.%'"
            )
            row = await cursor.fetchone()
            key_count = row[0] if row else 0
            if key_count > 0:
                logger.info("Found %d encrypted API key(s) in agent DB", key_count)
            else:
                logger.warning("No API keys found in agent DB settings table")
        except Exception:
            logger.debug("Settings table not yet available in agent DB")

    # Initialize agent DB
    _state.agent_db = await _init_agent_db(_state.data_dir)
    logger.info("Agent worker initialized: agent_id=%s", _state.agent_id)


async def _shutdown() -> None:
    """Clean up on shutdown."""
    if _state.agent_db:
        await _state.agent_db.close()
        _state.agent_db = None
    logger.info("Agent worker shut down")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint for ``python -m bond.agent.worker``."""
    parser = argparse.ArgumentParser(description="Bond Agent Worker")
    parser.add_argument("--port", type=int, default=18791)
    parser.add_argument("--config", default="/config/agent.json")
    parser.add_argument("--data-dir", default="/data")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    import uvicorn

    # Pass config via env vars — lifespan handler reads them
    os.environ["BOND_WORKER_CONFIG"] = args.config
    os.environ["BOND_WORKER_DATA_DIR"] = args.data_dir

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
