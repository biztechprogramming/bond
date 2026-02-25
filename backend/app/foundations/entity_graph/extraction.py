"""Entity extraction pipeline — LLM-based entity/relationship extraction."""

from __future__ import annotations

import logging
from typing import Any

from .models import CreateEntityInput, CreateRelationshipInput, Entity
from .repository import EntityRepository

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract entities and relationships from the following text.

Return JSON with this exact structure:
{
  "entities": [
    {"name": "...", "type": "person|project|task|decision|meeting|document|event", "metadata": {...}}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "type": "relationship type", "context": "brief explanation"}
  ]
}

Rules:
- Only extract entities explicitly mentioned or strongly implied
- Use the most specific entity type that fits
- Include metadata fields you can confidently extract (email, role, status, etc.)
- For relationships, include context explaining why the relationship exists
- If no entities are found, return {"entities": [], "relationships": []}

Text:
{content}"""


class EntityExtractor:
    """Extract entities and relationships from text using an LLM."""

    def __init__(self, llm_client: Any, entity_repo: EntityRepository) -> None:
        self._llm = llm_client
        self._repo = entity_repo

    async def extract(
        self, content: str, source_type: str, source_id: str
    ) -> list[Entity]:
        """Extract entities from content, resolve/create them, and record mentions.

        The LLM call is currently a stub — returns empty result.
        Override _call_llm or mock it in tests for real extraction.
        """
        raw = await self._call_llm(content)
        if not raw:
            return []

        return await self._process_extraction(raw, source_type, source_id)

    async def _call_llm(self, content: str) -> dict | None:
        """Call the LLM to extract entities. Currently a stub."""
        logger.info(
            "Entity extraction LLM is a stub — configure LLM for real extraction"
        )
        return None

    async def _process_extraction(
        self, raw: dict, source_type: str, source_id: str
    ) -> list[Entity]:
        """Process raw LLM extraction result into entities and relationships."""
        entities_data = raw.get("entities", [])
        relationships_data = raw.get("relationships", [])

        # Map entity names to resolved/created Entity objects
        name_to_entity: dict[str, Entity] = {}
        created_entities: list[Entity] = []

        for ent_data in entities_data:
            name = ent_data.get("name", "").strip()
            ent_type = ent_data.get("type", "").strip()
            metadata = ent_data.get("metadata", {})

            if not name or not ent_type:
                continue

            # Resolve or create
            existing = await self._repo.resolve(
                name, type=ent_type, metadata=metadata
            )
            if existing:
                entity = existing
            else:
                entity = await self._repo.create(
                    CreateEntityInput(type=ent_type, name=name, metadata=metadata)
                )

            name_to_entity[name] = entity
            created_entities.append(entity)

            # Record mention
            await self._repo.add_mention(entity.id, source_type, source_id)

        # Create relationships
        for rel_data in relationships_data:
            source_name = rel_data.get("source", "").strip()
            target_name = rel_data.get("target", "").strip()
            rel_type = rel_data.get("type", "related_to").strip()
            context = rel_data.get("context")
            weight = float(rel_data.get("weight", 0.7))

            source_entity = name_to_entity.get(source_name)
            target_entity = name_to_entity.get(target_name)

            if source_entity and target_entity:
                await self._repo.add_relationship(
                    CreateRelationshipInput(
                        source_id=source_entity.id,
                        target_id=target_entity.id,
                        type=rel_type,
                        weight=weight,
                        context=context,
                    )
                )

        return created_entities
