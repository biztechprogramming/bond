"""Skills tool — search, read, and list skills from the federated catalog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level router instance, lazily initialized
_router = None


def _get_router():
    """Get or create the SkillRouter singleton with local embedding engine."""
    global _router
    if _router is None:
        from backend.app.agent.skills_router import SkillRouter
        try:
            from backend.app.foundations.embeddings.engine import EmbeddingEngine
            engine = EmbeddingEngine(
                settings={"embedding.provider": "local", "embedding.model": "voyage-4-nano"},
                db_engine=None,
            )
            _router = SkillRouter(embedding_engine=engine)
            logger.info("Skills router initialized with local embedding engine")
        except Exception:
            logger.warning("Failed to init embedding engine — skills router will use LIKE fallback", exc_info=True)
            _router = SkillRouter()
    return _router


def set_router_engine(embedding_engine, openviking_adapter=None) -> None:
    """Set the embedding engine on the router (called during app startup if available)."""
    global _router
    from backend.app.agent.skills_router import SkillRouter
    _router = SkillRouter(embedding_engine=embedding_engine, openviking_adapter=openviking_adapter)


async def handle_skills(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    from .skills_db import search_skills, list_all_skills, get_skill_by_id

    action = arguments.get("action", "list")

    if action == "search":
        query = arguments.get("skill_name") or arguments.get("query", "")
        if not query:
            return {"error": "search requires a 'skill_name' or 'query' parameter"}

        # Use semantic router when available, fall back to LIKE matching
        router = _get_router()
        session_id = context.get("session_id", "")
        results = await router.route(query, session_id)

        if not results:
            # Explicit LIKE fallback if router returned nothing
            like_results = await search_skills(query)
            return {
                "skills": [
                    {"id": s["id"], "name": s["name"], "source": s["source"],
                     "description": s["l0_summary"] or s["description"], "score": s["score"]}
                    for s in like_results
                ],
                "count": len(like_results),
            }

        return {
            "skills": [
                {"id": s["id"], "name": s["name"], "source": s["source"],
                 "description": s["description"],
                 "similarity": s.get("similarity", 0), "score": s.get("score", 0.5)}
                for s in results
            ],
            "count": len(results),
        }

    elif action == "route":
        query = arguments.get("query") or arguments.get("skill_name", "")
        if not query:
            return {"error": "route requires a 'query' parameter"}
        max_skills = int(arguments.get("max_skills", 3))
        router = _get_router()
        session_id = context.get("session_id", "")
        prompt = await router.get_relevant_skills_prompt(query, session_id, max_skills=max_skills)
        results = await router.route(query, session_id)
        return {
            "prompt": prompt,
            "skills": [
                {"id": s["id"], "name": s["name"], "source": s["source"],
                 "description": s["description"], "similarity": s.get("similarity", 0)}
                for s in results[:max_skills]
            ],
            "count": min(len(results), max_skills),
        }

    elif action == "read":
        skill_name = arguments.get("skill_name", "")
        if not skill_name:
            return {"error": "read requires a 'skill_name' parameter"}
        # Try exact ID match first, then search
        skill = await get_skill_by_id(skill_name)
        if not skill:
            results = await search_skills(skill_name, limit=1)
            skill = results[0] if results else None
        if not skill:
            return {"error": f"Skill '{skill_name}' not found"}
        # Read the SKILL.md content
        skill_path = Path(skill["path"])
        if not skill_path.is_absolute():
            # Resolve relative to project root
            skill_path = Path.cwd() / skill_path
        if not skill_path.exists():
            return {"error": f"Skill file not found: {skill['path']}"}
        try:
            content = skill_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"error": f"Failed to read skill: {e}"}
        return {
            "id": skill["id"],
            "name": skill["name"],
            "source": skill["source"],
            "content": content,
            "_skill_activated": {
                "id": skill["id"],
                "name": skill["name"],
                "source": skill["source"],
                "path": skill["path"],
            },
        }

    elif action == "list":
        all_skills = await list_all_skills()
        return {
            "skills": [
                {"id": s["id"], "name": s["name"], "source": s["source"],
                 "description": s["l0_summary"] or s["description"], "score": s["score"]}
                for s in all_skills
            ],
            "count": len(all_skills),
        }

    else:
        return {"error": f"Unknown action: {action}. Use 'search', 'read', 'route', or 'list'."}
