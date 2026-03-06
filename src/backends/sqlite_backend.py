"""SQLite-based fallback storage backend.

Used when SpacetimeDB is unavailable (e.g., SPACETIMEDB_TOKEN not set).
Provides local, ephemeral storage so agent tools continue to function.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from .base import StorageBackend

logger = logging.getLogger(__name__)

DEFAULT_DB_DIR = os.path.expanduser("~/.bond")
DEFAULT_DB_NAME = "fallback.db"


class SQLiteBackend(StorageBackend):
    """Local SQLite storage backend.

    Stores data in a simple key-value table with namespace partitioning.
    Uses WAL mode for better concurrent access handling.

    Args:
        db_path: Path to the SQLite database file. Defaults to ~/.bond/fallback.db.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_dir = DEFAULT_DB_DIR
            Path(db_dir).mkdir(parents=True, exist_ok=True)
            db_path = os.path.join(db_dir, DEFAULT_DB_NAME)

        self._db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._initialize_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(self._db_path)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
        return self._connection

    def _initialize_db(self) -> None:
        """Create the storage table if it doesn't exist."""
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (namespace, key)
            )
            """
        )
        conn.commit()
        logger.debug("SQLite fallback database initialized at %s", self._db_path)

    def store(self, namespace: str, key: str, value: dict) -> None:
        """Store a value, upserting if the key already exists."""
        conn = self._get_connection()
        serialized = json.dumps(value, default=str)
        conn.execute(
            """
            INSERT INTO kv_store (namespace, key, value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (namespace, key, serialized),
        )
        conn.commit()

    def retrieve(self, namespace: str, key: str) -> Optional[dict]:
        """Retrieve a value by namespace and key."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT value FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def list_keys(self, namespace: str) -> List[str]:
        """List all keys in a namespace."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT key FROM kv_store WHERE namespace = ? ORDER BY key",
            (namespace,),
        )
        return [row[0] for row in cursor.fetchall()]

    def delete(self, namespace: str, key: str) -> bool:
        """Delete a key-value pair. Returns True if it existed."""
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM kv_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        conn.commit()
        return cursor.rowcount > 0

    def is_available(self) -> bool:
        """Check if SQLite is operational."""
        try:
            conn = self._get_connection()
            conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    @property
    def backend_name(self) -> str:
        return f"SQLite ({self._db_path})"

    def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
