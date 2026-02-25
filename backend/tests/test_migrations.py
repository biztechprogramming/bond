"""Tests for migrations 000002-000004 and ensure_vec_tables."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _apply_sql(db: aiosqlite.Connection, sql_file: Path) -> None:
    """Execute a SQL migration file against an aiosqlite connection."""
    sql = sql_file.read_text()
    await db.executescript(sql)


async def _table_exists(db: aiosqlite.Connection, table_name: str) -> bool:
    """Check if a table (or virtual table) exists in the database."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE name = ?", (table_name,)
    )
    row = await cursor.fetchone()
    return row is not None


async def _trigger_exists(db: aiosqlite.Connection, trigger_name: str) -> bool:
    """Check if a trigger exists in the database."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (trigger_name,),
    )
    row = await cursor.fetchone()
    return row is not None


# ── Migration 000002: Knowledge Store + Memory ──


@pytest.mark.asyncio
async def test_migration_000002_up(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")

        # Tables
        for table in [
            "embedding_configs",
            "content_chunks",
            "content_chunks_fts",
            "memories",
            "memories_fts",
            "memory_versions",
            "session_summaries",
            "session_summaries_fts",
        ]:
            assert await _table_exists(db, table), f"Table {table} should exist"

        # Triggers
        for trigger in [
            "content_chunks_updated_at",
            "cc_fts_insert",
            "cc_fts_update",
            "cc_fts_delete",
            "memories_updated_at",
            "mem_fts_insert",
            "mem_fts_update",
            "mem_fts_delete",
            "session_summaries_updated_at",
            "ss_fts_insert",
            "ss_fts_update",
            "ss_fts_delete",
        ]:
            assert await _trigger_exists(db, trigger), f"Trigger {trigger} should exist"

        # Embedding configs are seeded
        cursor = await db.execute("SELECT COUNT(*) FROM embedding_configs")
        row = await cursor.fetchone()
        assert row[0] == 8  # 4 voyage + 3 qwen + 1 gemini


@pytest.mark.asyncio
async def test_migration_000002_down(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.down.sql")

        for table in [
            "embedding_configs",
            "content_chunks",
            "content_chunks_fts",
            "memories",
            "memories_fts",
            "memory_versions",
            "session_summaries",
            "session_summaries_fts",
        ]:
            assert not await _table_exists(db, table), f"Table {table} should be dropped"


# ── Migration 000003: Entity Graph ──


@pytest.mark.asyncio
async def test_migration_000003_up(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")

        for table in ["entities", "relationships", "entity_mentions"]:
            assert await _table_exists(db, table), f"Table {table} should exist"

        for trigger in ["entities_updated_at", "relationships_updated_at"]:
            assert await _trigger_exists(db, trigger), f"Trigger {trigger} should exist"


@pytest.mark.asyncio
async def test_migration_000003_down(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.down.sql")

        for table in ["entities", "relationships", "entity_mentions"]:
            assert not await _table_exists(db, table), f"Table {table} should be dropped"


# ── Migration 000004: Audit Log ──


@pytest.mark.asyncio
async def test_migration_000004_up(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000004_audit_log.up.sql")

        assert await _table_exists(db, "audit_log"), "audit_log table should exist"


@pytest.mark.asyncio
async def test_migration_000004_down(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000004_audit_log.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000004_audit_log.down.sql")

        assert not await _table_exists(db, "audit_log"), "audit_log table should be dropped"


# ── Full round-trip: up all → down all → up all ──


@pytest.mark.asyncio
async def test_full_migration_round_trip(tmp_path):
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(db_path) as db:
        # Up all
        for name in [
            "000001_init.up.sql",
            "000002_knowledge_store.up.sql",
            "000003_entity_graph.up.sql",
            "000004_audit_log.up.sql",
        ]:
            await _apply_sql(db, MIGRATIONS_DIR / name)

        assert await _table_exists(db, "settings")
        assert await _table_exists(db, "memories")
        assert await _table_exists(db, "entities")
        assert await _table_exists(db, "audit_log")

        # Down all (reverse order)
        for name in [
            "000004_audit_log.down.sql",
            "000003_entity_graph.down.sql",
            "000002_knowledge_store.down.sql",
            "000001_init.down.sql",
        ]:
            await _apply_sql(db, MIGRATIONS_DIR / name)

        assert not await _table_exists(db, "settings")
        assert not await _table_exists(db, "memories")
        assert not await _table_exists(db, "entities")
        assert not await _table_exists(db, "audit_log")

        # Up all again
        for name in [
            "000001_init.up.sql",
            "000002_knowledge_store.up.sql",
            "000003_entity_graph.up.sql",
            "000004_audit_log.up.sql",
        ]:
            await _apply_sql(db, MIGRATIONS_DIR / name)

        assert await _table_exists(db, "memories")
        assert await _table_exists(db, "entities")
        assert await _table_exists(db, "audit_log")


# ── ensure_vec_tables ──


@pytest.mark.asyncio
async def test_ensure_vec_tables_graceful_degradation(tmp_path):
    """When sqlite-vec is not available, ensure_vec_tables returns has_vec=False."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from backend.app.foundations.knowledge.capabilities import ensure_vec_tables

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Apply base migrations so tables exist
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")

    caps = await ensure_vec_tables(engine, dimension=1024)

    # sqlite-vec is likely not installed in test environment
    # Either way, the function should not raise
    assert isinstance(caps.vec_dimension, int)
    assert caps.vec_dimension == 1024

    if not caps.has_vec:
        # Verify vec tables were NOT created
        async with aiosqlite.connect(db_path) as db:
            assert not await _table_exists(db, "content_chunks_vec")
            assert not await _table_exists(db, "memories_vec")

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_vec_tables_creates_tables_when_available(tmp_path):
    """When sqlite-vec IS available, vec0 tables should be created."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from backend.app.foundations.knowledge.capabilities import ensure_vec_tables

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Apply base migrations
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")

    caps = await ensure_vec_tables(engine, dimension=512)

    if caps.has_vec:
        # Verify vec tables were created
        async with aiosqlite.connect(db_path) as db:
            for table in [
                "content_chunks_vec",
                "memories_vec",
                "session_summaries_vec",
                "entities_vec",
            ]:
                assert await _table_exists(db, table), f"Vec table {table} should exist"

    await engine.dispose()


@pytest.mark.asyncio
async def test_check_capabilities(tmp_path):
    """check_capabilities should probe and return a valid capabilities object."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from backend.app.foundations.knowledge.capabilities import check_capabilities

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Apply migrations including settings table
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")

    caps = await check_capabilities(engine)
    assert isinstance(caps.vec_dimension, int)
    assert caps.vec_dimension == 1024  # default when no setting exists

    await engine.dispose()


@pytest.mark.asyncio
async def test_check_capabilities_reads_dimension_from_settings(tmp_path):
    """check_capabilities should read dimension from settings table."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from backend.app.foundations.knowledge.capabilities import check_capabilities

    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("embedding.output_dimension", "512"),
        )
        await db.commit()

    caps = await check_capabilities(engine)
    assert caps.vec_dimension == 512

    await engine.dispose()
