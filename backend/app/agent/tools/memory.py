"""Memory save/update tools — persist information to long-term memory."""

from __future__ import annotations

import logging
from typing import Any

from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
from backend.app.foundations.knowledge.search import HybridSearcher
from backend.app.features.memory.repository import MemoryRepository
from backend.app.features.memory.models import SaveMemoryInput

logger = logging.getLogger("bond.agent.tools.memory")


async def handle_memory_save(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Save a new memory."""
    content = arguments.get("content", "")
    memory_type = arguments.get("memory_type", "general")
    summary = arguments.get("summary", "")

    db = context.get("db")
    if db is None:
        return {"error": "No database session available."}

    try:
        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(db, caps)
        repo = MemoryRepository(db, searcher)

        input_data = SaveMemoryInput(
            type=memory_type,
            content=content,
            summary=summary or content[:100],
            source_type="agent",
            source_id="tool_call",
            sensitivity="normal",
            metadata={},
            importance=0.5,
        )
        memory = await repo.save(input_data)
        await db.commit()
        return {"status": "saved", "memory_id": memory.id}
    except Exception as e:
        logger.warning("Memory save failed: %s", e)
        return {"error": str(e)}


async def handle_memory_update(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Update an existing memory."""
    memory_id = arguments.get("memory_id", "")
    content = arguments.get("content", "")
    reason = arguments.get("reason", "Updated by agent")

    db = context.get("db")
    if db is None:
        return {"error": "No database session available."}

    try:
        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(db, caps)
        repo = MemoryRepository(db, searcher)

        memory = await repo.update(memory_id, content, "agent", reason)
        await db.commit()
        return {"status": "updated", "memory_id": memory.id}
    except Exception as e:
        logger.warning("Memory update failed: %s", e)
        return {"error": str(e)}


async def handle_memory_delete(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Soft-delete a memory."""
    memory_id = arguments.get("memory_id", "")

    db = context.get("db")
    if db is None:
        return {"error": "No database session available."}

    try:
        caps = KnowledgeStoreCapabilities(has_vec=False)
        searcher = HybridSearcher(db, caps)
        repo = MemoryRepository(db, searcher)

        await repo.soft_delete(memory_id, "agent")
        await db.commit()
        return {"status": "deleted", "memory_id": memory_id}
    except Exception as e:
        logger.warning("Memory delete failed: %s", e)
        return {"error": str(e)}
