"""Skills API — feedback and management endpoints for the federated skill catalog."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("bond.api.skills")

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillFeedbackRequest(BaseModel):
    activation_id: str
    vote: str  # "up" or "down"


class SkillPinRequest(BaseModel):
    skill_id: str
    pinned: bool


class SkillExcludeRequest(BaseModel):
    skill_id: str
    excluded: bool


class SourceExcludeRequest(BaseModel):
    source: str
    excluded: bool


@router.post("/feedback")
async def skill_feedback(req: SkillFeedbackRequest) -> dict:
    """Record user feedback (thumbs up/down) for a skill activation."""
    if req.vote not in ("up", "down"):
        return {"error": "vote must be 'up' or 'down'"}
    try:
        from backend.app.agent.tools.skills_db import record_feedback
        await record_feedback(req.activation_id, req.vote)
        return {"status": "ok"}
    except Exception as e:
        logger.exception("Failed to record skill feedback")
        return {"error": str(e)}


@router.get("/")
async def list_skills() -> list[dict]:
    """List all skills with scores, usage counts, sources."""
    from backend.app.agent.tools.skills_db import list_skills_with_scores
    return await list_skills_with_scores()


@router.get("/sources")
async def list_sources() -> list[dict]:
    """List skill sources with counts."""
    from backend.app.agent.tools.skills_db import get_skill_sources
    return await get_skill_sources()


@router.get("/{skill_id:path}/usage")
async def skill_usage(skill_id: str) -> list[dict]:
    """Return usage history for a skill (last 20 activations with votes)."""
    from backend.app.agent.tools.skills_db import get_skill_usage_history
    return await get_skill_usage_history(skill_id)


@router.post("/pin")
async def pin_skill(req: SkillPinRequest) -> dict:
    """Pin or unpin a skill."""
    from backend.app.agent.tools.skills_db import set_skill_pinned
    await set_skill_pinned(req.skill_id, req.pinned)
    return {"status": "ok"}


@router.post("/exclude")
async def exclude_skill(req: SkillExcludeRequest) -> dict:
    """Exclude or include a skill."""
    from backend.app.agent.tools.skills_db import set_skill_excluded
    await set_skill_excluded(req.skill_id, req.excluded)
    return {"status": "ok"}


@router.post("/exclude-source")
async def exclude_source(req: SourceExcludeRequest) -> dict:
    """Exclude or include all skills from a source."""
    from backend.app.agent.tools.skills_db import set_source_excluded
    await set_source_excluded(req.source, req.excluded)
    return {"status": "ok"}


@router.post("/reindex")
async def reindex_skills() -> dict:
    """Trigger a re-index of the skill catalog."""
    try:
        from pathlib import Path
        from backend.app.agent.tools.skills_db import index_skills_from_json
        catalog_path = Path(__file__).resolve().parent.parent.parent.parent / "skills.json"
        if not catalog_path.exists():
            return {"error": "skills.json not found — run scripts/index-skills.py first"}
        count = await index_skills_from_json(catalog_path)
        return {"status": "ok", "indexed": count}
    except Exception as e:
        logger.exception("Failed to reindex skills")
        return {"error": str(e)}
