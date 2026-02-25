"""Mediator handlers for memory commands."""

from __future__ import annotations

from backend.app.foundations.knowledge.capabilities import KnowledgeStoreCapabilities
from backend.app.foundations.knowledge.models import SearchResult
from backend.app.foundations.knowledge.search import HybridSearcher
from backend.app.mediator.base import CommandHandler
from backend.app.mediator.registry import handles

from .commands import DeleteMemory, SaveMemory, SearchMemory, UpdateMemory
from .models import Memory, SaveMemoryInput
from .repository import MemoryRepository


def _make_repo(db) -> MemoryRepository:
    """Create a MemoryRepository from a db session."""
    caps = KnowledgeStoreCapabilities(has_vec=False)
    searcher = HybridSearcher(db, caps)
    return MemoryRepository(db, searcher)


@handles(SaveMemory)
class SaveMemoryHandler(CommandHandler[SaveMemory, Memory]):
    """Handle saving a new memory."""

    async def handle(self, command: SaveMemory) -> Memory:
        repo = _make_repo(self.db)
        return await repo.save(
            SaveMemoryInput(
                type=command.type,
                content=command.content,
                summary=command.summary,
                source_type=command.source_type,
                source_id=command.source_id,
                sensitivity=command.sensitivity,
                metadata=command.metadata,
                importance=command.importance,
            )
        )


@handles(SearchMemory)
class SearchMemoryHandler(CommandHandler[SearchMemory, list[SearchResult]]):
    """Handle searching memories."""

    async def handle(self, command: SearchMemory) -> list[SearchResult]:
        repo = _make_repo(self.db)
        return await repo.search(
            command.query,
            limit=command.limit,
            memory_types=command.memory_types,
        )


@handles(UpdateMemory)
class UpdateMemoryHandler(CommandHandler[UpdateMemory, Memory]):
    """Handle updating a memory."""

    async def handle(self, command: UpdateMemory) -> Memory:
        repo = _make_repo(self.db)
        return await repo.update(
            command.id, command.content, command.changed_by, command.reason
        )


@handles(DeleteMemory)
class DeleteMemoryHandler(CommandHandler[DeleteMemory, bool]):
    """Handle soft-deleting a memory."""

    async def handle(self, command: DeleteMemory) -> bool:
        repo = _make_repo(self.db)
        return await repo.soft_delete(
            command.id, command.changed_by, command.reason
        )
