"""Entity repository — CRUD, graph traversal, resolution."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.foundations.knowledge.models import SearchResult

from .models import (
    CreateEntityInput,
    CreateRelationshipInput,
    Entity,
    EntityGraph,
    EntityMention,
    Relationship,
    UpdateEntityInput,
)

logger = logging.getLogger(__name__)

# Weight decay half-life in days
WEIGHT_DECAY_HALF_LIFE = 180


def _effective_weight(base_weight: float, updated_at: str) -> float:
    """Compute effective weight with time decay.

    effective_weight = base_weight * 0.5^(days_since_updated / 180)
    """
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = max(0, (now - dt).total_seconds() / 86400)
        return base_weight * math.pow(0.5, days / WEIGHT_DECAY_HALF_LIFE)
    except (ValueError, TypeError):
        return base_weight


class EntityRepository:
    """Repository for entities, relationships, mentions, and graph traversal."""

    def __init__(
        self,
        session: AsyncSession,
        searcher=None,
    ) -> None:
        self._session = session
        self._searcher = searcher

    # ── CRUD ──

    async def create(self, input: CreateEntityInput) -> Entity:
        """Insert a new entity."""
        entity_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(input.metadata)

        await self._session.execute(
            text(
                "INSERT INTO entities (id, type, name, metadata, created_at, updated_at) "
                "VALUES (:id, :type, :name, :metadata, :created_at, :updated_at)"
            ),
            {
                "id": entity_id,
                "type": input.type,
                "name": input.name,
                "metadata": meta_json,
                "created_at": now,
                "updated_at": now,
            },
        )
        await self._session.flush()
        return await self.get(entity_id)

    async def get(self, id: str) -> Entity | None:
        """Fetch an entity by ID."""
        result = await self._session.execute(
            text("SELECT * FROM entities WHERE id = :id"),
            {"id": id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return Entity.from_row(row)

    async def get_by_name(
        self, name: str, type: str | None = None
    ) -> list[Entity]:
        """Fetch entities by name (case-insensitive), optionally filtered by type."""
        if type:
            result = await self._session.execute(
                text(
                    "SELECT * FROM entities "
                    "WHERE LOWER(name) = LOWER(:name) AND type = :type"
                ),
                {"name": name, "type": type},
            )
        else:
            result = await self._session.execute(
                text("SELECT * FROM entities WHERE LOWER(name) = LOWER(:name)"),
                {"name": name},
            )
        return [Entity.from_row(row) for row in result.mappings().all()]

    async def update(self, id: str, updates: UpdateEntityInput) -> Entity:
        """Update an entity's name and/or metadata."""
        current = await self.get(id)
        if current is None:
            raise ValueError(f"Entity {id} not found")

        if updates.name is not None:
            await self._session.execute(
                text("UPDATE entities SET name = :name WHERE id = :id"),
                {"id": id, "name": updates.name},
            )

        if updates.metadata is not None:
            merged = {**current.metadata, **updates.metadata}
            await self._session.execute(
                text("UPDATE entities SET metadata = :metadata WHERE id = :id"),
                {"id": id, "metadata": json.dumps(merged)},
            )

        await self._session.flush()
        return await self.get(id)

    async def merge(self, keep_id: str, merge_id: str) -> Entity:
        """Merge merge_id entity into keep_id. Re-point mentions and relationships."""
        keep = await self.get(keep_id)
        merge_entity = await self.get(merge_id)
        if keep is None:
            raise ValueError(f"Entity {keep_id} not found")
        if merge_entity is None:
            raise ValueError(f"Entity {merge_id} not found")

        # Merge metadata: append aliases, merge other fields
        merged_meta = {**keep.metadata}
        keep_aliases = set(merged_meta.get("aliases", []))
        merge_aliases = set(merge_entity.metadata.get("aliases", []))
        keep_aliases.add(merge_entity.name)
        keep_aliases |= merge_aliases
        merged_meta["aliases"] = sorted(keep_aliases)

        # Merge other metadata fields (new fields from merge entity)
        for key, value in merge_entity.metadata.items():
            if key != "aliases" and key not in merged_meta:
                merged_meta[key] = value

        await self._session.execute(
            text("UPDATE entities SET metadata = :metadata WHERE id = :id"),
            {"id": keep_id, "metadata": json.dumps(merged_meta)},
        )

        # Re-point entity_mentions
        await self._session.execute(
            text(
                "UPDATE entity_mentions SET entity_id = :keep_id "
                "WHERE entity_id = :merge_id"
            ),
            {"keep_id": keep_id, "merge_id": merge_id},
        )

        # Re-point relationships (source)
        await self._session.execute(
            text(
                "UPDATE OR IGNORE relationships SET source_id = :keep_id "
                "WHERE source_id = :merge_id"
            ),
            {"keep_id": keep_id, "merge_id": merge_id},
        )

        # Re-point relationships (target)
        await self._session.execute(
            text(
                "UPDATE OR IGNORE relationships SET target_id = :keep_id "
                "WHERE target_id = :merge_id"
            ),
            {"keep_id": keep_id, "merge_id": merge_id},
        )

        # Delete the merged entity (cascade deletes remaining relationships/mentions)
        await self._session.execute(
            text("DELETE FROM entities WHERE id = :id"),
            {"id": merge_id},
        )

        await self._session.flush()
        return await self.get(keep_id)

    async def delete(self, id: str) -> bool:
        """Delete an entity (cascade deletes relationships and mentions)."""
        result = await self._session.execute(
            text("DELETE FROM entities WHERE id = :id"),
            {"id": id},
        )
        await self._session.flush()
        return result.rowcount > 0

    # ── Relationships ──

    async def add_relationship(self, rel: CreateRelationshipInput) -> Relationship:
        """Create a relationship. Upserts: if same source/target/type exists, bump weight."""
        # Check for existing
        existing = await self._session.execute(
            text(
                "SELECT * FROM relationships "
                "WHERE source_id = :source_id AND target_id = :target_id AND type = :type"
            ),
            {
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "type": rel.type,
            },
        )
        row = existing.mappings().first()

        if row is not None:
            # Bump weight (capped at 1.0)
            new_weight = min(1.0, float(row["weight"]) + 0.1)
            now = datetime.now(timezone.utc).isoformat()
            await self._session.execute(
                text(
                    "UPDATE relationships SET weight = :weight, "
                    "context = COALESCE(:context, context), updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {
                    "id": row["id"],
                    "weight": new_weight,
                    "context": rel.context,
                    "updated_at": now,
                },
            )
            await self._session.flush()
            result = await self._session.execute(
                text("SELECT * FROM relationships WHERE id = :id"),
                {"id": row["id"]},
            )
            return Relationship.from_row(result.mappings().first())

        rel_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        await self._session.execute(
            text(
                "INSERT INTO relationships "
                "(id, source_id, target_id, type, weight, context, created_at, updated_at) "
                "VALUES (:id, :source_id, :target_id, :type, :weight, :context, "
                ":created_at, :updated_at)"
            ),
            {
                "id": rel_id,
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "type": rel.type,
                "weight": rel.weight,
                "context": rel.context,
                "created_at": now,
                "updated_at": now,
            },
        )
        await self._session.flush()

        result = await self._session.execute(
            text("SELECT * FROM relationships WHERE id = :id"),
            {"id": rel_id},
        )
        return Relationship.from_row(result.mappings().first())

    async def get_relationships(
        self,
        entity_id: str,
        direction: str = "both",
        rel_types: list[str] | None = None,
    ) -> list[Relationship]:
        """Get relationships for an entity with weight decay applied."""
        clauses = []
        params: dict = {"entity_id": entity_id}

        if direction == "outgoing":
            clauses.append("source_id = :entity_id")
        elif direction == "incoming":
            clauses.append("target_id = :entity_id")
        else:  # both
            clauses.append("(source_id = :entity_id OR target_id = :entity_id)")

        if rel_types:
            placeholders = ", ".join(f":rt{i}" for i in range(len(rel_types)))
            clauses.append(f"type IN ({placeholders})")
            for i, rt in enumerate(rel_types):
                params[f"rt{i}"] = rt

        where = " AND ".join(clauses)
        sql = f"SELECT * FROM relationships WHERE {where}"

        result = await self._session.execute(text(sql), params)
        rows = result.mappings().all()

        relationships = []
        for row in rows:
            rel = Relationship.from_row(row)
            rel.weight = _effective_weight(rel.weight, rel.updated_at)
            relationships.append(rel)

        return relationships

    async def update_relationship_weight(
        self, rel_id: str, weight: float
    ) -> Relationship:
        """Update a relationship's base weight."""
        now = datetime.now(timezone.utc).isoformat()
        await self._session.execute(
            text(
                "UPDATE relationships SET weight = :weight, updated_at = :updated_at "
                "WHERE id = :id"
            ),
            {"id": rel_id, "weight": weight, "updated_at": now},
        )
        await self._session.flush()

        result = await self._session.execute(
            text("SELECT * FROM relationships WHERE id = :id"),
            {"id": rel_id},
        )
        row = result.mappings().first()
        if row is None:
            raise ValueError(f"Relationship {rel_id} not found")
        return Relationship.from_row(row)

    # ── Mentions ──

    async def add_mention(
        self, entity_id: str, source_type: str, source_id: str
    ) -> EntityMention:
        """Record an entity mention."""
        mention_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        await self._session.execute(
            text(
                "INSERT INTO entity_mentions "
                "(id, entity_id, source_type, source_id, created_at) "
                "VALUES (:id, :entity_id, :source_type, :source_id, :created_at)"
            ),
            {
                "id": mention_id,
                "entity_id": entity_id,
                "source_type": source_type,
                "source_id": source_id,
                "created_at": now,
            },
        )
        await self._session.flush()

        result = await self._session.execute(
            text("SELECT * FROM entity_mentions WHERE id = :id"),
            {"id": mention_id},
        )
        return EntityMention.from_row(result.mappings().first())

    async def get_mentions(self, entity_id: str) -> list[EntityMention]:
        """Get all mentions for an entity."""
        result = await self._session.execute(
            text(
                "SELECT * FROM entity_mentions WHERE entity_id = :entity_id "
                "ORDER BY created_at"
            ),
            {"entity_id": entity_id},
        )
        return [EntityMention.from_row(row) for row in result.mappings().all()]

    # ── Graph traversal ──

    async def get_neighborhood(
        self,
        entity_id: str,
        depth: int = 1,
        rel_types: list[str] | None = None,
        min_weight: float = 0.0,
    ) -> EntityGraph:
        """BFS traversal up to `depth` hops from the center entity."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(entity_id, 0)]
        entities: dict[str, Entity] = {}
        relationships: list[Relationship] = []
        seen_rels: set[str] = set()

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            entity = await self.get(current_id)
            if entity:
                entities[current_id] = entity

            if current_depth < depth:
                rels = await self.get_relationships(
                    current_id, direction="both", rel_types=rel_types
                )
                for rel in rels:
                    if rel.weight >= min_weight and rel.id not in seen_rels:
                        seen_rels.add(rel.id)
                        relationships.append(rel)
                        next_id = (
                            rel.target_id
                            if rel.source_id == current_id
                            else rel.source_id
                        )
                        queue.append((next_id, current_depth + 1))

        return EntityGraph(
            entities=entities,
            relationships=relationships,
            center_id=entity_id,
        )

    async def find_path(
        self,
        source_id: str,
        target_id: str,
        max_depth: int = 4,
    ) -> list[Relationship] | None:
        """Find shortest path between two entities using BFS."""
        if source_id == target_id:
            return []

        visited: set[str] = set()
        # Queue stores: (current_entity_id, path_of_relationships)
        queue: list[tuple[str, list[Relationship]]] = [(source_id, [])]

        while queue:
            current_id, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            if current_id in visited:
                continue
            visited.add(current_id)

            rels = await self.get_relationships(current_id, direction="both")
            for rel in rels:
                next_id = (
                    rel.target_id
                    if rel.source_id == current_id
                    else rel.source_id
                )
                if next_id == target_id:
                    return path + [rel]
                if next_id not in visited:
                    queue.append((next_id, path + [rel]))

        return None

    # ── Search ──

    async def search(
        self,
        query: str,
        embedding: list[float] | None = None,
        *,
        entity_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search entities by name (LIKE) and optionally by embedding similarity."""
        params: dict = {"query": f"%{query}%", "limit": limit}
        where_clauses = ["name LIKE :query"]

        if entity_types:
            placeholders = ", ".join(f":et{i}" for i in range(len(entity_types)))
            where_clauses.append(f"type IN ({placeholders})")
            for i, et in enumerate(entity_types):
                params[f"et{i}"] = et

        where = " AND ".join(where_clauses)
        sql = f"SELECT * FROM entities WHERE {where} LIMIT :limit"

        result = await self._session.execute(text(sql), params)
        rows = result.mappings().all()

        return [
            SearchResult(
                id=row["id"],
                content=row["name"],
                summary=None,
                score=1.0,
                source_table="entities",
                source_type=row["type"],
                metadata=json.loads(row["metadata"])
                if isinstance(row["metadata"], str)
                else (row["metadata"] or {}),
            )
            for row in rows
        ]

    # ── Resolution ──

    async def resolve(
        self,
        name: str,
        type: str | None = None,
        metadata: dict | None = None,
    ) -> Entity | None:
        """Resolve a name to an existing entity using multi-signal matching.

        Steps:
        1. Exact name match (case-insensitive)
        2. Alias match (metadata.aliases)
        3. Metadata match (email for persons)
        4. Embedding similarity (not implemented without vec0)
        """
        # Step 1: Exact name match
        matches = await self.get_by_name(name, type=type)
        if matches:
            return matches[0]

        # Step 2: Alias match — scan entities for matching aliases
        if type:
            result = await self._session.execute(
                text("SELECT * FROM entities WHERE type = :type"),
                {"type": type},
            )
        else:
            result = await self._session.execute(text("SELECT * FROM entities"))
        all_rows = result.mappings().all()

        for row in all_rows:
            entity = Entity.from_row(row)
            aliases = entity.metadata.get("aliases", [])
            for alias in aliases:
                if alias.lower() == name.lower():
                    return entity

        # Step 3: Metadata match (email for persons)
        if metadata and metadata.get("email"):
            email = metadata["email"].lower()
            for row in all_rows:
                entity = Entity.from_row(row)
                entity_email = entity.metadata.get("email", "")
                if entity_email and entity_email.lower() == email:
                    return entity

        # Step 4: Embedding similarity would go here (requires vec0)

        return None
