"""Entity context enrichment — graph to natural language for LLM prompts."""

from __future__ import annotations

import re

from .repository import EntityRepository


class EntityContextEnricher:
    """Enrich a query with entity graph context."""

    def __init__(self, entity_repo: EntityRepository) -> None:
        self._repo = entity_repo

    async def enrich(
        self, query: str, max_entities: int = 5, depth: int = 1
    ) -> str:
        """Extract entity names from query, resolve, get neighborhoods, return context."""
        names = self._extract_names(query)
        if not names:
            return ""

        context_parts: list[str] = []
        seen_entities: set[str] = set()

        for name in names[:max_entities]:
            entity = await self._repo.resolve(name)
            if entity is None or entity.id in seen_entities:
                continue
            seen_entities.add(entity.id)

            graph = await self._repo.get_neighborhood(entity.id, depth=depth)
            ctx = graph.to_context_string()
            if ctx:
                context_parts.append(ctx)

        if not context_parts:
            return ""

        return "Known context: " + " ".join(context_parts)

    def _extract_names(self, query: str) -> list[str]:
        """Extract potential entity names from a query.

        Simple heuristic: capitalized words and quoted strings.
        """
        names: list[str] = []

        # Quoted strings
        quoted = re.findall(r'"([^"]+)"', query)
        names.extend(quoted)

        # Capitalized words (not at sentence start, not common words)
        skip_words = {
            "I", "The", "A", "An", "What", "Who", "When", "Where", "Why",
            "How", "Is", "Are", "Was", "Were", "Do", "Does", "Did", "Can",
            "Could", "Should", "Would", "Will", "Has", "Have", "Had",
            "Tell", "Show", "Get", "Find", "Search",
        }
        words = query.split()
        for word in words:
            clean = word.strip(".,!?;:'\"")
            if clean and clean[0].isupper() and clean not in skip_words:
                if clean not in names:
                    names.append(clean)

        return names
