"""ExceptionBehavior — catch exceptions and return consistent error responses."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..base import Command, Query
from ..pipeline import PipelineBehavior

logger = logging.getLogger("mediator")


class ExceptionBehavior(PipelineBehavior):
    async def handle(
        self,
        request: Command | Query,
        next_behavior: Callable[[], Awaitable[Any]],
        *,
        http_request: Request | None = None,
        db: AsyncSession | None = None,
    ) -> Any:
        try:
            return await next_behavior()
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Unhandled exception in %s", type(request).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "internal_error", "message": str(exc)},
            ) from exc
