"""Gemini embedding provider — calls the Google Generative AI embedding API."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
GEMINI_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"


class GeminiAPIProvider:
    """Gemini embedding provider using the Generative AI REST API.

    Uses text-embedding-004 by default (768 dimensions, free tier).
    """

    def __init__(
        self,
        model_name: str = "text-embedding-004",
        dimension: int = 768,
        api_key: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not self._api_key:
            logger.warning("No Gemini/Google API key — provider will return zero vectors")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via Gemini batch embedding API."""
        if not self._api_key:
            return [[0.0] * self.dimension for _ in texts]

        url = GEMINI_BATCH_URL.format(model=self.model_name)
        requests_body = [
            {
                "model": f"models/{self.model_name}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": self.dimension,
            }
            for t in texts
        ]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                params={"key": self._api_key},
                json={"requests": requests_body},
            )
            if resp.status_code != 200:
                logger.error("Gemini embedding API error %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"]["values"] for item in data["embeddings"]]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query via Gemini embedding API."""
        if not self._api_key:
            return [0.0] * self.dimension

        url = GEMINI_EMBED_URL.format(model=self.model_name)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                params={"key": self._api_key},
                json={
                    "model": f"models/{self.model_name}",
                    "content": {"parts": [{"text": query}]},
                    "outputDimensionality": self.dimension,
                },
            )
            if resp.status_code != 200:
                logger.error("Gemini embedding API error %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            return data["embedding"]["values"]
