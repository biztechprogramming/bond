"""Optional OpenViking adapter for semantic skill search.

Wraps the openviking Python client. Gracefully degrades if openviking
is not installed — all methods return empty results with a logged warning.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from openviking import Client as VikingClient  # type: ignore[import-untyped]

    _HAS_OPENVIKING = True
except ImportError:
    _HAS_OPENVIKING = False
    logger.info("openviking not installed — OpenVikingAdapter will be a no-op stub")


class OpenVikingAdapter:
    """Adapter that wraps OpenViking for semantic skill search.

    If openviking is not installed, all methods return empty/stub results.
    """

    def __init__(self, data_path: str = "backend/data/viking") -> None:
        self._data_path = Path(data_path)
        self._client: Any = None
        self._available = _HAS_OPENVIKING

        if self._available:
            try:
                self._data_path.mkdir(parents=True, exist_ok=True)
                self._client = VikingClient(str(self._data_path))
                logger.info("OpenViking initialized at %s", self._data_path)
            except Exception:
                logger.exception("Failed to initialize OpenViking client")
                self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def ingest_catalog(self, catalog_path: str) -> int:
        """Read skills.json and add each skill as a resource to OpenViking.

        Returns the number of skills ingested.
        """
        if not self._available:
            logger.warning("OpenViking not available — skipping catalog ingest")
            return 0

        catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
        count = 0
        for skill in catalog:
            try:
                self._client.add(
                    resource_id=skill["id"],
                    content=f"{skill['name']}: {skill.get('description', '')}",
                    metadata={
                        "source": skill.get("source", ""),
                        "path": skill.get("path", ""),
                        "l0_summary": skill.get("l0_summary", ""),
                    },
                )
                count += 1
            except Exception:
                logger.warning("Failed to ingest skill %s into OpenViking", skill["id"])
        logger.info("Ingested %d skills into OpenViking", count)
        return count

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Semantic search using OpenViking's find().

        Returns a list of dicts with keys: id, score, metadata.
        """
        if not self._available:
            return []

        try:
            results = self._client.find(query, limit=limit)
            return [
                {
                    "id": r.resource_id,
                    "score": r.score,
                    "metadata": r.metadata,
                }
                for r in results
            ]
        except Exception:
            logger.exception("OpenViking search failed")
            return []

    def get_abstract(self, skill_id: str) -> str:
        """Get L0 summary for a skill."""
        if not self._available:
            return ""
        try:
            resource = self._client.get(skill_id)
            return resource.metadata.get("l0_summary", "") if resource else ""
        except Exception:
            return ""

    def get_overview(self, skill_id: str) -> str:
        """Get L1 overview for a skill."""
        if not self._available:
            return ""
        try:
            resource = self._client.get(skill_id)
            return resource.content if resource else ""
        except Exception:
            return ""
