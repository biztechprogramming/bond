"""Content repository — CRUD for content_chunks with FTS integration."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from .models import ContentChunk, SaveChunkInput, SearchResult

logger = logging.getLogger(__name__)


class ContentRepository:
    """Repository for content_chunks table with FTS search."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_chunk(self, chunk: SaveChunkInput) -> ContentChunk:
        """Insert a content chunk. FTS is synced via database trigger."""
        chunk_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(chunk.metadata)

        await self._session.execute(
            _text(
                "INSERT INTO content_chunks "
                "(id, source_type, source_id, text, summary, chunk_index, "
                "parent_id, sensitivity, metadata, created_at, updated_at) "
                "VALUES (:id, :source_type, :source_id, :text, :summary, "
                ":chunk_index, :parent_id, :sensitivity, :metadata, :created_at, :updated_at)"
            ),
            {
                "id": chunk_id,
                "source_type": chunk.source_type,
                "source_id": chunk.source_id,
                "text": chunk.text,
                "summary": chunk.summary,
                "chunk_index": chunk.chunk_index,
                "parent_id": chunk.parent_id,
                "sensitivity": chunk.sensitivity,
                "metadata": meta_json,
                "created_at": now,
                "updated_at": now,
            },
        )
        await self._session.flush()

        return await self.get(chunk_id)

    async def get(self, id: str) -> ContentChunk | None:
        """Fetch a content chunk by ID."""
        result = await self._session.execute(
            _text("SELECT * FROM content_chunks WHERE id = :id"),
            {"id": id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return ContentChunk.from_row(row)

    async def get_by_source(
        self, source_type: str, source_id: str
    ) -> list[ContentChunk]:
        """Fetch all chunks for a given source."""
        result = await self._session.execute(
            _text(
                "SELECT * FROM content_chunks "
                "WHERE source_type = :source_type AND source_id = :source_id "
                "ORDER BY chunk_index"
            ),
            {"source_type": source_type, "source_id": source_id},
        )
        return [ContentChunk.from_row(row) for row in result.mappings().all()]

    async def delete(self, id: str) -> bool:
        """Delete a content chunk. FTS cleanup via trigger."""
        result = await self._session.execute(
            _text("DELETE FROM content_chunks WHERE id = :id"),
            {"id": id},
        )
        await self._session.flush()
        return result.rowcount > 0

    async def search(
        self,
        query: str,
        embedding: list[float] | None = None,
        *,
        limit: int = 10,
        source_types: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[SearchResult]:
        """Search content chunks using FTS5 (vector search delegated to HybridSearcher)."""
        # Build FTS query
        params: dict = {"query": query, "limit": limit}
        where_clauses = []

        if source_types:
            placeholders = ", ".join(f":st{i}" for i in range(len(source_types)))
            where_clauses.append(f"cc.source_type IN ({placeholders})")
            for i, st in enumerate(source_types):
                params[f"st{i}"] = st

        if since:
            where_clauses.append("cc.created_at >= :since")
            params["since"] = since

        if until:
            where_clauses.append("cc.created_at <= :until")
            params["until"] = until

        where_sql = ""
        if where_clauses:
            where_sql = "AND " + " AND ".join(where_clauses)

        sql = (
            "SELECT cc.*, fts.rank AS fts_rank "
            "FROM content_chunks_fts fts "
            "JOIN content_chunks cc ON cc.id = fts.id "
            f"WHERE content_chunks_fts MATCH :query {where_sql} "
            "ORDER BY fts.rank "
            f"LIMIT :limit"
        )

        result = await self._session.execute(_text(sql), params)
        rows = result.mappings().all()

        results = []
        for row in rows:
            meta = row["metadata"] if row["metadata"] else "{}"
            if isinstance(meta, str):
                meta = json.loads(meta)
            results.append(
                SearchResult(
                    id=row["id"],
                    content=row["text"],
                    summary=row["summary"],
                    fts_score=abs(row["fts_rank"]) if row["fts_rank"] else 0.0,
                    score=abs(row["fts_rank"]) if row["fts_rank"] else 0.0,
                    source_table="content_chunks",
                    source_type=row["source_type"],
                    metadata=meta,
                    sensitivity=row["sensitivity"],
                )
            )

        return results


def _text(sql: str):
    """Create a SQLAlchemy text() object."""
    from sqlalchemy import text

    return text(sql)
