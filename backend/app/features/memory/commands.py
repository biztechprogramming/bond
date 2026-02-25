"""Mediator commands for memory operations."""

from __future__ import annotations

from backend.app.mediator.base import Command


class SaveMemory(Command):
    """Save a new memory."""

    type: str
    content: str
    summary: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    sensitivity: str = "normal"
    metadata: dict = {}
    importance: float = 0.5


class SearchMemory(Command):
    """Search memories."""

    query: str
    limit: int = 10
    memory_types: list[str] | None = None


class UpdateMemory(Command):
    """Update a memory's content."""

    id: str
    content: str
    changed_by: str
    reason: str


class DeleteMemory(Command):
    """Soft-delete a memory."""

    id: str
    changed_by: str
    reason: str
