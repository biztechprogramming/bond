"""Skills database — SQLite-backed skill index, usage tracking, and scoring.

Uses aiosqlite for async access. Database lives at $BOND_WORKER_DATA_DIR/skills.db
(consistent with all other agent DBs).
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

def _resolve_db_path() -> Path:
    """Resolve skills.db path consistently across host and container.

    Priority:
    1. BOND_WORKER_DATA_DIR env var (set in containers)
    2. Project-root/data/ (fallback for host — scheduler, CLI tools)
    """
    env = os.environ.get("BOND_WORKER_DATA_DIR")
    if env:
        return Path(env) / "skills.db"
    # Host fallback: resolve relative to project root (repo/data/)
    return Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "skills.db"


DB_PATH = _resolve_db_path()

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_index (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    source_type TEXT NOT NULL,
    path        TEXT NOT NULL,
    description TEXT,
    l0_summary  TEXT,
    l1_overview TEXT,
    embedding   BLOB,
    updated_at  REAL NOT NULL,
    priority    INTEGER DEFAULT 50,
    pinned      INTEGER DEFAULT 0,
    excluded    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skill_usage (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    activated_at    REAL NOT NULL,
    loaded_at       REAL,
    references_read INTEGER DEFAULT 0,
    scripts_run     INTEGER DEFAULT 0,
    task_completed  INTEGER DEFAULT 0,
    turns_after     INTEGER DEFAULT 0,
    tokens_used     INTEGER DEFAULT 0,
    user_vote       TEXT CHECK(user_vote IN ('up', 'down')),
    voted_at        REAL,
    task_category   TEXT,
    FOREIGN KEY (skill_id) REFERENCES skill_index(id)
);

CREATE TABLE IF NOT EXISTS skill_scores (
    skill_id       TEXT PRIMARY KEY,
    score          REAL NOT NULL DEFAULT 0.5,
    precision_rate REAL,
    depth_rate     REAL,
    total_loads    INTEGER DEFAULT 0,
    total_uses     INTEGER DEFAULT 0,
    thumbs_up      INTEGER DEFAULT 0,
    thumbs_down    INTEGER DEFAULT 0,
    last_used      REAL,
    updated_at     REAL NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES skill_index(id)
);
"""


_MIGRATIONS = [
    "ALTER TABLE skill_index ADD COLUMN l1_overview TEXT",
    "ALTER TABLE skill_index ADD COLUMN embedding BLOB",
    "ALTER TABLE skill_index ADD COLUMN pinned INTEGER DEFAULT 0",
    "ALTER TABLE skill_index ADD COLUMN excluded INTEGER DEFAULT 0",
]


async def _get_db() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    # Run migrations for columns that may not exist in older databases
    for stmt in _MIGRATIONS:
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception:
            pass  # Column already exists
    return db


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

async def index_skills_from_json(catalog_path: str | Path) -> int:
    """Load a skills.json catalog into the skill_index table.

    Returns the number of skills indexed.
    """
    catalog_path = Path(catalog_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    now = time.time()

    db = await _get_db()
    try:
        # Clear and re-insert (full reindex)
        await db.execute("DELETE FROM skill_index")
        for skill in catalog:
            priority = 100 if skill.get("source_type") == "local" else 50
            if skill.get("source") in ("anthropics", "openai"):
                priority = 55  # first-party boost
            await db.execute(
                """INSERT OR REPLACE INTO skill_index
                   (id, name, source, source_type, path, description, l0_summary, l1_overview, updated_at, priority)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    skill["id"], skill["name"], skill["source"],
                    skill["source_type"], skill["path"],
                    skill.get("description", ""), skill.get("l0_summary", ""),
                    skill.get("l1_overview", ""),
                    now, priority,
                ),
            )
            # Ensure a score row exists (cold start = 0.5)
            await db.execute(
                """INSERT OR IGNORE INTO skill_scores
                   (skill_id, score, updated_at) VALUES (?, ?, ?)""",
                (skill["id"], 0.55 if priority > 50 else 0.5, now),
            )
        await db.commit()
        return len(catalog)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

async def record_activation(
    skill_id: str, session_id: str, *, activation_id: str | None = None
) -> str:
    """Record that a skill was activated (L2 loaded). Returns activation_id."""
    act_id = activation_id or f"act_{uuid.uuid4().hex[:12]}"
    now = time.time()
    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO skill_usage (id, skill_id, session_id, activated_at, loaded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (act_id, skill_id, session_id, now, now),
        )
        # Increment total_loads in scores
        await db.execute(
            """UPDATE skill_scores SET total_loads = total_loads + 1, last_used = ?, updated_at = ?
               WHERE skill_id = ?""",
            (now, now, skill_id),
        )
        await db.commit()
        return act_id
    finally:
        await db.close()


async def record_feedback(activation_id: str, vote: str) -> None:
    """Record user feedback (thumbs up/down) for a skill activation."""
    if vote not in ("up", "down"):
        raise ValueError(f"Invalid vote: {vote}")
    now = time.time()
    db = await _get_db()
    try:
        # Update the usage row
        await db.execute(
            "UPDATE skill_usage SET user_vote = ?, voted_at = ? WHERE id = ?",
            (vote, now, activation_id),
        )
        # Get the skill_id for this activation
        cursor = await db.execute(
            "SELECT skill_id FROM skill_usage WHERE id = ?", (activation_id,)
        )
        row = await cursor.fetchone()
        if row:
            skill_id = row[0]
            col = "thumbs_up" if vote == "up" else "thumbs_down"
            await db.execute(
                f"UPDATE skill_scores SET {col} = {col} + 1, updated_at = ? WHERE skill_id = ?",
                (now, skill_id),
            )
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Scoring (§5.2)
# ---------------------------------------------------------------------------

async def get_skill_score(skill_id: str) -> float | None:
    """Get the composite score for a skill."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT score FROM skill_scores WHERE skill_id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else None
    finally:
        await db.close()


async def recalculate_scores() -> int:
    """Recalculate composite scores for all skills per §5.2 model.

    Weights:
      precision (used/loaded): 0.25
      depth:                   0.20
      task_completion:         0.15
      re-activation:           0.10
      recency (30-day decay):  0.10
      user_vote:               0.20

    Returns count of skills updated.
    """
    now = time.time()
    THIRTY_DAYS = 30 * 86400
    SIXTY_DAYS = 60 * 86400

    db = await _get_db()
    try:
        cursor = await db.execute("SELECT skill_id, total_loads, total_uses, thumbs_up, thumbs_down, last_used FROM skill_scores")
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            skill_id = row[0]
            total_loads = row[1] or 0
            total_uses = row[2] or 0
            thumbs_up = row[3] or 0
            thumbs_down = row[4] or 0
            last_used = row[5]

            # Precision
            precision = (total_uses / total_loads) if total_loads > 0 else 0.5

            # Depth — query average references_read + scripts_run
            dcur = await db.execute(
                "SELECT AVG(references_read + scripts_run) FROM skill_usage WHERE skill_id = ?",
                (skill_id,),
            )
            drow = await dcur.fetchone()
            depth_avg = float(drow[0]) if drow and drow[0] else 0
            depth = min(depth_avg / 3.0, 1.0)  # normalize: 3+ references = max

            # Task completion rate
            tcur = await db.execute(
                "SELECT AVG(task_completed) FROM skill_usage WHERE skill_id = ?",
                (skill_id,),
            )
            trow = await tcur.fetchone()
            task_rate = float(trow[0]) if trow and trow[0] else 0.5

            # Re-activation (multiple loads = consistently useful)
            reactivation = min(total_loads / 10.0, 1.0)

            # Recency — exponential decay with 30-day half-life
            if last_used:
                age = now - last_used
                recency = math.exp(-0.693 * age / THIRTY_DAYS)  # ln(2) ≈ 0.693
            else:
                recency = 0.3

            # User vote score
            total_votes = thumbs_up + thumbs_down
            if total_votes > 0:
                vote_score = thumbs_up / total_votes
            else:
                vote_score = 0.5  # neutral

            # Composite
            score = (
                0.25 * precision
                + 0.20 * depth
                + 0.15 * task_rate
                + 0.10 * reactivation
                + 0.10 * recency
                + 0.20 * vote_score
            )

            # Decay toward 0.3 if not used in 60+ days
            if last_used and (now - last_used) > SIXTY_DAYS:
                score = max(score, 0.3) * 0.9 + 0.3 * 0.1

            score = max(0.0, min(1.0, score))

            await db.execute(
                "UPDATE skill_scores SET score = ?, precision_rate = ?, depth_rate = ?, updated_at = ? WHERE skill_id = ?",
                (score, precision, depth, now, skill_id),
            )
            count += 1

        await db.commit()
        return count
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

async def search_skills(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search skills by text similarity (Phase 1: LIKE matching)."""
    db = await _get_db()
    try:
        pattern = f"%{query}%"
        cursor = await db.execute(
            """SELECT si.id, si.name, si.source, si.source_type, si.path,
                      si.description, si.l0_summary, si.priority,
                      COALESCE(ss.score, 0.5) as score
               FROM skill_index si
               LEFT JOIN skill_scores ss ON si.id = ss.skill_id
               WHERE si.name LIKE ? OR si.description LIKE ? OR si.l0_summary LIKE ?
               ORDER BY si.priority DESC, ss.score DESC
               LIMIT ?""",
            (pattern, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def list_all_skills() -> list[dict[str, Any]]:
    """List all indexed skills with their scores."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT si.id, si.name, si.source, si.source_type, si.path,
                      si.description, si.l0_summary, si.priority,
                      COALESCE(ss.score, 0.5) as score
               FROM skill_index si
               LEFT JOIN skill_scores ss ON si.id = ss.skill_id
               ORDER BY si.priority DESC, ss.score DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def list_skills_with_scores() -> list[dict[str, Any]]:
    """List all skills joined with scores for the management UI."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT si.id, si.name, si.source, si.source_type, si.path,
                      si.description, si.l0_summary, si.l1_overview, si.priority,
                      si.pinned, si.excluded, si.updated_at,
                      COALESCE(ss.score, 0.5) as score,
                      COALESCE(ss.total_loads, 0) as total_loads,
                      COALESCE(ss.total_uses, 0) as total_uses,
                      COALESCE(ss.thumbs_up, 0) as thumbs_up,
                      COALESCE(ss.thumbs_down, 0) as thumbs_down,
                      ss.last_used
               FROM skill_index si
               LEFT JOIN skill_scores ss ON si.id = ss.skill_id
               ORDER BY si.pinned DESC, ss.score DESC, si.priority DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_skill_usage_history(skill_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent usage history for a skill."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT id, session_id, activated_at, loaded_at, references_read,
                      scripts_run, task_completed, user_vote, voted_at, task_category
               FROM skill_usage
               WHERE skill_id = ?
               ORDER BY activated_at DESC
               LIMIT ?""",
            (skill_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def set_skill_pinned(skill_id: str, pinned: bool) -> None:
    """Pin or unpin a skill."""
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE skill_index SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, skill_id),
        )
        await db.commit()
    finally:
        await db.close()


async def set_skill_excluded(skill_id: str, excluded: bool) -> None:
    """Exclude or include a skill."""
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE skill_index SET excluded = ? WHERE id = ?",
            (1 if excluded else 0, skill_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_skill_sources() -> list[dict[str, Any]]:
    """List skill sources with counts."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            """SELECT source, source_type, COUNT(*) as count,
                      MAX(updated_at) as last_sync
               FROM skill_index
               GROUP BY source, source_type
               ORDER BY count DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def set_source_excluded(source: str, excluded: bool) -> None:
    """Exclude or include all skills from a source."""
    db = await _get_db()
    try:
        await db.execute(
            "UPDATE skill_index SET excluded = ? WHERE source = ?",
            (1 if excluded else 0, source),
        )
        await db.commit()
    finally:
        await db.close()


async def get_skill_by_id(skill_id: str) -> dict[str, Any] | None:
    """Get a single skill by ID."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM skill_index WHERE id = ?", (skill_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

async def store_embedding(skill_id: str, embedding: list[float]) -> None:
    """Store an embedding vector for a skill as a JSON blob."""
    db = await _get_db()
    try:
        blob = json.dumps(embedding).encode("utf-8")
        await db.execute(
            "UPDATE skill_index SET embedding = ? WHERE id = ?",
            (blob, skill_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_embeddings() -> list[tuple[str, list[float]]]:
    """Return (skill_id, embedding) pairs for all skills with embeddings."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT id, embedding FROM skill_index WHERE embedding IS NOT NULL"
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            try:
                vec = json.loads(row[1])
                results.append((row[0], vec))
            except (json.JSONDecodeError, TypeError):
                continue
        return results
    finally:
        await db.close()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors. Pure Python, no numpy."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
