"""Tests for the optimization dashboard API (Design Doc 050)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS optimization_candidates (
    id TEXT PRIMARY KEY,
    lesson_text TEXT NOT NULL,
    source_observation_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    similar_count INTEGER DEFAULT 0,
    promoted BOOLEAN DEFAULT FALSE,
    promoted_at TEXT
);
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
CREATE TABLE IF NOT EXISTS optimization_param_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    param_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    experiment_id TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""

SIGNALS_OK = json.dumps({
    "tool_calls": 3,
    "iterations": 2,
    "total_cost": 0.05,
    "had_loop_intervention": 0,
    "had_user_correction": 0,
    "had_continuation": 0,
    "had_compression": 0,
})

SIGNALS_BAD = json.dumps({
    "tool_calls": 10,
    "iterations": 8,
    "total_cost": 0.20,
    "had_loop_intervention": 1,
    "had_user_correction": 1,
    "had_continuation": 1,
    "had_compression": 1,
})


def _uid() -> str:
    return uuid.uuid4().hex[:26]


@pytest.fixture()
async def opt_db(tmp_path):
    """Create a temp aiosqlite DB with the optimization schema."""
    db_path = tmp_path / "agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.executescript(SCHEMA)
    await db.commit()
    yield db
    await db.close()


@pytest.fixture()
async def opt_client(opt_db, tmp_path, monkeypatch):
    """AsyncClient with the optimization router, using the temp DB and lesson dirs."""
    from fastapi import FastAPI

    import backend.app.api.v1.optimization as opt_mod

    app = FastAPI()
    app.include_router(opt_mod.router, prefix="/api/v1")

    # Override the DB dependency
    async def _override_db():
        return opt_db

    app.dependency_overrides[opt_mod.get_agent_db] = _override_db

    # Override lesson directories to use tmp_path
    proposed = tmp_path / "lessons" / "proposed"
    approved = tmp_path / "lessons" / "approved"
    rejected = tmp_path / "lessons" / "rejected"
    proposed.mkdir(parents=True, exist_ok=True)
    approved.mkdir(parents=True, exist_ok=True)
    rejected.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(opt_mod, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr(opt_mod, "PROPOSED_DIR", proposed)
    monkeypatch.setattr(opt_mod, "APPROVED_DIR", approved)
    monkeypatch.setattr(opt_mod, "REJECTED_DIR", rejected)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _seed_observations(db: aiosqlite.Connection, count: int, **overrides):
    """Insert count observations with optional overrides."""
    base_time = datetime.utcnow() - timedelta(days=15)
    for i in range(count):
        ts = (base_time + timedelta(hours=i * 4)).isoformat()
        await db.execute(
            "INSERT INTO optimization_observations "
            "(id, conversation_id, turn_index, created_at, task_category, "
            "user_message_preview, signals_json, outcome_score, cohort) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                overrides.get("id", _uid()),
                overrides.get("conversation_id", f"conv-{i}"),
                overrides.get("turn_index", i),
                overrides.get("created_at", ts),
                overrides.get("task_category", "coding"),
                overrides.get("user_message_preview", f"do thing {i}"),
                overrides.get("signals_json", SIGNALS_OK),
                overrides.get("outcome_score", 0.8),
                overrides.get("cohort", "control"),
            ),
        )
    await db.commit()


# ── Overview ──


@pytest.mark.asyncio
async def test_overview_empty_db(opt_client):
    r = await opt_client.get("/api/v1/optimization/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total_observations"] == 0
    assert data["avg_score_7d"] is None
    assert data["pending_lessons"] == 0


@pytest.mark.asyncio
async def test_overview_with_data(opt_client, opt_db):
    await _seed_observations(opt_db, 110)
    r = await opt_client.get("/api/v1/optimization/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total_observations"] >= 100
    assert data["avg_score_7d"] is not None or data["avg_score_30d"] is not None


@pytest.mark.asyncio
async def test_overview_period_filter(opt_client, opt_db):
    # Seed old data (>30 days ago) and recent data
    old_ts = (datetime.utcnow() - timedelta(days=40)).isoformat()
    new_ts = (datetime.utcnow() - timedelta(days=2)).isoformat()
    await _seed_observations(opt_db, 20, created_at=old_ts, outcome_score=0.3)
    await _seed_observations(opt_db, 20, created_at=new_ts, outcome_score=0.9)

    r7 = await opt_client.get("/api/v1/optimization/overview?days=7")
    r30 = await opt_client.get("/api/v1/optimization/overview?days=30")
    d7 = r7.json()
    d30 = r30.json()
    # 7-day window should only see recent data
    assert d7["total_observations"] <= d30["total_observations"]


# ── Outcomes ──


@pytest.mark.asyncio
async def test_outcomes_daily_aggregation(opt_client, opt_db):
    # Seed data across 3 days
    for day_offset in range(3):
        ts = (datetime.utcnow() - timedelta(days=day_offset)).isoformat()
        await _seed_observations(opt_db, 5, created_at=ts)

    r = await opt_client.get("/api/v1/optimization/outcomes?days=7")
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) >= 1


@pytest.mark.asyncio
async def test_outcomes_category_filter(opt_client, opt_db):
    await _seed_observations(opt_db, 10, task_category="coding")
    await _seed_observations(opt_db, 5, task_category="research")

    r = await opt_client.get("/api/v1/optimization/outcomes?days=30&category=coding")
    assert r.status_code == 200
    # Should have data (we don't verify exact counts since it's aggregated)
    assert "days" in r.json()


# ── Lessons ──


@pytest.mark.asyncio
async def test_list_lessons_empty(opt_client):
    r = await opt_client.get("/api/v1/optimization/lessons")
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_approve_lesson(opt_client, tmp_path):
    proposed = tmp_path / "lessons" / "proposed"
    approved = tmp_path / "lessons" / "approved"
    (proposed / "test-lesson.md").write_text("---\ntitle: Test Lesson\n---\nContent here")

    r = await opt_client.post("/api/v1/optimization/lessons/test-lesson/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert (approved / "test-lesson.md").exists()
    assert not (proposed / "test-lesson.md").exists()


@pytest.mark.asyncio
async def test_reject_lesson_with_reason(opt_client, tmp_path):
    proposed = tmp_path / "lessons" / "proposed"
    rejected = tmp_path / "lessons" / "rejected"
    (proposed / "bad-lesson.md").write_text("---\ntitle: Bad Lesson\n---\nBad content")

    r = await opt_client.post(
        "/api/v1/optimization/lessons/bad-lesson/reject",
        json={"reason": "Not actionable"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert (rejected / "bad-lesson.md").exists()
    content = (rejected / "bad-lesson.md").read_text()
    assert "rejected_reason: Not actionable" in content


@pytest.mark.asyncio
async def test_revoke_lesson(opt_client, tmp_path):
    approved = tmp_path / "lessons" / "approved"
    proposed = tmp_path / "lessons" / "proposed"
    (approved / "revoke-me.md").write_text("---\ntitle: Revoke Me\n---\nSome content")

    r = await opt_client.post("/api/v1/optimization/lessons/revoke-me/revoke")
    assert r.status_code == 200
    assert r.json()["status"] == "proposed"
    assert (proposed / "revoke-me.md").exists()
    assert not (approved / "revoke-me.md").exists()
    content = (proposed / "revoke-me.md").read_text()
    assert "revoked_at:" in content


@pytest.mark.asyncio
async def test_update_lesson(opt_client, tmp_path):
    proposed = tmp_path / "lessons" / "proposed"
    (proposed / "edit-me.md").write_text("---\ntitle: Old Title\n---\nOld content")

    r = await opt_client.put(
        "/api/v1/optimization/lessons/edit-me",
        json={"title": "New Title Here", "content": "Updated content body"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "New Title Here"
    content = (proposed / "edit-me.md").read_text()
    assert "New Title Here" in content
    assert "Updated content body" in content


@pytest.mark.asyncio
async def test_update_lesson_rejects_code_blocks(opt_client, tmp_path):
    proposed = tmp_path / "lessons" / "proposed"
    (proposed / "code-lesson.md").write_text("---\ntitle: Code\n---\nSome text")

    r = await opt_client.put(
        "/api/v1/optimization/lessons/code-lesson",
        json={"title": "Code Lesson Title", "content": "Do this:\n```python\nprint('hi')\n```"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_lesson_not_found(opt_client):
    r = await opt_client.post("/api/v1/optimization/lessons/nonexistent/approve")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_lesson_id_traversal(opt_client):
    # IDs with path traversal chars should be rejected (400) or not found (404)
    r = await opt_client.post("/api/v1/optimization/lessons/..%2F..%2Fetc%2Fpasswd/approve")
    assert r.status_code in (400, 404)
    # Direct invalid chars that aren't URL-decoded
    r2 = await opt_client.put(
        "/api/v1/optimization/lessons/bad..id",
        json={"title": "Traversal test title", "content": "Traversal test content here"},
    )
    assert r2.status_code == 400


# ── Parameters ──


@pytest.mark.asyncio
async def test_list_params(opt_client):
    r = await opt_client.get("/api/v1/optimization/params")
    assert r.status_code == 200
    params = r.json()["params"]
    assert len(params) > 0
    keys = {p["key"] for p in params}
    assert "COMPRESSION_THRESHOLD" in keys


@pytest.mark.asyncio
async def test_update_param_valid(opt_client):
    r = await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 8000},
    )
    assert r.status_code == 200
    assert r.json()["value"] == 8000


@pytest.mark.asyncio
async def test_update_param_out_of_range(opt_client):
    r = await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 999999},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_param_wrong_step(opt_client):
    # COMPRESSION_THRESHOLD step=1000, min=4000, so 4500 is invalid
    r = await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 4500},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_param_wrong_type(opt_client):
    r = await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": "not_a_number"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_rollback_param(opt_client, opt_db):
    # Set a value first
    await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 8000},
    )
    # Manually bump the changed_at so ordering is deterministic
    await opt_db.execute(
        "UPDATE optimization_param_history SET changed_at = datetime('now', '-1 minute') "
        "WHERE param_key = 'COMPRESSION_THRESHOLD'"
    )
    await opt_db.commit()
    # Change it (this gets a newer changed_at)
    await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 9000},
    )
    # Rollback — most recent history entry has old_value=8000
    r = await opt_client.post("/api/v1/optimization/params/COMPRESSION_THRESHOLD/rollback")
    assert r.status_code == 200, f"Rollback failed: {r.text}"
    assert r.json()["restored_value"] == 8000


@pytest.mark.asyncio
async def test_param_history(opt_client):
    await opt_client.put(
        "/api/v1/optimization/params/VERBATIM_MESSAGE_COUNT",
        json={"value": 4},
    )
    await opt_client.put(
        "/api/v1/optimization/params/VERBATIM_MESSAGE_COUNT",
        json={"value": 5},
    )
    r = await opt_client.get("/api/v1/optimization/params/VERBATIM_MESSAGE_COUNT/history")
    assert r.status_code == 200
    history = r.json()["history"]
    assert len(history) >= 2


# ── Experiments ──


@pytest.mark.asyncio
async def test_create_experiment(opt_client):
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["param_key"] == "COMPRESSION_THRESHOLD"


@pytest.mark.asyncio
async def test_create_experiment_already_active(opt_client):
    await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "VERBATIM_MESSAGE_COUNT", "proposed_value": 4},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_experiment_same_value(opt_client):
    # Set param to 8000 first
    await opt_client.put(
        "/api/v1/optimization/params/COMPRESSION_THRESHOLD",
        json={"value": 8000},
    )
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_conclude_experiment(opt_client, opt_db):
    # Create experiment
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    exp_id = r.json()["id"]

    # Seed some observation data for both cohorts
    await _seed_observations(opt_db, 15, cohort="control", outcome_score=0.7)
    await _seed_observations(opt_db, 15, cohort=exp_id, outcome_score=0.8)

    r = await opt_client.post(f"/api/v1/optimization/experiments/{exp_id}/conclude")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "concluded"
    assert "conclusion" in data


@pytest.mark.asyncio
async def test_conclude_experiment_insufficient_data(opt_client, opt_db):
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    exp_id = r.json()["id"]

    # Only seed a few observations (< 10)
    await _seed_observations(opt_db, 3, cohort="control", outcome_score=0.7)
    await _seed_observations(opt_db, 3, cohort=exp_id, outcome_score=0.8)

    r = await opt_client.post(f"/api/v1/optimization/experiments/{exp_id}/conclude")
    assert r.status_code == 200
    assert r.json()["conclusion"] == "inconclusive"
    assert "warning" in r.json()


@pytest.mark.asyncio
async def test_cancel_experiment(opt_client):
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    exp_id = r.json()["id"]

    r = await opt_client.post(f"/api/v1/optimization/experiments/{exp_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_experiment_scores(opt_client, opt_db):
    r = await opt_client.post(
        "/api/v1/optimization/experiments",
        json={"param_key": "COMPRESSION_THRESHOLD", "proposed_value": 8000},
    )
    exp_id = r.json()["id"]
    await _seed_observations(opt_db, 5, cohort="control", outcome_score=0.7)
    await _seed_observations(opt_db, 5, cohort=exp_id, outcome_score=0.9)

    r = await opt_client.get(f"/api/v1/optimization/experiments/{exp_id}/scores")
    assert r.status_code == 200
    data = r.json()
    assert "control" in data
    assert "experiment" in data
    assert "control_stats" in data


# ── Retention ──


@pytest.mark.asyncio
async def test_get_retention(opt_client, opt_db):
    await _seed_observations(opt_db, 10)
    r = await opt_client.get("/api/v1/optimization/retention")
    assert r.status_code == 200
    data = r.json()
    assert data["observations"]["total_count"] == 10
    assert "retention_policy" in data


@pytest.mark.asyncio
async def test_update_retention(opt_client):
    r = await opt_client.put(
        "/api/v1/optimization/retention",
        json={
            "observations_max_days": 90,
            "observations_max_rows": 10000,
            "auto_purge_enabled": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["policy"]["observations_max_days"] == 90
