"""Tests for work_plan tool — plan CRUD, notes append, context snapshots, recovery."""

from __future__ import annotations

import asyncio
import json
import tempfile
import os

import aiosqlite
import pytest

from backend.app.agent.tools.work_plan import (
    handle_work_plan,
    load_active_plan,
    format_recovery_context,
    checkpoint_active_plan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Schema needed for work_plan tables (minimal subset from migrations)
_SCHEMA = """\
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO agents (id, name, display_name, is_default)
VALUES ('test-agent', 'test', 'Test Agent', 1);

CREATE TABLE IF NOT EXISTS prompt_fragments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_prompt_fragments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    fragment_id TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_fragment_versions (
    id TEXT PRIMARY KEY,
    fragment_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_reason TEXT,
    changed_by TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE work_plans (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    conversation_id TEXT,
    parent_plan_id TEXT REFERENCES work_plans(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'completed', 'failed', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE TABLE work_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES work_plans(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'in_progress', 'done', 'in_review', 'approved',
                         'in_test', 'tested', 'complete', 'blocked', 'failed')),
    ordinal INTEGER NOT NULL DEFAULT 0,
    context_snapshot JSON,
    notes JSON DEFAULT '[]',
    files_changed JSON DEFAULT '[]',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
"""


@pytest.fixture
def db_path():
    """Create a temp DB with work plan schema."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="bond_wp_test_")
    os.close(fd)

    async def _setup():
        db = await aiosqlite.connect(path)
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript(_SCHEMA)
        await db.commit()
        await db.close()

    asyncio.run(_setup())
    yield path
    os.unlink(path)


def _ctx(agent_id="test-agent", conversation_id="conv-1"):
    """Build a mock context — db gets set per-test."""
    return {"agent_id": agent_id, "conversation_id": conversation_id}


def _run(coro):
    return asyncio.run(coro)


async def _open_db(path):
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA foreign_keys=ON")
    return db


# ---------------------------------------------------------------------------
# create_plan
# ---------------------------------------------------------------------------


class TestCreatePlan:
    def test_create_plan_basic(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan({"action": "create_plan", "title": "Test Plan"}, ctx)
            assert result["status"] == "created"
            assert result["plan_id"]
            assert result["title"] == "Test Plan"
            assert "_sse_event" in result
            assert result["_sse_event"]["event"] == "plan_created"
            await db.close()

        _run(_test())

    def test_create_plan_missing_title(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan({"action": "create_plan"}, ctx)
            assert "error" in result
            await db.close()

        _run(_test())

    def test_create_plan_no_db(self):
        # work_plan now uses its own shared plans DB, not agent_db from context.
        # Passing agent_db=None should still succeed.
        result = _run(handle_work_plan(
            {"action": "create_plan", "title": "Test"},
            {"agent_id": "a", "agent_db": None},
        ))
        assert result.get("status") == "created"
        assert "plan_id" in result


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------


class TestAddItem:
    def test_add_item_basic(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            plan_id = plan["plan_id"]

            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Step 1"}, ctx
            )
            assert item["status"] == "added"
            assert item["ordinal"] == 0
            assert item["item_id"]

            item2 = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Step 2"}, ctx
            )
            assert item2["ordinal"] == 1
            await db.close()

        _run(_test())

    def test_add_item_custom_ordinal(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            plan_id = plan["plan_id"]

            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Step", "ordinal": 5}, ctx
            )
            assert item["ordinal"] == 5
            await db.close()

        _run(_test())

    def test_add_item_to_completed_plan(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            plan_id = plan["plan_id"]
            await handle_work_plan({"action": "complete_plan", "plan_id": plan_id, "status": "completed"}, ctx)

            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Step"}, ctx
            )
            assert "error" in item
            await db.close()

        _run(_test())


# ---------------------------------------------------------------------------
# update_item
# ---------------------------------------------------------------------------


class TestUpdateItem:
    def test_update_status(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            item_id = item["item_id"]

            result = await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "status": "in_progress"}, ctx
            )
            assert result["status"] == "updated"
            assert result["item_status"] == "in_progress"

            # Verify started_at was set
            cursor = await db.execute("SELECT started_at FROM work_items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            assert row[0] is not None
            await db.close()

        _run(_test())

    def test_append_notes(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            item_id = item["item_id"]

            # Append first note
            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "notes": "Found issue in line 42"}, ctx
            )
            # Append second note
            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "notes": "Fixed the issue"}, ctx
            )

            # Verify notes are appended, not overwritten
            cursor = await db.execute("SELECT notes FROM work_items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            notes = json.loads(row[0])
            assert len(notes) == 2
            assert notes[0]["text"] == "Found issue in line 42"
            assert notes[1]["text"] == "Fixed the issue"
            assert "at" in notes[0]  # timestamped
            assert "at" in notes[1]
            await db.close()

        _run(_test())

    def test_context_snapshot(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            item_id = item["item_id"]

            snapshot = {"files_read": {"/path/to/file.py": {"lines": 100}}, "decisions_made": ["Use cache"]}
            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "context_snapshot": snapshot}, ctx
            )

            cursor = await db.execute("SELECT context_snapshot FROM work_items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            loaded = json.loads(row[0])
            assert loaded["decisions_made"] == ["Use cache"]
            await db.close()

        _run(_test())

    def test_files_changed_merge(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            item_id = item["item_id"]

            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "files_changed": ["/a.py", "/b.py"]}, ctx
            )
            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "files_changed": ["/b.py", "/c.py"]}, ctx
            )

            cursor = await db.execute("SELECT files_changed FROM work_items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            files = json.loads(row[0])
            assert files == ["/a.py", "/b.py", "/c.py"]  # deduplicated
            await db.close()

        _run(_test())

    def test_invalid_status(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            result = await handle_work_plan(
                {"action": "update_item", "item_id": item["item_id"], "status": "invalid_status"}, ctx
            )
            assert "error" in result
            await db.close()

        _run(_test())

    def test_completed_at_set_on_terminal(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            item_id = item["item_id"]

            await handle_work_plan(
                {"action": "update_item", "item_id": item_id, "status": "done"}, ctx
            )

            cursor = await db.execute("SELECT completed_at FROM work_items WHERE id = ?", (item_id,))
            row = await cursor.fetchone()
            assert row[0] is not None
            await db.close()

        _run(_test())


# ---------------------------------------------------------------------------
# complete_plan
# ---------------------------------------------------------------------------


class TestCompletePlan:
    def test_complete_plan(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            plan_id = plan["plan_id"]

            result = await handle_work_plan(
                {"action": "complete_plan", "plan_id": plan_id, "status": "completed"}, ctx
            )
            assert result["status"] == "completed"
            assert result["_sse_event"]["event"] == "plan_completed"
            await db.close()

        _run(_test())

    def test_complete_already_completed(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            plan_id = plan["plan_id"]
            await handle_work_plan({"action": "complete_plan", "plan_id": plan_id, "status": "completed"}, ctx)

            result = await handle_work_plan(
                {"action": "complete_plan", "plan_id": plan_id, "status": "failed"}, ctx
            )
            assert "error" in result
            await db.close()

        _run(_test())

    def test_invalid_terminal_status(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "Plan"}, ctx)
            result = await handle_work_plan(
                {"action": "complete_plan", "plan_id": plan["plan_id"], "status": "active"}, ctx
            )
            assert "error" in result
            await db.close()

        _run(_test())


# ---------------------------------------------------------------------------
# get_plan
# ---------------------------------------------------------------------------


class TestGetPlan:
    def test_get_plan_with_items(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            plan = await handle_work_plan({"action": "create_plan", "title": "My Plan"}, ctx)
            plan_id = plan["plan_id"]

            await handle_work_plan({"action": "add_item", "plan_id": plan_id, "title": "Step 1"}, ctx)
            await handle_work_plan({"action": "add_item", "plan_id": plan_id, "title": "Step 2"}, ctx)

            result = await handle_work_plan({"action": "get_plan", "plan_id": plan_id}, ctx)
            assert result["title"] == "My Plan"
            assert result["status"] == "active"
            assert len(result["items"]) == 2
            assert result["items"][0]["title"] == "Step 1"
            assert result["items"][1]["title"] == "Step 2"
            assert result["items"][0]["ordinal"] == 0
            assert result["items"][1]["ordinal"] == 1
            await db.close()

        _run(_test())

    def test_get_plan_not_found(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan({"action": "get_plan", "plan_id": "nonexistent"}, ctx)
            assert "error" in result
            await db.close()

        _run(_test())


# ---------------------------------------------------------------------------
# Recovery helpers
# ---------------------------------------------------------------------------


class TestRecovery:
    def test_load_active_plan(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}

            # No active plan initially
            result = await load_active_plan(db, "test-agent")
            assert result is None

            # Create a plan
            plan = await handle_work_plan({"action": "create_plan", "title": "Active Plan"}, ctx)
            await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )

            result = await load_active_plan(db, "test-agent")
            assert result is not None
            assert result["title"] == "Active Plan"
            assert len(result["items"]) == 1
            await db.close()

        _run(_test())

    def test_format_recovery_context(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}

            plan = await handle_work_plan({"action": "create_plan", "title": "Recovery Test"}, ctx)
            plan_id = plan["plan_id"]
            item1 = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Done step"}, ctx
            )
            item2 = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Current step"}, ctx
            )
            item3 = await handle_work_plan(
                {"action": "add_item", "plan_id": plan_id, "title": "Future step"}, ctx
            )

            # Mark first as done, second as in_progress with context
            await handle_work_plan(
                {"action": "update_item", "item_id": item1["item_id"], "status": "done",
                 "notes": "Completed successfully"}, ctx
            )
            await handle_work_plan(
                {"action": "update_item", "item_id": item2["item_id"], "status": "in_progress",
                 "notes": "Working on it",
                 "context_snapshot": {"decisions_made": ["Use cache"], "files_read": {"/worker.py": {}}}}, ctx
            )

            loaded = await load_active_plan(db, "test-agent")
            text = format_recovery_context(loaded)

            assert "Recovery Test" in text
            assert "Done step" in text
            assert "Current step" in text
            assert "Future step" in text
            assert "\u2705" in text  # completed marker
            assert "\U0001f504" in text  # in_progress marker
            assert "Use cache" in text
            await db.close()

        _run(_test())

    def test_checkpoint_active_plan(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}

            plan = await handle_work_plan({"action": "create_plan", "title": "Checkpoint Test"}, ctx)
            item = await handle_work_plan(
                {"action": "add_item", "plan_id": plan["plan_id"], "title": "Step 1"}, ctx
            )
            await handle_work_plan(
                {"action": "update_item", "item_id": item["item_id"], "status": "in_progress"}, ctx
            )

            saved = await checkpoint_active_plan(db, "test-agent", "Max iterations reached")
            assert saved is True

            # Verify the note was appended
            cursor = await db.execute("SELECT notes FROM work_items WHERE id = ?", (item["item_id"],))
            row = await cursor.fetchone()
            notes = json.loads(row[0])
            assert any("Max iterations reached" in n["text"] for n in notes)
            await db.close()

        _run(_test())

    def test_checkpoint_no_active_plan(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            saved = await checkpoint_active_plan(db, "test-agent", "test")
            assert saved is False
            await db.close()

        _run(_test())


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_action(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan({"action": "invalid"}, ctx)
            assert "error" in result
            await db.close()

        _run(_test())

    def test_missing_action(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan({}, ctx)
            assert "error" in result
            await db.close()

        _run(_test())

    def test_update_nonexistent_item(self, db_path):
        async def _test():
            db = await _open_db(db_path)
            ctx = {**_ctx(), "agent_db": db}
            result = await handle_work_plan(
                {"action": "update_item", "item_id": "nonexistent", "status": "done"}, ctx
            )
            assert "error" in result
            await db.close()

        _run(_test())
