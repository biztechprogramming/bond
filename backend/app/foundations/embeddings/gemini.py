"""Gemini embedding provider — stub implementation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class GeminiAPIProvider:
    """Gemini embedding provider (stub).

    Returns zero vectors — real implementation pending.
    """

    def __init__(
        self,
        model_name: str = "gemini-embedding-001",
        dimension: int = 768,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        logger.warning("Gemini embedding provider is a stub — returns zero vectors")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return zero vectors for each input text."""
        return [[0.0] * self.dimension for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        """Return a zero vector for the query."""
        return [0.0] * self.dimension
