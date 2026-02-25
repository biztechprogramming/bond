"""Pipeline behavior base and ordered pipeline runner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

from starlette.requests import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Command, Query


class PipelineBehavior(ABC):
    """Base class for pipeline behaviors (middleware around handlers)."""

    @abstractmethod
    async def handle(
        self,
        request: Command | Query,
        next_behavior: Callable[[], Awaitable[Any]],
        *,
        http_request: Request | None = None,
        db: AsyncSession | None = None,
    ) -> Any:
        """Process the request. Call ``await next_behavior()`` to continue."""
        ...


async def run_pipeline(
    behaviors: list[PipelineBehavior],
    request: Command | Query,
    handler_call: Callable[[], Awaitable[Any]],
    *,
    http_request: Request | None = None,
    db: AsyncSession | None = None,
) -> Any:
    """Execute behaviors in order, innermost calling the handler."""

    async def build_chain(index: int) -> Any:
        if index >= len(behaviors):
            return await handler_call()
        behavior = behaviors[index]
        return await behavior.handle(
            request,
            lambda: build_chain(index + 1),
            http_request=http_request,
            db=db,
        )

    return await build_chain(0)
