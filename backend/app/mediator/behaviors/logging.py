"""LoggingBehavior — structured request/response logging with correlation IDs."""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from ..base import Command, Query
from ..pipeline import PipelineBehavior

logger = logging.getLogger("mediator")

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Get the current correlation ID (usable from handlers)."""
    return correlation_id.get()


class LoggingBehavior(PipelineBehavior):
    def __init__(self, *, log_params: bool = True, max_param_length: int = 200) -> None:
        self.log_params = log_params
        self.max_param_length = max_param_length

    async def handle(
        self,
        request: Command | Query,
        next_behavior: Callable[[], Awaitable[Any]],
        *,
        http_request: Request | None = None,
        db: AsyncSession | None = None,
    ) -> Any:
        cid = uuid.uuid4().hex[:8]
        token = correlation_id.set(cid)

        name = type(request).__name__
        kind = "CMD" if isinstance(request, Command) else "QRY"

        route = ""
        if http_request is not None:
            route = f" {http_request.method} {http_request.url.path}"

        params = ""
        if self.log_params:
            params = self._format_params(request)

        logger.info("[%s] %s %s%s%s", cid, kind, name, route, params)

        start = time.perf_counter()
        try:
            result = await next_behavior()
            elapsed = (time.perf_counter() - start) * 1000
            logger.info("[%s] %s %.1fms", cid, name, elapsed)
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(
                "[%s] %s %.1fms — %s: %s",
                cid, name, elapsed, type(exc).__name__, exc,
            )
            raise
        finally:
            correlation_id.reset(token)

    def _format_params(self, request: Command | Query) -> str:
        try:
            data = request.model_dump(exclude_none=True)
            if not data:
                return ""
            text = str(data)
            if len(text) > self.max_param_length:
                text = text[: self.max_param_length] + "..."
            return f" params={text}"
        except Exception:
            return ""
