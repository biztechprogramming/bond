"""Data classes for the entity graph."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class CreateEntityInput:
    """Input for creating an entity."""

    type: str
    name: str
    metadata: dict = field(default_factory=dict)


@dataclass
class UpdateEntityInput:
    """Input for updating an entity."""

    name: str | None = None
    metadata: dict | None = None


@dataclass
class Entity:
    """A stored entity."""

    id: str
    type: str
    name: str
    metadata: dict
    embedding_model: str | None
    processed_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> Entity:
        """Build from a database row mapping."""
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            metadata=meta,
            embedding_model=row["embedding_model"],
            processed_at=row["processed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class CreateRelationshipInput:
    """Input for creating a relationship."""

    source_id: str
    target_id: str
    type: str
    weight: float = 1.0
    context: str | None = None


@dataclass
class Relationship:
    """A stored relationship between two entities."""

    id: str
    source_id: str
    target_id: str
    type: str
    weight: float
    context: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> Relationship:
        """Build from a database row mapping."""
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            type=row["type"],
            weight=float(row["weight"]),
            context=row["context"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class EntityMention:
    """A record of where an entity was mentioned."""

    id: str
    entity_id: str
    source_type: str
    source_id: str
    created_at: str

    @classmethod
    def from_row(cls, row) -> EntityMention:
        """Build from a database row mapping."""
        return cls(
            id=row["id"],
            entity_id=row["entity_id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            created_at=row["created_at"],
        )


@dataclass
class EntityGraph:
    """A subgraph centered on one or more entities."""

    entities: dict[str, Entity] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    center_id: str = ""

    def get_related(
        self, entity_id: str, rel_type: str | None = None
    ) -> list[Entity]:
        """Get entities related to the given entity, optionally filtered by type."""
        related_ids: set[str] = set()
        for rel in self.relationships:
            if rel_type and rel.type != rel_type:
                continue
            if rel.source_id == entity_id:
                related_ids.add(rel.target_id)
            elif rel.target_id == entity_id:
                related_ids.add(rel.source_id)
        return [self.entities[eid] for eid in related_ids if eid in self.entities]

    def to_context_string(self) -> str:
        """Render the graph as natural language for LLM prompt injection."""
        if not self.entities:
            return ""

        center = self.entities.get(self.center_id)
        if not center:
            return ""

        lines: list[str] = []
        lines.append(f"{center.name} is a {center.type}.")

        for rel in self.relationships:
            if rel.source_id == self.center_id:
                target = self.entities.get(rel.target_id)
                if target:
                    lines.append(
                        f"{center.name} {rel.type} {target.name} ({target.type})."
                    )
            elif rel.target_id == self.center_id:
                source = self.entities.get(rel.source_id)
                if source:
                    lines.append(
                        f"{source.name} ({source.type}) {rel.type} {center.name}."
                    )

        return " ".join(lines)
