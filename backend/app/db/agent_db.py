"""FastAPI dependency for the agent's local SQLite database.

Thin wrapper around ``agent_schema.init_agent_db`` — provides the
``Depends(get_agent_db)`` injection point and connection lifecycle.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import aiosqlite
from fastapi import HTTPException

from backend.app.db.agent_schema import init_agent_db

logger = logging.getLogger("bond.db.agent_db")

_connection: aiosqlite.Connection | None = None


def _data_dir() -> Path:
    """Resolve the agent data directory.

    Priority:
      1. BOND_WORKER_DATA_DIR env var (set by the worker on startup)
      2. <repo>/data/agents/01JBOND0000000000000DEFAULT  (local dev)
    """
    explicit = os.environ.get("BOND_WORKER_DATA_DIR")
    if explicit:
        return Path(explicit)
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "data" / "agents" / "01JBOND0000000000000DEFAULT"


async def get_agent_db() -> aiosqlite.Connection:
    """Return a shared aiosqlite connection to the agent DB.

    Creates the DB (with schema + migrations) if it doesn't exist yet.
    FastAPI dependency: ``db = Depends(get_agent_db)``.
    """
    global _connection
    if _connection is None:
        data_dir = _data_dir()
        try:
            _connection = await init_agent_db(data_dir)
        except Exception as e:
            _connection = None
            logger.error("Failed to open agent DB at %s/agent.db: %s", data_dir, e)
            raise HTTPException(
                status_code=503,
                detail=f"Agent database unavailable: {e}",
            )
    return _connection


def set_agent_db(conn: aiosqlite.Connection) -> None:
    """Allow the worker to register its already-initialised connection.

    When the worker calls ``init_agent_db`` itself (e.g. with vec extension
    loaded), it can push that connection here so the FastAPI dependency
    reuses it instead of opening a second one.
    """
    global _connection
    _connection = conn


async def close_agent_db() -> None:
    """Close the shared connection (call on shutdown)."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
