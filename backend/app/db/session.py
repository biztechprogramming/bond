"""SQLAlchemy async session factory for SQLite."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

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

        # Load sqlite-vec extension on every connection
        @event.listens_for(_engine.sync_engine, "connect")
        def _load_vec_extension(dbapi_conn, connection_record):
            try:
                import sqlite_vec
                # aiosqlite wraps the real sqlite3 connection
                raw_conn = getattr(dbapi_conn, "_connection", dbapi_conn)
                raw_conn = getattr(raw_conn, "_conn", raw_conn)
                raw_conn.enable_load_extension(True)
                sqlite_vec.load(raw_conn)
                raw_conn.enable_load_extension(False)
                logger.debug("sqlite-vec loaded")
            except Exception as e:
                logger.error("Failed to load sqlite-vec: %s", e)
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
    """Initialize the database (enable WAL mode, create vec0 tables)."""
    from backend.app.foundations.knowledge.capabilities import ensure_vec_tables

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # Read embedding dimension from settings (default 1024)
    dimension = 1024
    try:
        async with engine.begin() as conn:
            result = await conn.exec_driver_sql(
                "SELECT value FROM settings WHERE key = 'embedding.output_dimension'"
            )
            row = result.fetchone()
            if row:
                dimension = int(row[0])
    except Exception:
        logger.debug("Could not read embedding dimension from settings, using default 1024")

    await ensure_vec_tables(engine, dimension=dimension)
