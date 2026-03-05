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
from backend.app.agent.tool_result_filter import filter_tool_result, rule_based_prune
from backend.app.agent.context_pipeline import (
    COMPRESSION_THRESHOLD,
    VERBATIM_MESSAGE_COUNT,
    _estimate_tokens,
    _estimate_messages_tokens,
    _select_relevant_fragments,
    _compress_history,
    _log_compression_stats,
    _apply_sliding_window,
)
from backend.app.agent.cache_manager import (
    _advance_cache_breakpoint,
    _decay_in_loop_tool_results,
)
from backend.app.agent.persistence_client import PersistenceClient
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
        self.persistence: PersistenceClient | None = None
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
    
    # MCP Setup for worker
    try:
        from backend.app.mcp import mcp_manager
        # In worker, we can load servers from the local agent_db if they exist
        if _state.agent_db:
            # We need to wrap the aiosqlite connection in something load_servers_from_db accepts
            # Or just implement a simplified version for aiosqlite
            await _worker_load_mcp_servers(mcp_manager)
    except Exception as e:
        logger.error(f"Failed to load MCP servers in worker: {e}")
        
    yield
    
    # MCP Shutdown
    try:
        from backend.app.mcp import mcp_manager
        await mcp_manager.stop_all()
    except:
        pass
        
    await _shutdown()

async def _worker_load_mcp_servers(manager):
    """Load MCP servers from SpacetimeDB via the Gateway API."""
    from backend.app.mcp import MCPServerConfig
    if not _state.persistence or _state.persistence.mode != "api":
        logger.warning("Cannot load MCP servers: persistence not in API mode")
        return
    try:
        gateway_url = _state.persistence.gateway_url.rstrip("/")
        url = f"{gateway_url}/api/v1/mcp?agent_id={_state.agent_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            servers = resp.json()
        for s in servers:
            config = MCPServerConfig(
                name=s["name"],
                command=s["command"],
                args=s.get("args", []),
                env=s.get("env", {}),
                enabled=s.get("enabled", True),
            )
            await manager.add_server(config)
        logger.info("Loaded %d MCP server(s) from SpacetimeDB", len(servers))
    except Exception as e:
        logger.error("Failed to load MCP servers from Gateway: %s", e)

app = FastAPI(title="Bond Agent Worker", lifespan=_lifespan)

# Module-level manifest cache (updated by /reload)
_prompt_manifest_cache: str | None = None


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


@app.post("/reload")
async def reload_prompts():
    """Called by Gateway after main branch merge to refresh prompt manifest."""
    global _prompt_manifest_cache
    import subprocess as _sp
    from backend.app.agent.tools.dynamic_loader import generate_manifest as _gen_manifest

    # Pull latest from main
    bond_root = Path("/bond")
    try:
        _sp.run(
            ["git", "pull", "origin", "main", "--ff-only"],
            cwd=bond_root, capture_output=True, timeout=30,
        )
    except Exception:
        pass  # Non-fatal — manifest still regenerates from current state

    prompts_dir = bond_root / "prompts"
    if prompts_dir.exists():
        _prompt_manifest_cache = _gen_manifest(prompts_dir)
        count = _prompt_manifest_cache.count(",") + 1
    else:
        count = 0

    return {"ok": True, "categories": count}




def _discover_workspace() -> str | None:
    """Set process cwd to /workspace (if mounted) and return a listing for the system prompt.

    All file tools (file_read, file_edit, file_write, code_execute) share the same
    working directory, so relative paths are consistent across every tool call.
    """
    workspace = Path("/workspace")
    if not workspace.exists():
        return None
    try:
        # Set process cwd so relative paths in file_read/file_edit/file_write
        # resolve the same way as code_execute (which uses cwd=/workspace explicitly).
        os.chdir(workspace)
        entries = sorted(p.name for p in workspace.iterdir() if not p.name.startswith("."))
        if not entries:
            return None
        listing = ", ".join(entries)
        return (
            "## Workspace\n"
            f"Working directory: {workspace}\n"
            "Contents: " + listing + "\n"
            "Use relative paths for file operations (e.g. DecorApps/Foo/Bar.cs). "
            "All tools share this working directory."
        )
    except OSError:
        return None


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
    plan_id = body.get("plan_id", "")

    import asyncio
    event_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def run_loop():
        try:
            response_text, tool_calls_made = await _run_agent_loop(
                message, history, conversation_id, event_queue=event_queue, plan_id=plan_id,
            )
            
            # Persist assistant response
            if _state.persistence:
                try:
                    await _state.persistence.save_message(
                        session_id=conversation_id,
                        role="assistant",
                        content=response_text,
                        agent_db=_state.agent_db,
                    )
                except Exception as e:
                    logger.error("Failed to persist assistant message: %s", e)

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


async def _run_agent_loop(
    user_message: str,
    history: list[dict],
    conversation_id: str,
    *,
    event_queue: Any = None,
    plan_id: str = "",
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
        # First, check if model_id has provider/model format
        if "/" in model_id:
            prefix = model_id.split("/")[0]
            return provider_aliases.get(prefix, prefix)
        
        # Check if model_id starts with any known alias
        # Common patterns: gemini-..., claude-..., gpt-..., o1-..., o3-..., o4-...
        model_lower = model_id.lower()
        for alias in provider_aliases:
            if model_lower.startswith(alias.lower() + "-"):
                return provider_aliases.get(alias, alias)
        
        # Default to anthropic for backward compatibility
        return "anthropic"

    async def _resolve_api_key(model_id: str) -> str | None:
        """Resolve API key: injected from host DB → SpacetimeDB → Vault → env var."""
        prov = _resolve_provider(model_id)

        # 1. Keys from provider_api_keys (injected at container launch)
        key = injected_keys.get(prov)
        if key:
            return key

        # 2. SpacetimeDB via Gateway (encrypted API keys)
        try:
            if _state.persistence and _state.persistence.mode == "api":
                encrypted_key = await _state.persistence.get_provider_api_key(prov)
                if encrypted_key:
                    # Decrypt the key using the crypto module
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_key)
                    if decrypted and decrypted != encrypted_key:  # Check if decryption worked
                        logger.debug("Got API key for %s from SpacetimeDB", prov)
                        return decrypted
        except Exception as e:
            logger.debug("Could not read API key from SpacetimeDB for %s: %s", prov, e)

        # 3. Vault (mounted from host)
        try:
            from backend.app.core.vault import Vault
            vault = Vault()
            key = vault.get_api_key(prov)
            if key:
                return key
        except Exception as e:
            logger.debug("Could not read API key from vault for %s: %s", prov, e)

        # 4. Environment variable
        env_key = os.environ.get(f"{prov.upper()}_API_KEY")
        if env_key:
            return env_key
        
        # Special case: Google provider can use GEMINI_API_KEY
        if prov == "google":
            return os.environ.get("GEMINI_API_KEY")
        
        return None

    # Primary model kwargs
    extra_kwargs: dict = {}
    primary_key = await _resolve_api_key(model)
    if primary_key:
        extra_kwargs["api_key"] = primary_key

    # Utility model kwargs (may be a different provider)
    utility_model = config.get("utility_model", "claude-sonnet-4-6")
    utility_kwargs: dict = {}
    utility_key = await _resolve_api_key(utility_model)
    if utility_key:
        utility_kwargs["api_key"] = utility_key

    # --- Crash Recovery: check for active work plans ---
    _has_active_plan = False
    _active_plan_id: str | None = None
    try:
        from backend.app.agent.tools.work_plan import load_active_plan, format_plan_context, format_recovery_context
        active_plan = await load_active_plan(_state.agent_db, _state.agent_id, conversation_id=conversation_id, plan_id=plan_id)
        if active_plan:
            _has_active_plan = True
            _active_plan_id = active_plan["id"]

            # Always inject plan context with real IDs so the agent never hallucinates them.
            # Use recovery format if the plan has in-progress items (resuming interrupted work),
            # otherwise use the compact ID-focused format.
            in_progress = [i for i in active_plan.get("items", []) if i["status"] == "in_progress"]
            if in_progress:
                plan_ctx = format_recovery_context(active_plan) + "\n\n" + format_plan_context(active_plan)
            else:
                plan_ctx = format_plan_context(active_plan)

            # Inject as a system message prefix so the agent always has IDs in context
            history = [{"role": "user", "content": plan_ctx}] + history
            logger.info("Injected active plan context for plan %s (%d items)", _active_plan_id, len(active_plan.get("items", [])))
    except Exception as e:
        logger.debug("Work plan context injection skipped: %s", e)

    # --- Context Distillation Pipeline ---

    # Stage 1: Select relevant fragments via utility model
    fragments = config.get("prompt_fragments", [])
    enabled_fragments = [f for f in fragments if f.get("enabled", True)]
    
    # Concurrent Context Retrieval: Retrieve relevant fragments and memory search in parallel
    async def _fetch_fragments():
        return await _select_relevant_fragments(
            enabled_fragments, user_message, history, config, utility_kwargs,
        )

    async def _fetch_memory():
        # Quick search for related context
        try:
            from backend.app.agent.tools.native import handle_search_memory
            res = await handle_search_memory(
                {"query": user_message, "limit": 3},
                {"agent_db": _state.agent_db}
            )
            return res.get("results", [])
        except Exception:
            return []

    context_results = await asyncio.gather(_fetch_fragments(), _fetch_memory())
    selected_fragments = context_results[0]
    recent_memories = context_results[1]
    
    fragment_stats = {"selected": len(selected_fragments), "total": len(enabled_fragments)}
    prompt_parts = [system_prompt] + [f["content"] for f in selected_fragments]
    
    # Inject relevant memories directly into the system prompt prefix
    if recent_memories:
        mem_text = "\n".join([f"- {m['content']}" for m in recent_memories])
        prompt_parts.append(f"## Relevant Memories\n{mem_text}")
        
    full_system_prompt = "\n\n".join(prompt_parts)

    # Inject prompt hierarchy: universal fragments + manifest into system prompt
    from backend.app.agent.tools.dynamic_loader import generate_manifest, load_universal_fragments
    import backend.app.worker as _worker_module

    _prompts_dir = Path("/bond/prompts")
    if not _prompts_dir.exists():
        # dev fallback
        _prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

    _manifest = _worker_module._prompt_manifest_cache
    if _manifest is None:
        _manifest = generate_manifest(_prompts_dir)
        _worker_module._prompt_manifest_cache = _manifest

    # Universal fragments are always-on guidelines — inject once into system prompt
    # so the agent has them without burning a tool call. load_context only loads
    # task-specific category chains on top of these.
    _universal = load_universal_fragments(_prompts_dir)
    if _universal:
        full_system_prompt = full_system_prompt + "\n\n" + _universal

    if _manifest:
        full_system_prompt = full_system_prompt + "\n\n" + _manifest

    # Set process cwd to /workspace so file_read/file_edit/file_write resolve
    # relative paths the same way code_execute does.
    _workspace_ctx = _discover_workspace()
    if _workspace_ctx:
        full_system_prompt = full_system_prompt + "\n\n" + _workspace_ctx

    # Stage 2: Sliding window — limit history to WINDOW_SIZE + rolling summary
    windowed_history = history
    if history:
        windowed_history = await _apply_sliding_window(
            history, conversation_id, config, utility_kwargs,
            agent_db=_state.agent_db,
        )

    # Stage 3: Progressive decay on tool results
    # Only decay messages that will remain verbatim — messages above the
    # compression threshold will be summarized anyway, so decaying them
    # is wasted work (and the decay output gets discarded).
    if windowed_history:
        total_tokens = sum(_estimate_tokens(m.get("content", "")) for m in windowed_history)
        if total_tokens >= COMPRESSION_THRESHOLD and len(windowed_history) > VERBATIM_MESSAGE_COUNT:
            # Only decay the verbatim tail — the rest will be compressed
            head = windowed_history[:-VERBATIM_MESSAGE_COUNT]
            tail = windowed_history[-VERBATIM_MESSAGE_COUNT:]
            tail = apply_progressive_decay(tail)
            windowed_history = head + tail
        else:
            windowed_history = apply_progressive_decay(windowed_history)

    # Stage 4: Compress remaining history if still over threshold
    compressed_history = windowed_history
    compression_stats = {"original_tokens": 0, "compressed_tokens": 0}
    if windowed_history:
        compressed_history, compression_stats = await _compress_history(
            windowed_history, conversation_id, config, utility_kwargs,
            agent_db=_state.agent_db,
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
        agent_db=_state.agent_db,
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
    
    # Persist user message
    if _state.persistence:
        try:
            await _state.persistence.save_message(
                session_id=conversation_id,
                role="user",
                content=user_message,
                agent_db=_state.agent_db,
            )
        except Exception as e:
            logger.error("Failed to persist user message: %s", e)

    # Build tool definitions + registry with heuristic selection
    registry = build_native_registry()
    
    # Refresh MCP tools
    try:
        from backend.app.mcp import mcp_manager
        await mcp_manager.refresh_tools(registry)
        # Add any mcp tools to the enabled set for heuristic selection
        for name in registry.registered_names:
            if name.startswith("mcp_") and name not in agent_tools:
                agent_tools.append(name)
    except Exception as e:
        logger.error(f"Failed to refresh MCP tools in worker loop: {e}")

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
        has_active_plan=_has_active_plan,
    )

    # Use compact schemas to further reduce token usage
    tool_defs = [compact_tool_schema(TOOL_MAP[name]) for name in selected_tool_names if name in TOOL_MAP]

    # Tool context: local agent_db instead of host SQLAlchemy session
    tool_context: dict[str, Any] = {
        "agent_db": _state.agent_db,
        "agent_id": _state.agent_id,
        "conversation_id": conversation_id,
    }

    tool_calls_made = 0
    sse_events: list[str] = []  # collected for the SSE stream

    # File re-read dedup: avoid wasting tokens on identical file content
    _file_read_cache: dict[str, dict] = {}  # path -> {"content_hash": str, "tool_call_num": int, "total_lines": int, "size": int}

    # Adaptive max_tokens: start low (fast + cheap), escalate on truncation
    # Tiers: 32768 → 65536. Reset after each successful completion.
    TOKEN_TIERS = [32768, 65536]
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
        # Check interrupt: if pending messages, inject them and continue.
        # If no messages, this is a pure pause signal — break the loop.
        if _state.interrupt_event.is_set():
            _state.interrupt_event.clear()
            if _state.pending_messages:
                for msg in _state.pending_messages:
                    messages.append(msg)
                _state.pending_messages.clear()
            else:
                logger.info("Agent loop paused by interrupt signal (no pending messages)")
                if event_queue:
                    await event_queue.put(_sse_event("status", {"state": "paused"}))
                break

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

        # Token budget injection: append brief context to the last tool result
        # so the agent can self-regulate. Only appended to the latest (non-cached)
        # message, so it doesn't affect cache stability. ~15 tokens overhead.
        _budget_note = ""
        _budget_target_idx = -1
        if _iteration > 0 and messages and messages[-1].get("role") == "tool":
            _budget_note = f"\n[Turn {_iteration + 1}/{max_iterations} | ~{context_tokens} tokens | {tool_calls_made} tool calls]"
            _budget_target_idx = len(messages) - 1
            content = messages[_budget_target_idx].get("content", "")
            if isinstance(content, str):
                messages[_budget_target_idx]["content"] = content + _budget_note

        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tool_defs if tool_defs else None,
            temperature=0.7,
            max_tokens=current_max_tokens,
            **extra_kwargs,
        )

        # Strip budget note from the tool result after the LLM call
        if _budget_note and _budget_target_idx >= 0:
            content = messages[_budget_target_idx].get("content", "")
            if isinstance(content, str) and content.endswith(_budget_note):
                messages[_budget_target_idx]["content"] = content[:-len(_budget_note)]

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
                    
                    start_ts = time.time()
                    result = await registry.execute(tool_name, tool_args, tool_context)
                    duration = time.time() - start_ts
                    
                    # Persist tool log
                    if _state.persistence:
                        try:
                            await _state.persistence.log_tool(
                                session_id=conversation_id,
                                tool_name=tool_name,
                                input=tool_args,
                                output=result,
                                duration=duration,
                                agent_db=_state.agent_db,
                            )
                        except Exception as e:
                            logger.error("Failed to persist tool log: %s", e)

                # Emit any SSE events from tool results (e.g., plan/item updates)
                if isinstance(result, dict) and "_sse_event" in result and event_queue is not None:
                    sse = result.pop("_sse_event")
                    await event_queue.put(_sse_event(sse["event"], sse.get("data", {})))

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

                # File re-read dedup: replace duplicate unchanged reads with a short reference.
                # Tracks full file content by path. If the agent re-reads the same file
                # (or a line range of it), we check against the cached content.
                if tool_name == "file_read" and isinstance(result, dict) and "error" not in result:
                    _fpath = result.get("path", result.get("file_path", ""))
                    _fcontent = result.get("content", "")
                    _flines_returned = result.get("total_lines", 0)
                    _fsize = result.get("size", len(_fcontent) if isinstance(_fcontent, str) else 0)
                    _fline_start = result.get("line_start")
                    _fline_end = result.get("line_end")
                    _is_partial = _fline_start is not None or _fline_end is not None
                    _is_outline = bool(result.get("outline"))

                    if _fpath and _fpath in _file_read_cache:
                        cached = _file_read_cache[_fpath]
                        # For full reads: hash-compare content
                        # For partial/line-range reads: if we already read the full file, dedup
                        # For outline reads: always dedup if we have the file cached
                        if _is_outline:
                            result = {
                                "note": f"File already read at tool call #{cached['tool_call_num']} ({cached['total_lines']} lines, {cached['size']} bytes). Outline not needed — you already have the full content.",
                                "path": _fpath,
                                "total_lines": cached["total_lines"],
                            }
                            logger.info("File re-read dedup (outline): %s", _fpath)
                        elif _is_partial and cached.get("has_full_content"):
                            # Agent is re-reading a range of a file it already read in full
                            result = {
                                "note": f"File already read in full at tool call #{cached['tool_call_num']} ({cached['total_lines']} lines). You already have lines {_fline_start or 1}-{_fline_end or cached['total_lines']} from that read.",
                                "path": _fpath,
                                "total_lines": cached["total_lines"],
                            }
                            logger.info("File re-read dedup (partial of full): %s lines %s-%s", _fpath, _fline_start, _fline_end)
                        elif not _is_partial:
                            # Full read — hash compare
                            _fhash = hashlib.md5(_fcontent.encode(errors="replace")).hexdigest() if isinstance(_fcontent, str) else ""
                            if cached["content_hash"] == _fhash:
                                result = {
                                    "note": f"File already read at tool call #{cached['tool_call_num']} (unchanged, {cached['total_lines']} lines, {cached['size']} bytes).",
                                    "path": _fpath,
                                    "total_lines": cached["total_lines"],
                                }
                                logger.info("File re-read dedup (full, unchanged): %s saved ~%d tokens", _fpath, _fsize // 4)
                            else:
                                # File changed — update cache
                                _ftotal = _fcontent.count("\n") + 1 if isinstance(_fcontent, str) and _fcontent else 0
                                _file_read_cache[_fpath] = {"content_hash": _fhash, "tool_call_num": tool_calls_made, "total_lines": _ftotal, "size": _fsize, "has_full_content": True}
                        # else: partial read of a file we only have partial cache for — allow it through
                    elif _fpath:
                        _fhash = hashlib.md5(_fcontent.encode(errors="replace")).hexdigest() if isinstance(_fcontent, str) else ""
                        _ftotal = _flines_returned if _flines_returned else (_fcontent.count("\n") + 1 if isinstance(_fcontent, str) and _fcontent else 0)
                        _file_read_cache[_fpath] = {"content_hash": _fhash, "tool_call_num": tool_calls_made, "total_lines": _ftotal, "size": _fsize, "has_full_content": not _is_partial}

                # Invalidate file read cache on successful file_edit/file_write
                if tool_name in ("file_edit", "file_write") and isinstance(result, dict) and "error" not in result:
                    _epath = result.get("path", result.get("file_path", tool_args.get("path", "")))
                    if _epath and _epath in _file_read_cache:
                        del _file_read_cache[_epath]

                logger.info("Tool result [%d]: %s",  tool_calls_made,
                            {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in result.items()} if isinstance(result, dict) else result)

                # Check for promotable memory -> emit SSE memory event
                if "_promote" in result:
                    _state._last_sse_events = getattr(_state, "_last_sse_events", [])
                    _state._last_sse_events.append(("memory", result["_promote"]))
                    del result["_promote"]

                # Work plan SSE events -> emit to event queue
                if "_sse_event" in result:
                    sse_evt = result.pop("_sse_event")
                    if event_queue is not None:
                        await event_queue.put(_sse_event(sse_evt["event"], sse_evt["data"]))
                    # Track active plan for tool selection
                    if sse_evt["event"] == "plan_created":
                        _has_active_plan = True
                        _active_plan_id = sse_evt["data"].get("plan_id")
                        # Ensure work_plan stays in tool set
                        if "work_plan" not in selected_tool_names and "work_plan" in agent_tools:
                            selected_tool_names.append("work_plan")
                            if "work_plan" in TOOL_MAP:
                                tool_defs.append(compact_tool_schema(TOOL_MAP["work_plan"]))

                # Check terminal tool
                if result.get("_terminal"):
                    return result.get("message", ""), tool_calls_made

                # Rule-based pruning (no LLM call) before utility model filter
                pruned = rule_based_prune(tool_name, tool_args, result)
                if pruned is not None:
                    result_json = json.dumps(pruned)
                else:
                    # Fall through to utility model filter
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

    # Hit max iterations — save work plan checkpoint if active
    if _has_active_plan and _state.agent_db:
        try:
            from backend.app.agent.tools.work_plan import checkpoint_active_plan
            saved = await checkpoint_active_plan(
                _state.agent_db, _state.agent_id,
                f"Max iterations ({max_iterations}) reached — saving checkpoint. "
                f"Tool calls made: {tool_calls_made}.",
            )
            if saved:
                logger.info("Work plan checkpoint saved at max iterations")
        except Exception as e:
            logger.warning("Failed to save work plan checkpoint: %s", e)

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

    # Run the hardcoded schema first (CREATE IF NOT EXISTS — safe to re-run)
    await db.executescript(_AGENT_DB_SCHEMA)
    await db.commit()

    # Run migration scripts from /bond/migrations/ (same scripts as host DB).
    # Each migration is wrapped in a try/except so already-applied migrations
    # (tables that exist from _AGENT_DB_SCHEMA or prior runs) are skipped.
    migrations_dir = Path("/bond/migrations")
    if not migrations_dir.exists():
        # Fallback: check relative to this file (for non-container environments)
        migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"
    if migrations_dir.exists():
        migration_files = sorted(f for f in migrations_dir.iterdir() if f.name.endswith(".up.sql"))
        for mf in migration_files:
            try:
                sql = mf.read_text()
                await db.executescript(sql)
                logger.debug("Migration applied to agent.db: %s", mf.name)
            except Exception as e:
                logger.debug("Migration skipped (already applied): %s — %s", mf.name, e)
        await db.commit()
        logger.info("Agent DB migrations complete: %d scripts processed", len(migration_files))
    else:
        logger.warning("Migrations directory not found, skipping agent DB migrations")

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
    
    # Initialize persistence client (auto-detects mode if not configured)
    _state.persistence = PersistenceClient(agent_id=_state.agent_id)
    await _state.persistence.init()
    
    # ── Parallel SpacetimeDB Schema Reflection ──
    try:
        if _state.persistence and _state.persistence.mode == "api":
            logger.info("Starting parallel SpacetimeDB schema reflection...")
            # We concurrently fetch tables and reducers to orient the agent faster
            async def _fetch_schema(endpoint: str):
                try:
                    url = f"{_state.persistence.gateway_url}/api/v1/spacetimedb/{endpoint}"
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        resp = await client.get(url)
                        return resp.json() if resp.status_code == 200 else None
                except Exception:
                    return None

            schema_tasks = [
                _fetch_schema("tables"),
                _fetch_schema("reducers")
            ]
            schema_results = await asyncio.gather(*schema_tasks)
            _state.spacetimedb_schema = {
                "tables": schema_results[0],
                "reducers": schema_results[1]
            }
            logger.info("SpacetimeDB schema reflection complete: %d tables, %d reducers", 
                        len(_state.spacetimedb_schema["tables"] or []),
                        len(_state.spacetimedb_schema["reducers"] or []))
    except Exception as e:
        logger.warning("Failed parallel schema reflection: %s", e)
    
    logger.info(
        "Agent worker initialized: agent_id=%s persistence_mode=%s",
        _state.agent_id, _state.persistence.mode,
    )


async def _shutdown() -> None:
    """Clean up on shutdown."""
    if _state.persistence:
        await _state.persistence.close()
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
