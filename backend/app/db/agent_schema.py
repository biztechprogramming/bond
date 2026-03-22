"""Agent DB schema and initialisation.

Single source of truth for the agent.db SQLite schema and the
``init_agent_db()`` function.  Both the worker and the FastAPI
dependency (``agent_db.get_agent_db``) call into this module so
schema creation and migrations are never duplicated.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger("bond.db.agent_schema")

# ---------------------------------------------------------------------------
# Agent DB schema (applied on startup via CREATE IF NOT EXISTS)
# ---------------------------------------------------------------------------

AGENT_DB_SCHEMA = """\
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
# Migrations
# ---------------------------------------------------------------------------

def _resolve_migrations_dir() -> Path | None:
    """Locate the migrations directory (container or local dev)."""
    candidates = [
        Path("/bond/migrations"),
        Path(__file__).resolve().parent.parent.parent.parent / "migrations",
    ]
    for d in candidates:
        if d.exists():
            return d
    return None


async def _run_migrations(db: aiosqlite.Connection) -> int:
    """Run .up.sql migration scripts. Returns count of scripts processed."""
    migrations_dir = _resolve_migrations_dir()
    if not migrations_dir:
        logger.warning("Migrations directory not found, skipping agent DB migrations")
        return 0

    migration_files = sorted(
        f for f in migrations_dir.iterdir() if f.name.endswith(".up.sql")
    )
    for mf in migration_files:
        try:
            sql = mf.read_text()
            await db.executescript(sql)
            logger.debug("Migration applied to agent.db: %s", mf.name)
        except Exception as e:
            logger.debug(
                "Migration skipped (already applied or incompatible): %s — %s",
                mf.name, e,
            )
    await db.commit()
    return len(migration_files)


# ---------------------------------------------------------------------------
# Public init function
# ---------------------------------------------------------------------------

async def init_agent_db(
    data_dir: Path | str,
    *,
    load_vec_extension: bool = False,
) -> aiosqlite.Connection:
    """Open (or create) the agent's local SQLite DB, apply schema & migrations.

    This is the **single** initialisation path for agent.db.

    Args:
        data_dir: Directory that contains (or will contain) ``agent.db``.
        load_vec_extension: If *True*, load the ``sqlite-vec`` extension
            (requires the package to be installed).  The worker sets this;
            the lightweight FastAPI path does not need it.
    """
    data_dir = Path(data_dir)
    db_path = data_dir / "agent.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row

    if load_vec_extension:
        try:
            import sqlite_vec
            await db.enable_load_extension(True)
            await db.load_extension(sqlite_vec.loadable_path())
        except Exception as e:
            logger.warning("sqlite-vec extension not loaded: %s", e)

    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    # Apply hardcoded schema (CREATE IF NOT EXISTS — safe to re-run)
    await db.executescript(AGENT_DB_SCHEMA)
    await db.commit()

    # Run migration scripts
    count = await _run_migrations(db)
    logger.info(
        "Agent DB ready at %s (%d migration scripts processed)", db_path, count
    )

    # Attach shared.db if present
    shared_path = data_dir / "shared" / "shared.db"
    if shared_path.exists():
        try:
            await db.execute(f"ATTACH DATABASE '{shared_path}' AS shared")
            logger.info("Attached shared.db from %s", shared_path)
        except Exception as e:
            logger.warning("Failed to attach shared.db: %s", e)

    return db
