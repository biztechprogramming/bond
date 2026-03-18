"""Voyage AI embedding provider — calls the Voyage REST API."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Models that support output_dimension truncation
_SUPPORTS_OUTPUT_DIM = {"voyage-3-large", "voyage-code-3"}


class VoyageAPIProvider:
    """Voyage AI embedding provider.

    Calls the Voyage REST API when an API key is available.
    Falls back to zero vectors when no key is configured.
    """

    def __init__(
        self,
        model_name: str = "voyage-4-large",
        dimension: int = 1024,
        api_key: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._api_key = api_key
        if not api_key:
            logger.warning(
                "Voyage API key not set — provider will return zero vectors"
            )

    def _build_payload(self, texts: list[str], input_type: str) -> dict:
        payload = {
            "model": self.model_name,
            "input": texts,
            "input_type": input_type,
        }
        # Only include output_dimension for models that support it
        if self.model_name in _SUPPORTS_OUTPUT_DIM:
            payload["output_dimension"] = self.dimension
        return payload

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via Voyage API, or return zero vectors if no key."""
        if not self._api_key:
            return [[0.0] * self.dimension for _ in texts]

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._build_payload(texts, "document"),
            )
            if resp.status_code != 200:
                logger.error("Voyage API error %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]

    async def embed_query(self, query: str) -> list[float]:
        """Embed a single query with input_type='query'."""
        if not self._api_key:
            return [0.0] * self.dimension

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._build_payload([query], "query"),
            )
            if resp.status_code != 200:
                logger.error("Voyage API error %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
