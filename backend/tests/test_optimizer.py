"""Tests for optimizer functions (Design Doc 050)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import aiosqlite
import pytest

from backend.app.agent.optimizer import _welch_t_test, purge_stale_observations

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
CREATE TABLE IF NOT EXISTS optimization_observations_vec (
    id TEXT PRIMARY KEY
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
"""


@pytest.fixture()
async def opt_db(tmp_path):
    db_path = tmp_path / "agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.executescript(SCHEMA)
    await db.commit()
    yield db
    await db.close()


# ── Welch's t-test ──


def test_welch_ttest_known_values():
    # Two clearly different groups
    a = [10.0, 12.0, 11.0, 13.0, 14.0, 10.5, 11.5, 12.5, 13.5, 14.5,
         10.2, 11.2, 12.2, 13.2, 14.2, 10.8, 11.8, 12.8, 13.8, 14.8]
    b = [5.0, 6.0, 4.0, 7.0, 5.5, 6.5, 4.5, 7.5, 5.2, 6.2,
         4.2, 7.2, 5.8, 6.8, 4.8, 7.8, 5.1, 6.1, 4.1, 7.1]
    t_stat, p_value = _welch_t_test(a, b)
    assert t_stat > 0  # a has higher mean
    assert p_value < 0.01  # highly significant


def test_welch_ttest_identical_groups():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    t_stat, p_value = _welch_t_test(a, b)
    assert abs(t_stat) < 1e-9
    assert p_value > 0.99


def test_welch_ttest_small_sample():
    t_stat, p_value = _welch_t_test([1.0], [2.0])
    assert t_stat == 0.0
    assert p_value == 1.0

    t_stat, p_value = _welch_t_test([], [])
    assert t_stat == 0.0
    assert p_value == 1.0


# ── purge_stale_observations ──


def _uid():
    return uuid.uuid4().hex[:26]


async def _insert_obs(db, obs_id, created_at, cohort="control"):
    await db.execute(
        "INSERT INTO optimization_observations "
        "(id, conversation_id, turn_index, created_at, signals_json, outcome_score, cohort) "
        "VALUES (?, 'c1', 0, ?, '{}', 0.5, ?)",
        (obs_id, created_at, cohort),
    )
    # Also insert into vec table
    await db.execute(
        "INSERT INTO optimization_observations_vec (id) VALUES (?)", (obs_id,)
    )
    await db.commit()


@pytest.mark.asyncio
async def test_purge_stale_observations(opt_db):
    old_ts = (datetime.utcnow() - timedelta(days=200)).isoformat()
    new_ts = datetime.utcnow().isoformat()

    old_id = _uid()
    new_id = _uid()
    await _insert_obs(opt_db, old_id, old_ts)
    await _insert_obs(opt_db, new_id, new_ts)

    await purge_stale_observations(opt_db, max_days=180, max_rows=50000)

    cur = await opt_db.execute("SELECT id FROM optimization_observations")
    remaining = [r[0] for r in await cur.fetchall()]
    assert new_id in remaining
    assert old_id not in remaining

    # Vec table should also be cleaned
    cur = await opt_db.execute("SELECT id FROM optimization_observations_vec")
    vec_remaining = [r[0] for r in await cur.fetchall()]
    assert old_id not in vec_remaining


@pytest.mark.asyncio
async def test_purge_preserves_active_experiment(opt_db):
    old_ts = (datetime.utcnow() - timedelta(days=200)).isoformat()
    exp_id = _uid()
    obs_id = _uid()

    # Create an active experiment
    await opt_db.execute(
        "INSERT INTO optimization_experiments "
        "(id, param_key, baseline_value, proposed_value, status) "
        "VALUES (?, 'COMPRESSION_THRESHOLD', '4000', '8000', 'active')",
        (exp_id,),
    )
    # Insert old observation assigned to this experiment's cohort
    await _insert_obs(opt_db, obs_id, old_ts, cohort=exp_id)
    await opt_db.commit()

    await purge_stale_observations(opt_db, max_days=180, max_rows=50000)

    cur = await opt_db.execute(
        "SELECT id FROM optimization_observations WHERE id = ?", (obs_id,)
    )
    row = await cur.fetchone()
    assert row is not None, "Observation for active experiment should be preserved"
