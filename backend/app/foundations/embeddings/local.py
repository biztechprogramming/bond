"""Local embedding provider — uses sentence-transformers for on-device embeddings."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Map short names to HuggingFace model IDs
_MODEL_MAP = {
    "voyage-4-nano": "voyageai/voyage-4-nano",
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
}


@lru_cache(maxsize=1)
def _load_model(model_id: str):
    """Load and cache the sentence-transformers model."""
    from sentence_transformers import SentenceTransformer
    logger.info("Loading local embedding model: %s", model_id)
    model = SentenceTransformer(model_id)
    logger.info("Loaded local embedding model: %s (dim=%d)", model_id, model.get_sentence_embedding_dimension())
    return model


class LocalEmbeddingProvider:
    """Local embedding using sentence-transformers."""

    def __init__(self, model_name: str = "voyage-4-nano", dimension: int = 1024) -> None:
        self.model_name = model_name
        self.dimension = dimension
        # Resolve short name to full HuggingFace ID
        self._model_id = _MODEL_MAP.get(model_name, model_name)

    def _get_model(self):
        return _load_model(self._model_id)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using local sentence-transformers model."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query."""
        results = await self.embed([query])
        return results[0]
