"""Skills tool — search, read, and list skills from the federated catalog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level router instance, lazily initialized
_router = None


def _get_router():
    """Get or create the SkillRouter singleton.

    Requires init_router() to have been called first to load settings from
    the Embedding tab. Raises if settings are missing — no silent fallbacks.
    """
    global _router
    if _router is None:
        from backend.app.agent.skills_router import SkillRouter

        if _router_settings is None:
            raise RuntimeError(
                "Embedding settings not loaded. init_router() must be called before "
                "_get_router(). Check that the Embedding tab in Settings is configured."
            )

        from backend.app.foundations.embeddings.engine import EmbeddingEngine
        engine = EmbeddingEngine(
            settings=_router_settings,
            db_engine=None,
        )
        _router = SkillRouter(embedding_engine=engine)
        logger.info(
            "Skills router initialized (execution_mode=%s, model=%s)",
            _router_settings.get("embedding.execution_mode"),
            _router_settings.get("embedding.model"),
        )
    return _router


# Settings loaded async before first use
_router_settings: dict | None = None


async def init_router(persistence=None) -> None:
    """Load embedding settings and pre-initialize the router.

    Called from the agent loop before first use.
    Args:
        persistence: PersistenceClient for fetching API keys from gateway
    """
    global _router_settings, _router
    if _router is not None:
        return  # Already initialized

    settings = {}

    _persistence = persistence
    if not _persistence:
        try:
            from backend.app.worker import _state
            _persistence = _state.persistence
        except Exception:
            pass

    if not _persistence:
        raise RuntimeError(
            "Cannot load embedding settings: no persistence client available. "
            "Ensure the worker is connected to the gateway."
        )

    # Read embedding settings from DB — these MUST exist (configured via Settings → Embedding)
    required_keys = ("embedding.model", "embedding.output_dimension", "embedding.execution_mode")
    for key in required_keys:
        try:
            val = await _persistence.get_setting(key)
            if val:
                settings[key] = val
        except Exception:
            logger.error("Failed to read %s from DB", key, exc_info=True)

    missing = [k for k in required_keys if k not in settings]
    if missing:
        raise RuntimeError(
            f"Embedding settings not configured: {', '.join(missing)}. "
            "Configure them in Settings → Embedding tab."
        )

    # Read API keys (optional — only required for api/gemini execution modes)
    for provider_id, settings_key in (("voyage", "embedding.api_key.voyage"), ("gemini", "embedding.api_key.gemini")):
        try:
            encrypted = await _persistence.get_provider_api_key(provider_id)
            if encrypted:
                from backend.app.core.crypto import decrypt_value, is_encrypted
                if is_encrypted(encrypted):
                    settings[settings_key] = decrypt_value(encrypted)
                else:
                    settings[settings_key] = encrypted
                logger.info("%s API key loaded via persistence client", provider_id.capitalize())
        except Exception:
            logger.debug("Failed to read %s key via persistence client", provider_id, exc_info=True)

    _router_settings = settings
    logger.info("Embedding settings loaded: execution_mode=%s model=%s dimension=%s has_voyage_key=%s has_gemini_key=%s",
                settings.get("embedding.execution_mode"),
                settings.get("embedding.model"),
                settings.get("embedding.output_dimension"),
                bool(settings.get("embedding.api_key.voyage")),
                bool(settings.get("embedding.api_key.gemini")))


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
