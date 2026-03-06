"""SpacetimeDB client with graceful degradation.

This module provides a factory function `get_client()` that returns the
appropriate storage backend based on the current environment:

- If SPACETIMEDB_TOKEN is set → SpacetimeDBBackend (primary)
- If SPACETIMEDB_TOKEN is absent → SQLiteBackend (fallback)

Usage:
    from src.spacetime_client import get_client

    client = get_client()
    client.store("work_plans", "plan-123", {"title": "My Plan", "status": "active"})
    plan = client.retrieve("work_plans", "plan-123")
"""

import logging
import os
from typing import Optional

from .backends.base import StorageBackend
from .backends.sqlite_backend import SQLiteBackend
from .backends.spacetimedb_backend import SpacetimeDBBackend

logger = logging.getLogger(__name__)

# Module-level singleton to avoid re-creating on every call
_client: Optional[StorageBackend] = None
_fallback_warning_shown = False


def get_client(force_new: bool = False) -> StorageBackend:
    """Get the appropriate storage backend.

    Returns a SpacetimeDB client if SPACETIMEDB_TOKEN is configured and the
    service is reachable. Otherwise, falls back to a local SQLite backend
    with a warning.

    Args:
        force_new: If True, create a fresh client instead of returning the
                   cached singleton. Useful for testing.

    Returns:
        A StorageBackend instance ready for use.
    """
    global _client, _fallback_warning_shown

    if _client is not None and not force_new:
        return _client

    token = os.environ.get("SPACETIMEDB_TOKEN")

    if token:
        try:
            backend = SpacetimeDBBackend(token=token)
            if backend.is_available():
                logger.info("Using SpacetimeDB backend.")
                _client = backend
                return _client
            else:
                logger.warning(
                    "SPACETIMEDB_TOKEN is set but SpacetimeDB is not reachable. "
                    "Falling back to local SQLite storage."
                )
        except Exception as e:
            logger.warning(
                "Failed to initialize SpacetimeDB backend: %s. "
                "Falling back to local SQLite storage.",
                e,
            )
    else:
        if not _fallback_warning_shown:
            logger.warning(
                "SPACETIMEDB_TOKEN is not set. Using local SQLite fallback. "
                "Set SPACETIMEDB_TOKEN to use SpacetimeDB for persistent storage."
            )
            _fallback_warning_shown = True

    _client = SQLiteBackend()
    return _client


def reset_client() -> None:
    """Reset the cached client singleton.

    Useful for testing or when environment variables change at runtime.
    """
    global _client, _fallback_warning_shown
    _client = None
    _fallback_warning_shown = False
