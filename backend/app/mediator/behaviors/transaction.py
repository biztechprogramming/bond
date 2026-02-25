"""TransactionBehavior — commit on success, rollback on exception."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..base import Command, Query
from ..pipeline import PipelineBehavior


class TransactionBehavior(PipelineBehavior):
    async def handle(
        self,
        request: Command | Query,
        next_behavior: Callable[[], Awaitable[Any]],
        *,
        http_request: Request | None = None,
        db: AsyncSession | None = None,
    ) -> Any:
        if db is None:
            return await next_behavior()
        try:
            result = await next_behavior()
            await db.commit()
            return result
        except Exception:
            await db.rollback()
            raise
