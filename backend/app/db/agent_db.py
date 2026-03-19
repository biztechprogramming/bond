"""Async sqlite connection to the agent's local database.

The agent DB lives at $BOND_WORKER_DATA_DIR/agent.db (default /data/agent.db).
Used by the optimization dashboard API to read observation/experiment data.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger("bond.db.agent_db")

_connection: aiosqlite.Connection | None = None


def _db_path() -> Path:
    data_dir = os.environ.get("BOND_WORKER_DATA_DIR", "/data")
    return Path(data_dir) / "agent.db"


async def get_agent_db() -> aiosqlite.Connection:
    """Return a shared aiosqlite connection to the agent DB.

    Enables WAL mode and foreign keys on first connect.
    FastAPI dependency: ``db = Depends(get_agent_db)``.
    """
    global _connection
    if _connection is None:
        db_path = _db_path()
        if not db_path.exists():
            raise FileNotFoundError(f"Agent DB not found at {db_path}")
        _connection = await aiosqlite.connect(str(db_path))
        _connection.row_factory = aiosqlite.Row
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA foreign_keys=ON")
        logger.info("Connected to agent DB at %s", db_path)
    return _connection


async def close_agent_db() -> None:
    """Close the shared connection (call on shutdown)."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
