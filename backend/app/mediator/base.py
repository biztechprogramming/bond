"""Base classes for Commands, Queries, and their Handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

TResult = TypeVar("TResult")
TCommand = TypeVar("TCommand", bound="Command")
TQuery = TypeVar("TQuery", bound="Query")


class Command(BaseModel):
    """Base for all write operations."""
    pass


class Query(BaseModel):
    """Base for all read operations."""
    pass


def no_auth(cls: type) -> type:
    """Decorator marking a Command/Query as not requiring authentication."""
    cls.__no_auth__ = True  # type: ignore[attr-defined]
    return cls


def requires_auth(request: Command | Query) -> bool:
    """Return True if the request type requires authentication."""
    return not getattr(type(request), "__no_auth__", False)


class CommandHandler(ABC, Generic[TCommand, TResult]):
    """Base handler for write operations."""

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    @abstractmethod
    async def handle(self, command: TCommand) -> TResult: ...


class QueryHandler(ABC, Generic[TQuery, TResult]):
    """Base handler for read operations."""

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db

    @abstractmethod
    async def handle(self, query: TQuery) -> TResult: ...
