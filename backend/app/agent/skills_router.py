"""Semantic skill router — matches user queries to relevant skills via embeddings.

Implements the 4-step routing pipeline from §3.3 of Design Doc 047:
1. Embed the query
2. Vector similarity against all skill embeddings → top-K candidates
3. Filter by min_similarity threshold
4. Return matched skills with L0 summaries

Phase 3: Boost results using adaptive skill_scores.
Phase 4: Optional OpenViking adapter for semantic search.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from backend.app.agent.tools.skills_db import (
    cosine_similarity,
    get_all_embeddings,
    get_skill_by_id,
    get_skill_score,
    search_skills,
)
from backend.app.foundations.embeddings.engine import EmbeddingEngine

if TYPE_CHECKING:
    from backend.app.agent.skills_openviking import OpenVikingAdapter

logger = logging.getLogger(__name__)

TOP_K = 8
MIN_SIMILARITY = 0.35
MAX_SKILLS_DEFAULT = 3

# Score boosting weights (Phase 3)
SIMILARITY_WEIGHT = 0.7
SCORE_WEIGHT = 0.3


class SkillRouter:
    """Semantic router for matching user queries to skills."""

    def __init__(
        self,
        embedding_engine: EmbeddingEngine | None = None,
        openviking_adapter: OpenVikingAdapter | None = None,
    ) -> None:
        self._engine = embedding_engine
        self._viking = openviking_adapter

    async def route(self, query: str, session_id: str) -> list[dict[str, Any]]:
        """Route a query to matching skills using the 4-step pipeline.

        Falls back to LIKE-matching if no embedding engine or no embeddings available.
        """
        # Phase 4: Try OpenViking first if available
        if self._viking and self._viking.available:
            try:
                results = await self._route_via_openviking(query)
                if results:
                    return results
            except Exception:
                logger.exception("OpenViking search failed — falling through to homegrown")

        if not self._engine:
            logger.debug("No embedding engine — falling back to LIKE matching")
            return await self._like_fallback(query)

        # Step 1: Embed the query
        try:
            query_vec = await self._engine.embed_query(query)
        except Exception:
            logger.exception("Failed to embed query — falling back to LIKE matching")
            return await self._like_fallback(query)

        # Check for zero vector (no API key configured)
        if all(v == 0.0 for v in query_vec):
            logger.debug("Zero embedding vector — falling back to LIKE matching")
            return await self._like_fallback(query)

        # Step 2: Vector similarity against all skill embeddings
        all_embeddings = await get_all_embeddings()
        if not all_embeddings:
            logger.debug("No skill embeddings found — falling back to LIKE matching")
            return await self._like_fallback(query)

        scored: list[tuple[str, float]] = []
        for skill_id, skill_vec in all_embeddings:
            sim = cosine_similarity(query_vec, skill_vec)
            scored.append((skill_id, sim))

        # Sort by similarity descending, take top-K
        scored.sort(key=lambda x: x[1], reverse=True)
        top_k = scored[:TOP_K]

        # Step 3: Filter by minimum similarity threshold
        candidates = [(sid, sim) for sid, sim in top_k if sim >= MIN_SIMILARITY]

        if not candidates:
            logger.debug("No skills above similarity threshold %.2f", MIN_SIMILARITY)
            return []

        # Phase 3: Boost with adaptive skill_scores
        boosted: list[tuple[str, float, float]] = []  # (skill_id, final_score, raw_sim)
        for skill_id, similarity in candidates:
            skill_score = await get_skill_score(skill_id)
            if skill_score is None:
                skill_score = 0.5  # cold start default
            final = SIMILARITY_WEIGHT * similarity + SCORE_WEIGHT * skill_score
            boosted.append((skill_id, final, similarity))
            if abs(final - similarity) > 0.01:
                logger.debug(
                    "Score boost for %s: sim=%.3f, skill_score=%.3f → final=%.3f",
                    skill_id, similarity, skill_score, final,
                )

        # Sort by final_score descending
        boosted.sort(key=lambda x: x[1], reverse=True)

        # Step 4: Return matched skills with metadata
        results = []
        for skill_id, final_score, raw_sim in boosted:
            skill = await get_skill_by_id(skill_id)
            if skill:
                results.append({
                    "id": skill["id"],
                    "name": skill["name"],
                    "source": skill["source"],
                    "description": skill.get("l0_summary") or skill.get("description", ""),
                    "l1_overview": skill.get("l1_overview", ""),
                    "similarity": round(raw_sim, 4),
                    "score": round(final_score, 4),
                })

        logger.info("Routed query to %d skills (top: %s, sim=%.3f, score=%.3f)",
                     len(results),
                     results[0]["name"] if results else "none",
                     results[0]["similarity"] if results else 0,
                     results[0]["score"] if results else 0)
        return results

    async def _route_via_openviking(self, query: str) -> list[dict[str, Any]]:
        """Use OpenViking adapter for semantic search, then enrich from skill_index."""
        assert self._viking is not None
        viking_results = self._viking.search(query, limit=TOP_K)
        if not viking_results:
            return []

        results = []
        for vr in viking_results:
            skill_id = vr["id"]
            skill = await get_skill_by_id(skill_id)
            if not skill:
                continue
            # Boost with adaptive score
            skill_score = await get_skill_score(skill_id) or 0.5
            raw_sim = vr.get("score", 0.0)
            final = SIMILARITY_WEIGHT * raw_sim + SCORE_WEIGHT * skill_score
            results.append({
                "id": skill["id"],
                "name": skill["name"],
                "source": skill["source"],
                "description": skill.get("l0_summary") or skill.get("description", ""),
                "l1_overview": skill.get("l1_overview", ""),
                "similarity": round(raw_sim, 4),
                "score": round(final, 4),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        logger.info("OpenViking routed query to %d skills", len(results))
        return results

    async def get_relevant_skills_prompt(
        self, query: str, session_id: str, max_skills: int = MAX_SKILLS_DEFAULT
    ) -> str:
        """Return an <available_skills> XML block with matched skills.

        Falls back to top-3-by-score if no embeddings are available.
        """
        results = await self.route(query, session_id)

        if not results:
            # Fallback: top skills by score via LIKE on empty query
            fallback = await search_skills("", limit=max_skills)
            results = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "source": s["source"],
                    "description": s.get("l0_summary") or s.get("description", ""),
                    "l1_overview": "",
                }
                for s in fallback
            ]

        # Limit to max_skills
        results = results[:max_skills]

        if not results:
            return ""

        lines = ["<available_skills>"]
        for skill in results:
            lines.append(f'  <skill id="{skill["id"]}" source="{skill["source"]}">')
            lines.append(f"    <name>{skill['name']}</name>")
            lines.append(f"    <description>{skill['description']}</description>")
            if skill.get("l1_overview"):
                lines.append(f"    <overview>{skill['l1_overview']}</overview>")
            lines.append("  </skill>")
        lines.append("</available_skills>")

        return "\n".join(lines)

    async def _like_fallback(self, query: str) -> list[dict[str, Any]]:
        """Fall back to LIKE-based text search from Phase 1."""
        results = await search_skills(query, limit=TOP_K)
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "source": s["source"],
                "description": s.get("l0_summary") or s.get("description", ""),
                "l1_overview": s.get("l1_overview", ""),
                "similarity": 0.0,
                "score": s.get("score", 0.5),
            }
            for s in results
        ]
