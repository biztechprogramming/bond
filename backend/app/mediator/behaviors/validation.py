"""ValidationBehavior — Pydantic validation (re-validate after mutations)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import ValidationError
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..base import Command, Query
from ..pipeline import PipelineBehavior


class ValidationBehavior(PipelineBehavior):
    async def handle(
        self,
        request: Command | Query,
        next_behavior: Callable[[], Awaitable[Any]],
        *,
        http_request: Request | None = None,
        db: AsyncSession | None = None,
    ) -> Any:
        try:
            type(request).model_validate(request.model_dump())
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from exc
        return await next_behavior()
