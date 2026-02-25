"""Tests for HybridSearcher — FTS, RRF merge, and pre-filtering."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
from backend.app.foundations.knowledge.models import SearchResult
from backend.app.foundations.knowledge.search import HybridSearcher, _recency_boost

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
async def session(tmp_path):
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


async def _insert_chunk(session, chunk_id: str, text: str, source_type: str = "test"):
    """Insert a content chunk directly for test setup."""
    from sqlalchemy import text as sa_text

    await session.execute(
        sa_text(
            "INSERT INTO content_chunks (id, source_type, text, summary, metadata) "
            "VALUES (:id, :source_type, :text, :summary, '{}')"
        ),
        {"id": chunk_id, "source_type": source_type, "text": text, "summary": text[:50]},
    )
    await session.flush()


async def _insert_memory(session, mem_id: str, content: str, mem_type: str = "fact"):
    """Insert a memory directly for test setup."""
    from sqlalchemy import text as sa_text

    await session.execute(
        sa_text(
            "INSERT INTO memories "
            "(id, type, content, summary, sensitivity, metadata, importance, access_count) "
            "VALUES (:id, :type, :content, :summary, 'normal', '{}', 0.5, 0)"
        ),
        {"id": mem_id, "type": mem_type, "content": content, "summary": content[:50]},
    )
    await session.flush()


# ── FTS-only search (no vec) ──


@pytest.mark.asyncio
async def test_fts_only_search_content_chunks(session):
    await _insert_chunk(session, "c1", "Python is excellent for data science")
    await _insert_chunk(session, "c2", "JavaScript powers the modern web")
    await _insert_chunk(session, "c3", "Python and machine learning go together")

    caps = KnowledgeStoreCapabilities(has_vec=False)
    searcher = HybridSearcher(session, caps)

    results = await searcher.search("content_chunks", "Python data science")
    assert len(results) >= 1
    ids = [r.id for r in results]
    assert "c1" in ids


@pytest.mark.asyncio
async def test_fts_only_search_memories(session):
    await _insert_memory(session, "m1", "The user likes dark mode")
    await _insert_memory(session, "m2", "The user prefers Python over Java")
    await _insert_memory(session, "m3", "The user's timezone is UTC+1")

    caps = KnowledgeStoreCapabilities(has_vec=False)
    searcher = HybridSearcher(session, caps)

    results = await searcher.search("memories", "Python Java")
    assert len(results) >= 1
    ids = [r.id for r in results]
    assert "m2" in ids


@pytest.mark.asyncio
async def test_fts_search_with_source_type_filter(session):
    await _insert_chunk(session, "c1", "API design patterns", source_type="file")
    await _insert_chunk(session, "c2", "API testing strategies", source_type="conversation")

    caps = KnowledgeStoreCapabilities(has_vec=False)
    searcher = HybridSearcher(session, caps)

    results = await searcher.search(
        "content_chunks", "API", source_types=["file"]
    )
    assert all(r.source_type == "file" for r in results)


# ── RRF merge logic ──


def test_rrf_merge_fts_only():
    caps = KnowledgeStoreCapabilities(has_vec=False)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._caps = caps

    fts_results = [
        SearchResult(id="a", content="first", fts_score=10.0),
        SearchResult(id="b", content="second", fts_score=5.0),
        SearchResult(id="c", content="third", fts_score=1.0),
    ]

    merged = searcher._rrf_merge(fts_results, {})
    assert len(merged) == 3
    # First result should have highest RRF score
    scores = {r.id: r.score for r in merged}
    assert scores["a"] > scores["b"] > scores["c"]


def test_rrf_merge_with_vec_scores():
    caps = KnowledgeStoreCapabilities(has_vec=True)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._caps = caps

    fts_results = [
        SearchResult(id="a", content="first", fts_score=10.0),
        SearchResult(id="b", content="second", fts_score=5.0),
    ]
    vec_scores = {"b": 0.95, "a": 0.80}

    merged = searcher._rrf_merge(fts_results, vec_scores)
    assert len(merged) == 2
    # Both should have combined FTS + vec RRF scores
    for r in merged:
        assert r.score > 0


def test_rrf_merge_vec_boosts_lower_fts_result():
    """Vec score can boost a result that was lower in FTS ranking."""
    caps = KnowledgeStoreCapabilities(has_vec=True)
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher._caps = caps

    fts_results = [
        SearchResult(id="a", content="first", fts_score=10.0),
        SearchResult(id="b", content="second", fts_score=5.0),
    ]
    # b is the top vec result, a is not in vec at all
    vec_scores = {"b": 0.99}

    merged = searcher._rrf_merge(fts_results, vec_scores)
    scores = {r.id: r.score for r in merged}
    # b should get boosted by vec RRF score
    assert scores["b"] > scores["a"]


# ── Recency boost ──


def test_recency_boost_recent():
    """Very recent items get maximum boost."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    boost = _recency_boost(now)
    assert 0.009 < boost <= 0.01  # close to max 0.01


def test_recency_boost_old():
    """Items from 90 days ago get diminished boost."""
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    boost = _recency_boost(old)
    assert boost < 0.003  # significantly decayed


def test_recency_boost_none():
    assert _recency_boost(None) == 0.0


def test_recency_boost_invalid():
    assert _recency_boost("not-a-date") == 0.0
