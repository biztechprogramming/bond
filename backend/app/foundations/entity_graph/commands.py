"""Mediator commands for entity graph operations."""

from __future__ import annotations

from backend.app.mediator.base import Command


class ExtractEntities(Command):
    """Extract entities from content text."""

    content: str
    source_type: str
    source_id: str


class LookupEntity(Command):
    """Look up an entity by name and get its neighborhood."""

    name: str
    type: str | None = None
    depth: int = 1


class MergeEntities(Command):
    """Merge two entities, keeping one and absorbing the other."""

    keep_id: str
    merge_id: str


class GetEntityContext(Command):
    """Get enrichment context for entities mentioned in a query."""

    query: str
    max_entities: int = 5
    depth: int = 1
