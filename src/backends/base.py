"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class StorageBackend(ABC):
    """Abstract interface for storage backends.

    All storage backends (SpacetimeDB, SQLite fallback, etc.) must implement
    this interface. Data is organized by namespace and key, with values stored
    as dictionaries (JSON-serializable).
    """

    @abstractmethod
    def store(self, namespace: str, key: str, value: dict) -> None:
        """Store a value under the given namespace and key.

        Args:
            namespace: Logical grouping (e.g., 'work_plans', 'memory').
            key: Unique identifier within the namespace.
            value: JSON-serializable dictionary to store.
        """
        ...

    @abstractmethod
    def retrieve(self, namespace: str, key: str) -> Optional[dict]:
        """Retrieve a value by namespace and key.

        Args:
            namespace: Logical grouping.
            key: Unique identifier within the namespace.

        Returns:
            The stored dictionary, or None if not found.
        """
        ...

    @abstractmethod
    def list_keys(self, namespace: str) -> List[str]:
        """List all keys within a namespace.

        Args:
            namespace: Logical grouping.

        Returns:
            List of key strings.
        """
        ...

    @abstractmethod
    def delete(self, namespace: str, key: str) -> bool:
        """Delete a value by namespace and key.

        Args:
            namespace: Logical grouping.
            key: Unique identifier within the namespace.

        Returns:
            True if the key existed and was deleted, False otherwise.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is currently operational.

        Returns:
            True if the backend can accept reads and writes.
        """
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable name of this backend (for logging)."""
        ...
