"""Knowledge store capability detection and vec0 table management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_VEC_TABLES = [
    "content_chunks_vec",
    "memories_vec",
    "session_summaries_vec",
    "entities_vec",
    "optimization_observations_vec",
    "optimization_candidates_vec",
]


@dataclass
class KnowledgeStoreCapabilities:
    has_vec: bool = False
    has_embeddings: bool = False
    has_usearch: bool = False
    vec_dimension: int = 1024
    hnsw_tables: set[str] = field(default_factory=set)


async def ensure_vec_tables(
    engine: AsyncEngine, dimension: int = 1024
) -> KnowledgeStoreCapabilities:
    """Create vec0 virtual tables if sqlite-vec is available.

    Gracefully degrades if sqlite-vec is not installed — sets has_vec=False
    and returns capabilities reflecting FTS-only mode.
    """
    caps = KnowledgeStoreCapabilities(vec_dimension=dimension)

    async with engine.begin() as conn:
        # Try to load sqlite-vec extension
        try:
            await conn.exec_driver_sql("SELECT vec_version()")
            caps.has_vec = True
            logger.info("sqlite-vec extension loaded successfully")
        except Exception:
            logger.error(
                "CRITICAL: sqlite-vec not available — vector search disabled! "
                "Install with: uv add sqlite-vec"
            )
            return caps

        # Create vec0 virtual tables
        for table_name in _VEC_TABLES:
            try:
                await conn.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} "
                    f"USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[{dimension}])"
                )
                logger.info("Created vec0 table: %s (dimension=%d)", table_name, dimension)
            except Exception:
                logger.exception("Failed to create vec0 table: %s", table_name)
                caps.has_vec = False
                return caps

    return caps


async def check_capabilities(engine: AsyncEngine) -> KnowledgeStoreCapabilities:
    """Probe the database to determine what's available."""
    caps = KnowledgeStoreCapabilities()

    async with engine.begin() as conn:
        # Check sqlite-vec
        try:
            await conn.exec_driver_sql("SELECT vec_version()")
            caps.has_vec = True
        except Exception:
            caps.has_vec = False

        # Check vec dimension from existing tables
        if caps.has_vec:
            for table_name in _VEC_TABLES:
                try:
                    result = await conn.exec_driver_sql(
                        f"SELECT * FROM {table_name} LIMIT 0"
                    )
                    result.close()
                except Exception:
                    pass

        # Read configured dimension from settings
        try:
            result = await conn.exec_driver_sql(
                "SELECT value FROM settings WHERE key = 'embedding.output_dimension'"
            )
            row = result.fetchone()
            if row:
                caps.vec_dimension = int(row[0])
            else:
                caps.vec_dimension = 1024
        except Exception:
            caps.vec_dimension = 1024

    return caps
