"""Tests for EntityRepository — CRUD, relationships, mentions, graph traversal, resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.foundations.entity_graph.models import (
    CreateEntityInput,
    CreateRelationshipInput,
    UpdateEntityInput,
)
from backend.app.foundations.entity_graph.repository import (
    EntityRepository,
    _effective_weight,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _setup_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        for name in [
            "000001_init.up.sql",
            "000002_knowledge_store.up.sql",
            "000003_entity_graph.up.sql",
        ]:
            sql = (MIGRATIONS_DIR / name).read_text()
            await db.executescript(sql)


@pytest.fixture()
async def repo(tmp_path):
    """Create an EntityRepository with migrated schema."""
    db_path = tmp_path / "test.db"
    await _setup_db(db_path)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Enable foreign keys for this session
        await session.execute(text("PRAGMA foreign_keys = ON"))
        yield EntityRepository(session)
    await engine.dispose()


# ── CRUD ──


@pytest.mark.asyncio
async def test_create_and_get(repo):
    entity = await repo.create(
        CreateEntityInput(type="person", name="Sarah Chen", metadata={"role": "lead"})
    )
    assert entity.id is not None
    assert entity.type == "person"
    assert entity.name == "Sarah Chen"
    assert entity.metadata["role"] == "lead"

    fetched = await repo.get(entity.id)
    assert fetched is not None
    assert fetched.id == entity.id


@pytest.mark.asyncio
async def test_get_nonexistent(repo):
    assert await repo.get("nonexistent") is None


@pytest.mark.asyncio
async def test_get_by_name(repo):
    await repo.create(CreateEntityInput(type="person", name="Alice"))
    await repo.create(CreateEntityInput(type="project", name="Alice"))

    results = await repo.get_by_name("alice")  # case-insensitive
    assert len(results) == 2

    results = await repo.get_by_name("alice", type="person")
    assert len(results) == 1
    assert results[0].type == "person"


@pytest.mark.asyncio
async def test_update(repo):
    entity = await repo.create(CreateEntityInput(type="person", name="Bob"))
    updated = await repo.update(entity.id, UpdateEntityInput(name="Robert"))
    assert updated.name == "Robert"

    updated = await repo.update(entity.id, UpdateEntityInput(metadata={"email": "bob@test.com"}))
    assert updated.metadata["email"] == "bob@test.com"


@pytest.mark.asyncio
async def test_delete(repo):
    entity = await repo.create(CreateEntityInput(type="person", name="ToDelete"))
    assert await repo.delete(entity.id) is True
    assert await repo.get(entity.id) is None
    assert await repo.delete(entity.id) is False


@pytest.mark.asyncio
async def test_merge(repo):
    keep = await repo.create(
        CreateEntityInput(type="person", name="Sarah Chen", metadata={"role": "lead"})
    )
    merge = await repo.create(
        CreateEntityInput(
            type="person", name="Sarah",
            metadata={"email": "sarah@test.com", "aliases": ["SC"]},
        )
    )

    # Add a mention to the merge entity
    await repo.add_mention(merge.id, "conversation", "sess_1")

    merged = await repo.merge(keep.id, merge.id)
    assert merged.name == "Sarah Chen"
    assert "Sarah" in merged.metadata["aliases"]
    assert "SC" in merged.metadata["aliases"]
    assert merged.metadata["email"] == "sarah@test.com"

    # Merge entity should be gone
    assert await repo.get(merge.id) is None

    # Mentions re-pointed
    mentions = await repo.get_mentions(keep.id)
    assert len(mentions) == 1
    assert mentions[0].source_id == "sess_1"


# ── Relationships ──


@pytest.mark.asyncio
async def test_add_and_get_relationships(repo):
    a = await repo.create(CreateEntityInput(type="person", name="Alice"))
    b = await repo.create(CreateEntityInput(type="project", name="Bond"))

    rel = await repo.add_relationship(
        CreateRelationshipInput(
            source_id=a.id, target_id=b.id, type="works_on", weight=0.9,
            context="Alice works on Bond",
        )
    )
    assert rel.type == "works_on"
    assert rel.context == "Alice works on Bond"

    # Get outgoing
    rels = await repo.get_relationships(a.id, direction="outgoing")
    assert len(rels) == 1
    assert rels[0].target_id == b.id

    # Get incoming
    rels = await repo.get_relationships(b.id, direction="incoming")
    assert len(rels) == 1

    # Get both
    rels = await repo.get_relationships(a.id, direction="both")
    assert len(rels) == 1


@pytest.mark.asyncio
async def test_relationship_upsert_bumps_weight(repo):
    a = await repo.create(CreateEntityInput(type="person", name="Alice"))
    b = await repo.create(CreateEntityInput(type="project", name="Bond"))

    rel1 = await repo.add_relationship(
        CreateRelationshipInput(source_id=a.id, target_id=b.id, type="works_on", weight=0.7)
    )

    rel2 = await repo.add_relationship(
        CreateRelationshipInput(source_id=a.id, target_id=b.id, type="works_on", weight=0.7)
    )

    # Same relationship, weight bumped by 0.1
    assert rel2.id == rel1.id
    # Base weight in DB should be 0.8 (0.7 + 0.1)
    # But effective weight has decay applied (very small since just created)
    assert rel2.weight >= 0.79


@pytest.mark.asyncio
async def test_get_relationships_with_type_filter(repo):
    a = await repo.create(CreateEntityInput(type="person", name="Alice"))
    b = await repo.create(CreateEntityInput(type="project", name="Bond"))
    c = await repo.create(CreateEntityInput(type="person", name="Bob"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=a.id, target_id=b.id, type="works_on")
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=a.id, target_id=c.id, type="reports_to")
    )

    rels = await repo.get_relationships(a.id, rel_types=["works_on"])
    assert len(rels) == 1
    assert rels[0].type == "works_on"


@pytest.mark.asyncio
async def test_update_relationship_weight(repo):
    a = await repo.create(CreateEntityInput(type="person", name="Alice"))
    b = await repo.create(CreateEntityInput(type="project", name="Bond"))

    rel = await repo.add_relationship(
        CreateRelationshipInput(source_id=a.id, target_id=b.id, type="works_on", weight=0.5)
    )

    updated = await repo.update_relationship_weight(rel.id, 0.9)
    assert updated.weight == 0.9


# ── Mentions ──


@pytest.mark.asyncio
async def test_mentions(repo):
    entity = await repo.create(CreateEntityInput(type="person", name="Alice"))

    await repo.add_mention(entity.id, "conversation", "sess_1")
    await repo.add_mention(entity.id, "email", "email_42")

    mentions = await repo.get_mentions(entity.id)
    assert len(mentions) == 2
    assert mentions[0].source_type == "conversation"
    assert mentions[1].source_type == "email"


# ── Graph traversal ──


@pytest.mark.asyncio
async def test_neighborhood_depth_1(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))
    bob = await repo.create(CreateEntityInput(type="person", name="Bob"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on")
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bob.id, type="reports_to")
    )

    graph = await repo.get_neighborhood(alice.id, depth=1)
    assert alice.id in graph.entities
    assert bond.id in graph.entities
    assert bob.id in graph.entities
    assert len(graph.relationships) == 2
    assert graph.center_id == alice.id


@pytest.mark.asyncio
async def test_neighborhood_depth_2(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))
    task = await repo.create(CreateEntityInput(type="task", name="Fix login"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on")
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=task.id, target_id=bond.id, type="part_of")
    )

    # Depth 1: Alice sees Bond, but not Task
    graph1 = await repo.get_neighborhood(alice.id, depth=1)
    assert task.id not in graph1.entities

    # Depth 2: Alice sees Bond and Task
    graph2 = await repo.get_neighborhood(alice.id, depth=2)
    assert alice.id in graph2.entities
    assert bond.id in graph2.entities
    assert task.id in graph2.entities
    assert len(graph2.relationships) == 2


@pytest.mark.asyncio
async def test_neighborhood_min_weight(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))
    weak = await repo.create(CreateEntityInput(type="project", name="Weak"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on", weight=0.9)
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=weak.id, type="related_to", weight=0.1)
    )

    graph = await repo.get_neighborhood(alice.id, depth=1, min_weight=0.5)
    assert bond.id in graph.entities
    assert weak.id not in graph.entities


@pytest.mark.asyncio
async def test_find_path(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))
    task = await repo.create(CreateEntityInput(type="task", name="Fix login"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on")
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=task.id, target_id=bond.id, type="part_of")
    )

    path = await repo.find_path(alice.id, task.id)
    assert path is not None
    assert len(path) == 2  # Alice -> Bond -> Task


@pytest.mark.asyncio
async def test_find_path_no_connection(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bob = await repo.create(CreateEntityInput(type="person", name="Bob"))

    path = await repo.find_path(alice.id, bob.id)
    assert path is None


@pytest.mark.asyncio
async def test_find_path_same_entity(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    path = await repo.find_path(alice.id, alice.id)
    assert path == []


# ── Weight decay ──


def test_effective_weight_no_decay():
    """Weight should be near base when updated_at is recent."""
    now = datetime.now(timezone.utc).isoformat()
    w = _effective_weight(1.0, now)
    assert w > 0.99


def test_effective_weight_half_life():
    """Weight should be ~0.5 after 180 days."""
    past = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    w = _effective_weight(1.0, past)
    assert 0.45 <= w <= 0.55


def test_effective_weight_full_decay():
    """Weight should be very small after a long time."""
    past = (datetime.now(timezone.utc) - timedelta(days=720)).isoformat()
    w = _effective_weight(1.0, past)
    assert w < 0.1


# ── Resolution ──


@pytest.mark.asyncio
async def test_resolve_exact_name(repo):
    entity = await repo.create(
        CreateEntityInput(type="person", name="Sarah Chen")
    )
    resolved = await repo.resolve("sarah chen")  # case-insensitive
    assert resolved is not None
    assert resolved.id == entity.id


@pytest.mark.asyncio
async def test_resolve_alias(repo):
    entity = await repo.create(
        CreateEntityInput(
            type="person", name="Sarah Chen",
            metadata={"aliases": ["Sarah", "SC"]},
        )
    )
    resolved = await repo.resolve("SC", type="person")
    assert resolved is not None
    assert resolved.id == entity.id


@pytest.mark.asyncio
async def test_resolve_email(repo):
    entity = await repo.create(
        CreateEntityInput(
            type="person", name="Sarah Chen",
            metadata={"email": "sarah@test.com"},
        )
    )
    resolved = await repo.resolve(
        "Unknown Person", type="person",
        metadata={"email": "sarah@test.com"},
    )
    assert resolved is not None
    assert resolved.id == entity.id


@pytest.mark.asyncio
async def test_resolve_no_match(repo):
    resolved = await repo.resolve("Nobody")
    assert resolved is None


# ── Search ──


@pytest.mark.asyncio
async def test_search(repo):
    await repo.create(CreateEntityInput(type="person", name="Alice Smith"))
    await repo.create(CreateEntityInput(type="project", name="Bond"))

    results = await repo.search("Alice")
    assert len(results) == 1
    assert results[0].content == "Alice Smith"


@pytest.mark.asyncio
async def test_search_with_type_filter(repo):
    await repo.create(CreateEntityInput(type="person", name="Bond James"))
    await repo.create(CreateEntityInput(type="project", name="Bond"))

    results = await repo.search("Bond", entity_types=["project"])
    assert len(results) == 1
    assert results[0].source_type == "project"


# ── EntityGraph methods ──


@pytest.mark.asyncio
async def test_entity_graph_get_related(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))
    bob = await repo.create(CreateEntityInput(type="person", name="Bob"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on")
    )
    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bob.id, type="reports_to")
    )

    graph = await repo.get_neighborhood(alice.id, depth=1)
    related = graph.get_related(alice.id)
    assert len(related) == 2

    related_works = graph.get_related(alice.id, rel_type="works_on")
    assert len(related_works) == 1
    assert related_works[0].name == "Bond"


@pytest.mark.asyncio
async def test_entity_graph_to_context_string(repo):
    alice = await repo.create(CreateEntityInput(type="person", name="Alice"))
    bond = await repo.create(CreateEntityInput(type="project", name="Bond"))

    await repo.add_relationship(
        CreateRelationshipInput(source_id=alice.id, target_id=bond.id, type="works_on")
    )

    graph = await repo.get_neighborhood(alice.id, depth=1)
    ctx = graph.to_context_string()
    assert "Alice" in ctx
    assert "person" in ctx
    assert "works_on" in ctx
    assert "Bond" in ctx
