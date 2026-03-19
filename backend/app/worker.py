"""Agent Worker — standalone FastAPI app that runs inside a container.

Provides /turn (SSE), /interrupt, and /health endpoints.  Runs the agent
loop locally with native tool handlers and a local aiosqlite database.

Usage::

    python -m bond.agent.worker --port 18791 --config /config/agent.json --data-dir /data
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import hashlib

import aiosqlite
import sqlite_vec
import httpx
import litellm
from litellm.cost_calculator import completion_cost as _litellm_completion_cost

from backend.app.agent.context_decay import apply_progressive_decay
from backend.app.agent.lifecycle import (
    LifecycleState,
    Phase,
    detect_phase,
    format_lifecycle_injection,
    format_precommit_injection,
    is_git_commit_command,
    is_git_push_command,
    is_pr_create_command,
    load_lifecycle_fragments,
)
from backend.app.agent.tool_selection import select_tools, compact_tool_schema
from backend.app.agent.tool_result_filter import filter_tool_result, rule_based_prune
from backend.app.agent.context_pipeline import (
    COMPRESSION_THRESHOLD,
    VERBATIM_MESSAGE_COUNT,
    _estimate_tokens,
    _estimate_messages_tokens,
    _compress_history,
    _log_compression_stats,
    _apply_sliding_window,
)
from backend.app.agent.cache_manager import (
    _advance_cache_breakpoint,
)
from backend.app.agent.parallel_worker import (
    ParallelWorkerPool,
    classify_tool_call,
    format_parallel_summary,
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

-- Doc 049: Closed-loop optimization engine — observation store
CREATE TABLE IF NOT EXISTS optimization_observations (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    task_category TEXT,
    user_message_preview TEXT,
    signals_json TEXT NOT NULL,
    outcome_score REAL NOT NULL,
    config_snapshot_json TEXT,
    active_lessons_hash TEXT,
    cohort TEXT DEFAULT 'control'
);

CREATE INDEX IF NOT EXISTS idx_oo_conv
    ON optimization_observations(conversation_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_oo_score
    ON optimization_observations(outcome_score);

-- Doc 049: Lesson candidates (replaces candidates.jsonl)
CREATE TABLE IF NOT EXISTS optimization_candidates (
    id TEXT PRIMARY KEY,
    lesson_text TEXT NOT NULL,
    source_observation_id TEXT REFERENCES optimization_observations(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    similar_count INTEGER DEFAULT 0,
    promoted BOOLEAN DEFAULT FALSE,
    promoted_at TEXT
);

-- Doc 049: Parameter experiments
CREATE TABLE IF NOT EXISTS optimization_experiments (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    param_key TEXT NOT NULL,
    baseline_value TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    rationale TEXT,
    status TEXT DEFAULT 'proposed',
    control_obs_count INTEGER DEFAULT 0,
    experiment_obs_count INTEGER DEFAULT 0,
    control_mean_score REAL,
    experiment_mean_score REAL,
    p_value REAL,
    concluded_at TEXT,
    conclusion TEXT
);

CREATE INDEX IF NOT EXISTS idx_oe_status
    ON optimization_experiments(status);

-- Doc 050: Parameter change history (rollback support)
CREATE TABLE IF NOT EXISTS optimization_param_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    param_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    experiment_id TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_param_history_key
    ON optimization_param_history(param_key, changed_at DESC);

-- Doc 050: Performance indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_obs_created_at
    ON optimization_observations(created_at);
CREATE INDEX IF NOT EXISTS idx_obs_category_created
    ON optimization_observations(task_category, created_at);
CREATE INDEX IF NOT EXISTS idx_obs_cohort
    ON optimization_observations(cohort);
CREATE INDEX IF NOT EXISTS idx_candidates_promoted
    ON optimization_candidates(promoted);

-- Doc 049: Vec0 tables for semantic search (created by capabilities.py
-- for the knowledge DB, but also needed in the agent DB)
CREATE VIRTUAL TABLE IF NOT EXISTS optimization_observations_vec
    USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[1024]);

CREATE VIRTUAL TABLE IF NOT EXISTS optimization_candidates_vec
    USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[1024]);
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
# Cancellable LLM call (Design doc 037 §5.2.1)
# ---------------------------------------------------------------------------


async def _cancellable_llm_call(
    interrupt_event: asyncio.Event,
    **kwargs: Any,
) -> Any | None:
    """Run LLM call but abort if interrupt_event fires.

    Returns the LiteLLM response on success, or None if interrupted.
    The caller should check for None and handle graceful exit.
    """
    llm_task = asyncio.create_task(litellm.acompletion(**kwargs))
    interrupt_task = asyncio.create_task(interrupt_event.wait())

    done, pending = await asyncio.wait(
        {llm_task, interrupt_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if llm_task in done:
        return llm_task.result()
    else:
        # Interrupted — LLM call was cancelled
        logger.info("LLM call interrupted by user")
        return None


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

    # Kill any active coding agent sub-processes
    try:
        from backend.app.agent.tools.coding_agent import kill_all_coding_agents
        killed = await kill_all_coding_agents()
        if killed:
            logger.info("Killed %d active coding agent(s) on shutdown", killed)
    except Exception:
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


@app.get("/coding-agent/status")
async def coding_agent_status_endpoint():
    """Return status of all active coding agent sessions."""
    from backend.app.agent.tools.coding_agent import get_coding_agent_status
    return get_coding_agent_status()


@app.get("/coding-agent/status/{agent_id}")
async def coding_agent_status_by_id(agent_id: str):
    """Return status of a specific coding agent session."""
    from backend.app.agent.tools.coding_agent import get_coding_agent_status
    return get_coding_agent_status(agent_id)


@app.get("/coding-agent/events/{conversation_id}")
async def coding_agent_events(conversation_id: str) -> StreamingResponse:
    """SSE stream of incremental git diffs from an active coding agent.

    The gateway subscribes to this endpoint when a coding agent is active.
    Events: diff (per-file), done (summary), error, keepalive.
    """
    from backend.app.agent.tools.coding_agent import get_session_by_conversation

    session = get_session_by_conversation(conversation_id)
    if not session:
        return StreamingResponse(
            iter([_sse_event("error", {"message": "No active coding agent for this conversation"})]),
            media_type="text/event-stream",
        )

    async def event_stream():
        # Initial status
        yield _sse_event("coding_agent_started", {
            "agent_type": session.agent_type,
            "baseline": session.baseline_commit[:8],
            "conversation_id": conversation_id,
        })

        while True:
            try:
                event = await asyncio.wait_for(session.event_queue.get(), timeout=15)
            except asyncio.TimeoutError:
                # Keepalive — prevents connection timeout
                yield _sse_event("keepalive", {"elapsed": round(session.process.elapsed, 1)})
                continue

            if event is None:
                # Sentinel — stream is done
                break

            event_type = event.get("type", "unknown")

            if event_type == "diff":
                yield _sse_event("coding_agent_diff", {
                    "file": event["file"],
                    "diff": event["diff"],
                    "conversation_id": conversation_id,
                })
            elif event_type == "done":
                yield _sse_event("coding_agent_done", {
                    "status": event["status"],
                    "exit_code": event["exit_code"],
                    "elapsed_seconds": event["elapsed_seconds"],
                    "summary": event["summary"],
                    "git_stat": event.get("git_stat", ""),
                    "conversation_id": conversation_id,
                })
            elif event_type == "output":
                yield _sse_event("coding_agent_output", {
                    "text": event["text"],
                    "conversation_id": conversation_id,
                })
            elif event_type == "error":
                yield _sse_event("coding_agent_error", {
                    "message": event["message"],
                    "conversation_id": conversation_id,
                })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
            
            # Note: Assistant message is persisted by the backend (turn_stdb.py)
            # to avoid duplicate saves. The backend calls add_conversation_message
            # after receiving the response from the worker.

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
    from backend.app.agent.skills_tracker import SkillTracker

    _skill_tracker = SkillTracker()

    config = _state.config
    model = config["model"]
    system_prompt = config["system_prompt"] or "You are a helpful AI assistant."
    max_iterations = config["max_iterations"]

    # Use all registered tools. The DB tools field is legacy — tool
    # availability is now controlled by tool_selection.py heuristics
    # and runtime gating (Permission Broker, API key presence, etc.).
    agent_tools = list(TOOL_MAP.keys())

    # Gate deployment tools to deploy-* agents only (Design Doc 039)
    agent_name = config.get("name", "")
    DEPLOY_ONLY_TOOLS = {"deploy_action", "deployment_query", "file_bug_ticket"}
    if not agent_name.startswith("deploy-"):
        agent_tools = [t for t in agent_tools if t not in DEPLOY_ONLY_TOOLS]

    # API keys + provider aliases injected from host DB at container launch
    injected_keys: dict[str, str] = config.get("api_keys", {})
    provider_aliases: dict[str, str] = config.get("provider_aliases", {})
    # provider ID → litellm prefix (e.g., {"google": "gemini", "anthropic": "anthropic"})
    litellm_prefixes: dict[str, str] = config.get("litellm_prefixes", {})

    def _normalize_model_for_litellm(model_id: str) -> str:
        """Normalize model string so litellm recognizes the provider prefix.

        If the model uses a provider ID as prefix (e.g., 'google/gemini-2.5-flash'),
        replace it with the litellm prefix (e.g., 'gemini/gemini-2.5-flash').
        """
        if "/" not in model_id or not litellm_prefixes:
            return model_id
        prefix, rest = model_id.split("/", 1)
        if prefix in litellm_prefixes and litellm_prefixes[prefix] != prefix:
            normalized = f"{litellm_prefixes[prefix]}/{rest}"
            logger.info("Normalized model %s → %s for litellm", model_id, normalized)
            return normalized
        return model_id

    # Normalize model strings so litellm recognizes the provider prefix.
    model = _normalize_model_for_litellm(model)

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
        logger.error("DEBUG: Resolving API key for provider: %s (model: %s)", prov, model_id)

        # 1. Keys from provider_api_keys (injected at container launch)
        key = injected_keys.get(prov)
        if key:
            logger.error("DEBUG: Got API key for %s from injected_keys (length: %d, starts with: %s)", 
                       prov, len(key), key[:10] if len(key) > 10 else key)
            return key
        else:
            logger.error("DEBUG: No API key for %s in injected_keys", prov)

        # 2. SpacetimeDB via Gateway (encrypted API keys)
        try:
            if _state.persistence and _state.persistence.mode == "api":
                logger.error("DEBUG: Trying to get API key for %s from SpacetimeDB (mode: api)", prov)
                
                # Try provider_api_keys table first
                encrypted_key = await _state.persistence.get_provider_api_key(prov)
                if not encrypted_key and prov == "gemini":
                    # Try "google" as fallback for gemini models
                    logger.error("DEBUG: No key found for provider 'gemini', trying 'google' as fallback")
                    encrypted_key = await _state.persistence.get_provider_api_key("google")
                
                if encrypted_key:
                    logger.error("DEBUG: Got encrypted key for %s from provider_api_keys table (encrypted length: %d, starts with: %s)", 
                               prov, len(encrypted_key), encrypted_key[:20])
                    # Decrypt the key using the crypto module
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_key)
                    logger.error("DEBUG: Decrypted key for %s (length: %d, starts with: %s, is_encrypted: %s)", 
                               prov, len(decrypted), decrypted[:10] if len(decrypted) > 10 else decrypted, 
                               encrypted_key.startswith("enc:"))
                    if decrypted and decrypted != encrypted_key:  # Check if decryption worked
                        # Trim whitespace from the key
                        decrypted = decrypted.strip()
                        logger.error("DEBUG: Got API key for %s from SpacetimeDB provider_api_keys (length: %d, starts with: %s)", 
                                   prov, len(decrypted), decrypted[:10] if len(decrypted) > 10 else decrypted)
                        return decrypted
                    else:
                        logger.error("DEBUG: Decryption failed or returned same value for %s", prov)
                
                # Try provider_api_keys table for LLM API keys {provider}
                logger.error("DEBUG: Trying provider_api_keys table with key: %s", prov)
                encrypted_llm_key = await _state.persistence.get_provider_api_key(prov)
                if not encrypted_llm_key and prov == "gemini":
                    # Try "google" as fallback for gemini models
                    logger.error("DEBUG: No llm.api_key.gemini setting found, trying google")
                    encrypted_llm_key = await _state.persistence.get_provider_api_key("google")

                    
                logger.error("DEBUG: encrypted_llm_key: %s", encrypted_llm_key)
                
                if encrypted_llm_key:
                    logger.error("DEBUG: Got encrypted key for %s from settings table (encrypted length: %d)", prov, len(encrypted_llm_key))
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_llm_key)
                    if decrypted and decrypted != encrypted_llm_key:
                        # Trim whitespace from the key
                        decrypted = decrypted.strip()
                        logger.error("DEBUG: Got API key for %s from SpacetimeDB settings (llm.api_key) (length: %d)", prov, len(decrypted))
                        return decrypted
                
                # Try settings table for embedding API keys (embedding.api_key.{provider})
                # Note: Google provider uses gemini for embedding
                if prov == "google":
                    embedding_key_name = "embedding.api_key.gemini"
                    logger.debug("Trying embedding API key with key: %s", embedding_key_name)
                    embedding_key = await _state.persistence.get_setting(embedding_key_name)
                    if embedding_key:
                        logger.debug("Got encrypted embedding key for google/gemini (encrypted length: %d)", len(embedding_key))
                        from backend.app.core.crypto import decrypt_value
                        decrypted = decrypt_value(embedding_key)
                        if decrypted and decrypted != embedding_key:
                            # Trim whitespace from the key
                            decrypted = decrypted.strip()
                            logger.debug("Got embedding API key for google/gemini from SpacetimeDB settings (length: %d)", len(decrypted))
                            return decrypted
            else:
                logger.debug("Not trying SpacetimeDB (persistence: %s, mode: %s)", 
                           _state.persistence, _state.persistence.mode if _state.persistence else "none")
        except Exception as e:
            logger.debug("Could not read API key from SpacetimeDB for %s: %s", prov, e, exc_info=True)

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
    utility_model = _normalize_model_for_litellm(config.get("utility_model", "claude-sonnet-4-6"))
    utility_kwargs: dict = {}
    utility_key = await _resolve_api_key(utility_model)
    if utility_key:
        utility_kwargs["api_key"] = utility_key

    # --- Plan-Aware Continuation (Design Doc 034) ---
    # Classify intent, load plan, and build minimal context for continuations.
    _has_active_plan = False
    _active_plan_id: str | None = None
    _is_continuation = False
    try:
        from backend.app.agent.tools.work_plan import load_active_plan, format_plan_context, format_recovery_context
        from backend.app.agent.continuation import (
            classify_intent,
            ContinuationIntent,
            resolve_plan_position,
            build_continuation_context,
            build_checkpoint_from_history,
            format_checkpoint_context,
        )

        active_plan = await load_active_plan(_state.agent_db, _state.agent_id, conversation_id=conversation_id, plan_id=plan_id)
        if active_plan:
            _has_active_plan = True
            _active_plan_id = active_plan["id"]

        # Classify user intent
        intent = classify_intent(user_message, _has_active_plan)
        logger.info("Continuation intent: %s (has_plan=%s)", intent.value, _has_active_plan)

        if intent in (ContinuationIntent.CONTINUE, ContinuationIntent.ADJUST) and active_plan:
            # --- Plan-Aware Fresh Context ---
            # Instead of injecting bloated history, build minimal continuation context.
            _is_continuation = True

            # Resolve position against real state
            _workspace_dir_for_plan = os.environ.get("WORKSPACE_DIR", "/workspace")
            position = await resolve_plan_position(active_plan, _workspace_dir_for_plan)

            # Build focused context
            adjustment = user_message if intent == ContinuationIntent.ADJUST else None
            continuation_ctx = build_continuation_context(position, active_plan, adjustment)

            # Also include plan IDs for the work_plan tool
            plan_id_ctx = format_plan_context(active_plan)

            # Replace history with minimal continuation context
            # This is the key optimization: ~2K tokens instead of ~100K
            history = [{"role": "user", "content": continuation_ctx + "\n\n" + plan_id_ctx}]

            logger.info(
                "Continuation: plan %s, %d/%d complete, next=%s, history replaced (%d tokens)",
                _active_plan_id,
                len(position.completed_items),
                position.total_items,
                position.next_item.get("title", "none") if position.next_item else "none",
                len(continuation_ctx) // 4,
            )

            # Emit continuation event for the frontend
            if event_queue is not None:
                await event_queue.put(_sse_event("status", {
                    "state": "continuing",
                    "plan_id": _active_plan_id,
                    "progress": f"{len(position.completed_items)}/{position.total_items}",
                    "next_item": position.next_item.get("title", "") if position.next_item else "",
                }))

        elif intent == ContinuationIntent.CONTINUE and not active_plan and history:
            # --- Fallback: No Work Plan ---
            # Build a lightweight checkpoint from history instead of sending it all.
            _is_continuation = True
            checkpoint = build_checkpoint_from_history(history)
            checkpoint_ctx = format_checkpoint_context(checkpoint)

            # Replace history with checkpoint (~500 tokens)
            history = [{"role": "user", "content": checkpoint_ctx}]

            logger.info("Continuation (no plan): checkpoint built, history replaced")

        elif active_plan:
            # Normal message with active plan — use existing format_plan_context
            in_progress = [i for i in active_plan.get("items", []) if i["status"] == "in_progress"]
            if in_progress:
                plan_ctx = format_recovery_context(active_plan) + "\n\n" + format_plan_context(active_plan)
            else:
                plan_ctx = format_plan_context(active_plan)

            # Inject as a system message prefix so the agent always has IDs in context
            history = [{"role": "user", "content": plan_ctx}] + history
            logger.info("Injected active plan context for plan %s (%d items)", _active_plan_id, len(active_plan.get("items", [])))

    except Exception as e:
        logger.debug("Plan-aware continuation skipped: %s", e)

    # --- Context Distillation Pipeline ---

    # Stage 1: Memory search (fragments are now loaded from disk via manifest)
    recent_memories: list[dict] = []
    try:
        from backend.app.agent.tools.native import handle_search_memory
        res = await handle_search_memory(
            {"query": user_message, "limit": 3},
            {"agent_db": _state.agent_db}
        )
        recent_memories = res.get("results", [])
    except Exception:
        pass

    prompt_parts = [system_prompt]

    # Inject relevant memories directly into the system prompt prefix
    if recent_memories:
        mem_text = "\n".join([f"- {m['content']}" for m in recent_memories])
        prompt_parts.append(f"## Relevant Memories\n{mem_text}")
        
    full_system_prompt = "\n\n".join(prompt_parts)

    # Inject prompt hierarchy: Tier 1 fragments + category manifest from disk
    from backend.app.agent.manifest import load_manifest, get_tier1_content, get_tier1_meta
    from backend.app.agent.tools.dynamic_loader import generate_manifest as _generate_category_manifest

    _prompts_dir = Path("/bond/prompts")
    if not _prompts_dir.exists():
        # dev fallback
        _prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"

    # Load the three-tier manifest (cached, hot-reloads on file change)
    _fragment_manifest = load_manifest(_prompts_dir)

    # Tier 1: always-on fragments → system prompt
    _tier1_content = get_tier1_content(_fragment_manifest)
    _tier1_meta = get_tier1_meta(_fragment_manifest)
    if _tier1_content:
        full_system_prompt = full_system_prompt + "\n\n" + _tier1_content

    # Tier 3: semantic router selects context-dependent fragments
    # Requires numpy + semantic_router + sentence-transformers (heavy deps).
    # Gracefully skip if not installed in this environment.
    _tier3_meta: list[dict] = []
    try:
        from backend.app.agent.fragment_router import (
            build_route_layer,
            get_tier3_meta,
            select_fragments_by_similarity,
        )

        build_route_layer(_prompts_dir)
        tier3_picks = await select_fragments_by_similarity(user_message, top_k=5)
        if tier3_picks:
            tier3_content = "\n\n---\n\n".join(f.content for f in tier3_picks)
            full_system_prompt = full_system_prompt + "\n\n" + tier3_content
            _tier3_meta = get_tier3_meta(tier3_picks)
    except ImportError as e:
        logger.debug("Tier 3 semantic router unavailable (missing dep: %s) — skipping", e.name)

    # Category manifest for load_context tool (still useful for Tier 3 categories)
    import backend.app.worker as _worker_module
    _category_manifest = _worker_module._prompt_manifest_cache
    if _category_manifest is None:
        _category_manifest = _generate_category_manifest(_prompts_dir)
        _worker_module._prompt_manifest_cache = _category_manifest
    if _category_manifest:
        full_system_prompt = full_system_prompt + "\n\n" + _category_manifest

    # Auto-skills: surface relevant skills so the agent knows they exist
    try:
        from backend.app.agent.tools.skills import _get_router, init_router
        await init_router(persistence=_state.persistence)
        skill_router = _get_router()
        skills_prompt = await skill_router.get_relevant_skills_prompt(
            user_message, session_id=conversation_id,
        )
        if skills_prompt:
            full_system_prompt += (
                "\n\n## Skills\n"
                "Before answering, scan these matched skills. If one clearly applies, "
                "use the `skills` tool with action='read' and the skill name to load "
                "its full instructions, then follow them.\n"
                + skills_prompt
            )
    except Exception:
        logger.debug("Skills injection skipped", exc_info=True)

    # Set process cwd to /workspace so file_read/file_edit/file_write resolve
    # relative paths the same way code_execute does.
    _workspace_ctx = _discover_workspace()
    if _workspace_ctx:
        full_system_prompt = full_system_prompt + "\n\n" + _workspace_ctx

    # ── Doc 049: Inject learned lessons from approved/ directory ──
    try:
        from backend.app.agent.critic import load_lessons
        _lessons_content = load_lessons()
        if _lessons_content:
            full_system_prompt += _lessons_content
            logger.debug("Injected learned lessons (%d chars)", len(_lessons_content))
    except Exception:
        logger.debug("Lessons injection skipped", exc_info=True)

    # Inject MCP integrations summary so the agent knows what external services
    # are available (or NOT available) without having to search the filesystem.
    try:
        from backend.app.mcp import mcp_manager
        if mcp_manager.connections:
            _mcp_names = sorted(mcp_manager.connections.keys())
            _mcp_connected = [
                n for n in _mcp_names if mcp_manager.connections[n].session
            ]
            _mcp_summary = (
                "## MCP Integrations\n"
                "Connected external services (via MCP servers): "
                + ", ".join(_mcp_connected)
            )
            if len(_mcp_connected) < len(_mcp_names):
                _mcp_disconnected = [n for n in _mcp_names if n not in _mcp_connected]
                _mcp_summary += "\nDisconnected: " + ", ".join(_mcp_disconnected)
            _mcp_summary += (
                "\nTools from these servers are prefixed `mcp_<server>_`. "
                "If asked about a service you don't have an MCP connection for, "
                "say so directly — don't search the filesystem for it."
            )
            full_system_prompt += "\n\n" + _mcp_summary
        else:
            full_system_prompt += (
                "\n\n## MCP Integrations\n"
                "No MCP servers are connected. If asked about external service "
                "integrations (e.g. time tracking, CRM), say you don't currently "
                "have access rather than searching the filesystem."
            )
    except Exception:
        pass

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
        conversation_id, 0, compression_stats, {"selected": len(_tier1_meta), "total": len(_fragment_manifest)},
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
            await _state.persistence.save_conversation_message(
                conversation_id=conversation_id,
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
        agent_name=agent_name,
    )

    # Use compact schemas to further reduce token usage
    tool_defs = [compact_tool_schema(TOOL_MAP[name]) for name in selected_tool_names if name in TOOL_MAP]

    # Append MCP tool definitions (not in TOOL_MAP — they live in mcp_manager)
    try:
        from backend.app.mcp import mcp_manager
        mcp_selected = [n for n in selected_tool_names if n.startswith("mcp_") and n not in TOOL_MAP]
        if mcp_selected:
            mcp_defs = mcp_manager.get_definitions(mcp_selected)
            tool_defs.extend([compact_tool_schema(d) for d in mcp_defs])
            logger.info("Added %d MCP tool definition(s) to LLM call", len(mcp_defs))
    except Exception as e:
        logger.debug("MCP tool defs injection skipped: %s", e)

    # Read coding agent settings from local DB (if available)
    coding_agent_settings: dict[str, str] = {}
    if _state.agent_db:
        try:
            async with _state.agent_db.execute(
                "SELECT key, value FROM settings WHERE key LIKE 'coding_agent.%'"
            ) as cursor:
                async for row in cursor:
                    coding_agent_settings[row[0]] = row[1]
        except Exception:
            pass  # Table may not exist yet — use defaults

    # Tool context: local agent_db instead of host SQLAlchemy session
    tool_context: dict[str, Any] = {
        "agent_db": _state.agent_db,
        "agent_id": _state.agent_id,
        "conversation_id": conversation_id,
        "event_queue": event_queue,
        "api_keys": injected_keys,
        "coding_agent_settings": coding_agent_settings,
    }

    tool_calls_made = 0
    sse_events: list[str] = []  # collected for the SSE stream

    # File re-read dedup: avoid wasting tokens on identical file content
    # _file_read_cache removed — see comment at "File re-read dedup — DISABLED"

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

    # Cyclical loop detection — catches patterns like A→B→C→A→B→C
    # where individual calls differ but the sequence repeats
    _CYCLE_WINDOW = 30  # track last N tool calls for cycle detection
    _CYCLE_MIN_PERIOD = 2  # shortest cycle to look for (e.g. A→B→A→B)
    _CYCLE_MAX_PERIOD = 8  # longest cycle to look for
    _CYCLE_REPEATS = 3  # how many times a cycle must repeat to trigger
    _loop_intervention_count = 0  # how many times we've intervened
    _LOOP_MAX_INTERVENTIONS = 2  # after this many, hard-stop the loop

    # Track where the pre-turn messages end so we know which are in-loop
    _preturn_msg_count = len(messages)

    # Track cache breakpoint 2 position for Anthropic prompt caching stability.
    # Initialize to after history + user message (the last pre-turn message).
    _cache_bp2_index = len(messages) - 1

    # Info-gathering tools — used for batching nudge detection (Phase 1B)
    # and early termination tracking (Phase 2B)
    INFO_GATHERING_TOOLS = frozenset({
        "file_read", "search_memory",
        "web_search", "web_read", "work_plan",
        "shell_find", "shell_ls", "shell_grep", "git_info",
        "shell_wc", "shell_head", "shell_tree", "project_search",
    })
    CONSEQUENTIAL_TOOLS = frozenset({
        "file_write", "file_edit", "code_execute", "respond", "memory_save",
    })

    # ── Phase 1B: Batching nudge tracking ──
    _consecutive_single_info_iterations = 0

    # ── Phase 2A: Adaptive iteration budget ──
    _adaptive_budget_set = False
    _adaptive_budget = max_iterations

    # ── Phase 2B: Early termination for read-only tasks ──
    _has_made_consequential_call = False

    # ── Phase 4B: Per-session cost tracking ──
    _cost_tracking = {
        "primary_calls": 0,
        "filter_calls": 0,
        "compression_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": 0.0,  # Real cost from litellm.completion_cost()
        "iterations_used": 0,
        "iteration_budget": max_iterations,
    }

    # ── Doc 049: Outcome tracking for closed-loop optimization ──
    _outcome_tool_names: list[str] = []
    _outcome_fragment_names: list[str] = []
    _outcome_had_loop_intervention = False
    _outcome_had_continuation = False
    _outcome_had_compression = False
    _outcome_turn_counter = getattr(_state, "_optimization_turn_counter", 0)
    _outcome_start_time_ms = int(time.time() * 1000)

    # Collect fragment names from tiers 1 and 3
    try:
        for _m in _tier1_meta:
            _n = _m.get("name") or _m.get("path", "")
            if _n:
                _outcome_fragment_names.append(_n)
        for _m in _tier3_meta:
            _n = _m.get("name") or _m.get("path", "")
            if _n:
                _outcome_fragment_names.append(_n)
    except Exception:
        pass

    # Capture config snapshot for experiment tracking
    _config_snapshot: dict[str, Any] = {}
    _outcome_cohort = "control"
    try:
        from backend.app.agent.optimizer import TUNABLE_PARAMS, apply_experiment_overrides
        for _pk in TUNABLE_PARAMS:
            if _pk in config:
                _config_snapshot[_pk] = config[_pk]
        # Apply active experiment overrides
        if _state.agent_db:
            config, _outcome_cohort = await apply_experiment_overrides(
                config, conversation_id, _state.agent_db,
            )
    except Exception:
        logger.debug("Experiment override check skipped", exc_info=True)

    async def _record_outcome():
        """Doc 049: record outcome observation after the turn completes."""
        try:
            from backend.app.agent.outcome import collect_signals, classify_task
            from ulid import ULID
            import hashlib as _hl

            _wall_time = int(time.time() * 1000) - _outcome_start_time_ms
            _task_cat = classify_task(user_message, _outcome_tool_names)

            signals = collect_signals(
                tool_calls=tool_calls_made,
                iterations=_cost_tracking["iterations_used"],
                total_cost=_cost_tracking["total_cost"],
                input_tokens=_cost_tracking["total_input_tokens"],
                output_tokens=_cost_tracking["total_output_tokens"],
                wall_time_ms=_wall_time,
                had_loop_intervention=_outcome_had_loop_intervention,
                had_continuation=_outcome_had_continuation,
                had_compression=_outcome_had_compression,
                fragments_selected=len(_outcome_fragment_names),
                fragment_names=_outcome_fragment_names,
                user_correction=False,  # detected retroactively on next message
                task_category=_task_cat,
                user_message_preview=user_message[:200] if user_message else "",
                tool_names=_outcome_tool_names,
            )

            if _state.agent_db:
                _obs_id = str(ULID())
                _lessons_hash = _hl.md5((_lessons_content or "").encode()).hexdigest()[:12]
                await _state.agent_db.execute(
                    """
                    INSERT INTO optimization_observations
                        (id, conversation_id, turn_index, task_category,
                         user_message_preview, signals_json, outcome_score,
                         config_snapshot_json, active_lessons_hash, cohort)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _obs_id, conversation_id, tool_calls_made, _task_cat,
                        user_message[:200] if user_message else "",
                        json.dumps(signals), signals["outcome_score"],
                        json.dumps(_config_snapshot), _lessons_hash,
                        _outcome_cohort,
                    ),
                )
                await _state.agent_db.commit()

                # Every 50 turns, trigger background analysis
                _state._optimization_turn_counter = _outcome_turn_counter + 1
                if (_outcome_turn_counter + 1) % 50 == 0:
                    asyncio.ensure_future(_run_optimization_analysis())

        except Exception:
            logger.debug("Outcome recording failed (non-fatal)", exc_info=True)

    async def _run_optimization_analysis():
        """Spawn background optimization analysis."""
        try:
            from backend.app.agent.optimizer import run_analysis
            from backend.app.agent.tools.skills import _router_settings
            from backend.app.foundations.embeddings.engine import EmbeddingEngine
            _engine = EmbeddingEngine(
                settings=_router_settings or {"embedding.provider": "local"},
                db_engine=None,
            )
            await run_analysis(_state.agent_db, _engine)
        except Exception:
            logger.debug("Background optimization analysis failed", exc_info=True)

    # ── Lifecycle phase tracking (Doc 024 — Tier 2 injection) ──
    _lifecycle_phase = Phase.IDLE
    _lifecycle_injected = False  # whether Tier 2 content is in the system prompt
    _lifecycle_turn_number = 0  # logical turn counter for lifecycle detection

    # ── Langfuse metadata for observability ──
    # Build once before the loop. Updated if load_context adds fragments mid-turn.
    _langfuse_meta: dict[str, Any] = {}
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _audit_fragments: list[dict] = []

        # Tier 1 fragments (from manifest)
        for meta in _tier1_meta:
            _audit_fragments.append(meta)

        # Tier 3 fragments (from semantic router)
        for meta in _tier3_meta:
            _audit_fragments.append(meta)

        # Category manifest
        if _category_manifest:
            _audit_fragments.append({
                "source": "category-manifest",
                "name": "prompt_manifest",
                "tokenEstimate": _estimate_tokens(_category_manifest),
            })

        # Build fragment names and metadata summary
        _fragment_names = [f.get("name", "") for f in _audit_fragments]
        _fragment_total_tokens = sum(f.get("tokens", f.get("tokenEstimate", 0)) for f in _audit_fragments)

        _langfuse_meta = {
            "trace_name": f"agent-turn-{_state.agent_id}",
            "session_id": conversation_id,
            "tags": [
                f"agent:{_state.agent_id}",
                f"fragments:{len(_audit_fragments)}",
            ] + [f"prompt:{n}" for n in _fragment_names],

            # trace_metadata puts data on the TRACE level (visible without
            # clicking into a generation). Keys prefixed with trace_ are
            # hoisted by litellm's langfuse callback.
            "trace_metadata": {
                "fragment_count": len(_audit_fragments),
                "fragment_names": _fragment_names,
                "fragment_total_tokens": _fragment_total_tokens,
                "system_prompt_tokens": _estimate_tokens(full_system_prompt),
                "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
                "had_history_compression": compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD,
                "had_sliding_window": len(history) != len(windowed_history) if history else False,
            },

            # Full detail stays on the generation level
            "fragments_injected": _audit_fragments,
            "fragment_count": len(_audit_fragments),
            "fragment_names": _fragment_names,
            "fragment_total_tokens": _fragment_total_tokens,
            "system_prompt_tokens": _estimate_tokens(full_system_prompt),
            "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
            "had_history_compression": compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD,
            "had_sliding_window": len(history) != len(windowed_history) if history else False,
        }

    # ── Phase 4B/4C: Cost tracking helper ──
    _raw_cost_thresh = os.environ.get("LLM_COST_ALERT_THRESHOLD")
    _raw_iter_thresh = os.environ.get("LLM_ITERATION_ALERT_THRESHOLD")
    try:
        _cost_alert_threshold = float(_raw_cost_thresh) if isinstance(_raw_cost_thresh, str) else 0.25
    except (TypeError, ValueError):
        _cost_alert_threshold = 0.25
    try:
        _iteration_alert_threshold = int(_raw_iter_thresh) if isinstance(_raw_iter_thresh, str) else 20
    except (TypeError, ValueError):
        _iteration_alert_threshold = 20

    def _calc_call_cost(resp, resp_model: str) -> float:
        """Calculate real cost for a single LLM call via litellm's cost calculator.

        Falls back to token-based estimate if the calculator doesn't have pricing
        for the model (e.g. custom/self-hosted models).
        """
        try:
            return _litellm_completion_cost(completion_response=resp, model=resp_model)
        except Exception:
            # Fallback: rough estimate using Opus pricing
            _usage = getattr(resp, "usage", None)
            _in = getattr(_usage, "prompt_tokens", 0) or 0
            _out = getattr(_usage, "completion_tokens", 0) or 0
            return _in * 15.0 / 1_000_000 + _out * 75.0 / 1_000_000

    def _emit_cost_summary():
        """Log per-session cost summary (Phase 4B) and check for cost alerts (Phase 4C)."""
        _total = _cost_tracking["total_cost"]

        logger.info(
            "Cost summary: calls=%d (primary=%d, filter=%d, compression=%d) "
            "tokens_in=%d tokens_out=%d cost=$%.4f iterations=%d/%d",
            _cost_tracking["primary_calls"] + _cost_tracking["filter_calls"] + _cost_tracking["compression_calls"],
            _cost_tracking["primary_calls"],
            _cost_tracking["filter_calls"],
            _cost_tracking["compression_calls"],
            _cost_tracking["total_input_tokens"],
            _cost_tracking["total_output_tokens"],
            _total,
            _cost_tracking["iterations_used"],
            _cost_tracking["iteration_budget"],
        )

        # Phase 4C: Cost alerting
        try:
            _cost_exceeded = _total > _cost_alert_threshold or _cost_tracking["iterations_used"] > _iteration_alert_threshold
        except TypeError:
            _cost_exceeded = False
        if _cost_exceeded:
            logger.warning(
                "COST ALERT: session %s exceeded thresholds (cost=$%.4f > $%.2f or iterations=%d > %d)",
                conversation_id, _total, _cost_alert_threshold,
                _cost_tracking["iterations_used"], _iteration_alert_threshold,
            )
            if _langfuse_meta:
                _langfuse_meta.setdefault("tags", []).append("cost:high")

    # ── Phase 0-2: Pre-gathering (Design Doc 038) ──
    _pre_gather_result = None
    if not _is_continuation:
        try:
            from backend.app.agent.pre_gather_integration import run_pre_gather, PreGatherResult
            _repo_root = os.environ.get("WORKSPACE_DIR", "/workspace")
            _pre_gather_result = await run_pre_gather(
                user_message=user_message,
                history=compressed_history or [],
                conversation_id=conversation_id,
                model=model,
                api_key=extra_kwargs.get("api_key"),
                extra_kwargs=extra_kwargs,
                utility_model=utility_model,
                utility_kwargs=utility_kwargs,
                tool_registry=registry,
                tool_context=tool_context,
                repo_root=_repo_root,
                max_iterations=max_iterations,
                event_queue=event_queue,
                langfuse_meta=_langfuse_meta if _langfuse_meta else None,
                interrupt_event=_state.interrupt_event,
                is_continuation=False,
            )

            if _pre_gather_result and _pre_gather_result.context_bundle:
                # Inject gathered context as a user message before the loop
                messages.append({
                    "role": "user",
                    "content": f"[Pre-gathered context for this task]\n\n{_pre_gather_result.context_bundle}",
                })
                logger.info(
                    "Pre-gather: injected %d tokens of context",
                    len(_pre_gather_result.context_bundle) // 4,
                )

            if _pre_gather_result and _pre_gather_result.adaptive_budget is not None:
                _adaptive_budget = min(max_iterations, _pre_gather_result.adaptive_budget)
                _adaptive_budget_set = True
                _cost_tracking["iteration_budget"] = _adaptive_budget
                logger.info("Pre-gather: set adaptive budget to %d", _adaptive_budget)
        except Exception as e:
            logger.warning("Pre-gather phase failed, falling through to normal loop: %s", e)

    # Reset per-turn budgets
    from backend.app.agent.tools.native import reset_load_context_budget
    reset_load_context_budget()

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

        # ── Phase 3B: In-loop tool result decay — DISABLED ──
        # Previously compressed older tool results to save context, but this
        # destroys file content the agent paid for. Combined with file_read
        # dedup (which correctly refuses re-reads), this created an impossible
        # loop: agent loses content to compression, tries to re-read, dedup
        # blocks it saying "you already have it", agent is stuck.
        # The ~2K tokens saved per compression is not worth a wasted iteration.

        current_max_tokens = TOKEN_TIERS[current_tier]
        context_tokens = _estimate_messages_tokens(messages) + _estimate_tokens(json.dumps(tool_defs))

        # All iterations use the primary model (Phase 1A: removed speculative utility routing)
        _iter_model = model
        _iter_kwargs = extra_kwargs

        # Advance prompt cache breakpoint 2 before each call (Anthropic only).
        if _is_anthropic_model:
            _cache_bp2_index = _advance_cache_breakpoint(messages, _cache_bp2_index)

        # ── Phase 4A: Distinguished Langfuse trace naming ──
        _iter_langfuse_meta = dict(_langfuse_meta) if _langfuse_meta else {}
        if _iter_langfuse_meta:
            _iter_langfuse_meta["trace_name"] = f"agent-turn-{_state.agent_id}-iter-{_iteration}"
            _iter_langfuse_meta.setdefault("tags", [])
            if "call_type:primary" not in _iter_langfuse_meta["tags"]:
                _iter_langfuse_meta["tags"].append("call_type:primary")

        logger.info(
            "LLM request: model=%s tools=%d max_tokens=%d tier=%d context_tokens=~%d msgs=%d cache=%s",
            _iter_model, len(tool_defs), current_max_tokens, current_tier,
            context_tokens, len(messages),
            "anthropic" if _is_anthropic_model else "none",
        )

        # Token budget injection: append brief context to the last tool result
        _budget_note = ""
        _budget_target_idx = -1
        if _iteration > 0 and messages and messages[-1].get("role") == "tool":
            _budget_note = f"\n[Turn {_iteration + 1}/{max_iterations} | ~{context_tokens} tokens | {tool_calls_made} tool calls]"
            _budget_target_idx = len(messages) - 1
            content = messages[_budget_target_idx].get("content", "")
            if isinstance(content, str):
                messages[_budget_target_idx]["content"] = content + _budget_note

        # ── Phase 2A + Doc 034: Plan-aware iteration budget ──
        # Uses IterationBudget for 50%/80%/95% thresholds with plan context.
        try:
            from backend.app.agent.continuation import IterationBudget
            _iter_budget = IterationBudget(total=max_iterations, used=_iteration)
            _budget_msg = _iter_budget.get_budget_message()
            if _budget_msg:
                # At 95%: checkpoint the plan before it's too late
                if _iter_budget.should_stop and _has_active_plan:
                    try:
                        from backend.app.agent.tools.work_plan import checkpoint_active_plan
                        await checkpoint_active_plan(
                            _state.agent_db, _state.agent_id,
                            f"Budget at {_iter_budget.pct_used:.0%} — auto-checkpoint at iteration {_iteration}/{max_iterations}",
                        )
                    except Exception:
                        pass
                messages.append({"role": "user", "content": f"SYSTEM: {_budget_msg}"})
        except Exception:
            # Fallback to simple 80% check
            if _iteration > 0 and _iteration >= int(max_iterations * 0.8):
                messages.append({
                    "role": "user",
                    "content": "SYSTEM: You're approaching your iteration limit. Wrap up or synthesize what you have.",
                })

        # ── Phase 2B: Early termination nudges for read-only tasks ──
        if not _has_made_consequential_call:
            if _iteration == 10:
                messages.append({
                    "role": "user",
                    "content": (
                        "SYSTEM: You've gathered substantial context over 10 iterations without making "
                        "any changes. Synthesize your findings and respond to the user now. "
                        "Do not read more files."
                    ),
                })
            elif _iteration >= 15:
                # Force respond-only tool set
                from backend.app.agent.tools import TOOL_MAP as _FULL_TOOL_MAP
                tool_defs = [compact_tool_schema(_FULL_TOOL_MAP["respond"])] if "respond" in _FULL_TOOL_MAP else tool_defs
                logger.info("Phase 2B: forced respond-only tool set at iteration %d", _iteration)

        # Log the API key info before calling LiteLLM
        if "api_key" in _iter_kwargs:
            _dbg_key = _iter_kwargs["api_key"]
            logger.debug("Calling LiteLLM with model %s, API key length: %d",
                       _iter_model, len(_dbg_key))
        else:
            logger.debug("Calling LiteLLM with model %s, no API key in kwargs", _iter_model)
        # ── LLM call with retry on empty responses (rate limiting) ──
        # Uses _cancellable_llm_call (037 §5.2.1) so interrupts take effect
        # mid-call instead of waiting for the full response.
        _retry_max = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", "10"))
        _retry_max_wait = float(os.environ.get("LLM_RETRY_MAX_WAIT_SECONDS", "180"))
        response = None

        for _retry_attempt in range(_retry_max):
            response = await _cancellable_llm_call(
                _state.interrupt_event,
                model=_iter_model,
                messages=messages,
                tools=tool_defs if tool_defs else None,
                temperature=0.7,
                max_tokens=current_max_tokens,
                metadata=_iter_langfuse_meta if _iter_langfuse_meta else None,
                **_iter_kwargs,
            )

            # Interrupted mid-call — check for injected context or stop
            if response is None:
                _state.interrupt_event.clear()
                if _state.pending_messages:
                    logger.info(
                        "LLM call interrupted with %d pending messages — injecting context",
                        len(_state.pending_messages),
                    )
                    for msg in _state.pending_messages:
                        messages.append(msg)
                    _state.pending_messages.clear()
                    if event_queue is not None:
                        await event_queue.put(_sse_event("status", {"state": "context_injected"}))
                    break  # break retry loop, continue outer iteration
                else:
                    logger.info("LLM call interrupted — stopping agent loop")
                    # Kill any active coding agent sub-processes
                    from backend.app.agent.tools.coding_agent import kill_coding_agent
                    await kill_coding_agent(_state.agent_id)
                    if event_queue is not None:
                        await event_queue.put(_sse_event("status", {"state": "interrupted"}))
                    # Strip budget note before returning
                    if _budget_note and _budget_target_idx >= 0:
                        content = messages[_budget_target_idx].get("content", "")
                        if isinstance(content, str) and content.endswith(_budget_note):
                            messages[_budget_target_idx]["content"] = content[:-len(_budget_note)]
                    await _skill_tracker.flush()
                    await _record_outcome()
                    _emit_cost_summary()
                    return "", tool_calls_made

            if response.choices:
                break

            # Empty response — compute exponential backoff delay.
            # Delays form a geometric series that sums to _retry_max_wait,
            # so the last attempt fires right around the configured ceiling.
            if _retry_max > 1:
                _ratio = (_retry_max_wait / 1.0) ** (1.0 / (_retry_max - 1))
                _delay = 1.0 * (_ratio ** _retry_attempt)
                _delay = min(_delay, _retry_max_wait)
            else:
                _delay = _retry_max_wait

            if _retry_attempt < _retry_max - 1:
                logger.warning(
                    "LLM returned empty response (attempt %d/%d). "
                    "Retrying in %.1fs (possible rate limiting).",
                    _retry_attempt + 1, _retry_max, _delay,
                )
                if event_queue is not None:
                    await event_queue.put(_sse_event("status", {
                        "state": "rate_limited",
                        "retry_attempt": _retry_attempt + 1,
                        "retry_max": _retry_max,
                        "retry_delay": round(_delay, 1),
                    }))
                await asyncio.sleep(_delay)
            else:
                raise RuntimeError(
                    f"LLM returned empty response (no choices) after {_retry_max} attempts "
                    f"over ~{_retry_max_wait}s. "
                    "This may indicate rate limiting, content filtering, or a malformed request."
                )

        # If we broke out of retry loop due to context injection, continue outer loop
        if response is None:
            continue

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
            _outcome_had_continuation = True
            partial_content = llm_message.content or ""

            if continuation_attempts > MAX_CONTINUATIONS:
                logger.error(
                    "Aborting after %d continuation attempts — response keeps exceeding token limit",
                    continuation_attempts,
                )
                await _record_outcome()
                _emit_cost_summary()
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

        # ── Phase 4B: Track cost per iteration ──
        _iter_cost = _calc_call_cost(response, _iter_model)
        _cost_tracking["primary_calls"] += 1
        _cost_tracking["total_input_tokens"] += input_tokens
        _cost_tracking["total_output_tokens"] += output_tokens
        _cost_tracking["total_cost"] += _iter_cost
        _cost_tracking["iterations_used"] = _iteration + 1
        logger.debug("Iteration %d cost: $%.4f (cumulative: $%.4f)", _iteration, _iter_cost, _cost_tracking["total_cost"])

        # ── Phase 2A: Adaptive iteration budget ──
        if _iteration == 0 and not _adaptive_budget_set:
            _adaptive_budget_set = True
            if not llm_message.tool_calls:
                _adaptive_budget = min(max_iterations, 2)
                logger.info("Phase 2A: simple Q&A, budget=%d", _adaptive_budget)
            else:
                _first_tool_names = [tc.function.name for tc in llm_message.tool_calls]
                _has_edits = any(t in ("file_edit", "file_write") for t in _first_tool_names)
                _has_plan = any(t == "work_plan" for t in _first_tool_names)
                _has_reads = any(t in ("file_read", "shell_grep", "search_memory") for t in _first_tool_names)
                if _has_plan and len(_first_tool_names) >= 5:
                    _adaptive_budget = min(max_iterations, 25)
                    logger.info("Phase 2A: complex multi-file, budget=%d", _adaptive_budget)
                elif _has_edits:
                    _adaptive_budget = min(max_iterations, 20)
                    logger.info("Phase 2A: implementation, budget=%d", _adaptive_budget)
                elif _has_reads and not _has_edits:
                    _adaptive_budget = min(max_iterations, 10)
                    logger.info("Phase 2A: analysis, budget=%d", _adaptive_budget)
                else:
                    _adaptive_budget = min(max_iterations, 8)
                    logger.info("Phase 2A: file lookup, budget=%d", _adaptive_budget)
            _cost_tracking["iteration_budget"] = _adaptive_budget

        # ── Approaching budget: hand off to coding_agent (enhanced with Doc 038 handoff context) ──
        _budget_threshold = int(_adaptive_budget * 0.8)
        if (_iteration >= _budget_threshold
            and _iteration > 2
            and not any(tc.function.name == "coding_agent" for tc in (llm_message.tool_calls or []))):
            _remaining = _adaptive_budget - _iteration - 1
            # Build structured handoff context from what the agent has done so far
            try:
                from backend.app.agent.pre_gather import build_handoff_context
                _handoff_ctx = build_handoff_context(messages)
                _handoff_msg = (
                    f"SYSTEM: You are at iteration {_iteration + 1}/{_adaptive_budget}. "
                    f"You have {_remaining} iterations left. Hand off your remaining work "
                    f"to the coding_agent tool NOW.\n\n"
                    f"**Files you've read:**\n{_handoff_ctx['files_read']}\n\n"
                    f"**Changes you've made:**\n{_handoff_ctx['edits_made']}\n\n"
                    f"Write a coding_agent task prompt that covers the remaining work. "
                    f"Include the file paths you've already identified. "
                    f"The coding agent has access to the same repo — reference files by path, "
                    f"don't paste their contents.\n\n"
                    f"Spawn coding_agent in your next response. Do not read more files."
                )
            except Exception:
                _handoff_msg = (
                    f"SYSTEM: You are at iteration {_iteration + 1}/{_adaptive_budget}. "
                    f"You have {_remaining} iterations left. Hand off your remaining work "
                    f"to the coding_agent tool now. Summarize what's done and what's left."
                )
            messages.append({"role": "user", "content": _handoff_msg})
            logger.info("Budget escalation: iteration %d/%d, injecting coding_agent handoff", _iteration + 1, _adaptive_budget)

            # After 4 ignored handoff hints, restrict tools to coding_agent + respond only
            _overbudget_by = _iteration - _budget_threshold
            if _overbudget_by >= 4:
                from backend.app.agent.tools import TOOL_MAP as _FULL_TOOL_MAP
                _forced_tools = []
                for _tname in ("coding_agent", "respond"):
                    if _tname in _FULL_TOOL_MAP:
                        _forced_tools.append(compact_tool_schema(_FULL_TOOL_MAP[_tname]))
                if _forced_tools:
                    tool_defs = _forced_tools
                    logger.warning(
                        "Budget hard restriction: iteration %d, forced tool set to coding_agent+respond",
                        _iteration + 1,
                    )

            # After 8 ignored handoff hints, hard-stop the loop
            if _overbudget_by >= 8:
                logger.warning(
                    "Budget hard cap: stopping loop at iteration %d (adaptive budget was %d)",
                    _iteration + 1, _adaptive_budget,
                )
                break

        if llm_message.tool_calls:
            _iter_tool_names = [tc.function.name for tc in llm_message.tool_calls]

            # ── Phase 2B: Track consequential calls ──
            if any(t in CONSEQUENTIAL_TOOLS for t in _iter_tool_names):
                _has_made_consequential_call = True

            # ── Phase 1B: Batching nudge for single info-gathering calls ──
            _is_single_info = (
                len(llm_message.tool_calls) == 1
                and _iter_tool_names[0] in INFO_GATHERING_TOOLS
                and not (llm_message.content and llm_message.content.strip())
            )
            if _is_single_info:
                _consecutive_single_info_iterations += 1
                if _consecutive_single_info_iterations >= 3:
                    messages.append({
                        "role": "user",
                        "content": (
                            "SYSTEM: You have made 3+ consecutive single-tool info-gathering calls. "
                            "This is inefficient. Batch ALL remaining information needs into a SINGLE "
                            "response with multiple tool calls. The system executes them in parallel."
                        ),
                    })
                    logger.info("Phase 1B: strong batching nudge after %d consecutive single-tool iterations",
                              _consecutive_single_info_iterations)
                else:
                    messages.append({
                        "role": "user",
                        "content": (
                            "SYSTEM: You made a single info-gathering call. If you need more information, "
                            "batch multiple tool calls in your next response."
                        ),
                    })
            else:
                _consecutive_single_info_iterations = 0
            # Update last_assistant for tool result filter context
            if llm_message.content:
                last_assistant = llm_message.content
            messages.append(llm_message.model_dump())

            # ── Parallel pre-execution: classify & batch parallel-safe calls ──
            _parallel_precomputed: dict[str, tuple[dict, float]] = {}  # tool_call.id -> (result, duration)
            if len(llm_message.tool_calls) > 1:
                _parallel_candidates = []
                _all_parsed_args: dict[str, dict] = {}
                for _tc in llm_message.tool_calls:
                    _tc_name = _tc.function.name
                    try:
                        _tc_args = json.loads(_tc.function.arguments)
                    except json.JSONDecodeError:
                        _tc_args = {}
                    _all_parsed_args[_tc.id] = _tc_args
                    if _tc_name in agent_tools and classify_tool_call(_tc_name, _tc_args) == "parallel":
                        _parallel_candidates.append(_tc)

                if len(_parallel_candidates) >= 2:
                    logger.info(
                        "Parallel pre-execution: %d/%d calls are parallel-safe",
                        len(_parallel_candidates), len(llm_message.tool_calls),
                    )
                    pool = ParallelWorkerPool(
                        registry=registry,
                        utility_model=utility_model,
                        utility_kwargs=utility_kwargs,
                        context=tool_context,
                        max_workers=10,
                        timeout_per_worker=30.0,
                    )
                    _par_calls = [
                        {"tool_call_id": tc.id, "tool_name": tc.function.name, "arguments": _all_parsed_args[tc.id]}
                        for tc in _parallel_candidates
                    ]
                    _par_results, _ = await pool.execute(_par_calls)
                    for _pr in _par_results:
                        _tcid = _pr.get("tool_call_id")
                        if _tcid:
                            _parallel_precomputed[_tcid] = (_pr["result"], _pr.get("elapsed", 0))
                    logger.info(format_parallel_summary(_par_results))

                    # Emit parallel execution SSE event
                    if event_queue is not None:
                        await event_queue.put(_sse_event("status", {
                            "state": "parallel_execution",
                            "parallel_count": len(_parallel_candidates),
                            "total_count": len(llm_message.tool_calls),
                        }))

            # Collect lifecycle hook messages to append AFTER all tool
            # results.  Injecting user messages between tool_use and
            # tool_result violates Anthropic's message contract and
            # causes "tool_use ids without tool_result" errors.
            _deferred_injections: list[dict] = []

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

                # ── Loop detection (consecutive + cyclical) ──
                _loop_detected = False
                _loop_msg = ""

                # 1. Consecutive repetition: same call N times in a row
                if len(recent_tool_calls) >= REPETITION_THRESHOLD:
                    last_n = recent_tool_calls[-REPETITION_THRESHOLD:]
                    if all(tc == last_n[0] for tc in last_n):
                        _loop_detected = True
                        _loop_msg = (
                            f"SYSTEM: You have called '{tool_name}' with the same arguments "
                            f"{REPETITION_THRESHOLD} times in a row. You appear to be in a loop."
                        )
                        logger.warning(
                            "Consecutive repetition detected: %s called %d times with same args",
                            tool_name, REPETITION_THRESHOLD,
                        )

                # 2. Cyclical repetition: A→B→C→A→B→C pattern
                if not _loop_detected and len(recent_tool_calls) >= _CYCLE_MIN_PERIOD * _CYCLE_REPEATS:
                    for period in range(_CYCLE_MIN_PERIOD, _CYCLE_MAX_PERIOD + 1):
                        needed = period * _CYCLE_REPEATS
                        if len(recent_tool_calls) < needed:
                            continue
                        tail = recent_tool_calls[-needed:]
                        cycle = tail[:period]
                        is_cycle = all(
                            tail[i] == cycle[i % period]
                            for i in range(needed)
                        )
                        if is_cycle:
                            cycle_tools = [c[0] for c in cycle]
                            _loop_detected = True
                            _loop_msg = (
                                f"SYSTEM: You are in a cyclical loop — repeating the pattern "
                                f"{' → '.join(cycle_tools)} ({_CYCLE_REPEATS} times). "
                                f"These actions have already been completed. Stop repeating them."
                            )
                            logger.warning(
                                "Cyclical loop detected: pattern %s repeated %d times (period=%d)",
                                cycle_tools, _CYCLE_REPEATS, period,
                            )
                            break

                if _loop_detected:
                    _loop_intervention_count += 1

                    # Execute this tool call so there's a result for the tool_call_id
                    if tool_name not in agent_tools:
                        result = {"error": f"Tool '{tool_name}' is not enabled."}
                    else:
                        result = await registry.execute(tool_name, tool_args, tool_context)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })

                    if _loop_intervention_count > _LOOP_MAX_INTERVENTIONS:
                        # Hard stop — we've already warned and the model keeps looping
                        logger.error(
                            "Loop intervention limit reached (%d interventions). "
                            "Force-stopping agent loop at iteration %d, tool call %d.",
                            _loop_intervention_count, _iteration, tool_calls_made,
                        )
                        # Defer the user message so it comes AFTER all tool_result
                        # messages (including orphan fillers).  Inserting a user
                        # message between tool_results violates Anthropic's
                        # requirement that every tool_use has a matching
                        # tool_result immediately after.
                        _deferred_injections.append({
                            "role": "user",
                            "content": (
                                "SYSTEM: HARD STOP. You have been warned about looping multiple times "
                                "and continue to repeat the same actions. The agent loop is being terminated. "
                                "Use the respond tool NOW to report your current status."
                            ),
                        })
                        if event_queue is not None:
                            await event_queue.put(_sse_event("status", {
                                "state": "loop_terminated",
                                "interventions": _loop_intervention_count,
                                "tool_calls_made": tool_calls_made,
                            }))
                        # Give the model one last chance to respond
                        # by continuing the outer loop (it will see the HARD STOP)
                        recent_tool_calls.clear()
                        break

                    # Defer the loop intervention message so it comes AFTER all
                    # tool_result messages (including orphan fillers for any
                    # remaining tool_calls in this batch).  Appending a user
                    # message here would violate Anthropic's requirement that
                    # every tool_use has a matching tool_result immediately after.
                    _deferred_injections.append({
                        "role": "user",
                        "content": (
                            f"{_loop_msg} "
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

                # ── Pre-execution lifecycle hook (Doc 024) ──
                # Collect phase-specific guidance for consequential git
                # operations.  These are DEFERRED and appended after all
                # tool_result messages to avoid breaking Anthropic's
                # requirement that tool_results follow tool_use immediately.
                if is_git_commit_command(tool_name, tool_args):
                    _commit_frags = load_lifecycle_fragments(Phase.COMMITTING, _prompts_dir)
                    if _commit_frags:
                        _precommit_text = format_precommit_injection(_commit_frags)
                        _deferred_injections.append({
                            "role": "user",
                            "content": f"SYSTEM: {_precommit_text}",
                        })
                        logger.info(
                            "Pre-commit hook: deferred %d fragments (%s)",
                            len(_commit_frags),
                            [f.path for f in _commit_frags],
                        )
                elif is_pr_create_command(tool_name, tool_args):
                    _review_frags = load_lifecycle_fragments(Phase.REVIEWING, _prompts_dir)
                    if _review_frags:
                        _review_text = "\n\n---\n\n".join(
                            f.content for f in _review_frags if f.content
                        )
                        _deferred_injections.append({
                            "role": "user",
                            "content": f"SYSTEM: ## Before Creating This PR\n{_review_text}",
                        })
                        logger.info(
                            "Pre-PR hook: deferred %d fragments (%s)",
                            len(_review_frags),
                            [f.path for f in _review_frags],
                        )

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
                        elif tool_name.startswith("mcp_"):
                            try:
                                from backend.app.mcp import mcp_manager
                                _mcp_dyn = mcp_manager.get_definitions([tool_name])
                                tool_defs.extend([compact_tool_schema(d) for d in _mcp_dyn])
                            except Exception:
                                pass
                    
                    # Use precomputed parallel result if available
                    if tool_call.id in _parallel_precomputed:
                        result, duration = _parallel_precomputed[tool_call.id]
                        logger.info("Using precomputed parallel result for %s (%.2fs)", tool_name, duration)
                    else:
                        start_ts = time.time()
                        # For long-running tools (coding_agent), make execution
                        # interruptible (037 §5.2.2)
                        tool_task = asyncio.create_task(
                            registry.execute(tool_name, tool_args, tool_context)
                        )
                        interrupt_task = asyncio.create_task(
                            _state.interrupt_event.wait()
                        )
                        _done, _pending = await asyncio.wait(
                            {tool_task, interrupt_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for _t in _pending:
                            _t.cancel()
                            try:
                                await _t
                            except asyncio.CancelledError:
                                pass

                        if tool_task in _done:
                            result = tool_task.result()
                        else:
                            # Tool was interrupted
                            logger.info("Tool %s interrupted by user", tool_name)
                            from backend.app.agent.tools.coding_agent import kill_coding_agent
                            await kill_coding_agent(_state.agent_id)
                            result = {"error": "Tool execution interrupted by user"}
                            _state.interrupt_event.clear()
                            # Check for injected context
                            if _state.pending_messages:
                                for msg in _state.pending_messages:
                                    messages.append(msg)
                                _state.pending_messages.clear()
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

                # File re-read dedup — DISABLED.
                # Previously blocked re-reads of files already in context, but combined
                # with in-loop decay (also disabled), this trapped agents in an impossible
                # loop. The agent should be free to re-read any file at any time.
                # If we re-enable this, it MUST be aware of what's actually still in
                # the message context, not just what was read at some point.

                logger.info("Tool result [%d]: %s",  tool_calls_made,
                            {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in result.items()} if isinstance(result, dict) else result)

                # ── Doc 049: Track tool names for outcome signals ──
                if tool_name not in _outcome_tool_names:
                    _outcome_tool_names.append(tool_name)

                # Coding agent started — emit SSE event so gateway subscribes
                # to the background diff stream
                if (tool_name == "coding_agent"
                    and isinstance(result, dict)
                    and result.get("status") == "started"
                    and event_queue is not None):
                    await event_queue.put(_sse_event("coding_agent_started", {
                        "agent_type": result.get("agent_type", "claude"),
                        "conversation_id": conversation_id,
                    }))

                # Skill activated — emit SSE event for frontend toast
                if isinstance(result, dict) and "_skill_activated" in result:
                    _skill_info = result.pop("_skill_activated")
                    import uuid as _uuid
                    _act_id = f"act_{_uuid.uuid4().hex[:12]}"
                    if event_queue is not None:
                        await event_queue.put(_sse_event("skill_activated", {
                            "id": _act_id,
                            "skillName": _skill_info.get("name", ""),
                            "skillSource": _skill_info.get("source", ""),
                            "activatedAt": int(time.time()),
                        }))
                    # Track activation for adaptive learning (Phase 3)
                    _skill_tracker.on_skill_activated(
                        activation_id=_act_id,
                        skill_id=_skill_info.get("id", _skill_info.get("name", "")),
                        skill_path=_skill_info.get("path", ""),
                        session_id=conversation_id,
                    )

                # Track file reads that may be skill references
                if tool_name == "file_read" and _skill_tracker.has_activations:
                    _read_path = tool_args.get("path", "")
                    if _read_path:
                        _skill_tracker.on_file_read(_read_path)

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
                    _skill_tracker.on_turn_complete()
                    await _skill_tracker.flush()
                    await _record_outcome()
                    _emit_cost_summary()
                    return result.get("message", ""), tool_calls_made

                # Rule-based pruning (no LLM call) before utility model filter
                pruned = rule_based_prune(tool_name, tool_args, result)
                if pruned is not None:
                    result_json = json.dumps(pruned)
                else:
                    # Fall through to utility model filter
                    _filter_langfuse = {}
                    if _langfuse_meta:
                        _filter_langfuse = {
                            "trace_name": f"tool-filter-{_state.agent_id}-{tool_name}",
                            "tags": [f"agent:{_state.agent_id}", "call_type:filter"],
                        }
                    _filter_result = await filter_tool_result(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        raw_result=result,
                        user_message=user_message,
                        last_assistant_content=last_assistant,
                        utility_model=utility_model,
                        utility_kwargs=utility_kwargs,
                        langfuse_metadata=_filter_langfuse if _filter_langfuse else None,
                    )
                    if isinstance(_filter_result, tuple):
                        result_json, _filter_cost = _filter_result
                    else:
                        result_json, _filter_cost = _filter_result, 0.0
                    _cost_tracking["filter_calls"] += 1
                    _cost_tracking["total_cost"] += _filter_cost

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                })

            # Ensure every tool_use in this batch has a matching tool_result.
            # If the inner loop broke early (loop detection, etc.) some
            # tool calls may be orphaned — Anthropic rejects those.
            _expected_tc_ids = {tc.id for tc in llm_message.tool_calls}
            _emitted_tc_ids = {
                m["tool_call_id"]
                for m in messages[-len(_expected_tc_ids) * 3:]  # scan recent tail only
                if m.get("role") == "tool" and m.get("tool_call_id") in _expected_tc_ids
            }
            for _tc in llm_message.tool_calls:
                if _tc.id not in _emitted_tc_ids:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": _tc.id,
                        "content": json.dumps({"error": "Skipped — agent loop intervention"}),
                    })

            # Flush deferred lifecycle injections now that all tool_result
            # messages have been appended (safe for Anthropic).
            for _inj in _deferred_injections:
                messages.append(_inj)

            # ── Between-turn lifecycle injection (Doc 024) ──
            # After processing all tool calls for this iteration, detect the
            # lifecycle phase and inject Tier 2 fragments into the system prompt
            # for the next iteration. This ensures the agent sees phase-specific
            # guidance (e.g. testing rules during implementation, git rules during
            # committing) on the NEXT LLM call.
            _lifecycle_turn_number += 1
            _tool_call_strings = [
                f"{tc.function.name}:{tc.function.arguments}"
                for tc in llm_message.tool_calls
            ]
            _lc_state = LifecycleState(
                turn_number=_lifecycle_turn_number,
                last_tool_calls=_tool_call_strings,
                has_work_plan=_has_active_plan,
                work_plan_status="in_progress" if _has_active_plan else None,
            )
            _new_phase = detect_phase(_lc_state)

            if _new_phase != _lifecycle_phase:
                _lifecycle_phase = _new_phase
                logger.info("Lifecycle phase changed to: %s", _lifecycle_phase.name)

                # Remove previous lifecycle injection from system prompt
                # (it's appended at the end, so we strip it)
                sys_content = messages[0].get("content", "")
                if isinstance(sys_content, list):
                    # Anthropic cached format — modify text block
                    for block in sys_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block["text"]
                            marker = "\n\n## Current Phase: "
                            if marker in text:
                                block["text"] = text[:text.index(marker)]
                            break
                elif isinstance(sys_content, str):
                    marker = "\n\n## Current Phase: "
                    if marker in sys_content:
                        sys_content = sys_content[:sys_content.index(marker)]
                        messages[0]["content"] = sys_content

                # Inject new lifecycle fragments if not idle
                if _new_phase != Phase.IDLE:
                    _lc_frags = load_lifecycle_fragments(_new_phase, _prompts_dir)
                    _lc_injection = format_lifecycle_injection(_new_phase, _lc_frags)
                    if _lc_injection:
                        if isinstance(messages[0].get("content"), list):
                            for block in messages[0]["content"]:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    block["text"] += _lc_injection
                                    break
                        else:
                            messages[0]["content"] += _lc_injection
                        _lifecycle_injected = True
                        logger.info(
                            "Lifecycle injection: phase=%s fragments=%s",
                            _new_phase.name,
                            [f.path for f in _lc_frags],
                        )

                        # Update Langfuse metadata with Tier 2 fragments
                        if _langfuse_meta:
                            _lc_meta = [
                                {
                                    "source": "lifecycle-tier2",
                                    "path": f.path,
                                    "name": Path(f.path).stem,
                                    "phase": _new_phase.name,
                                    "tokenEstimate": f.token_estimate,
                                }
                                for f in _lc_frags
                            ]
                            # Rebuild audit list: remove old tier2, add new
                            _audit_fragments = [
                                f for f in _audit_fragments
                                if f.get("source") != "lifecycle-tier2"
                            ] + _lc_meta
                            _fragment_names = [f.get("name", "") for f in _audit_fragments]
                            _fragment_total_tokens = sum(
                                f.get("tokens", f.get("tokenEstimate", 0))
                                for f in _audit_fragments
                            )
                            _langfuse_meta.update({
                                "fragments_injected": _audit_fragments,
                                "fragment_count": len(_audit_fragments),
                                "fragment_names": _fragment_names,
                                "fragment_total_tokens": _fragment_total_tokens,
                                "tags": [
                                    f"agent:{_state.agent_id}",
                                    f"fragments:{len(_audit_fragments)}",
                                    f"phase:{_new_phase.name}",
                                ] + [f"prompt:{n}" for n in _fragment_names],
                            })
                            _langfuse_meta["trace_metadata"].update({
                                "fragment_count": len(_audit_fragments),
                                "fragment_names": _fragment_names,
                                "fragment_total_tokens": _fragment_total_tokens,
                                "lifecycle_phase": _new_phase.name,
                            })
                    else:
                        _lifecycle_injected = False
                else:
                    _lifecycle_injected = False

        else:
            await _skill_tracker.flush()
            await _record_outcome()
            _emit_cost_summary()
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

    await _record_outcome()
    _emit_cost_summary()
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
    await db.enable_load_extension(True)
    await db.load_extension(sqlite_vec.loadable_path())
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

    # Export data_dir so modules that resolve DB paths at import time
    # (e.g. skills_db.py) can find it consistently.
    os.environ["BOND_WORKER_DATA_DIR"] = data_dir

    # Load config
    config_file = Path(config_path)
    if config_file.exists():
        _state.config = json.loads(config_file.read_text())
    else:
        logger.warning("Config file not found at %s, using defaults", config_path)
        _state.config = {}

    _state.agent_id = _state.config.get("agent_id", "default")

    # ── Langfuse Observability ──
    # Enable if LANGFUSE_PUBLIC_KEY is set. LiteLLM's built-in callback
    # automatically logs all acompletion() calls to Langfuse.
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        litellm.success_callback = litellm.success_callback or []
        litellm.failure_callback = litellm.failure_callback or []
        if "langfuse" not in litellm.success_callback:
            litellm.success_callback.append("langfuse")
        if "langfuse" not in litellm.failure_callback:
            litellm.failure_callback.append("langfuse")
        logger.info(
            "Langfuse observability enabled (host=%s)",
            os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
    else:
        logger.debug("Langfuse not configured (LANGFUSE_PUBLIC_KEY not set)")

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
    
    # ── Skills: init router + ensure embeddings exist ──
    try:
        from backend.app.agent.tools.skills import init_router, _get_router
        await init_router(persistence=_state.persistence)

        from backend.app.agent.tools.skills_db import list_all_skills, DB_PATH
        import aiosqlite
        skills = await list_all_skills()
        if skills:
            async with aiosqlite.connect(str(DB_PATH)) as db:
                row = await db.execute_fetchall(
                    "SELECT COUNT(*) FROM skill_index WHERE embedding IS NULL"
                )
                missing = row[0][0] if row else 0

            if missing > 0:
                logger.info("Generating embeddings for %d/%d skills...", missing, len(skills))
                from backend.app.agent.tools.skills import _router_settings
                from backend.app.foundations.embeddings.engine import EmbeddingEngine
                from backend.app.agent.skills_embedder import embed_all_skills
                engine = EmbeddingEngine(
                    settings=_router_settings or {"embedding.provider": "local"},
                    db_engine=None,
                )
                count = await embed_all_skills(engine)
                logger.info("Embedded %d skills on startup", count)
            else:
                logger.info("All %d skills already have embeddings", len(skills))
        else:
            logger.info("No skills in skill_index — skipping embedding")
    except Exception:
        logger.warning("Skills embedding on startup failed", exc_info=True)

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
