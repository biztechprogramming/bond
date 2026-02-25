"""Local embedding provider — stub until sentence-transformers is wired."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider:
    """Local embedding using sentence-transformers (stub implementation).

    Returns zero vectors of the correct dimension until sentence-transformers
    is installed and wired.
    """

    def __init__(self, model_name: str = "voyage-4-nano", dimension: int = 1024) -> None:
        self.model_name = model_name
        self.dimension = dimension
        logger.warning(
            "Local embedding provider is a stub — install sentence-transformers "
            "for real embeddings"
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return zero vectors for each input text."""
        return [[0.0] * self.dimension for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        """Return a zero vector for the query."""
        return [0.0] * self.dimension
