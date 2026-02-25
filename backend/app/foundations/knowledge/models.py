"""Data classes for the knowledge store."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SaveChunkInput:
    """Input for saving a content chunk."""

    source_type: str
    source_id: str | None = None
    text: str = ""
    summary: str | None = None
    chunk_index: int = 0
    parent_id: str | None = None
    sensitivity: str = "normal"
    metadata: dict = field(default_factory=dict)


@dataclass
class ContentChunk:
    """A stored content chunk."""

    id: str
    source_type: str
    source_id: str | None
    text: str
    summary: str | None
    chunk_index: int
    parent_id: str | None
    sensitivity: str
    metadata: dict
    embedding_model: str | None
    processed_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row) -> ContentChunk:
        """Build from a database row (sqlite3.Row or tuple with column names)."""
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            text=row["text"],
            summary=row["summary"],
            chunk_index=row["chunk_index"],
            parent_id=row["parent_id"],
            sensitivity=row["sensitivity"],
            metadata=meta,
            embedding_model=row["embedding_model"],
            processed_at=row["processed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class SearchResult:
    """A search result combining FTS and vector scores."""

    id: str
    content: str
    summary: str | None = None
    score: float = 0.0
    vector_score: float = 0.0
    fts_score: float = 0.0
    recency_boost: float = 0.0
    source_table: str = ""
    source_type: str = ""
    metadata: dict = field(default_factory=dict)
    sensitivity: str = "normal"
