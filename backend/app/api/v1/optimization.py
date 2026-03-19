"""Optimization dashboard API — Design Doc 050.

Provides endpoints for viewing outcome trends, managing lessons,
tuning parameters, monitoring experiments, and configuring retention.
All data comes from the agent's local sqlite DB and the lesson filesystem.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from ulid import ULID

from backend.app.agent.optimizer import (
    PARAM_DEFAULTS,
    PARAM_DESCRIPTIONS,
    TUNABLE_PARAMS,
    _welch_t_test,
    purge_stale_observations,
)
from backend.app.db.agent_db import get_agent_db

logger = logging.getLogger("bond.api.optimization")
router = APIRouter(prefix="/optimization", tags=["optimization"])

# ---------------------------------------------------------------------------
# Paths (same resolution as critic.py)
# ---------------------------------------------------------------------------

_BOND_DIR = Path("/bond")
if not _BOND_DIR.exists():
    _BOND_DIR = Path(__file__).parent.parent.parent.parent.parent  # repo root

LESSONS_DIR = _BOND_DIR / "prompts" / "_optimization" / "lessons"
PROPOSED_DIR = LESSONS_DIR / "proposed"
APPROVED_DIR = LESSONS_DIR / "approved"
REJECTED_DIR = LESSONS_DIR / "rejected"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_LESSON_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_CODE_BLOCK_RE = re.compile(
    r"```(?:python|bash|sh|js|javascript)\b", re.IGNORECASE
)


def _validate_lesson_id(lesson_id: str) -> None:
    if not _LESSON_ID_RE.match(lesson_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid lesson ID",
            headers=None,
        )


def _sanitize_content(content: str) -> None:
    if _CODE_BLOCK_RE.search(content):
        raise HTTPException(
            status_code=400,
            detail="Content must not contain executable code blocks",
        )


def _error(status: int, detail: str, code: str, context: dict | None = None):
    raise HTTPException(
        status_code=status,
        detail={"detail": detail, "code": code, "context": context or {}},
    )


# ---------------------------------------------------------------------------
# Lesson filesystem helpers
# ---------------------------------------------------------------------------


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse optional YAML front-matter from markdown content."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm, parts[2].strip()


def _read_lessons(directory: Path, status: str) -> list[dict[str, Any]]:
    """Read all .md lesson files from a directory."""
    if not directory.exists():
        return []
    lessons = []
    for f in sorted(directory.glob("*.md")):
        if f.name == ".gitkeep":
            continue
        raw = f.read_text(encoding="utf-8")
        fm, body = _parse_front_matter(raw)
        lesson_id = f.stem
        # Extract title: from front-matter or first heading or filename
        title = fm.get("title", "")
        if not title:
            for line in body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        if not title:
            title = lesson_id

        lessons.append({
            "id": lesson_id,
            "filename": f.name,
            "title": title,
            "content": body,
            "status": status,
            "first_observed": fm.get("first_observed", fm.get("originally_proposed", "")),
            "recurrences": int(fm.get("recurrences", 0)),
            "correlated_low_score_turns": int(fm.get("correlated_low_score_turns", 0)),
            "rejected_reason": fm.get("rejected_reason", ""),
            "rejected_at": fm.get("rejected_at", ""),
            "revoked_at": fm.get("revoked_at", ""),
            "created_at": fm.get("created_at", ""),
            "updated_at": fm.get("updated_at", ""),
        })
    return lessons


def _find_lesson(lesson_id: str) -> tuple[Path, str] | None:
    """Find a lesson file across all status directories. Returns (path, status)."""
    _validate_lesson_id(lesson_id)
    for directory, status in [
        (PROPOSED_DIR, "proposed"),
        (APPROVED_DIR, "approved"),
        (REJECTED_DIR, "rejected"),
    ]:
        path = directory / f"{lesson_id}.md"
        if path.exists():
            return path, status
    return None


def _write_front_matter(fm: dict[str, str], body: str) -> str:
    """Serialize front-matter + body into markdown."""
    if not fm:
        return body
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LessonUpdate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    content: str = Field(..., min_length=10, max_length=2000)


class LessonRejectBody(BaseModel):
    reason: str = ""


class ParamUpdate(BaseModel):
    value: Any


class ExperimentCreate(BaseModel):
    param_key: str
    proposed_value: Any


class ExperimentCancelBody(BaseModel):
    reason: str = ""


class RetentionUpdate(BaseModel):
    observations_max_days: int = Field(180, ge=7, le=365)
    observations_max_rows: int = Field(50000, ge=1000, le=500000)
    auto_purge_enabled: bool = True


# ---------------------------------------------------------------------------
# Overview endpoints
# ---------------------------------------------------------------------------


@router.get("/overview")
async def get_overview(
    days: int = Query(30, ge=7, le=90),
    category: str | None = Query(None),
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Aggregated optimization overview stats."""
    cat_filter = ""
    params: list[Any] = []
    if category and category != "all":
        cat_filter = " AND task_category = ?"
        params.append(category)

    # Total observations in period
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM optimization_observations "
        f"WHERE created_at >= datetime('now', '-{days} days'){cat_filter}",
        params,
    )
    total = (await cursor.fetchone())[0]

    # Score averages
    async def _avg_score(d: int) -> float | None:
        p = list(params)
        cur = await db.execute(
            f"SELECT AVG(outcome_score) FROM optimization_observations "
            f"WHERE created_at >= datetime('now', '-{d} days'){cat_filter}",
            p,
        )
        row = await cur.fetchone()
        return round(row[0], 4) if row[0] is not None else None

    avg_7d = await _avg_score(7)
    avg_30d = await _avg_score(30)

    # Previous period for trend
    cur = await db.execute(
        f"SELECT AVG(outcome_score) FROM optimization_observations "
        f"WHERE created_at >= datetime('now', '-{days * 2} days') "
        f"AND created_at < datetime('now', '-{days} days'){cat_filter}",
        params,
    )
    row = await cur.fetchone()
    avg_prev = round(row[0], 4) if row[0] is not None else None

    trend = "stable"
    if avg_7d is not None and avg_prev is not None:
        if avg_7d > avg_prev + 0.02:
            trend = "improving"
        elif avg_7d < avg_prev - 0.02:
            trend = "declining"

    # Failure signals
    async def _signal_count(signal_key: str) -> dict:
        cur = await db.execute(
            f"SELECT COUNT(*) FROM optimization_observations "
            f"WHERE created_at >= datetime('now', '-{days} days') "
            f"AND json_extract(signals_json, '$.{signal_key}') = 1{cat_filter}",
            params,
        )
        count = (await cur.fetchone())[0]
        pct = round(count / total * 100, 1) if total > 0 else 0.0
        return {"count": count, "pct": pct}

    failure_signals = {
        "loop_interventions": await _signal_count("had_loop_intervention"),
        "user_corrections": await _signal_count("had_user_correction"),
        "continuations": await _signal_count("had_continuation"),
        "compressions": await _signal_count("had_compression"),
    }

    # Pending/approved lessons count
    pending = len(_read_lessons(PROPOSED_DIR, "proposed"))
    approved = len(_read_lessons(APPROVED_DIR, "approved"))

    # Experiments
    cur = await db.execute(
        "SELECT COUNT(*) FROM optimization_experiments WHERE status = 'active'"
    )
    active_exp = (await cur.fetchone())[0]
    cur = await db.execute(
        "SELECT COUNT(*) FROM optimization_experiments WHERE status = 'concluded'"
    )
    concluded_exp = (await cur.fetchone())[0]

    # Category breakdown
    cur = await db.execute(
        f"SELECT task_category, COUNT(*) FROM optimization_observations "
        f"WHERE created_at >= datetime('now', '-{days} days') "
        f"GROUP BY task_category"
    )
    categories = {r[0] or "unknown": r[1] for r in await cur.fetchall()}

    return {
        "period_days": days,
        "total_observations": total,
        "avg_score_7d": avg_7d,
        "avg_score_30d": avg_30d,
        "avg_score_prev_30d": avg_prev,
        "score_trend": trend,
        "pending_lessons": pending,
        "approved_lessons": approved,
        "active_experiments": active_exp,
        "concluded_experiments": concluded_exp,
        "failure_signals": failure_signals,
        "categories": categories,
    }


@router.get("/outcomes")
async def get_outcomes(
    days: int = Query(30, ge=7, le=90),
    category: str | None = Query(None),
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Daily aggregated outcome data for charts."""
    cat_filter = ""
    params: list[Any] = [f"-{days} days"]
    if category and category != "all":
        cat_filter = " AND task_category = ?"
        params.append(category)

    cursor = await db.execute(
        f"""
        SELECT
            date(created_at) as day,
            AVG(outcome_score) as avg_score,
            COUNT(*) as turn_count,
            AVG(json_extract(signals_json, '$.total_cost')) as avg_cost,
            AVG(json_extract(signals_json, '$.tool_calls')) as avg_tool_calls,
            AVG(json_extract(signals_json, '$.iterations')) as avg_iterations,
            SUM(CASE WHEN json_extract(signals_json, '$.had_loop_intervention') = 1 THEN 1 ELSE 0 END) as loop_interventions,
            SUM(CASE WHEN json_extract(signals_json, '$.had_user_correction') = 1 THEN 1 ELSE 0 END) as user_corrections
        FROM optimization_observations
        WHERE created_at >= datetime('now', ?){cat_filter}
        GROUP BY date(created_at)
        ORDER BY day
        """,
        params,
    )
    rows = await cursor.fetchall()

    result = []
    for r in rows:
        result.append({
            "date": r[0],
            "avg_score": round(r[1], 4) if r[1] else None,
            "turn_count": r[2],
            "avg_cost": round(r[3], 6) if r[3] else None,
            "avg_tool_calls": round(r[4], 1) if r[4] else None,
            "avg_iterations": round(r[5], 1) if r[5] else None,
            "loop_interventions": r[6],
            "user_corrections": r[7],
        })
    return {"days": result}


# ---------------------------------------------------------------------------
# Lessons endpoints
# ---------------------------------------------------------------------------


@router.get("/lessons")
async def list_lessons(
    status: str = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    q: str | None = Query(None),
):
    """List lessons from the filesystem."""
    all_lessons: list[dict] = []
    if status in ("all", "proposed"):
        all_lessons.extend(_read_lessons(PROPOSED_DIR, "proposed"))
    if status in ("all", "approved"):
        all_lessons.extend(_read_lessons(APPROVED_DIR, "approved"))
    if status in ("all", "rejected"):
        all_lessons.extend(_read_lessons(REJECTED_DIR, "rejected"))

    # Free-text filter
    if q:
        q_lower = q.lower()
        all_lessons = [
            l for l in all_lessons
            if q_lower in l["title"].lower() or q_lower in l["content"].lower()
        ]

    total = len(all_lessons)
    pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    items = all_lessons[start : start + per_page]

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.post("/lessons/{lesson_id}/approve")
async def approve_lesson(lesson_id: str):
    """Move a proposed lesson to approved."""
    result = _find_lesson(lesson_id)
    if result is None:
        _error(404, "Lesson not found", "LESSON_NOT_FOUND")
    path, status = result
    if status != "proposed":
        _error(409, f"Lesson is already {status}", "LESSON_NOT_PROPOSED", {"current_status": status})

    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    dest = APPROVED_DIR / path.name
    path.rename(dest)
    logger.info("Lesson approved: %s", lesson_id)
    return {"id": lesson_id, "status": "approved"}


@router.post("/lessons/{lesson_id}/reject")
async def reject_lesson(lesson_id: str, body: LessonRejectBody | None = None):
    """Move a proposed lesson to rejected."""
    result = _find_lesson(lesson_id)
    if result is None:
        _error(404, "Lesson not found", "LESSON_NOT_FOUND")
    path, status = result
    if status != "proposed":
        _error(409, f"Lesson is already {status}", "LESSON_NOT_PROPOSED", {"current_status": status})

    # Append rejection metadata to front-matter
    raw = path.read_text(encoding="utf-8")
    fm, content = _parse_front_matter(raw)
    fm["rejected_at"] = datetime.utcnow().isoformat()
    if body and body.reason:
        fm["rejected_reason"] = body.reason

    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    dest = REJECTED_DIR / path.name
    dest.write_text(_write_front_matter(fm, content), encoding="utf-8")
    path.unlink()
    logger.info("Lesson rejected: %s", lesson_id)
    return {"id": lesson_id, "status": "rejected"}


@router.post("/lessons/{lesson_id}/revoke")
async def revoke_lesson(lesson_id: str):
    """Move an approved lesson back to proposed."""
    result = _find_lesson(lesson_id)
    if result is None:
        _error(404, "Lesson not found", "LESSON_NOT_FOUND")
    path, status = result
    if status != "approved":
        _error(409, f"Lesson is {status}, not approved", "LESSON_NOT_APPROVED", {"current_status": status})

    raw = path.read_text(encoding="utf-8")
    fm, content = _parse_front_matter(raw)
    fm["revoked_at"] = datetime.utcnow().isoformat()

    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    dest = PROPOSED_DIR / path.name
    dest.write_text(_write_front_matter(fm, content), encoding="utf-8")
    path.unlink()
    logger.info("Lesson revoked: %s", lesson_id)
    return {"id": lesson_id, "status": "proposed"}


@router.put("/lessons/{lesson_id}")
async def update_lesson(lesson_id: str, body: LessonUpdate):
    """Update a lesson's title and content."""
    _sanitize_content(body.content)
    result = _find_lesson(lesson_id)
    if result is None:
        _error(404, "Lesson not found", "LESSON_NOT_FOUND")
    path, status = result

    raw = path.read_text(encoding="utf-8")
    fm, _ = _parse_front_matter(raw)
    fm["title"] = body.title
    fm["updated_at"] = datetime.utcnow().isoformat()

    path.write_text(_write_front_matter(fm, body.content), encoding="utf-8")
    logger.info("Lesson updated: %s", lesson_id)
    return {"id": lesson_id, "status": status, "title": body.title}


# ---------------------------------------------------------------------------
# Parameters endpoints
# ---------------------------------------------------------------------------


async def _get_current_params(db: aiosqlite.Connection) -> dict[str, Any]:
    """Read current param values from the agent DB settings table."""
    values: dict[str, Any] = {}
    try:
        cursor = await db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'param.%'"
        )
        for row in await cursor.fetchall():
            param_key = row[0].replace("param.", "")
            try:
                values[param_key] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                values[param_key] = row[1]
    except Exception:
        pass
    return values


async def _set_param(
    db: aiosqlite.Connection,
    key: str,
    value: Any,
    old_value: Any,
    changed_by: str,
    experiment_id: str | None = None,
) -> None:
    """Set a param value and record history."""
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (f"param.{key}", json.dumps(value)),
    )
    await db.execute(
        "INSERT INTO optimization_param_history (param_key, old_value, new_value, changed_by, experiment_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (key, json.dumps(old_value) if old_value is not None else None, json.dumps(value), changed_by, experiment_id),
    )
    await db.commit()


@router.get("/params")
async def list_params(db: aiosqlite.Connection = Depends(get_agent_db)):
    """List all tunable parameters with current values and experiment info."""
    current = await _get_current_params(db)
    result = []
    for key, spec in TUNABLE_PARAMS.items():
        default = PARAM_DEFAULTS.get(key)
        current_val = current.get(key, default)

        # Latest experiment for this param
        cur = await db.execute(
            "SELECT id, status, proposed_value, control_mean_score, experiment_mean_score, "
            "p_value, conclusion, control_obs_count, experiment_obs_count "
            "FROM optimization_experiments WHERE param_key = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (key,),
        )
        exp_row = await cur.fetchone()
        experiment = None
        if exp_row:
            experiment = {
                "id": exp_row[0],
                "status": exp_row[1],
                "proposed_value": exp_row[2],
                "control_mean_score": exp_row[3],
                "experiment_mean_score": exp_row[4],
                "p_value": exp_row[5],
                "conclusion": exp_row[6],
                "control_obs_count": exp_row[7],
                "experiment_obs_count": exp_row[8],
            }

        # Last change info
        cur = await db.execute(
            "SELECT changed_at, changed_by FROM optimization_param_history "
            "WHERE param_key = ? ORDER BY changed_at DESC LIMIT 1",
            (key,),
        )
        hist_row = await cur.fetchone()

        result.append({
            "key": key,
            "description": PARAM_DESCRIPTIONS.get(key, ""),
            "type": spec["type"],
            "min": spec["min"],
            "max": spec["max"],
            "step": spec["step"],
            "default_value": default,
            "current_value": current_val,
            "last_changed_at": hist_row[0] if hist_row else None,
            "last_changed_by": hist_row[1] if hist_row else None,
            "experiment": experiment,
        })
    return {"params": result}


@router.put("/params/{key}")
async def update_param(
    key: str,
    body: ParamUpdate,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Update a parameter value."""
    if key not in TUNABLE_PARAMS:
        _error(404, f"Parameter '{key}' not found", "PARAM_NOT_FOUND")
    spec = TUNABLE_PARAMS[key]
    value = body.value

    # Type check
    if spec["type"] == "int":
        if not isinstance(value, int):
            _error(400, "Value must be an integer", "PARAM_TYPE_MISMATCH")
    elif spec["type"] == "float":
        if not isinstance(value, (int, float)):
            _error(400, "Value must be a number", "PARAM_TYPE_MISMATCH")
        value = float(value)

    # Range check
    if value < spec["min"] or value > spec["max"]:
        _error(400, f"Value must be between {spec['min']} and {spec['max']}", "PARAM_OUT_OF_RANGE")

    # Step check
    remainder = (value - spec["min"]) % spec["step"]
    if abs(remainder) > 1e-9 and abs(remainder - spec["step"]) > 1e-9:
        _error(400, f"Value must land on a valid step of {spec['step']}", "PARAM_INVALID_STEP")

    current = await _get_current_params(db)
    old_value = current.get(key, PARAM_DEFAULTS.get(key))
    await _set_param(db, key, value, old_value, "manual")
    return {"key": key, "value": value, "previous_value": old_value}


@router.post("/params/{key}/rollback")
async def rollback_param(
    key: str,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Rollback a parameter to its previous value."""
    if key not in TUNABLE_PARAMS:
        _error(404, f"Parameter '{key}' not found", "PARAM_NOT_FOUND")

    # Get most recent history entries
    cursor = await db.execute(
        "SELECT old_value, new_value FROM optimization_param_history "
        "WHERE param_key = ? ORDER BY changed_at DESC LIMIT 1",
        (key,),
    )
    row = await cursor.fetchone()

    current = await _get_current_params(db)
    current_val = current.get(key, PARAM_DEFAULTS.get(key))

    if row and row[0] is not None:
        restore_val = json.loads(row[0])
    else:
        restore_val = PARAM_DEFAULTS.get(key)

    if restore_val is None:
        _error(400, "No previous value to rollback to", "NO_ROLLBACK_TARGET")

    await _set_param(db, key, restore_val, current_val, "rollback")
    return {"key": key, "restored_value": restore_val, "rolled_back_value": current_val}


@router.get("/params/{key}/history")
async def get_param_history(
    key: str,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Return change history for a parameter."""
    if key not in TUNABLE_PARAMS:
        _error(404, f"Parameter '{key}' not found", "PARAM_NOT_FOUND")

    cursor = await db.execute(
        "SELECT new_value, changed_at, changed_by, experiment_id "
        "FROM optimization_param_history WHERE param_key = ? "
        "ORDER BY changed_at ASC",
        (key,),
    )
    rows = await cursor.fetchall()
    history = []
    for r in rows:
        entry: dict[str, Any] = {
            "value": json.loads(r[0]) if r[0] else None,
            "changed_at": r[1],
            "changed_by": r[2],
        }
        if r[3]:
            entry["experiment_id"] = r[3]
        history.append(entry)

    return {"key": key, "history": history}


# ---------------------------------------------------------------------------
# Experiments endpoints
# ---------------------------------------------------------------------------


@router.get("/experiments")
async def list_experiments(
    status: str = Query("all"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """List experiments with pagination."""
    where = ""
    params: list[Any] = []
    if status != "all":
        where = "WHERE status = ?"
        params.append(status)

    # Total count
    cursor = await db.execute(
        f"SELECT COUNT(*) FROM optimization_experiments {where}", params
    )
    total = (await cursor.fetchone())[0]

    offset = (page - 1) * per_page
    cursor = await db.execute(
        f"""
        SELECT id, param_key, baseline_value, proposed_value, rationale,
               status, created_at, control_obs_count, experiment_obs_count,
               control_mean_score, experiment_mean_score, p_value,
               concluded_at, conclusion
        FROM optimization_experiments {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    )
    rows = await cursor.fetchall()

    items = []
    for r in rows:
        exp: dict[str, Any] = {
            "id": r[0],
            "param_key": r[1],
            "baseline_value": r[2],
            "proposed_value": r[3],
            "rationale": r[4],
            "status": r[5],
            "created_at": r[6],
            "control_obs_count": r[7],
            "experiment_obs_count": r[8],
            "control_mean_score": r[9],
            "experiment_mean_score": r[10],
            "p_value": r[11],
            "concluded_at": r[12],
            "conclusion": r[13],
        }
        # Add min_obs and expiry info for active experiments
        if r[5] == "active":
            exp["min_obs_per_cohort"] = 30
            exp["max_duration_days"] = 14
            created = datetime.fromisoformat(r[6])
            exp["expires_at"] = (created + timedelta(days=14)).isoformat()
        items.append(exp)

    pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


@router.post("/experiments")
async def create_experiment(
    body: ExperimentCreate,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Start a new parameter experiment."""
    if body.param_key not in TUNABLE_PARAMS:
        _error(404, f"Parameter '{body.param_key}' not found", "PARAM_NOT_FOUND")

    spec = TUNABLE_PARAMS[body.param_key]
    value = body.proposed_value

    # Type/range/step validation
    if spec["type"] == "int":
        if not isinstance(value, int):
            _error(400, "Proposed value must be an integer", "PARAM_TYPE_MISMATCH")
    elif spec["type"] == "float":
        if not isinstance(value, (int, float)):
            _error(400, "Proposed value must be a number", "PARAM_TYPE_MISMATCH")
        value = float(value)

    if value < spec["min"] or value > spec["max"]:
        _error(400, f"Value must be between {spec['min']} and {spec['max']}", "PARAM_OUT_OF_RANGE")

    remainder = (value - spec["min"]) % spec["step"]
    if abs(remainder) > 1e-9 and abs(remainder - spec["step"]) > 1e-9:
        _error(400, f"Value must land on a valid step of {spec['step']}", "PARAM_INVALID_STEP")

    # Check current value
    current = await _get_current_params(db)
    current_val = current.get(body.param_key, PARAM_DEFAULTS.get(body.param_key))
    if current_val is not None and value == current_val:
        _error(400, "Proposed value is the same as current value", "PARAM_SAME_VALUE")

    # Max 1 active experiment
    cursor = await db.execute(
        "SELECT id FROM optimization_experiments WHERE status = 'active' LIMIT 1"
    )
    active = await cursor.fetchone()
    if active:
        _error(409, "An experiment is already active", "EXPERIMENT_ALREADY_ACTIVE", {"active_experiment_id": active[0]})

    # 30-day cooldown for same param+value
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    cursor = await db.execute(
        "SELECT id FROM optimization_experiments "
        "WHERE param_key = ? AND proposed_value = ? AND created_at > ?",
        (body.param_key, str(value), cutoff),
    )
    recent = await cursor.fetchone()
    if recent:
        _error(409, "Same param+value was tested within the last 30 days", "EXPERIMENT_COOLDOWN", {"recent_experiment_id": recent[0]})

    # Create
    exp_id = str(ULID())
    baseline = str(current_val) if current_val is not None else str(spec.get("default"))
    await db.execute(
        """
        INSERT INTO optimization_experiments
            (id, param_key, baseline_value, proposed_value, status)
        VALUES (?, ?, ?, ?, 'active')
        """,
        (exp_id, body.param_key, baseline, str(value)),
    )
    await db.commit()
    logger.info("Experiment created: %s (%s: %s -> %s)", exp_id, body.param_key, baseline, value)
    return {"id": exp_id, "status": "active", "param_key": body.param_key, "proposed_value": value}


@router.post("/experiments/{experiment_id}/conclude")
async def conclude_experiment(
    experiment_id: str,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Force-conclude an experiment."""
    cursor = await db.execute(
        "SELECT id, param_key, status FROM optimization_experiments WHERE id = ?",
        (experiment_id,),
    )
    row = await cursor.fetchone()
    if not row:
        _error(404, "Experiment not found", "EXPERIMENT_NOT_FOUND")
    if row[2] != "active":
        _error(409, f"Experiment is {row[2]}, not active", "EXPERIMENT_NOT_ACTIVE")

    # Get scores
    from backend.app.agent.optimizer import get_cohort_scores

    control_scores = await get_cohort_scores(experiment_id, "control", db)
    experiment_scores = await get_cohort_scores(experiment_id, experiment_id, db)

    warning = None
    if len(control_scores) < 10 or len(experiment_scores) < 10:
        conclusion = "inconclusive"
        t_stat, p_value = 0.0, 1.0
        warning = "Insufficient data for statistical significance"
    else:
        t_stat, p_value = _welch_t_test(experiment_scores, control_scores)
        control_mean = statistics.mean(control_scores) if control_scores else 0
        experiment_mean = statistics.mean(experiment_scores) if experiment_scores else 0
        conclusion = "inconclusive"
        if p_value < 0.05:
            conclusion = "promoted" if experiment_mean > control_mean else "rejected"

    control_mean = statistics.mean(control_scores) if control_scores else None
    experiment_mean = statistics.mean(experiment_scores) if experiment_scores else None

    await db.execute(
        """
        UPDATE optimization_experiments
        SET status = 'concluded', conclusion = ?, p_value = ?,
            control_obs_count = ?, experiment_obs_count = ?,
            control_mean_score = ?, experiment_mean_score = ?,
            concluded_at = datetime('now')
        WHERE id = ?
        """,
        (conclusion, p_value, len(control_scores), len(experiment_scores),
         control_mean, experiment_mean, experiment_id),
    )
    await db.commit()

    result: dict[str, Any] = {
        "id": experiment_id,
        "status": "concluded",
        "conclusion": conclusion,
        "p_value": p_value,
        "control_obs_count": len(control_scores),
        "experiment_obs_count": len(experiment_scores),
        "control_mean_score": control_mean,
        "experiment_mean_score": experiment_mean,
    }
    if warning:
        result["warning"] = warning
    return result


@router.post("/experiments/{experiment_id}/cancel")
async def cancel_experiment(
    experiment_id: str,
    body: ExperimentCancelBody | None = None,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Cancel an active experiment."""
    cursor = await db.execute(
        "SELECT id, status FROM optimization_experiments WHERE id = ?",
        (experiment_id,),
    )
    row = await cursor.fetchone()
    if not row:
        _error(404, "Experiment not found", "EXPERIMENT_NOT_FOUND")
    if row[1] != "active":
        _error(409, f"Experiment is {row[1]}, not active", "EXPERIMENT_NOT_ACTIVE")

    reason = body.reason if body else ""
    await db.execute(
        "UPDATE optimization_experiments SET status = 'cancelled', "
        "conclusion = ?, concluded_at = datetime('now') WHERE id = ?",
        (reason or "cancelled", experiment_id),
    )
    await db.commit()
    return {"id": experiment_id, "status": "cancelled"}


@router.get("/experiments/{experiment_id}/scores")
async def get_experiment_scores(
    experiment_id: str,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Return raw score arrays for both cohorts."""
    cursor = await db.execute(
        "SELECT id FROM optimization_experiments WHERE id = ?",
        (experiment_id,),
    )
    if not await cursor.fetchone():
        _error(404, "Experiment not found", "EXPERIMENT_NOT_FOUND")

    from backend.app.agent.optimizer import get_cohort_scores

    control = await get_cohort_scores(experiment_id, "control", db)
    experiment = await get_cohort_scores(experiment_id, experiment_id, db)

    def _stats(scores: list[float]) -> dict[str, Any]:
        if not scores:
            return {"mean": None, "median": None, "std": None, "min": None, "max": None, "count": 0}
        return {
            "mean": round(statistics.mean(scores), 4),
            "median": round(statistics.median(scores), 4),
            "std": round(statistics.stdev(scores), 4) if len(scores) > 1 else 0.0,
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
            "count": len(scores),
        }

    return {
        "control": control,
        "experiment": experiment,
        "control_stats": _stats(control),
        "experiment_stats": _stats(experiment),
    }


# ---------------------------------------------------------------------------
# Retention endpoints
# ---------------------------------------------------------------------------


@router.get("/retention")
async def get_retention(db: aiosqlite.Connection = Depends(get_agent_db)):
    """Return retention stats and policy."""
    # Observations stats
    cur = await db.execute("SELECT COUNT(*) FROM optimization_observations")
    obs_count = (await cur.fetchone())[0]

    cur = await db.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM optimization_observations"
    )
    row = await cur.fetchone()
    oldest, newest = row[0], row[1]

    # Rough storage estimate: ~1KB per row + embeddings
    storage_mb = round(obs_count * 1.2 / 1024, 1)

    # Candidates stats
    cur = await db.execute("SELECT COUNT(*), SUM(CASE WHEN promoted THEN 1 ELSE 0 END) FROM optimization_candidates")
    cand_row = await cur.fetchone()
    cand_count = cand_row[0]
    promoted_count = cand_row[1] or 0

    # Read retention policy from settings
    policy = {
        "observations_max_days": 180,
        "observations_max_rows": 50000,
        "candidates_keep_promoted": True,
        "auto_purge_enabled": True,
        "last_purge_at": None,
        "next_purge_at": None,
    }
    try:
        cur = await db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'retention.%'"
        )
        for row in await cur.fetchall():
            setting_key = row[0].replace("retention.", "")
            try:
                policy[setting_key] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                policy[setting_key] = row[1]
    except Exception:
        pass

    return {
        "observations": {
            "total_count": obs_count,
            "oldest": oldest,
            "newest": newest,
            "storage_estimate_mb": storage_mb,
        },
        "candidates": {
            "total_count": cand_count,
            "promoted_count": promoted_count,
            "storage_estimate_mb": round(cand_count * 1.1 / 1024, 1) if cand_count else 0,
        },
        "retention_policy": policy,
    }


@router.put("/retention")
async def update_retention(
    body: RetentionUpdate,
    db: aiosqlite.Connection = Depends(get_agent_db),
):
    """Update retention policy settings."""
    for key, value in [
        ("observations_max_days", body.observations_max_days),
        ("observations_max_rows", body.observations_max_rows),
        ("auto_purge_enabled", body.auto_purge_enabled),
    ]:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (f"retention.{key}", json.dumps(value)),
        )
    await db.commit()

    # Run purge if enabled
    if body.auto_purge_enabled:
        await purge_stale_observations(db, body.observations_max_days, body.observations_max_rows)

    return {"status": "updated", "policy": {
        "observations_max_days": body.observations_max_days,
        "observations_max_rows": body.observations_max_rows,
        "auto_purge_enabled": body.auto_purge_enabled,
    }}
