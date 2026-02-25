"""Tests for MemoryRepository — CRUD, versioning, access tracking, search."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
from backend.app.foundations.knowledge.search import HybridSearcher

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _setup_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        for name in [
            "000001_init.up.sql",
            "000002_knowledge_store.up.sql",
        ]:
            sql = (MIGRATIONS_DIR / name).read_text()
            await db.executescript(sql)


@pytest.fixture()
async def repo(tmp_path):
    """Create a MemoryRepository with migrated schema."""
    db_path = tmp_path / "test.db"
    await _setup_db(db_path)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(session, caps)

        from backend.app.features.memory.repository import MemoryRepository

        yield MemoryRepository(session, searcher)
    await engine.dispose()


# ── Save and Get ──


@pytest.mark.asyncio
async def test_save_and_get(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="fact",
        content="The user's name is Alice",
        summary="User name is Alice",
        source_type="conversation",
        source_id="sess_001",
        importance=0.8,
    ))

    assert memory.id is not None
    assert memory.type == "fact"
    assert memory.content == "The user's name is Alice"
    assert memory.importance == 0.8
    assert memory.access_count == 0

    # Get by ID
    fetched = await repo.get(memory.id)
    assert fetched is not None
    assert fetched.id == memory.id


@pytest.mark.asyncio
async def test_get_nonexistent(repo):
    result = await repo.get("nonexistent-id")
    assert result is None


# ── Update with versioning ──


@pytest.mark.asyncio
async def test_update_creates_version(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="fact", content="User likes Python",
    ))

    updated = await repo.update(
        memory.id, "User loves Python and Rust", "user", "correction"
    )
    assert updated.content == "User loves Python and Rust"

    versions = await repo.get_versions(memory.id)
    assert len(versions) == 2
    assert versions[0].version == 1
    assert versions[0].new_content == "User likes Python"
    assert versions[0].previous_content is None
    assert versions[1].version == 2
    assert versions[1].previous_content == "User likes Python"
    assert versions[1].new_content == "User loves Python and Rust"
    assert versions[1].changed_by == "user"
    assert versions[1].change_reason == "correction"


@pytest.mark.asyncio
async def test_update_nonexistent_raises(repo):
    with pytest.raises(ValueError, match="not found"):
        await repo.update("nonexistent", "content", "user", "reason")


# ── Soft delete ──


@pytest.mark.asyncio
async def test_soft_delete(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="fact", content="Temporary fact",
    ))

    assert await repo.soft_delete(memory.id, "user", "no longer relevant") is True

    # Should not be retrievable via normal get
    assert await repo.get(memory.id) is None

    # Deleting again should return False
    assert await repo.soft_delete(memory.id, "user", "duplicate delete") is False


@pytest.mark.asyncio
async def test_soft_delete_creates_version(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="fact", content="Will be deleted",
    ))

    await repo.soft_delete(memory.id, "agent", "outdated")

    versions = await repo.get_versions(memory.id)
    assert len(versions) == 2
    assert versions[1].new_content == "[deleted]"
    assert versions[1].changed_by == "agent"
    assert versions[1].change_reason == "outdated"


# ── Access tracking ──


@pytest.mark.asyncio
async def test_update_access(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="fact", content="Accessed fact",
    ))
    assert memory.access_count == 0

    await repo.update_access(memory.id)
    updated = await repo.get(memory.id)
    assert updated.access_count == 1
    assert updated.last_accessed_at is not None

    await repo.update_access(memory.id)
    updated = await repo.get(memory.id)
    assert updated.access_count == 2


# ── Search (FTS-only mode) ──


@pytest.mark.asyncio
async def test_search_fts(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    await repo.save(SaveMemoryInput(
        type="fact", content="Python is the user's favorite language",
    ))
    await repo.save(SaveMemoryInput(
        type="preference", content="Dark mode is preferred",
    ))

    results = await repo.search("Python language")
    assert len(results) >= 1
    assert any("Python" in r.content for r in results)


# ── Duplicate check (no vec available) ──


@pytest.mark.asyncio
async def test_check_duplicates_no_vec(repo):
    """Without vec0, check_duplicates returns empty list."""
    results = await repo.check_duplicates([0.0] * 1024, "fact")
    assert results == []


# ── Get versions ──


@pytest.mark.asyncio
async def test_get_versions_empty(repo):
    versions = await repo.get_versions("nonexistent")
    assert versions == []


@pytest.mark.asyncio
async def test_multiple_updates_track_history(repo):
    from backend.app.features.memory.models import SaveMemoryInput

    memory = await repo.save(SaveMemoryInput(
        type="instruction", content="v1",
    ))

    await repo.update(memory.id, "v2", "user", "update 1")
    await repo.update(memory.id, "v3", "agent", "update 2")

    versions = await repo.get_versions(memory.id)
    assert len(versions) == 3
    assert [v.version for v in versions] == [1, 2, 3]
    assert versions[0].new_content == "v1"
    assert versions[1].new_content == "v2"
    assert versions[2].new_content == "v3"
