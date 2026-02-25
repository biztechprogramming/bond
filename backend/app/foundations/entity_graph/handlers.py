"""Mediator handlers for entity graph commands."""

from __future__ import annotations


from backend.app.mediator.base import CommandHandler
from backend.app.mediator.registry import handles

from .commands import ExtractEntities, GetEntityContext, LookupEntity, MergeEntities
from .context import EntityContextEnricher
from .extraction import EntityExtractor
from .models import Entity, EntityGraph
from .repository import EntityRepository


@handles(ExtractEntities)
class ExtractEntitiesHandler(CommandHandler[ExtractEntities, list[Entity]]):
    """Handle entity extraction from content."""

    async def handle(self, command: ExtractEntities) -> list[Entity]:
        repo = EntityRepository(self.db)
        extractor = EntityExtractor(llm_client=None, entity_repo=repo)
        return await extractor.extract(
            command.content, command.source_type, command.source_id
        )


@handles(LookupEntity)
class LookupEntityHandler(CommandHandler[LookupEntity, EntityGraph | None]):
    """Handle entity lookup with neighborhood traversal."""

    async def handle(self, command: LookupEntity) -> EntityGraph | None:
        repo = EntityRepository(self.db)
        entity = await repo.resolve(command.name, type=command.type)
        if entity is None:
            return None
        return await repo.get_neighborhood(entity.id, depth=command.depth)


@handles(MergeEntities)
class MergeEntitiesHandler(CommandHandler[MergeEntities, Entity]):
    """Handle entity merge."""

    async def handle(self, command: MergeEntities) -> Entity:
        repo = EntityRepository(self.db)
        return await repo.merge(command.keep_id, command.merge_id)


@handles(GetEntityContext)
class GetEntityContextHandler(CommandHandler[GetEntityContext, str]):
    """Handle entity context enrichment."""

    async def handle(self, command: GetEntityContext) -> str:
        repo = EntityRepository(self.db)
        enricher = EntityContextEnricher(repo)
        return await enricher.enrich(
            command.query,
            max_entities=command.max_entities,
            depth=command.depth,
        )
