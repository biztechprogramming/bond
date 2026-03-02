"""Memory repository — CRUD with versioning, soft-delete, and dedup detection."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.foundations.knowledge.models import SearchResult
from backend.app.foundations.knowledge.search import HybridSearcher

from .models import Memory, MemoryVersion, SaveMemoryInput

logger = logging.getLogger(__name__)


class MemoryRepository:
    """Repository for memories table with versioning and hybrid search."""

    def __init__(self, session: AsyncSession, searcher: HybridSearcher) -> None:
        self._session = session
        self._searcher = searcher

    async def save(self, input: SaveMemoryInput) -> Memory:
        """Insert a new memory and create version 1."""
        memory_id = str(ULID())
        version_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(input.metadata)

        # Insert memory
        await self._session.execute(
            text(
                "INSERT INTO memories "
                "(id, type, content, summary, source_type, source_id, "
                "sensitivity, metadata, importance, access_count, "
                "created_at, updated_at) "
                "VALUES (:id, :type, :content, :summary, :source_type, "
                ":source_id, :sensitivity, :metadata, :importance, 0, "
                ":created_at, :updated_at)"
            ),
            {
                "id": memory_id,
                "type": input.type,
                "content": input.content,
                "summary": input.summary,
                "source_type": input.source_type,
                "source_id": input.source_id,
                "sensitivity": input.sensitivity,
                "metadata": meta_json,
                "importance": input.importance,
                "created_at": now,
                "updated_at": now,
            },
        )

        # Insert version 1
        await self._session.execute(
            text(
                "INSERT INTO memory_versions "
                "(id, memory_id, version, previous_content, new_content, "
                "previous_type, new_type, changed_by, change_reason, created_at) "
                "VALUES (:id, :memory_id, 1, NULL, :new_content, "
                "NULL, :new_type, 'system', 'initial creation', :created_at)"
            ),
            {
                "id": version_id,
                "memory_id": memory_id,
                "new_content": input.content,
                "new_type": input.type,
                "created_at": now,
            },
        )

        await self._session.flush()
        return await self.get(memory_id)

    async def get(self, id: str) -> Memory | None:
        """Fetch a memory by ID (excludes soft-deleted)."""
        result = await self._session.execute(
            text("SELECT * FROM memories WHERE id = :id AND deleted_at IS NULL"),
            {"id": id},
        )
        row = result.mappings().first()
        if row is None:
            return None
        return Memory.from_row(row)

    async def update(
        self, id: str, content: str, changed_by: str, reason: str
    ) -> Memory:
        """Update a memory's content and append a new version."""
        # Get current state
        current = await self.get(id)
        if current is None:
            raise ValueError(f"Memory {id} not found")

        # Get current version number
        result = await self._session.execute(
            text(
                "SELECT MAX(version) as max_ver FROM memory_versions "
                "WHERE memory_id = :memory_id"
            ),
            {"memory_id": id},
        )
        row = result.mappings().first()
        next_version = (row["max_ver"] or 0) + 1

        # Update memory
        now = datetime.now(timezone.utc).isoformat()
        await self._session.execute(
            text(
                "UPDATE memories SET content = :content, updated_at = :now "
                "WHERE id = :id"
            ),
            {"id": id, "content": content, "now": now},
        )

        # Append version
        version_id = str(ULID())
        await self._session.execute(
            text(
                "INSERT INTO memory_versions "
                "(id, memory_id, version, previous_content, new_content, "
                "previous_type, new_type, changed_by, change_reason, created_at) "
                "VALUES (:id, :memory_id, :version, :previous_content, "
                ":new_content, :previous_type, :new_type, :changed_by, "
                ":change_reason, :created_at)"
            ),
            {
                "id": version_id,
                "memory_id": id,
                "version": next_version,
                "previous_content": current.content,
                "new_content": content,
                "previous_type": current.type,
                "new_type": current.type,
                "changed_by": changed_by,
                "change_reason": reason,
                "created_at": now,
            },
        )

        await self._session.flush()
        return await self.get(id)

    async def soft_delete(self, id: str, changed_by: str, reason: str) -> bool:
        """Soft-delete a memory by setting deleted_at."""
        now = datetime.now(timezone.utc).isoformat()

        result = await self._session.execute(
            text(
                "UPDATE memories SET deleted_at = :now "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": id, "now": now},
        )

        if result.rowcount == 0:
            return False

        # Record deletion in versions
        current_result = await self._session.execute(
            text("SELECT * FROM memories WHERE id = :id"),
            {"id": id},
        )
        current_row = current_result.mappings().first()

        ver_result = await self._session.execute(
            text(
                "SELECT MAX(version) as max_ver FROM memory_versions "
                "WHERE memory_id = :memory_id"
            ),
            {"memory_id": id},
        )
        ver_row = ver_result.mappings().first()
        next_version = (ver_row["max_ver"] or 0) + 1

        version_id = str(ULID())
        await self._session.execute(
            text(
                "INSERT INTO memory_versions "
                "(id, memory_id, version, previous_content, new_content, "
                "previous_type, new_type, changed_by, change_reason, created_at) "
                "VALUES (:id, :memory_id, :version, :previous_content, "
                ":new_content, :previous_type, :new_type, :changed_by, "
                ":change_reason, :created_at)"
            ),
            {
                "id": version_id,
                "memory_id": id,
                "version": next_version,
                "previous_content": current_row["content"] if current_row else "",
                "new_content": "[deleted]",
                "previous_type": current_row["type"] if current_row else "",
                "new_type": current_row["type"] if current_row else "",
                "changed_by": changed_by,
                "change_reason": reason,
                "created_at": now,
            },
        )

        await self._session.flush()
        return True

    async def search(
        self,
        query: str,
        embedding: list[float] | None = None,
        *,
        limit: int = 10,
        memory_types: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[SearchResult]:
        """Search memories using hybrid FTS + vector search."""
        return await self._searcher.search(
            table_name="memories",
            query_text=query,
            query_embedding=embedding,
            limit=limit,
            source_types=memory_types,
            since=since,
            until=until,
        )

    async def update_access(self, id: str) -> None:
        """Increment access_count and set last_accessed_at."""
        now = datetime.now(timezone.utc).isoformat()
        await self._session.execute(
            text(
                "UPDATE memories SET access_count = access_count + 1, "
                "last_accessed_at = :now WHERE id = :id"
            ),
            {"id": id, "now": now},
        )
        await self._session.flush()

    async def get_versions(self, memory_id: str) -> list[MemoryVersion]:
        """Get all versions for a memory, ordered by version number."""
        result = await self._session.execute(
            text(
                "SELECT * FROM memory_versions "
                "WHERE memory_id = :memory_id ORDER BY version"
            ),
            {"memory_id": memory_id},
        )
        return [MemoryVersion.from_row(row) for row in result.mappings().all()]

    async def check_duplicates(
        self, embedding: list[float], memory_type: str
    ) -> list[SearchResult]:
        """Find near-duplicate memories (cosine similarity > 0.92).

        Only works when vec0 is available. Returns empty list otherwise.
        """
        if not self._searcher._caps.has_vec:
            return []

        try:
            embedding_json = json.dumps(embedding)
            result = await self._session.execute(
                text(
                    "SELECT id, distance FROM memories_vec "
                    "WHERE embedding MATCH :embedding AND k = :k "
                    "ORDER BY distance"
                ),
                {"embedding": embedding_json, "k": 20},
            )
            rows = result.mappings().all()

            duplicates = []
            for row in rows:
                cosine_sim = 1.0 - float(row["distance"])
                if cosine_sim < 0.92:
                    continue

                # Fetch the memory to check type
                mem_result = await self._session.execute(
                    text(
                        "SELECT * FROM memories "
                        "WHERE id = :id AND type = :type AND deleted_at IS NULL"
                    ),
                    {"id": row["id"], "type": memory_type},
                )
                mem_row = mem_result.mappings().first()
                if mem_row is None:
                    continue

                meta = mem_row["metadata"] if mem_row["metadata"] else "{}"
                if isinstance(meta, str):
                    meta = json.loads(meta)

                duplicates.append(
                    SearchResult(
                        id=mem_row["id"],
                        content=mem_row["content"],
                        summary=mem_row["summary"],
                        vector_score=cosine_sim,
                        score=cosine_sim,
                        source_table="memories",
                        source_type=mem_row.get("source_type", ""),
                        metadata=meta,
                        sensitivity=mem_row["sensitivity"],
                    )
                )

            return duplicates
        except Exception:
            logger.warning("Duplicate check failed, vec0 may not be available")
            return []

    async def decay_importance(
        self, days_threshold: int = 30, decay_factor: float = 0.95
    ) -> int:
        """Decay importance for memories not accessed within the threshold."""
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days_threshold)).isoformat()
        now_iso = now.isoformat()

        result = await self._session.execute(
            text(
                "UPDATE memories SET "
                "importance = MAX(importance * :decay_factor, 0.05), "
                "updated_at = :now "
                "WHERE deleted_at IS NULL "
                "AND ("
                "  (last_accessed_at IS NOT NULL AND last_accessed_at < :cutoff)"
                "  OR (last_accessed_at IS NULL AND created_at < :cutoff)"
                ")"
            ),
            {"decay_factor": decay_factor, "now": now_iso, "cutoff": cutoff},
        )
        await self._session.flush()
        return result.rowcount

    async def boost_importance(
        self, memory_id: str, boost: float = 0.1, max_importance: float = 1.0
    ) -> Memory:
        """Boost a memory's importance score, capped at max_importance."""
        current = await self.get(memory_id)
        if current is None:
            raise ValueError(f"Memory {memory_id} not found")

        new_importance = min(current.importance + boost, max_importance)
        now = datetime.now(timezone.utc).isoformat()

        await self._session.execute(
            text(
                "UPDATE memories SET importance = :importance, updated_at = :now "
                "WHERE id = :id"
            ),
            {"id": memory_id, "importance": new_importance, "now": now},
        )
        await self._session.flush()
        return await self.get(memory_id)

    async def batch_update_type(
        self, memory_ids: list[str], new_type: str, changed_by: str, reason: str
    ) -> int:
        """Update the type for multiple memories, creating version entries."""
        updated = 0
        for memory_id in memory_ids:
            current = await self.get(memory_id)
            if current is None:
                continue

            now = datetime.now(timezone.utc).isoformat()

            await self._session.execute(
                text(
                    "UPDATE memories SET type = :new_type, updated_at = :now "
                    "WHERE id = :id AND deleted_at IS NULL"
                ),
                {"id": memory_id, "new_type": new_type, "now": now},
            )

            # Get next version number
            ver_result = await self._session.execute(
                text(
                    "SELECT MAX(version) as max_ver FROM memory_versions "
                    "WHERE memory_id = :memory_id"
                ),
                {"memory_id": memory_id},
            )
            ver_row = ver_result.mappings().first()
            next_version = (ver_row["max_ver"] or 0) + 1

            version_id = str(ULID())
            await self._session.execute(
                text(
                    "INSERT INTO memory_versions "
                    "(id, memory_id, version, previous_content, new_content, "
                    "previous_type, new_type, changed_by, change_reason, created_at) "
                    "VALUES (:id, :memory_id, :version, :previous_content, "
                    ":new_content, :previous_type, :new_type, :changed_by, "
                    ":change_reason, :created_at)"
                ),
                {
                    "id": version_id,
                    "memory_id": memory_id,
                    "version": next_version,
                    "previous_content": current.content,
                    "new_content": current.content,
                    "previous_type": current.type,
                    "new_type": new_type,
                    "changed_by": changed_by,
                    "change_reason": reason,
                    "created_at": now,
                },
            )
            updated += 1

        await self._session.flush()
        return updated
