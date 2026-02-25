"""Search memory tool — calls HybridSearcher to find relevant memories."""

from __future__ import annotations

import logging
from typing import Any

from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
from backend.app.foundations.knowledge.search import HybridSearcher

logger = logging.getLogger("bond.agent.tools.search")


async def handle_search_memory(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search memories using hybrid FTS + vector search."""
    query = arguments.get("query", "")
    limit = arguments.get("limit", 5)

    db = context.get("db")
    if db is None:
        return {"error": "No database session available."}

    try:
        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(db, caps)
        results = await searcher.search(
            table_name="memories",
            query_text=query,
            limit=limit,
        )
        return {
            "results": [
                {
                    "id": r.id,
                    "content": r.content,
                    "summary": r.summary,
                    "score": r.score,
                }
                for r in results
            ],
            "count": len(results),
        }
    except Exception as e:
        logger.warning("Memory search failed: %s", e)
        return {"results": [], "count": 0, "error": str(e)}
