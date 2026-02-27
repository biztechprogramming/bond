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

import hashlib

import aiosqlite
import litellm

from backend.app.agent.context_decay import apply_progressive_decay
from backend.app.agent.tool_selection import select_tools, compact_tool_schema
from backend.app.agent.tool_result_filter import filter_tool_result
litellm.suppress_debug_info = True
import logging as _logging
_logging.getLogger("LiteLLM").setLevel(_logging.WARNING)
_logging.getLogger("litellm").setLevel(_logging.WARNING)

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

-- Context distillation: cached summaries to avoid re-summarizing every turn
CREATE TABLE IF NOT EXISTS context_summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('topic', 'bulk')),
    covers_from INTEGER NOT NULL,
    covers_to INTEGER NOT NULL,
    original_token_count INTEGER NOT NULL,
    summary TEXT NOT NULL,
    summary_token_count INTEGER NOT NULL,
    utility_model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cs_conv
    ON context_summaries(conversation_id, tier, covers_from);

-- Audit log: compression stats per turn
CREATE TABLE IF NOT EXISTS context_compression_log (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    original_tokens INTEGER NOT NULL,
    compressed_tokens INTEGER NOT NULL,
    stages_applied TEXT NOT NULL,
    fragments_selected INTEGER,
    fragments_total INTEGER,
    topics_summarized INTEGER,
    tools_pruned INTEGER,
    processing_time_ms INTEGER NOT NULL,
    utility_model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ccl_conv
    ON context_compression_log(conversation_id, turn_number);
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

    import asyncio
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def run_loop():
        try:
            response_text, tool_calls_made = await _run_agent_loop(
                message, history, conversation_id, event_queue=event_queue,
            )
            await event_queue.put(_sse_event("chunk", {"content": response_text}))
            await event_queue.put(_sse_event("done", {"response": response_text, "tool_calls_made": tool_calls_made}))
        except Exception as e:
            logger.exception("Agent loop failed")
            await event_queue.put(_sse_event("error", {"message": str(e)}))
            await event_queue.put(_sse_event("done", {"response": "", "tool_calls_made": 0, "error": str(e)}))
        await event_queue.put(None)  # sentinel

    async def event_stream():
        yield _sse_event("status", {"state": "thinking", "conversation_id": conversation_id})

        task = asyncio.create_task(run_loop())
        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield event
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Agent loop (container-local, adapted from backend.app.agent.loop)
# ---------------------------------------------------------------------------


async def _select_relevant_fragments(
    fragments: list[dict],
    user_message: str,
    history: list[dict],
    config: dict,
    extra_kwargs: dict,
) -> list[dict]:
    """Use the utility model to select which prompt fragments are relevant.

    Sends fragment names + descriptions to a fast model (e.g. claude-sonnet-4-6)
    which returns the IDs of fragments that are relevant to the current turn.
    Falls back to all fragments if the utility call fails.
    """
    if not fragments:
        return []

    utility_model = config.get("utility_model", "claude-sonnet-4-6")
    if not utility_model:
        raise RuntimeError("No utility_model configured — refusing to send all fragments to primary model")

    # Build the fragment catalog for the utility model
    catalog_lines = []
    for i, frag in enumerate(fragments):
        name = frag.get("display_name") or frag.get("name", f"fragment-{i}")
        desc = frag.get("description", "")
        frag_id = frag.get("id", str(i))
        catalog_lines.append(f"- ID: {frag_id} | {name}: {desc}")

    catalog = "\n".join(catalog_lines)

    # Get recent history context (last 3 messages for efficiency)
    recent_history = ""
    if history:
        recent = history[-3:]
        recent_lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                recent_lines.append(f"{role}: {content[:200]}")
        recent_history = "\n".join(recent_lines)

    selection_prompt = f"""You are a prompt fragment selector. Given a user's message and recent conversation history, determine which prompt fragments are relevant and should be included in the agent's system prompt.

Available fragments:
{catalog}

Recent conversation:
{recent_history}

Current user message:
{user_message}

Return ONLY a JSON array of fragment IDs that are relevant to this turn. Include fragments that provide useful context or guidelines for handling this request. When in doubt, include the fragment.

Example response: ["01PFRAG_MEMORY_GUID", "01PFRAG_ERROR_HANDL"]

Respond with just the JSON array, nothing else."""

    try:
        response = await litellm.acompletion(
            model=utility_model,
            messages=[{"role": "user", "content": selection_prompt}],
            temperature=0.0,
            max_tokens=1024,
            **extra_kwargs,
        )

        result_text = response.choices[0].message.content or "[]"
        # Parse the JSON array — strip markdown fences if present
        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        selected_ids = json.loads(result_text)
        if not isinstance(selected_ids, list):
            raise RuntimeError(f"Utility model returned non-list for fragment selection: {result_text}")

        # Filter fragments by selected IDs
        selected = [f for f in fragments if f.get("id") in selected_ids]

        logger.info(
            "Fragment selection: %d/%d fragments selected by utility model (%s). Selected: %s",
            len(selected), len(fragments), utility_model,
            [f.get("name") for f in selected],
        )

        # If utility model selected nothing, return empty — do NOT fall back to all
        if not selected:
            logger.warning("Utility model selected 0 fragments — proceeding with none")

        return selected

    except Exception as e:
        raise RuntimeError(f"Fragment selection failed — refusing to send all {len(fragments)} fragments to primary model: {e}") from e


# ---------------------------------------------------------------------------
# Stage 2: History Compression
# ---------------------------------------------------------------------------

# Configuration constants (will be DB-configurable later)
VERBATIM_MESSAGE_COUNT = 4  # Recent messages kept as-is (reduced from 6)
COMPRESSION_THRESHOLD = 8000  # Don't compress if under this token count (reduced from 15000)
SUMMARY_MAX_WORDS = 100
TOPIC_MAX_MESSAGES = 8  # Force topic boundary after this many messages

# Sliding window: max messages loaded from DB per turn
HISTORY_WINDOW_SIZE = 20
# Update rolling summary when this many new messages accumulate
SUMMARY_UPDATE_THRESHOLD = 10


def _estimate_tokens(text: str) -> int:
    """Fast token estimation — ~4 chars per token for English."""
    if not text:
        return 0
    return len(text) // 4


def _estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_tokens(content)
        elif isinstance(content, dict):
            total += _estimate_tokens(json.dumps(content))
        # Tool calls add overhead
        if msg.get("tool_calls"):
            total += _estimate_tokens(json.dumps(msg["tool_calls"]))
    return total


def _prune_tool_result(msg: dict, age: str) -> dict:
    """Rule-based tool output pruning. No LLM call needed.

    age: 'current' | 'recent' | 'old'
    """
    if msg.get("role") != "tool":
        return msg

    content = msg.get("content", "")
    if not isinstance(content, str):
        return msg

    token_count = _estimate_tokens(content)

    # Small results: keep as-is regardless of age
    if token_count < 500:
        return msg

    # Current topic: keep verbatim
    if age == "current":
        return msg

    # Try to parse as JSON for structured pruning
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if age == "recent":
        # file_read: keep first/last 10 lines + summary
        if parsed and isinstance(parsed, dict):
            if "content" in parsed and isinstance(parsed["content"], str):
                lines = parsed["content"].splitlines()
                if len(lines) > 25:
                    head = "\n".join(lines[:10])
                    tail = "\n".join(lines[-10:])
                    pruned = {**parsed, "content": f"{head}\n\n<< {len(lines) - 20} LINES OMITTED >>\n\n{tail}"}
                    return {**msg, "content": json.dumps(pruned)}

        # Generic: truncate long text
        if token_count > 2000:
            half = 800 * 4  # ~800 tokens worth of chars
            truncated = content[:half] + f"\n\n<< {token_count - 1600} TOKENS OMITTED >>\n\n" + content[-half:]
            return {**msg, "content": truncated}

    elif age == "old":
        # Aggressive: one-line summary
        if parsed and isinstance(parsed, dict):
            # Try to extract meaningful summary fields
            summary_parts = []
            for key in ("file_path", "path", "command", "query", "url", "status", "exit_code", "error"):
                if key in parsed:
                    val = str(parsed[key])[:100]
                    summary_parts.append(f"{key}={val}")
            if summary_parts:
                return {**msg, "content": f"[Pruned tool result: {', '.join(summary_parts)}]"}

        # Fallback: first 200 chars
        return {**msg, "content": f"[Pruned: {content[:200]}...]"}

    return msg


async def _compress_history(
    messages: list[dict],
    conversation_id: str,
    config: dict,
    extra_kwargs: dict,
) -> tuple[list[dict], dict]:
    """Compress conversation history using tiered summarization.

    Returns (compressed_messages, stats_dict).
    """
    import time as _time
    start_time = _time.time()

    total_tokens = _estimate_messages_tokens(messages)
    stats = {
        "original_tokens": total_tokens,
        "compressed_tokens": total_tokens,
        "verbatim_messages": 0,
        "topics_summarized": 0,
        "tools_pruned": 0,
        "cache_hits": 0,
    }

    # Don't compress short histories
    if total_tokens < COMPRESSION_THRESHOLD or len(messages) <= VERBATIM_MESSAGE_COUNT:
        stats["verbatim_messages"] = len(messages)
        return messages, stats

    utility_model = config.get("utility_model", "claude-sonnet-4-6")

    # Split: compressible (older) vs verbatim (recent)
    verbatim = messages[-VERBATIM_MESSAGE_COUNT:]
    compressible = messages[:-VERBATIM_MESSAGE_COUNT]
    stats["verbatim_messages"] = len(verbatim)

    if not compressible:
        return messages, stats

    # --- Stage 3: Tool Output Pruning (rule-based, fast) ---
    # Classify messages by age relative to verbatim boundary
    pruned_compressible = []
    tools_pruned = 0
    # First half of compressible = "old", second half = "recent"
    midpoint = len(compressible) // 2

    for i, msg in enumerate(compressible):
        age = "old" if i < midpoint else "recent"
        pruned = _prune_tool_result(msg, age)
        if pruned is not msg:
            tools_pruned += 1
        pruned_compressible.append(pruned)

    stats["tools_pruned"] = tools_pruned

    # --- Stage 2: History Summarization ---
    # Check for cached summaries
    cached_summary = None
    cache_covers_to = 0

    if _state.agent_db:
        try:
            cursor = await _state.agent_db.execute(
                "SELECT summary, covers_to FROM context_summaries "
                "WHERE conversation_id = ? ORDER BY covers_to DESC LIMIT 1",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row:
                cached_summary = row[0]
                cache_covers_to = row[1]
                stats["cache_hits"] = 1
        except Exception as e:
            logger.debug("Failed to load cached summary: %s", e)

    # Determine what needs summarizing
    if cached_summary and cache_covers_to > 0:
        # We have a cached summary covering messages 0..cache_covers_to
        # Only need to summarize messages cache_covers_to..end_of_compressible
        already_summarized_count = min(cache_covers_to, len(pruned_compressible))
        new_messages_to_summarize = pruned_compressible[already_summarized_count:]

        if not new_messages_to_summarize:
            # Cache covers everything — use cached summary + verbatim
            compressed = [{"role": "user", "content": f"[Previous conversation summary]\n{cached_summary}"}]
            compressed.extend(verbatim)
            stats["compressed_tokens"] = _estimate_messages_tokens(compressed)
            stats["topics_summarized"] = 1
            stats["processing_time_ms"] = int((_time.time() - start_time) * 1000)
            return compressed, stats

        # Summarize only the new portion
        messages_to_summarize = new_messages_to_summarize
        existing_context = f"Previous summary: {cached_summary}\n\n"
    else:
        messages_to_summarize = pruned_compressible
        existing_context = ""

    # Build text representation for summarization
    summary_input_lines = []
    for msg in messages_to_summarize:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            # Truncate very long messages for the summary input
            if len(content) > 1000:
                content = content[:500] + f"\n[...{len(content) - 1000} chars omitted...]\n" + content[-500:]
            summary_input_lines.append(f"{role}: {content}")
        elif msg.get("tool_calls"):
            calls = msg["tool_calls"]
            if isinstance(calls, list):
                for tc in calls:
                    fn = tc.get("function", {})
                    summary_input_lines.append(f"assistant: [called {fn.get('name', '?')}({fn.get('arguments', '')[:100]})]")

    summary_input = "\n".join(summary_input_lines)

    if not summary_input.strip():
        return messages, stats

    # Call utility model for summarization
    try:
        summary_prompt = f"""Summarize the following conversation history concisely. Preserve:
- Key decisions made and their reasoning
- File paths, variable names, error codes, and specific technical details
- What was attempted and whether it succeeded or failed
- Current state of the work

Do NOT include:
- Verbose tool output content (just note what tool was called and the key result)
- Pleasantries or filler
- Redundant information

{existing_context}Conversation to summarize:
{summary_input}

Write a concise summary in {SUMMARY_MAX_WORDS}-{SUMMARY_MAX_WORDS * 2} words. Use bullet points for clarity. Start directly with the content, no preamble."""

        response = await litellm.acompletion(
            model=utility_model,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.0,
            max_tokens=1024,
            **extra_kwargs,
        )

        summary = response.choices[0].message.content or ""
        stats["topics_summarized"] += 1

        # Combine: existing cached summary + new summary if applicable
        if cached_summary:
            full_summary = f"{cached_summary}\n\n{summary}"
        else:
            full_summary = summary

        # Cache the summary
        if _state.agent_db:
            try:
                from ulid import ULID
                summary_id = str(ULID())
                summary_tokens = _estimate_tokens(full_summary)
                await _state.agent_db.execute(
                    "INSERT OR REPLACE INTO context_summaries "
                    "(id, conversation_id, tier, covers_from, covers_to, "
                    "original_token_count, summary, summary_token_count, utility_model) "
                    "VALUES (?, ?, 'topic', 0, ?, ?, ?, ?, ?)",
                    (
                        summary_id,
                        conversation_id,
                        len(pruned_compressible),
                        _estimate_messages_tokens(pruned_compressible),
                        full_summary,
                        summary_tokens,
                        utility_model,
                    ),
                )
                await _state.agent_db.commit()
            except Exception as e:
                logger.debug("Failed to cache summary: %s", e)

        # Assemble compressed history
        compressed = [{"role": "user", "content": f"[Previous conversation summary]\n{full_summary}"}]
        compressed.extend(verbatim)

        stats["compressed_tokens"] = _estimate_messages_tokens(compressed)
        stats["processing_time_ms"] = int((_time.time() - start_time) * 1000)

        logger.info(
            "History compression: %d→%d tokens (%.0f%% reduction), %d tools pruned, cache=%s, %dms",
            stats["original_tokens"], stats["compressed_tokens"],
            (1 - stats["compressed_tokens"] / max(stats["original_tokens"], 1)) * 100,
            tools_pruned,
            "hit" if stats["cache_hits"] else "miss",
            stats["processing_time_ms"],
        )

        return compressed, stats

    except Exception as e:
        raise RuntimeError(f"History compression failed — refusing to send uncompressed history to primary model: {e}") from e


async def _log_compression_stats(
    conversation_id: str,
    turn_number: int,
    stats: dict,
    fragment_stats: dict,
    utility_model: str,
) -> None:
    """Log compression statistics to the agent DB for auditing."""
    if not _state.agent_db:
        return
    try:
        from ulid import ULID
        await _state.agent_db.execute(
            "INSERT INTO context_compression_log "
            "(id, conversation_id, turn_number, original_tokens, compressed_tokens, "
            "stages_applied, fragments_selected, fragments_total, topics_summarized, "
            "tools_pruned, processing_time_ms, utility_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(ULID()),
                conversation_id,
                turn_number,
                stats.get("original_tokens", 0),
                stats.get("compressed_tokens", 0),
                json.dumps(["fragment_selection", "history_compression", "tool_pruning"]),
                fragment_stats.get("selected", 0),
                fragment_stats.get("total", 0),
                stats.get("topics_summarized", 0),
                stats.get("tools_pruned", 0),
                stats.get("processing_time_ms", 0),
                utility_model,
            ),
        )
        await _state.agent_db.commit()
    except Exception as e:
        logger.debug("Failed to log compression stats: %s", e)


async def _apply_sliding_window(
    history: list[dict],
    conversation_id: str,
    config: dict,
    extra_kwargs: dict,
) -> list[dict]:
    """Apply sliding window: keep last HISTORY_WINDOW_SIZE messages, prepend rolling summary.

    If history exceeds the window, summarize the overflow and store as a rolling summary
    in the agent DB for reuse on next turn.

    Returns the windowed history with optional summary prefix.
    """
    if len(history) <= HISTORY_WINDOW_SIZE:
        return history

    utility_model = config.get("utility_model", "claude-sonnet-4-6")

    # Split: overflow (to summarize) + window (to keep)
    overflow = history[:-HISTORY_WINDOW_SIZE]
    window = history[-HISTORY_WINDOW_SIZE:]

    # Check for existing rolling summary in agent DB
    existing_summary = ""
    summary_covers_to = 0
    if _state.agent_db:
        try:
            cursor = await _state.agent_db.execute(
                "SELECT summary, covers_to FROM context_summaries "
                "WHERE conversation_id = ? ORDER BY covers_to DESC LIMIT 1",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            if row:
                existing_summary = row[0]
                summary_covers_to = row[1]
        except Exception as e:
            logger.debug("Failed to load rolling summary: %s", e)

    # Determine how many overflow messages are already covered by existing summary
    new_overflow_start = min(summary_covers_to, len(overflow))
    new_overflow = overflow[new_overflow_start:]

    if not new_overflow and existing_summary:
        # Existing summary covers everything — just prepend it
        summary_msg = {"role": "user", "content": f"[Previous conversation summary]\n{existing_summary}"}
        return [summary_msg] + window

    # Need to summarize new overflow messages
    if new_overflow:
        summary_lines = []
        if existing_summary:
            summary_lines.append(f"Previous summary: {existing_summary}")
            summary_lines.append("")

        for msg in new_overflow:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                if len(content) > 500:
                    content = content[:250] + f"\n[...{len(content) - 500} chars omitted...]\n" + content[-250:]
                summary_lines.append(f"{role}: {content}")
            elif msg.get("tool_calls"):
                calls = msg["tool_calls"]
                if isinstance(calls, list):
                    for tc in calls:
                        fn = tc.get("function", {})
                        summary_lines.append(f"assistant: [called {fn.get('name', '?')}]")

        summary_input = "\n".join(summary_lines)

        try:
            response = await litellm.acompletion(
                model=utility_model,
                messages=[{"role": "user", "content": (
                    f"Summarize this conversation history in {SUMMARY_MAX_WORDS}-{SUMMARY_MAX_WORDS * 2} words. "
                    "Preserve key decisions, file paths, technical details, what was attempted and results. "
                    "Use bullet points. Start directly with content.\n\n"
                    f"{summary_input}"
                )}],
                temperature=0.0,
                max_tokens=1024,
                **extra_kwargs,
            )
            new_summary = response.choices[0].message.content or ""

            # Combine with existing
            if existing_summary:
                full_summary = f"{existing_summary}\n\n{new_summary}"
            else:
                full_summary = new_summary

            # Cache the updated summary
            if _state.agent_db:
                try:
                    from ulid import ULID
                    summary_tokens = _estimate_tokens(full_summary)
                    await _state.agent_db.execute(
                        "INSERT OR REPLACE INTO context_summaries "
                        "(id, conversation_id, tier, covers_from, covers_to, "
                        "original_token_count, summary, summary_token_count, utility_model) "
                        "VALUES (?, ?, 'rolling', 0, ?, ?, ?, ?, ?)",
                        (
                            str(ULID()),
                            conversation_id,
                            len(overflow),
                            _estimate_messages_tokens(overflow),
                            full_summary,
                            summary_tokens,
                            utility_model,
                        ),
                    )
                    await _state.agent_db.commit()
                except Exception as e:
                    logger.debug("Failed to cache rolling summary: %s", e)

            summary_msg = {"role": "user", "content": f"[Previous conversation summary]\n{full_summary}"}
            return [summary_msg] + window

        except Exception as e:
            raise RuntimeError(f"Rolling summary failed — refusing to drop context silently: {e}") from e

    return window


def _advance_cache_breakpoint(messages: list[dict], old_bp_index: int) -> int:
    """Advance Anthropic cache_control breakpoint 2 toward the end of messages.

    Strategy: Only advance the breakpoint when enough new messages
    (>= _CACHE_BP_ADVANCE_THRESHOLD) have accumulated after the current
    breakpoint. This keeps the prefix stable for multiple consecutive LLM
    calls, maximizing cache hits.

    When we DO advance, we move to messages[-2] (second-to-last), so only
    the very latest message pair pays full input price.

    Returns the new breakpoint index.
    """
    if len(messages) < 3:
        return old_bp_index

    new_bp_index = len(messages) - 2

    # Nothing to do if breakpoint hasn't moved
    if new_bp_index == old_bp_index:
        return old_bp_index

    # Only advance if enough messages have accumulated past the breakpoint.
    # Each tool call adds ~2 messages (assistant + tool result). With a
    # threshold of 12, the breakpoint stays stable for ~6 consecutive calls,
    # maximizing cache hits. The uncached tail (messages past the breakpoint)
    # is small relative to the cached prefix, so this is a net win.
    _CACHE_BP_ADVANCE_THRESHOLD = 12
    gap = new_bp_index - old_bp_index
    if gap < _CACHE_BP_ADVANCE_THRESHOLD:
        logger.debug("Cache BP2: holding at index %d (gap=%d < threshold=%d, msgs=%d)",
                      old_bp_index, gap, _CACHE_BP_ADVANCE_THRESHOLD, len(messages))
        return old_bp_index

    logger.info("Cache BP2: advancing %d → %d (gap=%d, msgs=%d)",
                old_bp_index, new_bp_index, gap, len(messages))

    # Clear cache_control from old breakpoint (skip system prompt)
    if old_bp_index > 0 and old_bp_index < len(messages):
        old_msg = messages[old_bp_index]
        if isinstance(old_msg.get("content"), list):
            for block in old_msg["content"]:
                if isinstance(block, dict) and "cache_control" in block:
                    del block["cache_control"]

    # Set cache_control on new breakpoint target
    target = messages[new_bp_index]
    if isinstance(target.get("content"), str):
        # Convert string content to block format (one-time, stable after)
        target["content"] = [{
            "type": "text",
            "text": target["content"],
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(target.get("content"), list):
        last_block = target["content"][-1] if target["content"] else None
        if last_block and isinstance(last_block, dict):
            last_block["cache_control"] = {"type": "ephemeral"}

    return new_bp_index


def _decay_in_loop_tool_results(messages: list[dict], preturn_count: int, *, frozen_up_to: int = 0) -> list[dict]:
    """Compress tool results accumulated during the current turn's tool loop.

    Keeps messages before the turn untouched. For in-turn messages:
    - Last 4 messages (2 tool call/result pairs): verbatim
    - Older tool results: aggressively compressed

    The frozen_up_to parameter protects all messages at indices < frozen_up_to
    from modification (preserves Anthropic prompt cache prefix stability).
    """
    # The compressible zone starts after whichever is later:
    # the pre-turn boundary or the cache-frozen zone
    compress_start = max(preturn_count, frozen_up_to)

    if len(messages) <= compress_start + 4:
        return messages

    frozen = messages[:compress_start]
    compressible = messages[compress_start:]

    # Split: older compressible messages vs recent (last 4)
    older = compressible[:-4]
    recent = compressible[-4:]

    compressed_older = []
    tokens_saved = 0
    for msg in older:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                # Try to extract key info from JSON results
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        summary_parts = []
                        for key in ("path", "file_path", "status", "exit_code", "error", "stdout", "stderr"):
                            if key in parsed and parsed[key]:
                                val = str(parsed[key])
                                if len(val) > 150:
                                    val = val[:75] + "..." + val[-75:]
                                summary_parts.append(f"{key}: {val}")
                        if "content" in parsed:
                            size = len(parsed["content"])
                            summary_parts.append(f"content: [{size} chars]")
                        if "size" in parsed:
                            summary_parts.append(f"size: {parsed['size']}")
                        compressed = "[Compressed] " + "; ".join(summary_parts)
                        tokens_saved += (_estimate_tokens(content) - _estimate_tokens(compressed))
                        compressed_older.append({**msg, "content": compressed})
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

                # Non-JSON: keep first/last lines
                lines = content.splitlines()
                if len(lines) > 10:
                    compressed = "\n".join(lines[:3]) + f"\n[...{len(lines)-6} lines omitted...]\n" + "\n".join(lines[-3:])
                    tokens_saved += (_estimate_tokens(content) - _estimate_tokens(compressed))
                    compressed_older.append({**msg, "content": compressed})
                    continue

        compressed_older.append(msg)

    if tokens_saved > 0:
        logger.info("In-loop decay: compressed %d tokens from older tool results", tokens_saved)

    return frozen + compressed_older + recent


async def _run_agent_loop(
    user_message: str,
    history: list[dict],
    conversation_id: str,
    *,
    event_queue: Any = None,
) -> tuple[str, int]:
    """Run the agent tool-use loop locally.

    Returns (response_text, tool_calls_count).
    Emits SSE events for tool_call and memory via the ``_state.sse_queue``
    which the /turn endpoint generator reads from.
    """
    from backend.app.agent.tools import TOOL_MAP
    from backend.app.agent.tools.native_registry import build_native_registry

    config = _state.config
    model = config["model"]
    system_prompt = config["system_prompt"]
    agent_tools = config["tools"]
    max_iterations = config["max_iterations"]

    # API keys + provider aliases injected from host DB at container launch
    injected_keys: dict[str, str] = config.get("api_keys", {})
    provider_aliases: dict[str, str] = config.get("provider_aliases", {})

    def _resolve_provider(model_id: str) -> str:
        """Resolve model prefix to canonical provider ID using DB aliases."""
        prefix = model_id.split("/")[0] if "/" in model_id else "anthropic"
        return provider_aliases.get(prefix, prefix)

    def _resolve_api_key(model_id: str) -> str | None:
        """Resolve API key: injected from host DB → Vault → env var."""
        prov = _resolve_provider(model_id)

        # 1. Keys from provider_api_keys (injected at container launch)
        key = injected_keys.get(prov)
        if key:
            return key

        # 2. Vault (mounted from host)
        try:
            from backend.app.core.vault import Vault
            vault = Vault()
            key = vault.get_api_key(prov)
            if key:
                return key
        except Exception as e:
            logger.debug("Could not read API key from vault for %s: %s", prov, e)

        # 3. Environment variable
        return os.environ.get(f"{prov.upper()}_API_KEY")

    # Primary model kwargs
    extra_kwargs: dict = {}
    primary_key = _resolve_api_key(model)
    if primary_key:
        extra_kwargs["api_key"] = primary_key

    # Utility model kwargs (may be a different provider)
    utility_model = config.get("utility_model", "claude-sonnet-4-6")
    utility_kwargs: dict = {}
    utility_key = _resolve_api_key(utility_model)
    if utility_key:
        utility_kwargs["api_key"] = utility_key

    # --- Context Distillation Pipeline ---

    # Stage 1: Select relevant fragments via utility model
    fragments = config.get("prompt_fragments", [])
    enabled_fragments = [f for f in fragments if f.get("enabled", True)]
    selected_fragments = await _select_relevant_fragments(
        enabled_fragments, user_message, history, config, utility_kwargs,
    )
    fragment_stats = {"selected": len(selected_fragments), "total": len(enabled_fragments)}
    prompt_parts = [system_prompt] + [f["content"] for f in selected_fragments]
    full_system_prompt = "\n\n".join(prompt_parts)

    # Stage 2: Sliding window — limit history to WINDOW_SIZE + rolling summary
    windowed_history = history
    if history:
        windowed_history = await _apply_sliding_window(
            history, conversation_id, config, utility_kwargs,
        )

    # Stage 3: Progressive decay on tool results
    if windowed_history:
        windowed_history = apply_progressive_decay(windowed_history)

    # Stage 4: Compress remaining history if still over threshold
    compressed_history = windowed_history
    compression_stats = {"original_tokens": 0, "compressed_tokens": 0}
    if windowed_history:
        compressed_history, compression_stats = await _compress_history(
            windowed_history, conversation_id, config, utility_kwargs,
        )

    # Emit compression stats via SSE
    if event_queue is not None and compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD:
        await event_queue.put(_sse_event("status", {
            "state": "context_compressed",
            "original_tokens": compression_stats["original_tokens"],
            "compressed_tokens": compression_stats["compressed_tokens"],
            "tools_pruned": compression_stats.get("tools_pruned", 0),
        }))

    # Log compression audit trail
    await _log_compression_stats(
        conversation_id, 0, compression_stats, fragment_stats,
        config.get("utility_model", "claude-sonnet-4-6"),
    )

    # Determine if the primary model supports Anthropic prompt caching
    _is_anthropic_model = _resolve_provider(model) == "anthropic"

    # Build messages with distilled context
    # Breakpoint 1: system prompt — cached across turns and tool loops
    if _is_anthropic_model:
        messages: list[dict] = [{
            "role": "system",
            "content": [{
                "type": "text",
                "text": full_system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
        }]
    else:
        messages: list[dict] = [{"role": "system", "content": full_system_prompt}]

    if compressed_history:
        messages.extend(compressed_history)
    messages.append({"role": "user", "content": user_message})

    # Build tool definitions + registry with heuristic selection
    registry = build_native_registry()

    # Extract last assistant message for tool selection context
    last_assistant = ""
    for msg in reversed(compressed_history or []):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_assistant = msg["content"]
            break

    # Extract recent tools used from history
    recent_tools: list[str] = []
    for msg in (compressed_history or []):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            calls = msg["tool_calls"]
            if isinstance(calls, list):
                for tc in calls:
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        recent_tools.append(fn["name"])

    selected_tool_names = select_tools(
        user_message=user_message,
        enabled_tools=agent_tools,
        recent_tools_used=recent_tools[-10:] if recent_tools else None,
        last_assistant_content=last_assistant,
    )

    # Use compact schemas to further reduce token usage
    tool_defs = [compact_tool_schema(TOOL_MAP[name]) for name in selected_tool_names if name in TOOL_MAP]

    # Tool context: local agent_db instead of host SQLAlchemy session
    tool_context: dict[str, Any] = {
        "agent_db": _state.agent_db,
        "agent_id": _state.agent_id,
    }

    tool_calls_made = 0
    sse_events: list[str] = []  # collected for the SSE stream

    # Adaptive max_tokens: start low (fast + cheap), escalate on truncation
    # Tiers: 8192 → 32768 → 65536. Reset after each successful completion.
    TOKEN_TIERS = [8192, 32768, 65536]
    current_tier = 0  # index into TOKEN_TIERS
    continuation_attempts = 0  # consecutive continuations for a single response
    MAX_CONTINUATIONS = 3  # max times we'll try to continue a truncated response

    # Repetition detection — break out of loops where agent keeps calling
    # the same tool with similar args
    REPETITION_THRESHOLD = 3  # consecutive similar calls before intervention
    recent_tool_calls: list[tuple[str, str]] = []  # (tool_name, args_hash)

    # Track where the pre-turn messages end so we know which are in-loop
    _preturn_msg_count = len(messages)

    # Track cache breakpoint 2 position for Anthropic prompt caching stability.
    # Initialize to after history + user message (the last pre-turn message).
    _cache_bp2_index = len(messages) - 1

    for _iteration in range(max_iterations):
        # Check interrupt
        if _state.interrupt_event.is_set():
            _state.interrupt_event.clear()
            # Inject pending messages
            for msg in _state.pending_messages:
                messages.append(msg)
            _state.pending_messages.clear()

        # In-loop decay: compress tool results accumulated during this turn.
        # Keep the last 2 tool results verbatim; older ones get progressively decayed.
        # frozen_up_to prevents modifying messages before the cache breakpoint.
        if _iteration > 0 and _iteration % 3 == 0:
            messages = _decay_in_loop_tool_results(messages, _preturn_msg_count, frozen_up_to=_cache_bp2_index)

        current_max_tokens = TOKEN_TIERS[current_tier]
        context_tokens = _estimate_messages_tokens(messages) + _estimate_tokens(json.dumps(tool_defs))

        # Advance prompt cache breakpoint 2 before each call (Anthropic only).
        # Runs on every iteration (including 0) so the first call benefits from
        # caching the system prompt + history + user message prefix.
        if _is_anthropic_model:
            _cache_bp2_index = _advance_cache_breakpoint(messages, _cache_bp2_index)

        logger.info(
            "LLM request: model=%s tools=%d max_tokens=%d tier=%d context_tokens=~%d msgs=%d cache=%s tool_names=%s",
            model, len(tool_defs), current_max_tokens, current_tier,
            context_tokens, len(messages),
            "anthropic" if _is_anthropic_model else "none",
            [t["function"]["name"] for t in tool_defs],
        )

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tool_defs if tool_defs else None,
            temperature=0.7,
            max_tokens=current_max_tokens,
            **extra_kwargs,
        )

        choice = response.choices[0]
        llm_message = choice.message

        # Log cache usage if available (Anthropic returns cache_creation_input_tokens / cache_read_input_tokens)
        usage = getattr(response, "usage", None)
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        logger.info(
            "LLM response: has_tool_calls=%s finish_reason=%s content_len=%d max_tokens=%d "
            "input=%d output=%d cache_read=%d cache_write=%d",
            bool(llm_message.tool_calls),
            choice.finish_reason,
            len(llm_message.content or ""),
            current_max_tokens,
            input_tokens, output_tokens, cache_read, cache_write,
        )

        # Handle finish_reason=length — output was truncated
        if choice.finish_reason == "length":
            continuation_attempts += 1
            partial_content = llm_message.content or ""

            if continuation_attempts > MAX_CONTINUATIONS:
                logger.error(
                    "Aborting after %d continuation attempts — response keeps exceeding token limit",
                    continuation_attempts,
                )
                return (
                    "I hit the output token limit multiple times even at the highest setting. "
                    "This usually happens with very large file writes. Try asking me to write "
                    "the file in smaller sections, or break the task into smaller pieces."
                ), tool_calls_made

            # Escalate to next tier
            if current_tier < len(TOKEN_TIERS) - 1:
                current_tier += 1
                logger.info(
                    "Truncated response — escalating max_tokens to %d (tier %d), attempting continuation %d/%d",
                    TOKEN_TIERS[current_tier], current_tier, continuation_attempts, MAX_CONTINUATIONS,
                )

            # Assistant prefill continuation (like Aider/Claude Code):
            # Append partial response as assistant, ask model to continue
            if partial_content:
                messages.append({"role": "assistant", "content": partial_content})
                messages.append({
                    "role": "user",
                    "content": "Your response was cut off due to the output length limit. Please continue exactly where you left off.",
                })
            else:
                # Truncated with no content (truncated tool call) — retry at higher tier
                logger.warning("Truncated with no content — retrying at higher tier")

            continue  # retry this iteration

        # Successful completion — reset adaptive tokens
        current_tier = 0
        continuation_attempts = 0

        if llm_message.tool_calls:
            # Update last_assistant for tool result filter context
            if llm_message.content:
                last_assistant = llm_message.content
            messages.append(llm_message.model_dump())

            for tool_call in llm_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                tool_calls_made += 1

                # Repetition detection: hash tool name + first 200 chars of args
                args_sig = hashlib.md5(f"{tool_name}:{json.dumps(tool_args)[:200]}".encode()).hexdigest()[:8]
                recent_tool_calls.append((tool_name, args_sig))

                # Check for consecutive repetition
                if len(recent_tool_calls) >= REPETITION_THRESHOLD:
                    last_n = recent_tool_calls[-REPETITION_THRESHOLD:]
                    if all(tc == last_n[0] for tc in last_n):
                        logger.warning(
                            "Repetition detected: %s called %d times with same args — injecting intervention",
                            tool_name, REPETITION_THRESHOLD,
                        )
                        # Execute this tool call, then inject a nudge
                        if tool_name not in agent_tools:
                            result = {"error": f"Tool '{tool_name}' is not enabled."}
                        else:
                            result = await registry.execute(tool_name, tool_args, tool_context)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                f"SYSTEM: You have called '{tool_name}' with the same arguments "
                                f"{REPETITION_THRESHOLD} times in a row. You appear to be in a loop. "
                                "Stop repeating this action. Either try a different approach, "
                                "report what you've found so far, or use the respond tool to "
                                "explain what's blocking you."
                            ),
                        })
                        recent_tool_calls.clear()
                        break  # break inner tool_call loop, continue outer iteration

                logger.info("Tool call [%d]: %s args=%s", tool_calls_made, tool_name,
                            {k: (v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v) for k, v in tool_args.items()})

                # Emit tool_call event for live progress
                if event_queue is not None:
                    await event_queue.put(_sse_event("status", {"state": "tool_calling"}))
                    await event_queue.put(_sse_event("tool_call", {
                        "tool_name": tool_name,
                        "args": {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in tool_args.items()},
                        "tool_calls_made": tool_calls_made,
                    }))

                if tool_name not in agent_tools:
                    result = {"error": f"Tool '{tool_name}' is not enabled."}
                else:
                    # If model called a tool not in the selected set but still enabled,
                    # execute it and add to selected set for future iterations
                    if tool_name not in selected_tool_names:
                        logger.info("Tool %s not in selected set but enabled — adding dynamically", tool_name)
                        selected_tool_names.append(tool_name)
                        if tool_name in TOOL_MAP:
                            tool_defs.append(compact_tool_schema(TOOL_MAP[tool_name]))
                    result = await registry.execute(tool_name, tool_args, tool_context)

                # Smart build output parsing: compress verbose build/test output
                if tool_name == "code_execute" and isinstance(result, dict):
                    from backend.app.agent.build_output_parser import parse_build_output
                    _bstdout = result.get("stdout", "")
                    _bstderr = result.get("stderr", "")
                    _bexit = result.get("exit_code", 0)
                    if len(_bstdout) + len(_bstderr) > 500:
                        _parsed = parse_build_output(_bstdout, _bstderr, _bexit)
                        if _parsed is not None:
                            result = {**result, "stdout": _parsed, "_build_parsed": True}

                logger.info("Tool result [%d]: %s",  tool_calls_made,
                            {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in result.items()} if isinstance(result, dict) else result)

                # Check for promotable memory -> emit SSE memory event
                if "_promote" in result:
                    _state._last_sse_events = getattr(_state, "_last_sse_events", [])
                    _state._last_sse_events.append(("memory", result["_promote"]))
                    del result["_promote"]

                # Check terminal tool
                if result.get("_terminal"):
                    return result.get("message", ""), tool_calls_made

                # Filter large tool results through utility model
                result_json = await filter_tool_result(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    raw_result=result,
                    user_message=user_message,
                    last_assistant_content=last_assistant,
                    utility_model=utility_model,
                    utility_kwargs=utility_kwargs,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                })
        else:
            return llm_message.content or "", tool_calls_made

    # Hit max iterations — save a memory of what was attempted
    try:
        # Build a summary from the tool calls in the conversation
        tool_summary_parts = []
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tool_summary_parts.append(fn.get("name", "unknown"))

        if tool_summary_parts and _state.agent_db:
            from backend.app.agent.tools.native import handle_memory_save
            summary = (
                f"Reached max iterations ({max_iterations}) while working on: "
                f"{messages[1]['content'][:200] if len(messages) > 1 else 'unknown task'}. "
                f"Tools used: {', '.join(tool_summary_parts[:10])}. "
                f"Task may be incomplete."
            )
            await handle_memory_save(
                {"content": summary, "memory_type": "general", "importance": 0.7},
                {"agent_db": _state.agent_db, "agent_id": _state.agent_id},
            )
            logger.info("Saved max-iterations memory: %s", summary[:100])
    except Exception as e:
        logger.warning("Failed to save max-iterations memory: %s", e)

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
