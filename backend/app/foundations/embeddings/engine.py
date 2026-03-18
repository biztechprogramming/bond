"""Embedding engine — provider selection and delegation."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from .base import EmbeddingProvider
from .gemini import GeminiAPIProvider
from .local import LocalEmbeddingProvider
from .voyage import VoyageAPIProvider

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    """Top-level embedding engine that delegates to the active provider.

    Provider selection logic:
    - If a Voyage API key is found in settings → VoyageAPIProvider
    - Else → LocalEmbeddingProvider (stub)
    """

    def __init__(self, settings: dict, db_engine: AsyncEngine) -> None:
        self._settings = settings
        self._db_engine = db_engine
        self._provider: EmbeddingProvider | None = None

        model_name = settings.get("embedding.model", "voyage-4-large")
        dimension = int(settings.get("embedding.output_dimension", "1024"))
        voyage_key = settings.get("embedding.api_key.voyage")
        provider_name = settings.get("embedding.provider", "auto")

        if provider_name == "local":
            logger.info("Using local embedding provider (model=%s)", model_name)
            self._provider = LocalEmbeddingProvider(
                model_name=model_name,
                dimension=dimension,
            )
        elif provider_name == "gemini":
            gemini_key = settings.get("embedding.api_key.gemini", "")
            logger.info("Using Gemini API embedding provider (model=%s)", model_name)
            self._provider = GeminiAPIProvider(
                model_name=model_name,
                dimension=dimension,
                api_key=gemini_key or None,
            )
        elif voyage_key:
            logger.info("Using Voyage API embedding provider (model=%s)", model_name)
            self._provider = VoyageAPIProvider(
                model_name=model_name,
                dimension=dimension,
                api_key=voyage_key,
            )
        else:
            logger.info("Using local embedding provider (model=%s)", model_name)
            self._provider = LocalEmbeddingProvider(
                model_name=model_name,
                dimension=dimension,
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using the active provider."""
        return await self._provider.embed(texts)

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query using the active provider."""
        return await self._provider.embed_query(query)

    def get_provider(self) -> EmbeddingProvider:
        """Return the active provider instance."""
        return self._provider
