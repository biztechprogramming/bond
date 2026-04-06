"""Embedding engine — provider selection and delegation.

Uses ONLY the settings configured in the Embedding tab (Settings UI).
No silent fallbacks — if settings are missing or misconfigured, we raise
so the error is visible rather than silently doing the wrong thing.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from .base import EmbeddingProvider
from .gemini import GeminiAPIProvider
from .local import LocalEmbeddingProvider
from .voyage import VoyageAPIProvider

logger = logging.getLogger(__name__)

# Models that only work locally — not available via the Voyage REST API.
# When auto mode has a Voyage key, these must use the local provider.
_LOCAL_ONLY_MODELS = {"voyage-4-nano"}


class EmbeddingConfigError(RuntimeError):
    """Raised when embedding settings are missing or invalid."""


class EmbeddingEngine:
    """Top-level embedding engine that delegates to the configured provider.

    Provider selection is driven entirely by ``embedding.execution_mode``:
      - ``local``  → LocalEmbeddingProvider
      - ``api``    → VoyageAPIProvider (requires Voyage key)
      - ``gemini`` → GeminiAPIProvider (requires Gemini key)
      - ``auto``   → Voyage if key present *and* model is API-compatible,
                      else Gemini if key present, else local

    Missing required keys raise ``EmbeddingConfigError`` — no silent fallbacks.
    """

    def __init__(self, settings: dict, db_engine: AsyncEngine) -> None:
        self._settings = settings
        self._db_engine = db_engine
        self._provider: EmbeddingProvider | None = None

        # All three are required from the Embedding tab
        model_name = settings.get("embedding.model")
        if not model_name:
            raise EmbeddingConfigError(
                "embedding.model is not configured. Set it in Settings → Embedding."
            )

        raw_dim = settings.get("embedding.output_dimension")
        if not raw_dim:
            raise EmbeddingConfigError(
                "embedding.output_dimension is not configured. Set it in Settings → Embedding."
            )
        dimension = int(raw_dim)

        execution_mode = settings.get("embedding.execution_mode")
        if not execution_mode:
            raise EmbeddingConfigError(
                "embedding.execution_mode is not configured. Set it in Settings → Embedding."
            )

        voyage_key = settings.get("embedding.api_key.voyage")
        gemini_key = settings.get("embedding.api_key.gemini")

        if execution_mode == "local":
            logger.info("Using local embedding provider (model=%s, dim=%d)", model_name, dimension)
            self._provider = LocalEmbeddingProvider(
                model_name=model_name,
                dimension=dimension,
            )

        elif execution_mode == "api":
            if model_name in _LOCAL_ONLY_MODELS:
                raise EmbeddingConfigError(
                    f"Model '{model_name}' is local-only and cannot be used with "
                    "execution_mode='api'. Switch to a Voyage API model "
                    "(e.g. voyage-4-lite) or set execution_mode to 'local'."
                )
            if not voyage_key:
                raise EmbeddingConfigError(
                    "execution_mode is 'api' but no Voyage API key is configured. "
                    "Add one in Settings → API Keys → Embedding Providers."
                )
            logger.info("Using Voyage API embedding provider (model=%s, dim=%d)", model_name, dimension)
            self._provider = VoyageAPIProvider(
                model_name=model_name,
                dimension=dimension,
                api_key=voyage_key,
            )

        elif execution_mode == "gemini":
            if not gemini_key:
                raise EmbeddingConfigError(
                    "execution_mode is 'gemini' but no Gemini API key is configured. "
                    "Add one in Settings → API Keys → Embedding Providers."
                )
            logger.info("Using Gemini API embedding provider (model=%s, dim=%d)", model_name, dimension)
            self._provider = GeminiAPIProvider(
                model_name=model_name,
                dimension=dimension,
                api_key=gemini_key,
            )

        elif execution_mode == "auto":
            if voyage_key and model_name not in _LOCAL_ONLY_MODELS:
                logger.info("Auto: using Voyage API embedding provider (model=%s, dim=%d)", model_name, dimension)
                self._provider = VoyageAPIProvider(
                    model_name=model_name,
                    dimension=dimension,
                    api_key=voyage_key,
                )
            elif gemini_key:
                logger.info("Auto: using Gemini API embedding provider (model=%s, dim=%d)", model_name, dimension)
                self._provider = GeminiAPIProvider(
                    model_name=model_name,
                    dimension=dimension,
                    api_key=gemini_key,
                )
            else:
                if model_name in _LOCAL_ONLY_MODELS and voyage_key:
                    logger.info(
                        "Auto: model %s is local-only — using local provider "
                        "despite Voyage key being present (dim=%d)",
                        model_name, dimension,
                    )
                else:
                    logger.info("Auto: no API keys configured, using local embedding provider (model=%s, dim=%d)", model_name, dimension)
                self._provider = LocalEmbeddingProvider(
                    model_name=model_name,
                    dimension=dimension,
                )

        else:
            raise EmbeddingConfigError(
                f"Unknown execution_mode '{execution_mode}'. "
                "Must be one of: local, api, gemini, auto."
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
