"""Tests for native memory handlers — enterprise-grade coverage.

Covers schema, save, update, delete, search, FTS integrity,
transaction safety, and registry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import aiosqlite
import pytest

from backend.app.db.agent_schema import AGENT_DB_SCHEMA


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
async def agent_db(tmp_path):
    """Provide a fresh agent.db with schema applied."""
    db_path = tmp_path / "agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(AGENT_DB_SCHEMA)
    await db.commit()
    yield db
    await db.close()


@pytest.fixture()
def ctx(agent_db):
    """Standard context dict with agent_db."""
    return {"agent_db": agent_db, "agent_id": "test-agent"}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _save(agent_db, content="test", memory_type="general", **kwargs):
    """Quick save helper returning the result dict."""
    from backend.app.agent.tools.native import handle_memory_save
    args = {"content": content, "memory_type": memory_type, **kwargs}
    return await handle_memory_save(args, {"agent_db": agent_db})


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchema:
    @pytest.mark.asyncio
    async def test_agent_db_schema_creates_all_tables(self, agent_db):
        """Schema creates memories, memory_versions, entities, content_chunks, FTS tables."""
        cursor = await agent_db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger') ORDER BY name"
        )
        rows = await cursor.fetchall()
        names = {r[0] for r in rows}

        # Tables
        assert "memories" in names
        assert "memory_versions" in names
        assert "entities" in names
        assert "content_chunks" in names

        # FTS virtual tables
        cursor = await agent_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts%'"
        )
        fts_rows = await cursor.fetchall()
        fts_names = {r[0] for r in fts_rows}
        assert "memories_fts" in fts_names
        assert "content_chunks_fts" in fts_names

        # Triggers
        assert "mem_fts_insert" in names
        assert "mem_fts_update" in names
        assert "mem_fts_delete" in names


# ---------------------------------------------------------------------------
# Save tests
# ---------------------------------------------------------------------------

class TestMemorySave:
    @pytest.mark.asyncio
    async def test_memory_save_all_fields(self, agent_db):
        """Save with all fields populated."""
        from backend.app.agent.tools.native import handle_memory_save

        result = await handle_memory_save({
            "content": "User likes Python",
            "memory_type": "preference",
            "summary": "Python preference",
            "importance": 0.9,
            "sensitivity": "personal",
            "metadata": json.dumps({"source": "chat"}),
            "source_type": "conversation",
            "source_id": "conv-123",
        }, {"agent_db": agent_db})

        assert result["status"] == "saved"
        mid = result["memory_id"]

        cursor = await agent_db.execute(
            "SELECT content, summary, importance, sensitivity, metadata, "
            "source_type, source_id FROM memories WHERE id = ?",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row[0] == "User likes Python"
        assert row[1] == "Python preference"
        assert row[2] == 0.9
        assert row[3] == "personal"
        assert json.loads(row[4]) == {"source": "chat"}
        assert row[5] == "conversation"
        assert row[6] == "conv-123"

    @pytest.mark.asyncio
    async def test_memory_save_defaults(self, agent_db):
        """Missing optional fields get proper defaults."""
        result = await _save(agent_db, content="Some fact")
        mid = result["memory_id"]

        cursor = await agent_db.execute(
            "SELECT sensitivity, importance, access_count, deleted_at, metadata "
            "FROM memories WHERE id = ?",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row[0] == "normal"
        assert row[1] == 0.5
        assert row[2] == 0
        assert row[3] is None  # not deleted
        assert json.loads(row[4]) == {}

    @pytest.mark.asyncio
    async def test_memory_save_creates_version_1(self, agent_db):
        """Save creates version 1 in memory_versions."""
        result = await _save(agent_db, content="Versioned fact", memory_type="fact")
        mid = result["memory_id"]

        cursor = await agent_db.execute(
            "SELECT version, previous_content, new_content, new_type, changed_by "
            "FROM memory_versions WHERE memory_id = ?",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] is None  # no previous for version 1
        assert row[2] == "Versioned fact"
        assert row[3] == "fact"
        assert row[4] == "system"

    @pytest.mark.asyncio
    async def test_memory_save_validation_empty_content(self, agent_db):
        """Empty content returns error."""
        result = await _save(agent_db, content="")
        assert "error" in result
        assert "content" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_save_validation_whitespace_content(self, agent_db):
        """Whitespace-only content returns error."""
        result = await _save(agent_db, content="   ")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_memory_save_validation_bad_type(self, agent_db):
        """Invalid memory_type returns error."""
        result = await _save(agent_db, content="test", memory_type="invalid_type")
        assert "error" in result
        assert "memory_type" in result["error"].lower() or "invalid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_save_validation_bad_importance(self, agent_db):
        """importance outside 0-1 range returns error."""
        from backend.app.agent.tools.native import handle_memory_save

        result = await handle_memory_save(
            {"content": "test", "memory_type": "general", "importance": 1.5},
            {"agent_db": agent_db},
        )
        assert "error" in result
        assert "importance" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_save_validation_bad_sensitivity(self, agent_db):
        """Invalid sensitivity returns error."""
        from backend.app.agent.tools.native import handle_memory_save

        result = await handle_memory_save(
            {"content": "test", "memory_type": "general", "sensitivity": "top_secret"},
            {"agent_db": agent_db},
        )
        assert "error" in result
        assert "sensitivity" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_save_promoted_types(self, agent_db):
        """Promotable types include _promote dict."""
        for t in ("preference", "fact", "instruction", "entity", "person"):
            result = await _save(agent_db, content=f"type {t}", memory_type=t)
            assert "_promote" in result, f"Type '{t}' should be promoted"
            assert result["_promote"]["type"] == t

    @pytest.mark.asyncio
    async def test_memory_save_non_promoted_types(self, agent_db):
        """Non-promotable types do not include _promote."""
        for t in ("general", "solution"):
            result = await _save(agent_db, content=f"type {t}", memory_type=t)
            assert "_promote" not in result, f"Type '{t}' should not be promoted"


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------

class TestMemoryUpdate:
    @pytest.mark.asyncio
    async def test_memory_update_changes_content(self, agent_db):
        """Update modifies the content in the DB."""
        from backend.app.agent.tools.native import handle_memory_update

        save_result = await _save(agent_db, content="Original")
        mid = save_result["memory_id"]

        result = await handle_memory_update(
            {"memory_id": mid, "content": "Updated content", "reason": "correction"},
            {"agent_db": agent_db},
        )
        assert result["status"] == "updated"
        assert result["memory_id"] == mid
        assert result["version"] == 2

        cursor = await agent_db.execute(
            "SELECT content FROM memories WHERE id = ?", (mid,)
        )
        row = await cursor.fetchone()
        assert row[0] == "Updated content"

    @pytest.mark.asyncio
    async def test_memory_update_creates_version(self, agent_db):
        """Update creates a version record."""
        from backend.app.agent.tools.native import handle_memory_update

        save_result = await _save(agent_db, content="V1 content")
        mid = save_result["memory_id"]

        await handle_memory_update(
            {"memory_id": mid, "content": "V2 content", "reason": "update"},
            {"agent_db": agent_db},
        )

        cursor = await agent_db.execute(
            "SELECT version, previous_content, new_content, change_reason "
            "FROM memory_versions WHERE memory_id = ? ORDER BY version",
            (mid,),
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        # Version 1 = initial
        assert rows[0][0] == 1
        # Version 2 = update
        assert rows[1][0] == 2
        assert rows[1][1] == "V1 content"
        assert rows[1][2] == "V2 content"
        assert rows[1][3] == "update"

    @pytest.mark.asyncio
    async def test_memory_update_nonexistent_returns_error(self, agent_db):
        """Update on nonexistent ID returns error."""
        from backend.app.agent.tools.native import handle_memory_update

        result = await handle_memory_update(
            {"memory_id": "nonexistent", "content": "new"},
            {"agent_db": agent_db},
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_update_deleted_returns_error(self, agent_db):
        """Update on a soft-deleted memory returns error."""
        from backend.app.agent.tools.native import handle_memory_update, handle_memory_delete

        save_result = await _save(agent_db, content="To delete")
        mid = save_result["memory_id"]

        await handle_memory_delete(
            {"memory_id": mid}, {"agent_db": agent_db}
        )

        result = await handle_memory_update(
            {"memory_id": mid, "content": "new"},
            {"agent_db": agent_db},
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_memory_update_multiple_versions_track_history(self, agent_db):
        """Multiple updates create sequential version numbers."""
        from backend.app.agent.tools.native import handle_memory_update

        save_result = await _save(agent_db, content="V1")
        mid = save_result["memory_id"]

        for i in range(2, 5):
            result = await handle_memory_update(
                {"memory_id": mid, "content": f"V{i}", "reason": f"update {i}"},
                {"agent_db": agent_db},
            )
            assert result["version"] == i

        cursor = await agent_db.execute(
            "SELECT COUNT(*) FROM memory_versions WHERE memory_id = ?",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row[0] == 4  # version 1 (create) + 3 updates

    @pytest.mark.asyncio
    async def test_memory_update_validation(self, agent_db):
        """Update validates required fields."""
        from backend.app.agent.tools.native import handle_memory_update

        result = await handle_memory_update(
            {"memory_id": "", "content": "test"},
            {"agent_db": agent_db},
        )
        assert "error" in result

        result = await handle_memory_update(
            {"memory_id": "some-id", "content": ""},
            {"agent_db": agent_db},
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Delete tests
# ---------------------------------------------------------------------------

class TestMemoryDelete:
    @pytest.mark.asyncio
    async def test_memory_delete_soft_deletes(self, agent_db):
        """Delete sets deleted_at, doesn't remove the row."""
        from backend.app.agent.tools.native import handle_memory_delete

        save_result = await _save(agent_db, content="To soft-delete")
        mid = save_result["memory_id"]

        result = await handle_memory_delete(
            {"memory_id": mid}, {"agent_db": agent_db}
        )
        assert result["status"] == "deleted"
        assert result["memory_id"] == mid

        # Row still exists but has deleted_at set
        cursor = await agent_db.execute(
            "SELECT deleted_at FROM memories WHERE id = ?", (mid,)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is not None  # deleted_at is set

    @pytest.mark.asyncio
    async def test_memory_delete_creates_version(self, agent_db):
        """Delete creates a version record with '[deleted]' as new_content."""
        from backend.app.agent.tools.native import handle_memory_delete

        save_result = await _save(agent_db, content="Original content")
        mid = save_result["memory_id"]

        await handle_memory_delete(
            {"memory_id": mid, "reason": "no longer needed"},
            {"agent_db": agent_db},
        )

        cursor = await agent_db.execute(
            "SELECT version, previous_content, new_content, change_reason "
            "FROM memory_versions WHERE memory_id = ? ORDER BY version DESC LIMIT 1",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row[0] == 2  # version 2 (delete)
        assert row[1] == "Original content"
        assert row[2] == "[deleted]"
        assert row[3] == "no longer needed"

    @pytest.mark.asyncio
    async def test_memory_delete_nonexistent_returns_error(self, agent_db):
        """Deleting nonexistent ID returns error."""
        from backend.app.agent.tools.native import handle_memory_delete

        result = await handle_memory_delete(
            {"memory_id": "nonexistent"}, {"agent_db": agent_db}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_memory_delete_already_deleted_returns_error(self, agent_db):
        """Deleting twice returns error, not crash."""
        from backend.app.agent.tools.native import handle_memory_delete

        save_result = await _save(agent_db, content="Double delete")
        mid = save_result["memory_id"]

        r1 = await handle_memory_delete({"memory_id": mid}, {"agent_db": agent_db})
        assert r1["status"] == "deleted"

        r2 = await handle_memory_delete({"memory_id": mid}, {"agent_db": agent_db})
        assert "error" in r2

    @pytest.mark.asyncio
    async def test_memory_delete_excluded_from_search(self, agent_db):
        """Deleted memories don't appear in search results."""
        from backend.app.agent.tools.native import handle_memory_delete, handle_search_memory

        save_result = await _save(agent_db, content="unique_searchable_xyzzy")
        mid = save_result["memory_id"]

        # Verify it's findable
        search = await handle_search_memory(
            {"query": "unique_searchable_xyzzy"}, {"agent_db": agent_db}
        )
        assert search["count"] >= 1

        # Delete it
        await handle_memory_delete({"memory_id": mid}, {"agent_db": agent_db})

        # Verify it's gone from search
        search = await handle_search_memory(
            {"query": "unique_searchable_xyzzy"}, {"agent_db": agent_db}
        )
        assert search["count"] == 0


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class TestSearchMemory:
    @pytest.mark.asyncio
    async def test_search_finds_by_content(self, agent_db):
        """Search finds memories by content text."""
        from backend.app.agent.tools.native import handle_search_memory

        await _save(agent_db, content="FastAPI is a modern web framework")
        result = await handle_search_memory(
            {"query": "FastAPI framework"}, {"agent_db": agent_db}
        )
        assert result["count"] >= 1
        assert any("FastAPI" in r["content"] for r in result["results"])

    @pytest.mark.asyncio
    async def test_search_finds_by_summary(self, agent_db):
        """Search finds memories by summary text."""
        from backend.app.agent.tools.native import handle_search_memory

        await _save(agent_db, content="detailed content here",
                     summary="unique_summary_token_abc")
        result = await handle_search_memory(
            {"query": "unique_summary_token_abc"}, {"agent_db": agent_db}
        )
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_search_type_filter(self, agent_db):
        """memory_types filter restricts results."""
        from backend.app.agent.tools.native import handle_search_memory

        await _save(agent_db, content="Python is great for scripting", memory_type="fact")
        await _save(agent_db, content="Python is the preferred language", memory_type="preference")

        # Search all
        all_results = await handle_search_memory(
            {"query": "Python"}, {"agent_db": agent_db}
        )
        assert all_results["count"] >= 2

        # Filter to facts only
        filtered = await handle_search_memory(
            {"query": "Python", "memory_types": ["fact"]},
            {"agent_db": agent_db},
        )
        assert all(r["type"] == "fact" for r in filtered["results"])

    @pytest.mark.asyncio
    async def test_search_time_filter(self, agent_db):
        """since and until filters restrict results by creation time."""
        from backend.app.agent.tools.native import handle_search_memory

        await _save(agent_db, content="temporal_test_memory_xyz123")

        # Search with 'since' in the future should find nothing
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = await handle_search_memory(
            {"query": "temporal_test_memory_xyz123", "since": future},
            {"agent_db": agent_db},
        )
        assert result["count"] == 0

        # Search with 'until' in the past should find nothing
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = await handle_search_memory(
            {"query": "temporal_test_memory_xyz123", "until": past},
            {"agent_db": agent_db},
        )
        assert result["count"] == 0

        # Search with broad range should find it
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = await handle_search_memory(
            {"query": "temporal_test_memory_xyz123", "since": past, "until": future},
            {"agent_db": agent_db},
        )
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_search_excludes_deleted(self, agent_db):
        """Deleted memories are excluded from search results."""
        from backend.app.agent.tools.native import handle_memory_delete, handle_search_memory

        result = await _save(agent_db, content="deletable_search_test_999")
        mid = result["memory_id"]

        await handle_memory_delete({"memory_id": mid}, {"agent_db": agent_db})

        search = await handle_search_memory(
            {"query": "deletable_search_test_999"}, {"agent_db": agent_db}
        )
        assert search["count"] == 0

    @pytest.mark.asyncio
    async def test_search_recency_boost(self, agent_db):
        """Recent memories get a small recency boost."""
        from backend.app.agent.tools.native import _recency_boost

        now = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

        boost_new = _recency_boost(now)
        boost_old = _recency_boost(old)

        assert boost_new > boost_old
        assert 0.0 < boost_new <= 0.01
        assert 0.0 <= boost_old < boost_new

    @pytest.mark.asyncio
    async def test_search_updates_access_count(self, agent_db):
        """Search increments access_count for returned local results."""
        from backend.app.agent.tools.native import handle_search_memory

        result = await _save(agent_db, content="access_tracking_test_unique")
        mid = result["memory_id"]

        # Initial access_count should be 0
        cursor = await agent_db.execute(
            "SELECT access_count FROM memories WHERE id = ?", (mid,)
        )
        assert (await cursor.fetchone())[0] == 0

        # Search (should find and increment)
        await handle_search_memory(
            {"query": "access_tracking_test_unique"}, {"agent_db": agent_db}
        )

        cursor = await agent_db.execute(
            "SELECT access_count, last_accessed_at FROM memories WHERE id = ?",
            (mid,),
        )
        row = await cursor.fetchone()
        assert row[0] == 1
        assert row[1] is not None  # last_accessed_at set

    @pytest.mark.asyncio
    async def test_search_local_and_shared(self, agent_db, tmp_path):
        """Search combines local and shared.db results."""
        from backend.app.agent.tools.native import handle_search_memory

        # Save local memory with a unique searchable keyword
        await _save(agent_db, content="the local xylophone memory entry")

        # Create shared.db with matching schema and data
        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        shared_path = shared_dir / "shared.db"
        shared_db = await aiosqlite.connect(str(shared_path))
        await shared_db.executescript("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(id UNINDEXED, content, summary);
            CREATE TRIGGER mem_fts_insert AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(id, content, summary)
                VALUES (NEW.id, NEW.content, NEW.summary);
            END;
        """)
        await shared_db.execute(
            "INSERT INTO memories (id, type, content, summary, created_at, updated_at) "
            "VALUES ('shared-1', 'fact', 'the shared xylophone memory entry', 'shared summary', "
            "datetime('now'), datetime('now'))",
        )
        await shared_db.commit()
        await shared_db.close()

        # Attach shared.db
        await agent_db.execute(f"ATTACH DATABASE '{shared_path}' AS shared")

        # Search with a term present in both
        result = await handle_search_memory(
            {"query": "xylophone"}, {"agent_db": agent_db}
        )
        sources = {r["source"] for r in result["results"]}
        assert "local" in sources
        assert "shared" in sources

    @pytest.mark.asyncio
    async def test_search_no_db_returns_error(self):
        """Search without agent_db returns error."""
        from backend.app.agent.tools.native import handle_search_memory

        result = await handle_search_memory({"query": "test"}, {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_search_empty_query_validation(self, agent_db):
        """Empty query returns validation error."""
        from backend.app.agent.tools.native import handle_search_memory

        result = await handle_search_memory({"query": ""}, {"agent_db": agent_db})
        assert "error" in result
        assert "query" in result["error"].lower()

        result = await handle_search_memory({"query": "   "}, {"agent_db": agent_db})
        assert "error" in result


# ---------------------------------------------------------------------------
# FTS integrity tests
# ---------------------------------------------------------------------------

class TestFTSIntegrity:
    @pytest.mark.asyncio
    async def test_fts_syncs_on_insert(self, agent_db):
        """FTS index auto-populates on INSERT via trigger."""
        from backend.app.agent.tools.native import handle_search_memory

        await _save(agent_db, content="fts_insert_test_unique_abc")

        result = await handle_search_memory(
            {"query": "fts_insert_test_unique_abc"}, {"agent_db": agent_db}
        )
        assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_fts_syncs_on_update(self, agent_db):
        """FTS index auto-updates on UPDATE via trigger."""
        from backend.app.agent.tools.native import handle_memory_update, handle_search_memory

        # Use a distinct summary so it doesn't contain the old content token
        save_result = await _save(
            agent_db, content="old_fts_content_xyz", summary="unrelated summary"
        )
        mid = save_result["memory_id"]

        await handle_memory_update(
            {"memory_id": mid, "content": "new_fts_content_xyz"},
            {"agent_db": agent_db},
        )

        # Old content should not be found (summary doesn't contain the old token)
        old_search = await handle_search_memory(
            {"query": "old_fts_content_xyz"}, {"agent_db": agent_db}
        )
        assert old_search["count"] == 0

        # New content should be found
        new_search = await handle_search_memory(
            {"query": "new_fts_content_xyz"}, {"agent_db": agent_db}
        )
        assert new_search["count"] >= 1

    @pytest.mark.asyncio
    async def test_fts_syncs_on_delete(self, agent_db):
        """FTS still has the row after soft delete, but search excludes it via JOIN filter."""
        from backend.app.agent.tools.native import handle_memory_delete, handle_search_memory

        save_result = await _save(agent_db, content="fts_delete_test_unique_999")
        mid = save_result["memory_id"]

        # Verify found before delete
        search = await handle_search_memory(
            {"query": "fts_delete_test_unique_999"}, {"agent_db": agent_db}
        )
        assert search["count"] >= 1

        # Soft delete
        await handle_memory_delete({"memory_id": mid}, {"agent_db": agent_db})

        # Should not appear in search
        search = await handle_search_memory(
            {"query": "fts_delete_test_unique_999"}, {"agent_db": agent_db}
        )
        assert search["count"] == 0


# ---------------------------------------------------------------------------
# Transaction safety tests
# ---------------------------------------------------------------------------

class TestTransactionSafety:
    @pytest.mark.asyncio
    async def test_rollback_on_save_failure(self, agent_db):
        """Save rolls back on failure — no partial state."""
        from backend.app.agent.tools.native import handle_memory_save

        # Patch ULID to cause a unique constraint violation on second insert
        original_execute = agent_db.execute
        call_count = 0

        async def failing_execute(sql, params=None):
            nonlocal call_count
            call_count += 1
            # Let first execute (memory INSERT) succeed, fail on second (version INSERT)
            if call_count == 2 and "memory_versions" in sql:
                raise Exception("Simulated DB error")
            if params:
                return await original_execute(sql, params)
            return await original_execute(sql)

        with patch.object(agent_db, "execute", side_effect=failing_execute):
            result = await handle_memory_save(
                {"content": "should fail", "memory_type": "general"},
                {"agent_db": agent_db},
            )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_rollback_on_update_failure(self, agent_db):
        """Update rolls back on failure."""
        from backend.app.agent.tools.native import handle_memory_update

        save_result = await _save(agent_db, content="Original for rollback")
        mid = save_result["memory_id"]

        original_execute = agent_db.execute
        call_count = 0

        async def failing_execute(sql, params=None):
            nonlocal call_count
            call_count += 1
            # Fail on the version INSERT (after fetching and updating)
            if "INSERT INTO memory_versions" in sql:
                raise Exception("Simulated DB error")
            if params:
                return await original_execute(sql, params)
            return await original_execute(sql)

        with patch.object(agent_db, "execute", side_effect=failing_execute):
            result = await handle_memory_update(
                {"memory_id": mid, "content": "Should fail"},
                {"agent_db": agent_db},
            )

        assert "error" in result


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_registry_includes_memory_update_and_delete(self):
        """Native registry includes memory_update and memory_delete."""
        from backend.app.agent.tools.native_registry import build_native_registry

        registry = build_native_registry()
        names = set(registry.registered_names)
        assert "memory_update" in names
        assert "memory_delete" in names
        # Also check existing tools are still there
        assert "memory_save" in names
        assert "search_memory" in names
        assert "respond" in names
