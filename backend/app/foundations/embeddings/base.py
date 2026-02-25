"""Embedding provider protocol — all providers implement this interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers."""

    model_name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per text."""
        ...

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query (may add a query-specific prefix)."""
        ...
