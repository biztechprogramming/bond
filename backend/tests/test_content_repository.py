"""Tests for ContentRepository — CRUD and FTS search."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _setup_db(db_path: Path) -> None:
    """Apply migrations to create schema."""
    async with aiosqlite.connect(db_path) as db:
        for name in [
            "000001_init.up.sql",
            "000002_knowledge_store.up.sql",
        ]:
            sql = (MIGRATIONS_DIR / name).read_text()
            await db.executescript(sql)


@pytest.fixture()
async def session(tmp_path):
    """Create an async session with migrated schema."""
    db_path = tmp_path / "test.db"
    await _setup_db(db_path)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_save_and_get(session):
    from backend.app.foundations.knowledge.models import SaveChunkInput
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)
    chunk = await repo.save_chunk(SaveChunkInput(
        source_type="conversation",
        source_id="sess_001",
        text="The user prefers dark mode",
        summary="Dark mode preference",
        metadata={"turn": 5},
    ))

    assert chunk.id is not None
    assert chunk.source_type == "conversation"
    assert chunk.text == "The user prefers dark mode"
    assert chunk.metadata == {"turn": 5}

    # Get by ID
    fetched = await repo.get(chunk.id)
    assert fetched is not None
    assert fetched.id == chunk.id
    assert fetched.text == chunk.text


@pytest.mark.asyncio
async def test_get_nonexistent(session):
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)
    result = await repo.get("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_by_source(session):
    from backend.app.foundations.knowledge.models import SaveChunkInput
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)

    await repo.save_chunk(SaveChunkInput(
        source_type="email", source_id="email_001", text="First chunk", chunk_index=0,
    ))
    await repo.save_chunk(SaveChunkInput(
        source_type="email", source_id="email_001", text="Second chunk", chunk_index=1,
    ))
    await repo.save_chunk(SaveChunkInput(
        source_type="email", source_id="email_002", text="Other email",
    ))

    chunks = await repo.get_by_source("email", "email_001")
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1


@pytest.mark.asyncio
async def test_delete(session):
    from backend.app.foundations.knowledge.models import SaveChunkInput
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)
    chunk = await repo.save_chunk(SaveChunkInput(
        source_type="web", text="Some content",
    ))

    assert await repo.delete(chunk.id) is True
    assert await repo.get(chunk.id) is None
    assert await repo.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_fts_search(session):
    from backend.app.foundations.knowledge.models import SaveChunkInput
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)

    await repo.save_chunk(SaveChunkInput(
        source_type="conversation", source_id="s1",
        text="Python is a great programming language for machine learning",
        summary="Python ML",
    ))
    await repo.save_chunk(SaveChunkInput(
        source_type="conversation", source_id="s1",
        text="JavaScript is used for web development",
        summary="JS web dev",
    ))
    await repo.save_chunk(SaveChunkInput(
        source_type="file", source_id="f1",
        text="Rust provides memory safety without garbage collection",
        summary="Rust safety",
    ))

    # Search for Python
    results = await repo.search("Python programming")
    assert len(results) >= 1
    assert any("Python" in r.content for r in results)

    # Search with source_type filter
    results = await repo.search("programming", source_types=["file"])
    # Should not match the conversation chunks
    for r in results:
        assert r.source_type == "file"


@pytest.mark.asyncio
async def test_fts_search_empty_results(session):
    from backend.app.foundations.knowledge.repository import ContentRepository

    repo = ContentRepository(session)
    results = await repo.search("xyznonexistentterm")
    assert results == []
