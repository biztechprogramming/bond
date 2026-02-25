"""Data classes for persistent memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SaveMemoryInput:
    """Input for saving a new memory."""

    type: str  # 'fact', 'solution', 'instruction', 'preference'
    content: str
    summary: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    sensitivity: str = "normal"
    metadata: dict = field(default_factory=dict)
    importance: float = 0.5


@dataclass
class Memory:
    """A stored memory."""

    id: str
    type: str
    content: str
    summary: str | None
    source_type: str | None
    source_id: str | None
    sensitivity: str
    metadata: dict
    importance: float
    access_count: int
    last_accessed_at: str | None
    embedding_model: str | None
    processed_at: str | None
    deleted_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> Memory:
        """Build from a database row mapping."""
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            summary=row["summary"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            sensitivity=row["sensitivity"],
            metadata=meta,
            importance=float(row["importance"]),
            access_count=int(row["access_count"]),
            last_accessed_at=row["last_accessed_at"],
            embedding_model=row["embedding_model"],
            processed_at=row["processed_at"],
            deleted_at=row["deleted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class MemoryVersion:
    """An immutable version record for a memory."""

    id: str
    memory_id: str
    version: int
    previous_content: str | None
    new_content: str
    previous_type: str | None
    new_type: str
    changed_by: str
    change_reason: str | None
    created_at: str

    @classmethod
    def from_row(cls, row) -> MemoryVersion:
        """Build from a database row mapping."""
        return cls(
            id=row["id"],
            memory_id=row["memory_id"],
            version=int(row["version"]),
            previous_content=row["previous_content"],
            new_content=row["new_content"],
            previous_type=row["previous_type"],
            new_type=row["new_type"],
            changed_by=row["changed_by"],
            change_reason=row["change_reason"],
            created_at=row["created_at"],
        )
