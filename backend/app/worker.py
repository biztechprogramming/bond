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
        # Turn refcounting for branch reload deferral
        self.active_turns: int = 0
        self.turn_lock: asyncio.Lock = asyncio.Lock()
        self.pending_reload: bool = False
        self.pending_reload_branch: str | None = None
        self.mcp_proxy: Any = None  # MCPProxyClient instance (Design Doc 054)


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

    # Startup branch checkout
    await _checkout_preferred_branch()

    # MCP Proxy Setup (Design Doc 054: host-side MCP via broker)
    try:
        from backend.app.agent.tools.mcp_proxy import MCPProxyClient
        agent_token = os.environ.get("BOND_AGENT_TOKEN", "")
        if _state.persistence and _state.persistence.mode == "api" and agent_token:
            gateway_url = _state.persistence.gateway_url
            _state.mcp_proxy = MCPProxyClient(gateway_url, _state.agent_id, agent_token)
            await _state.mcp_proxy.list_tools()
            logger.info("MCP proxy client initialized (gateway=%s, tools=%d)", gateway_url, len(_state.mcp_proxy._tool_cache))
        else:
            _state.mcp_proxy = None
            logger.info("MCP proxy client not initialized (no API persistence or token)")
    except Exception as e:
        _state.mcp_proxy = None
        logger.error(f"Failed to initialize MCP proxy client: {e}")

    yield

    # MCP Proxy Shutdown
    if getattr(_state, 'mcp_proxy', None):
        try:
            await _state.mcp_proxy.close()
        except Exception:
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

async def _checkout_preferred_branch():
    """Checkout the preferred branch on startup."""
    import subprocess as _sp

    # Try to get preferred branch from gateway
    target_branch = os.environ.get("BOND_GIT_BRANCH", "main")
    if _state.persistence and _state.persistence.mode == "api":
        try:
            gateway_url = _state.persistence.gateway_url.rstrip("/")
            # Pass agent_id so the gateway returns the branch preference for THIS agent
            params = f"?agent_id={_state.agent_id}" if _state.agent_id else ""
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{gateway_url}/api/v1/container/branch{params}")
                if resp.status_code == 200:
                    data = resp.json()
                    target_branch = data.get("branch", target_branch)
        except Exception as e:
            logger.debug("Could not fetch branch preference from gateway: %s", e)

    bond_root = Path("/bond") if Path("/bond").exists() else Path("/workspace/bond")
    if not bond_root.exists():
        return

    try:
        current = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=bond_root, capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        if current != target_branch:
            logger.info("Switching from branch '%s' to '%s'", current, target_branch)
            _sp.run(["git", "fetch", "origin"], cwd=bond_root, capture_output=True, timeout=30)
            _sp.run(["git", "checkout", target_branch], cwd=bond_root, capture_output=True, timeout=10)
            _sp.run(["git", "pull", "--ff-only"], cwd=bond_root, capture_output=True, timeout=30)
            os.environ["BOND_GIT_BRANCH"] = target_branch
    except Exception as e:
        logger.warning("Startup branch checkout failed: %s", e)


async def _shutdown_for_branch_change():
    """Shut down the worker so the container gets destroyed and recreated on the new branch.

    Waits briefly to let the current SSE response stream finish, then exits.
    The gateway saves the branch preference; the next container start will
    checkout the correct branch via _checkout_preferred_branch().
    """
    await asyncio.sleep(2)  # Give SSE stream time to flush
    logger.info("Exiting for branch change — container will be recreated")
    os._exit(0)


async def _do_branch_reload(branch: str):
    """Execute a branch switch: fetch, checkout, pull, rebuild manifest."""
    import subprocess as _sp
    from backend.app.agent.tools.dynamic_loader import generate_manifest as _gen_manifest
    global _prompt_manifest_cache

    bond_root = Path("/bond") if Path("/bond").exists() else Path("/workspace/bond")
    if not bond_root.exists():
        return

    _sp.run(["git", "fetch", "origin"], cwd=bond_root, capture_output=True, timeout=30)
    _sp.run(["git", "checkout", branch], cwd=bond_root, capture_output=True, timeout=10)
    _sp.run(["git", "pull", "--ff-only"], cwd=bond_root, capture_output=True, timeout=30)
    os.environ["BOND_GIT_BRANCH"] = branch

    prompts_dir = bond_root / "prompts"
    if prompts_dir.exists():
        _prompt_manifest_cache = _gen_manifest(prompts_dir)


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


@app.get("/branch")
async def get_branch():
    """Return current branch and turn status."""
    import subprocess as _sp
    bond_root = Path("/bond") if Path("/bond").exists() else Path("/workspace/bond")
    branch = "unknown"
    try:
        branch = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=bond_root, capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        pass
    return {
        "branch": branch,
        "active_turns": _state.active_turns,
        "pending_reload": _state.pending_reload,
    }


@app.post("/reload")
async def reload_prompts(request: Request):
    """Called by Gateway to switch branch.

    Instead of hot-reloading, the worker exits so the container gets
    destroyed and recreated on the correct branch.  If a turn is active,
    the exit is deferred until the turn completes.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    branch = body.get("branch") or os.environ.get("BOND_GIT_BRANCH", "main")

    async with _state.turn_lock:
        if _state.active_turns > 0:
            _state.pending_reload = True
            _state.pending_reload_branch = branch
            return {
                "ok": True,
                "deferred": True,
                "active_turns": _state.active_turns,
            }

    # No active turns — schedule shutdown for container recreation
    logger.info("Branch change to '%s' requested (idle) — shutting down for container recreation", branch)
    asyncio.create_task(_shutdown_for_branch_change())
    return {"ok": True, "deferred": False, "shutting_down": True}




def _tool_not_found_message(tool_name: str, agent_tools: list[str]) -> str:
    """Return an error message with fuzzy-match suggestions for misnamed tools."""
    from difflib import get_close_matches
    suggestions = get_close_matches(tool_name, agent_tools, n=3, cutoff=0.5)
    msg = f"Tool '{tool_name}' is not enabled."
    if suggestions:
        msg += f" Did you mean: {', '.join(suggestions)}?"
    return msg


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
        async with _state.turn_lock:
            _state.active_turns += 1
        try:
            yield _sse_event("status", {"state": "thinking", "conversation_id": conversation_id})

            task = asyncio.create_task(run_loop())
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                yield event
            await task
        finally:
            async with _state.turn_lock:
                _state.active_turns -= 1
                if _state.active_turns <= 0 and _state.pending_reload:
                    branch = _state.pending_reload_branch or os.environ.get("BOND_GIT_BRANCH", "main")
                    _state.pending_reload = False
                    _state.pending_reload_branch = None
                    # Exit the process so the container gets destroyed and recreated
                    # on the correct branch. The gateway has already saved the branch
                    # preference; the next container start will checkout that branch.
                    logger.info("Branch change to '%s' pending — shutting down for container recreation", branch)
                    asyncio.create_task(_shutdown_for_branch_change())

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
    from backend.app.agent.api_key_resolver import ApiKeyResolver
    from backend.app.agent.context_builder import build_agent_context
    from backend.app.agent.loop_state import LoopState
    from backend.app.agent.cost_tracker import CostTracker
    from backend.app.agent.outcome_recorder import OutcomeRecorder
    from backend.app.agent.iteration_handlers import (
        handle_truncation,
        handle_adaptive_budget,
        handle_budget_escalation,
        handle_early_termination,
        handle_batching_nudge,
        detect_loop,
        execute_tool_call,
        handle_lifecycle_injection,
    )

    _skill_tracker = SkillTracker()

    config = _state.config
    model = config["model"]
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

    # ── 1. API key resolution ──
    injected_keys: dict[str, str] = config.get("api_keys", {})
    resolver = ApiKeyResolver(
        injected_keys=injected_keys,
        provider_aliases=config.get("provider_aliases", {}),
        litellm_prefixes=config.get("litellm_prefixes", {}),
        persistence=_state.persistence,
    )
    model, extra_kwargs, utility_kwargs, utility_model = await resolver.resolve_all(
        model, config.get("utility_model", "claude-sonnet-4-6"),
    )

    # ── 2. Context building ──
    ctx = await build_agent_context(
        user_message=user_message,
        history=history,
        conversation_id=conversation_id,
        config=config,
        agent_db=_state.agent_db,
        agent_id=_state.agent_id,
        persistence=_state.persistence,
        plan_id=plan_id,
        event_queue=event_queue,
        sse_event_fn=_sse_event,
        utility_kwargs=utility_kwargs,
        discover_workspace_fn=_discover_workspace,
        mcp_proxy=getattr(_state, 'mcp_proxy', None),
    )
    full_system_prompt = ctx.full_system_prompt
    compressed_history = ctx.compressed_history
    compression_stats = ctx.compression_stats
    _has_active_plan = ctx.has_active_plan
    _active_plan_id = ctx.active_plan_id
    _is_continuation = ctx.is_continuation
    _tier1_meta = ctx.tier1_meta
    _tier3_meta = ctx.tier3_meta
    _fragment_manifest = ctx.fragment_manifest
    _category_manifest = ctx.category_manifest
    _lessons_content = ctx.lessons_content
    windowed_history = ctx.windowed_history

    # Determine if the primary model supports Anthropic prompt caching
    _is_anthropic_model = resolver.resolve_provider(model) == "anthropic"

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

    # ── 3. Tool setup ──
    registry = build_native_registry()

    # Register MCP proxy tools (Design Doc 054)
    mcp_proxy = getattr(_state, 'mcp_proxy', None)
    if mcp_proxy:
        try:
            mcp_tool_names = await mcp_proxy.register_proxy_handlers(registry)
            for name in mcp_tool_names:
                if name not in agent_tools:
                    agent_tools.append(name)
        except Exception as e:
            logger.error(f"Failed to register MCP proxy tools: {e}")

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

    # Append MCP proxy tool definitions (Design Doc 054)
    mcp_proxy = getattr(_state, 'mcp_proxy', None)
    if mcp_proxy:
        mcp_selected = [n for n in selected_tool_names if n.startswith("mcp_") and n not in TOOL_MAP]
        if mcp_selected:
            mcp_defs = mcp_proxy.get_tool_definitions(mcp_selected)
            tool_defs.extend([compact_tool_schema(d) for d in mcp_defs])
            logger.info("Added %d MCP proxy tool definition(s) to LLM call", len(mcp_defs))

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

    # ── 4. Loop state + cost + outcome init ──
    loop = LoopState.create(
        max_iterations=max_iterations,
        preturn_msg_count=len(messages),
        cache_bp2_index=len(messages) - 1,
    )

    cost = CostTracker(conversation_id, max_iterations)

    outcome = OutcomeRecorder(
        conversation_id=conversation_id,
        user_message=user_message,
        agent_db=_state.agent_db,
        config=config,
        tier1_meta=_tier1_meta,
        tier3_meta=_tier3_meta,
        lessons_content=_lessons_content,
        state=_state,
    )
    config = await outcome.apply_experiment_overrides()

    # Initialize lifecycle phase
    _lifecycle_phase = Phase.IDLE
    _prompts_dir = Path("/bond/prompts")
    if not _prompts_dir.exists():
        _prompts_dir = Path(__file__).parent.parent.parent.parent / "prompts"
    loop._lifecycle_phase = _lifecycle_phase

    # ── Langfuse metadata for observability ──
    _langfuse_meta: dict[str, Any] = {}
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        _audit_fragments: list[dict] = []
        for meta in _tier1_meta:
            _audit_fragments.append(meta)
        for meta in _tier3_meta:
            _audit_fragments.append(meta)
        if _category_manifest:
            _audit_fragments.append({
                "source": "category-manifest",
                "name": "prompt_manifest",
                "tokenEstimate": _estimate_tokens(_category_manifest),
            })
        _fragment_names = [f.get("name", "") for f in _audit_fragments]
        _fragment_total_tokens = sum(f.get("tokens", f.get("tokenEstimate", 0)) for f in _audit_fragments)

        _langfuse_meta = {
            "trace_name": f"agent-turn-{_state.agent_id}",
            "session_id": conversation_id,
            "tags": [
                f"agent:{_state.agent_id}",
                f"fragments:{len(_audit_fragments)}",
            ] + [f"prompt:{n}" for n in _fragment_names],
            "trace_metadata": {
                "fragment_count": len(_audit_fragments),
                "fragment_names": _fragment_names,
                "fragment_total_tokens": _fragment_total_tokens,
                "system_prompt_tokens": _estimate_tokens(full_system_prompt),
                "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
                "had_history_compression": compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD,
                "had_sliding_window": len(history) != len(windowed_history) if history else False,
            },
            "fragments_injected": _audit_fragments,
            "fragment_count": len(_audit_fragments),
            "fragment_names": _fragment_names,
            "fragment_total_tokens": _fragment_total_tokens,
            "system_prompt_tokens": _estimate_tokens(full_system_prompt),
            "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
            "had_history_compression": compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD,
            "had_sliding_window": len(history) != len(windowed_history) if history else False,
        }

    # ── 5. Pre-gathering (Design Doc 038) ──
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
                messages.append({
                    "role": "user",
                    "content": f"[Pre-gathered context for this task]\n\n{_pre_gather_result.context_bundle}",
                })
                logger.info(
                    "Pre-gather: injected %d tokens of context",
                    len(_pre_gather_result.context_bundle) // 4,
                )

            if _pre_gather_result and _pre_gather_result.adaptive_budget is not None:
                loop.adaptive_budget = min(max_iterations, _pre_gather_result.adaptive_budget)
                loop.adaptive_budget_set = True
                cost.tracking["iteration_budget"] = loop.adaptive_budget
                logger.info("Pre-gather: set adaptive budget to %d", loop.adaptive_budget)
        except Exception as e:
            logger.warning("Pre-gather phase failed, falling through to normal loop: %s", e)

    loop.is_coding_task = bool(
        _pre_gather_result and _pre_gather_result.delegate_to_coding_agent
    )

    # Reset per-turn budgets
    from backend.app.agent.tools.native import reset_load_context_budget
    reset_load_context_budget()

    # Helper closures for clean return paths
    async def _finish():
        await _skill_tracker.flush()
        await outcome.record(loop.tool_calls_made, cost.tracking)
        cost.emit_summary(_langfuse_meta)

    # ── 6. Main loop ──
    for _iteration in range(max_iterations):
        # Check interrupt
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

        current_max_tokens = loop.TOKEN_TIERS[loop.current_tier]
        context_tokens = _estimate_messages_tokens(messages) + _estimate_tokens(json.dumps(tool_defs))

        _iter_model = model
        _iter_kwargs = extra_kwargs

        # Advance prompt cache breakpoint 2 (Anthropic only)
        if _is_anthropic_model:
            loop.cache_bp2_index = _advance_cache_breakpoint(messages, loop.cache_bp2_index)

        # Langfuse trace naming
        _iter_langfuse_meta = dict(_langfuse_meta) if _langfuse_meta else {}
        if _iter_langfuse_meta:
            _iter_langfuse_meta["trace_name"] = f"agent-turn-{_state.agent_id}-iter-{_iteration}"
            _iter_langfuse_meta.setdefault("tags", [])
            if "call_type:primary" not in _iter_langfuse_meta["tags"]:
                _iter_langfuse_meta["tags"].append("call_type:primary")

        logger.info(
            "LLM request: model=%s tools=%d max_tokens=%d tier=%d context_tokens=~%d msgs=%d cache=%s",
            _iter_model, len(tool_defs), current_max_tokens, loop.current_tier,
            context_tokens, len(messages),
            "anthropic" if _is_anthropic_model else "none",
        )

        # Token budget injection
        _budget_note = ""
        _budget_target_idx = -1
        if _iteration > 0 and messages and messages[-1].get("role") == "tool":
            _budget_note = f"\n[Turn {_iteration + 1}/{max_iterations} | ~{context_tokens} tokens | {loop.tool_calls_made} tool calls]"
            _budget_target_idx = len(messages) - 1
            content = messages[_budget_target_idx].get("content", "")
            if isinstance(content, str):
                messages[_budget_target_idx]["content"] = content + _budget_note

        # Plan-aware iteration budget
        try:
            from backend.app.agent.continuation import IterationBudget
            _iter_budget = IterationBudget(total=max_iterations, used=_iteration)
            _budget_msg = _iter_budget.get_budget_message()
            if _budget_msg:
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
            if _iteration > 0 and _iteration >= int(max_iterations * 0.8):
                messages.append({
                    "role": "user",
                    "content": "SYSTEM: You're approaching your iteration limit. Wrap up or synthesize what you have.",
                })

        # Early termination nudges
        handle_early_termination(_iteration, loop, messages, tool_defs)

        # Log API key info
        if "api_key" in _iter_kwargs:
            logger.debug("Calling LiteLLM with model %s, API key length: %d",
                       _iter_model, len(_iter_kwargs["api_key"]))
        else:
            logger.debug("Calling LiteLLM with model %s, no API key in kwargs", _iter_model)

        # ── LLM call with retry ──
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
                    break
                else:
                    logger.info("LLM call interrupted — stopping agent loop")
                    from backend.app.agent.tools.coding_agent import kill_coding_agent
                    await kill_coding_agent(_state.agent_id)
                    if event_queue is not None:
                        await event_queue.put(_sse_event("status", {"state": "interrupted"}))
                    if _budget_note and _budget_target_idx >= 0:
                        content = messages[_budget_target_idx].get("content", "")
                        if isinstance(content, str) and content.endswith(_budget_note):
                            messages[_budget_target_idx]["content"] = content[:-len(_budget_note)]
                    await _finish()
                    return "", loop.tool_calls_made

            if response.choices:
                break

            # Empty response — exponential backoff
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

        if response is None:
            continue

        # Strip budget note
        if _budget_note and _budget_target_idx >= 0:
            content = messages[_budget_target_idx].get("content", "")
            if isinstance(content, str) and content.endswith(_budget_note):
                messages[_budget_target_idx]["content"] = content[:-len(_budget_note)]

        choice = response.choices[0]
        llm_message = choice.message

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

        # Handle truncation
        trunc_action = handle_truncation(choice, llm_message, loop, messages, outcome, cost, _langfuse_meta)
        if trunc_action == "abort":
            await _finish()
            return (
                "I hit the output token limit multiple times even at the highest setting. "
                "This usually happens with very large file writes. Try asking me to write "
                "the file in smaller sections, or break the task into smaller pieces."
            ), loop.tool_calls_made
        if trunc_action == "continue":
            continue

        # Successful completion — reset adaptive tokens
        loop.current_tier = 0
        loop.continuation_attempts = 0

        # Track cost
        cost.track_primary_call(response, _iter_model, _iteration, input_tokens, output_tokens)

        # Adaptive budget
        handle_adaptive_budget(_iteration, llm_message, loop, cost)

        # Budget escalation
        if handle_budget_escalation(_iteration, llm_message, loop, messages, tool_defs):
            break

        if llm_message.tool_calls:
            _iter_tool_names = [tc.function.name for tc in llm_message.tool_calls]

            # Track consequential calls
            if any(t in loop.CONSEQUENTIAL_TOOLS for t in _iter_tool_names):
                loop.has_made_consequential_call = True

            # Track coding task
            if any(t in loop.CODING_TOOLS for t in _iter_tool_names):
                loop.is_coding_task = True

            # Batching nudge
            handle_batching_nudge(llm_message, loop, messages)

            if llm_message.content:
                last_assistant = llm_message.content
            messages.append(llm_message.model_dump())

            # ── Parallel pre-execution ──
            _parallel_precomputed: dict[str, tuple[dict, float]] = {}
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

                    if event_queue is not None:
                        await event_queue.put(_sse_event("status", {
                            "state": "parallel_execution",
                            "parallel_count": len(_parallel_candidates),
                            "total_count": len(llm_message.tool_calls),
                        }))

            _deferred_injections: list[dict] = []

            for tool_call in llm_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                loop.tool_calls_made += 1

                # Loop detection
                _loop_detected, _loop_msg = detect_loop(tool_name, tool_args, loop)

                if _loop_detected:
                    loop.loop_intervention_count += 1
                    outcome.had_loop_intervention = True

                    if tool_name not in agent_tools:
                        result = {"error": _tool_not_found_message(tool_name, agent_tools)}
                    else:
                        result = await registry.execute(tool_name, tool_args, tool_context)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })

                    if loop.loop_intervention_count > loop.LOOP_MAX_INTERVENTIONS:
                        logger.error(
                            "Loop intervention limit reached (%d interventions). "
                            "Force-stopping agent loop at iteration %d, tool call %d.",
                            loop.loop_intervention_count, _iteration, loop.tool_calls_made,
                        )
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
                                "interventions": loop.loop_intervention_count,
                                "tool_calls_made": loop.tool_calls_made,
                            }))
                        loop.recent_tool_calls.clear()
                        break

                    _deferred_injections.append({
                        "role": "user",
                        "content": (
                            f"{_loop_msg} "
                            "Stop repeating this action. Either try a different approach, "
                            "report what you've found so far, or use the respond tool to "
                            "explain what's blocking you."
                        ),
                    })
                    loop.recent_tool_calls.clear()
                    break

                logger.info("Tool call [%d]: %s args=%s", loop.tool_calls_made, tool_name,
                            {k: (v[:80] + '...' if isinstance(v, str) and len(v) > 80 else v) for k, v in tool_args.items()})

                # Emit tool_call event
                if event_queue is not None:
                    await event_queue.put(_sse_event("status", {"state": "tool_calling"}))
                    await event_queue.put(_sse_event("tool_call", {
                        "tool_name": tool_name,
                        "args": {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in tool_args.items()},
                        "tool_calls_made": loop.tool_calls_made,
                    }))

                # Pre-execution lifecycle hooks (Doc 024)
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
                    result = {"error": _tool_not_found_message(tool_name, agent_tools)}
                else:
                    if tool_name not in selected_tool_names:
                        logger.info("Tool %s not in selected set but enabled — adding dynamically", tool_name)
                        selected_tool_names.append(tool_name)
                        if tool_name in TOOL_MAP:
                            tool_defs.append(compact_tool_schema(TOOL_MAP[tool_name]))
                        elif tool_name.startswith("mcp_") and mcp_proxy:
                            _mcp_dyn = mcp_proxy.get_tool_definitions([tool_name])
                            tool_defs.extend([compact_tool_schema(d) for d in _mcp_dyn])

                    if tool_call.id in _parallel_precomputed:
                        result, duration = _parallel_precomputed[tool_call.id]
                        logger.info("Using precomputed parallel result for %s (%.2fs)", tool_name, duration)
                    else:
                        result, duration = await execute_tool_call(
                            tool_call, tool_name, tool_args,
                            agent_tools, registry, tool_context,
                            _parallel_precomputed,
                            _state.interrupt_event, _state,
                            conversation_id, event_queue,
                        )
                        # Check for injected context from interrupt
                        if _state.pending_messages:
                            for msg in _state.pending_messages:
                                messages.append(msg)
                            _state.pending_messages.clear()

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

                # Emit SSE events from tool results
                if isinstance(result, dict) and "_sse_event" in result and event_queue is not None:
                    sse = result.pop("_sse_event")
                    await event_queue.put(_sse_event(sse["event"], sse.get("data", {})))

                # Smart build output parsing
                if tool_name == "code_execute" and isinstance(result, dict):
                    from backend.app.agent.build_output_parser import parse_build_output
                    _bstdout = result.get("stdout", "")
                    _bstderr = result.get("stderr", "")
                    _bexit = result.get("exit_code", 0)
                    if len(_bstdout) + len(_bstderr) > 500:
                        _parsed = parse_build_output(_bstdout, _bstderr, _bexit)
                        if _parsed is not None:
                            result = {**result, "stdout": _parsed, "_build_parsed": True}

                logger.info("Tool result [%d]: %s",  loop.tool_calls_made,
                            {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k, v in result.items()} if isinstance(result, dict) else result)

                # Track tool names for outcome
                outcome.track_tool(tool_name)

                # Coding agent started SSE
                if (tool_name == "coding_agent"
                    and isinstance(result, dict)
                    and result.get("status") == "started"
                    and event_queue is not None):
                    await event_queue.put(_sse_event("coding_agent_started", {
                        "agent_type": result.get("agent_type", "claude"),
                        "conversation_id": conversation_id,
                    }))

                # Skill activated SSE
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
                    _skill_tracker.on_skill_activated(
                        activation_id=_act_id,
                        skill_id=_skill_info.get("id", _skill_info.get("name", "")),
                        skill_path=_skill_info.get("path", ""),
                        session_id=conversation_id,
                    )

                if tool_name == "file_read" and _skill_tracker.has_activations:
                    _read_path = tool_args.get("path", "")
                    if _read_path:
                        _skill_tracker.on_file_read(_read_path)

                if "_promote" in result:
                    _state._last_sse_events = getattr(_state, "_last_sse_events", [])
                    _state._last_sse_events.append(("memory", result["_promote"]))
                    del result["_promote"]

                if "_sse_event" in result:
                    sse_evt = result.pop("_sse_event")
                    if event_queue is not None:
                        await event_queue.put(_sse_event(sse_evt["event"], sse_evt["data"]))
                    if sse_evt["event"] == "plan_created":
                        _has_active_plan = True
                        _active_plan_id = sse_evt["data"].get("plan_id")
                        if "work_plan" not in selected_tool_names and "work_plan" in agent_tools:
                            selected_tool_names.append("work_plan")
                            if "work_plan" in TOOL_MAP:
                                tool_defs.append(compact_tool_schema(TOOL_MAP["work_plan"]))

                # Terminal tool
                if result.get("_terminal"):
                    _skill_tracker.on_turn_complete()
                    await _finish()
                    return result.get("message", ""), loop.tool_calls_made

                # Tool result filtering
                pruned = rule_based_prune(tool_name, tool_args, result)
                if pruned is not None:
                    result_json = json.dumps(pruned)
                else:
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
                    cost.track_filter_cost(_filter_cost)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                })

            # Fill orphaned tool calls
            _expected_tc_ids = {tc.id for tc in llm_message.tool_calls}
            _emitted_tc_ids = {
                m["tool_call_id"]
                for m in messages[-len(_expected_tc_ids) * 3:]
                if m.get("role") == "tool" and m.get("tool_call_id") in _expected_tc_ids
            }
            for _tc in llm_message.tool_calls:
                if _tc.id not in _emitted_tc_ids:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": _tc.id,
                        "content": json.dumps({"error": "Skipped — agent loop intervention"}),
                    })

            for _inj in _deferred_injections:
                messages.append(_inj)

            # Between-turn lifecycle injection (Doc 024)
            handle_lifecycle_injection(
                llm_message, loop, messages, _has_active_plan,
                _langfuse_meta, _state.agent_id,
            )

        else:
            _final_content = llm_message.content or ""
            if _final_content.strip():
                await _finish()
                return _final_content, loop.tool_calls_made

            logger.warning("Model returned empty content with no tool calls at iteration %d; forcing response", _iteration)
            messages.append(llm_message.model_dump())
            messages.append({
                "role": "user",
                "content": (
                    "SYSTEM: You ended your turn without sending a response to the user. "
                    "You MUST respond. Use the respond tool now to tell the user the outcome "
                    "of what you were working on."
                ),
            })
            continue

    # ── 7. Max iterations cleanup ──
    if _has_active_plan and _state.agent_db:
        try:
            from backend.app.agent.tools.work_plan import checkpoint_active_plan
            saved = await checkpoint_active_plan(
                _state.agent_db, _state.agent_id,
                f"Max iterations ({max_iterations}) reached — saving checkpoint. "
                f"Tool calls made: {loop.tool_calls_made}.",
            )
            if saved:
                logger.info("Work plan checkpoint saved at max iterations")
        except Exception as e:
            logger.warning("Failed to save work plan checkpoint: %s", e)

    try:
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

    await _finish()
    return (
        "I've reached my maximum number of steps. Please try rephrasing.",
        loop.tool_calls_made,
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
                    settings=_router_settings or {"embedding.execution_mode": "local"},
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
