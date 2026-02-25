"""SQLAlchemy async session factory for SQLite."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = Path(settings.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncSession:
    """Yield an async session for FastAPI dependency injection."""
    factory = get_session_factory()
    async with factory() as session:
        yield session  # type: ignore[misc]


async def init_db() -> None:
    """Initialize the database (enable WAL mode)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
