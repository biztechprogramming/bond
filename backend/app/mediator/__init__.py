"""Mediator — dispatch Commands/Queries through a behavior pipeline."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .base import Command, Query, no_auth
from .behaviors import (
    ExceptionBehavior,
    LoggingBehavior,
    ValidationBehavior,
    TransactionBehavior,
)
from .behaviors.logging import get_correlation_id
from .logging_config import configure_logging
from .pipeline import PipelineBehavior, run_pipeline
from .registry import get_handler_class, handles

__all__ = [
    "Command",
    "Mediator",
    "Query",
    "configure_logging",
    "get_correlation_id",
    "get_mediator",
    "handles",
    "no_auth",
]

# Default pipeline order: Exception → Logging → Validation → Transaction
# Auth behavior removed for Sprint 1 (Bond is single-user, local-first)
DEFAULT_BEHAVIORS: list[PipelineBehavior] = [
    ExceptionBehavior(),
    LoggingBehavior(log_params=True),
    ValidationBehavior(),
    TransactionBehavior(),
]


class Mediator:
    """Dispatch a Command or Query through the pipeline to its handler."""

    def __init__(
        self,
        db: AsyncSession | None = None,
        http_request: Request | None = None,
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        self.db = db
        self.http_request = http_request
        self.behaviors = behaviors if behaviors is not None else DEFAULT_BEHAVIORS

    async def send(self, request: Command | Query) -> Any:
        handler_cls = get_handler_class(type(request))
        handler = handler_cls(self.db)

        return await run_pipeline(
            self.behaviors,
            request,
            lambda: handler.handle(request),
            http_request=self.http_request,
            db=self.db,
        )


async def get_mediator(request: Request) -> Mediator:
    """FastAPI dependency that provides a Mediator instance.

    Expects ``request.state.db`` to hold an ``AsyncSession``.
    If no db session is available, the mediator works without one
    (transaction behavior becomes a no-op).
    """
    db: AsyncSession | None = getattr(request.state, "db", None)
    return Mediator(db=db, http_request=request)
