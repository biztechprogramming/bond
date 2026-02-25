"""Hybrid search — FTS5 + vec0 cosine similarity with RRF merge."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .capabilities import KnowledgeStoreCapabilities
from .models import SearchResult

logger = logging.getLogger(__name__)

# Reciprocal Rank Fusion constant
RRF_K = 60

# Recency half-life in days — results older than this get diminishing boost
RECENCY_HALF_LIFE_DAYS = 30


class HybridSearcher:
    """Combines FTS5 BM25 ranking with vec0 cosine similarity via RRF."""

    def __init__(
        self,
        session: AsyncSession,
        capabilities: KnowledgeStoreCapabilities,
    ) -> None:
        self._session = session
        self._caps = capabilities

    async def search(
        self,
        table_name: str,
        query_text: str,
        query_embedding: list[float] | None = None,
        *,
        limit: int = 10,
        source_types: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[SearchResult]:
        """Execute hybrid search with FTS + optional vector + RRF merge.

        Args:
            table_name: Base table (e.g. 'content_chunks', 'memories').
            query_text: The text query for FTS5 MATCH.
            query_embedding: Optional embedding vector for vec0 search.
            limit: Max results to return.
            source_types: Filter by source_type column (if table has one).
            since: Filter by created_at >= since.
            until: Filter by created_at <= until.
        """
        fts_table = f"{table_name}_fts"
        vec_table = f"{table_name}_vec"

        # Determine the text column name based on table
        text_col = "content" if table_name == "memories" else "text"

        # Phase 1: FTS5 search
        fts_results = await self._fts_search(
            table_name, fts_table, text_col, query_text,
            source_types=source_types, since=since, until=until,
            limit=limit * 3,  # over-fetch for RRF merge
        )

        # Phase 2: Vec search (if available)
        vec_results: dict[str, float] = {}
        if self._caps.has_vec and query_embedding is not None:
            candidate_ids = [r.id for r in fts_results] if fts_results else None
            vec_results = await self._vec_search(
                vec_table, query_embedding,
                candidate_ids=candidate_ids,
                limit=limit * 3,
            )

        # Phase 3: RRF merge
        merged = self._rrf_merge(fts_results, vec_results)

        # Apply recency boost
        for result in merged:
            result.recency_boost = _recency_boost(result.metadata.get("created_at"))
            result.score += result.recency_boost

        # Sort by final score descending and limit
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:limit]

    async def _fts_search(
        self,
        table_name: str,
        fts_table: str,
        text_col: str,
        query_text: str,
        *,
        source_types: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 30,
    ) -> list[SearchResult]:
        """Phase 1: FTS5 BM25-ranked search with optional filters."""
        params: dict = {"query": query_text, "limit": limit}
        where_clauses: list[str] = []

        if source_types:
            placeholders = ", ".join(f":st{i}" for i in range(len(source_types)))
            where_clauses.append(f"t.source_type IN ({placeholders})")
            for i, st in enumerate(source_types):
                params[f"st{i}"] = st

        if since:
            where_clauses.append("t.created_at >= :since")
            params["since"] = since

        if until:
            where_clauses.append("t.created_at <= :until")
            params["until"] = until

        extra_where = ""
        if where_clauses:
            extra_where = "AND " + " AND ".join(where_clauses)

        sql = (
            f"SELECT t.*, fts.rank AS fts_rank "
            f"FROM {fts_table} fts "
            f"JOIN {table_name} t ON t.id = fts.id "
            f"WHERE {fts_table} MATCH :query {extra_where} "
            f"ORDER BY fts.rank "
            f"LIMIT :limit"
        )

        result = await self._session.execute(text(sql), params)
        rows = result.mappings().all()

        results = []
        for i, row in enumerate(rows):
            meta = row.get("metadata", "{}")
            if isinstance(meta, str):
                meta = json.loads(meta) if meta else {}
            # Store created_at in metadata for recency boost
            meta["created_at"] = row.get("created_at")
            results.append(
                SearchResult(
                    id=row["id"],
                    content=row.get(text_col, ""),
                    summary=row.get("summary"),
                    fts_score=abs(row["fts_rank"]) if row["fts_rank"] else 0.0,
                    source_table=table_name,
                    source_type=row.get("source_type", ""),
                    metadata=meta,
                    sensitivity=row.get("sensitivity", "normal"),
                )
            )

        return results

    async def _vec_search(
        self,
        vec_table: str,
        query_embedding: list[float],
        *,
        candidate_ids: list[str] | None = None,
        limit: int = 30,
    ) -> dict[str, float]:
        """Phase 2: vec0 cosine similarity search.

        Uses two-phase pre-filtering when candidate_ids are provided:
        search only among FTS candidate IDs for better relevance.
        """
        try:
            # vec0 MATCH expects a JSON array for the embedding
            embedding_json = json.dumps(query_embedding)

            if candidate_ids:
                # Two-phase: search among FTS candidates only
                # We query vec0 with a larger k and filter in Python
                sql = (
                    f"SELECT id, distance FROM {vec_table} "
                    f"WHERE embedding MATCH :embedding AND k = :k "
                    f"ORDER BY distance"
                )
                params = {"embedding": embedding_json, "k": limit}
                result = await self._session.execute(text(sql), params)
                rows = result.mappings().all()

                candidate_set = set(candidate_ids)
                return {
                    row["id"]: 1.0 - float(row["distance"])
                    for row in rows
                    if row["id"] in candidate_set
                }
            else:
                sql = (
                    f"SELECT id, distance FROM {vec_table} "
                    f"WHERE embedding MATCH :embedding AND k = :k "
                    f"ORDER BY distance"
                )
                params = {"embedding": embedding_json, "k": limit}
                result = await self._session.execute(text(sql), params)
                rows = result.mappings().all()

                return {
                    row["id"]: 1.0 - float(row["distance"])
                    for row in rows
                }
        except Exception:
            logger.warning("Vec search failed for %s, falling back to FTS-only", vec_table)
            return {}

    def _rrf_merge(
        self,
        fts_results: list[SearchResult],
        vec_scores: dict[str, float],
    ) -> list[SearchResult]:
        """Phase 3: Reciprocal Rank Fusion merge of FTS and vec results."""
        # Build score map: id -> SearchResult
        result_map: dict[str, SearchResult] = {}

        # FTS RRF scores
        for rank, result in enumerate(fts_results):
            rrf_score = 1.0 / (RRF_K + rank + 1)
            result.score = rrf_score
            result_map[result.id] = result

        # Add vec RRF scores
        if vec_scores:
            # Sort vec by score descending for ranking
            sorted_vec = sorted(vec_scores.items(), key=lambda x: x[1], reverse=True)
            for rank, (doc_id, cosine_sim) in enumerate(sorted_vec):
                rrf_score = 1.0 / (RRF_K + rank + 1)
                if doc_id in result_map:
                    result_map[doc_id].score += rrf_score
                    result_map[doc_id].vector_score = cosine_sim

        return list(result_map.values())


def _recency_boost(created_at: str | None) -> float:
    """Compute a small recency boost based on age.

    Uses exponential decay with a 30-day half-life.
    Returns a value between 0.0 and 0.01 (small enough not to dominate RRF).
    """
    if not created_at:
        return 0.0

    try:
        if isinstance(created_at, str):
            # Handle SQLite timestamp format
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            return 0.0

        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - dt).total_seconds() / 86400)
        decay = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)
        return 0.01 * decay
    except (ValueError, TypeError):
        return 0.0
