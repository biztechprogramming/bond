"""Periodic job: recalculate composite skill scores from usage data."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)


async def recalculate_skill_scores(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recalculate all skill scores. Runs as a scheduled job."""
    from backend.app.agent.tools.skills_db import recalculate_scores

    count = await recalculate_scores()
    logger.info("Recalculated scores for %d skills", count)
