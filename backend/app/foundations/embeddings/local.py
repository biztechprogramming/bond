"""Local embedding provider — uses fastembed or sentence-transformers for on-device embeddings."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Map short names to model IDs per library
_FASTEMBED_MODEL_MAP = {
    "voyage-4-nano": "BAAI/bge-small-en-v1.5",  # similar quality, works with fastembed
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
}

_ST_MODEL_MAP = {
    "voyage-4-nano": "voyageai/voyage-4-nano",
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
}


def _detect_backend() -> str:
    """Detect which embedding backend is available."""
    try:
        from fastembed import TextEmbedding  # noqa: F401
        return "fastembed"
    except ImportError:
        pass
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        return "sentence_transformers"
    except ImportError:
        pass
    return "none"


@lru_cache(maxsize=1)
def _load_fastembed_model(model_id: str):
    from fastembed import TextEmbedding
    logger.info("Loading fastembed model: %s", model_id)
    model = TextEmbedding(model_id)
    logger.info("Loaded fastembed model: %s", model_id)
    return model


@lru_cache(maxsize=1)
def _load_st_model(model_id: str):
    from sentence_transformers import SentenceTransformer
    logger.info("Loading sentence-transformers model: %s", model_id)
    model = SentenceTransformer(model_id)
    logger.info("Loaded sentence-transformers model: %s (dim=%d)", model_id, model.get_sentence_embedding_dimension())
    return model


class LocalEmbeddingProvider:
    """Local embedding using fastembed (preferred) or sentence-transformers."""

    def __init__(self, model_name: str = "voyage-4-nano", dimension: int = 1024) -> None:
        self.model_name = model_name
        self.requested_dimension = dimension
        self._backend = _detect_backend()

        if self._backend == "fastembed":
            self._model_id = _FASTEMBED_MODEL_MAP.get(model_name, model_name)
            logger.info("Local embeddings: fastembed backend (model=%s)", self._model_id)
        elif self._backend == "sentence_transformers":
            self._model_id = _ST_MODEL_MAP.get(model_name, model_name)
            logger.info("Local embeddings: sentence-transformers backend (model=%s)", self._model_id)
        else:
            logger.warning(
                "No local embedding backend available — install fastembed or sentence-transformers. "
                "Returning zero vectors."
            )
            self._model_id = model_name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using local model."""
        if self._backend == "none":
            return [[0.0] * self.requested_dimension for _ in texts]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._embed_sync, texts)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._backend == "fastembed":
            model = _load_fastembed_model(self._model_id)
            embeddings = list(model.embed(texts))
            return [e.tolist() for e in embeddings]
        elif self._backend == "sentence_transformers":
            model = _load_st_model(self._model_id)
            embeddings = model.encode(texts, normalize_embeddings=True)
            return [e.tolist() for e in embeddings]
        return [[0.0] * self.requested_dimension for _ in texts]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query."""
        results = await self.embed([query])
        return results[0]
