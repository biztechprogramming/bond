"""Tests for the spacetime_client module and storage backends."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.backends.base import StorageBackend
from src.backends.sqlite_backend import SQLiteBackend
from src.backends.spacetimedb_backend import SpacetimeDBBackend
from src.spacetime_client import get_client, reset_client


class TestSQLiteBackend(unittest.TestCase):
    """Tests for the SQLite fallback backend."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.backend = SQLiteBackend(db_path=self.tmp.name)

    def tearDown(self):
        self.backend.close()
        os.unlink(self.tmp.name)

    def test_store_and_retrieve(self):
        data = {"title": "Test Plan", "status": "active"}
        self.backend.store("plans", "plan-1", data)
        result = self.backend.retrieve("plans", "plan-1")
        self.assertEqual(result, data)

    def test_retrieve_nonexistent_returns_none(self):
        result = self.backend.retrieve("plans", "nonexistent")
        self.assertIsNone(result)

    def test_store_upsert(self):
        self.backend.store("plans", "plan-1", {"v": 1})
        self.backend.store("plans", "plan-1", {"v": 2})
        result = self.backend.retrieve("plans", "plan-1")
        self.assertEqual(result, {"v": 2})

    def test_list_keys_empty(self):
        keys = self.backend.list_keys("empty_ns")
        self.assertEqual(keys, [])

    def test_list_keys(self):
        self.backend.store("ns", "a", {"x": 1})
        self.backend.store("ns", "c", {"x": 3})
        self.backend.store("ns", "b", {"x": 2})
        keys = self.backend.list_keys("ns")
        self.assertEqual(keys, ["a", "b", "c"])  # sorted

    def test_list_keys_namespace_isolation(self):
        self.backend.store("ns1", "key1", {"x": 1})
        self.backend.store("ns2", "key2", {"x": 2})
        self.assertEqual(self.backend.list_keys("ns1"), ["key1"])
        self.assertEqual(self.backend.list_keys("ns2"), ["key2"])

    def test_delete_existing(self):
        self.backend.store("ns", "key1", {"x": 1})
        result = self.backend.delete("ns", "key1")
        self.assertTrue(result)
        self.assertIsNone(self.backend.retrieve("ns", "key1"))

    def test_delete_nonexistent(self):
        result = self.backend.delete("ns", "nonexistent")
        self.assertFalse(result)

    def test_is_available(self):
        self.assertTrue(self.backend.is_available())

    def test_backend_name(self):
        self.assertIn("SQLite", self.backend.backend_name)
        self.assertIn(self.tmp.name, self.backend.backend_name)

    def test_store_complex_value(self):
        data = {
            "title": "Complex Plan",
            "items": [
                {"id": 1, "name": "Step 1", "done": False},
                {"id": 2, "name": "Step 2", "done": True},
            ],
            "metadata": {"created_by": "test", "tags": ["a", "b"]},
        }
        self.backend.store("plans", "complex", data)
        result = self.backend.retrieve("plans", "complex")
        self.assertEqual(result, data)

    def test_close_and_reopen(self):
        self.backend.store("ns", "key", {"v": 42})
        self.backend.close()
        # Reopen
        backend2 = SQLiteBackend(db_path=self.tmp.name)
        result = backend2.retrieve("ns", "key")
        self.assertEqual(result, {"v": 42})
        backend2.close()


class TestSpacetimeDBBackend(unittest.TestCase):
    """Tests for the SpacetimeDB backend initialization."""

    def test_raises_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove SPACETIMEDB_TOKEN if present
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            with self.assertRaises(ValueError) as ctx:
                SpacetimeDBBackend()
            self.assertIn("token is required", str(ctx.exception))

    def test_accepts_explicit_token(self):
        backend = SpacetimeDBBackend(token="test-token-123")
        self.assertIn("SpacetimeDB", backend.backend_name)

    def test_backend_name_includes_url(self):
        backend = SpacetimeDBBackend(
            token="test", base_url="https://example.com/api"
        )
        self.assertIn("example.com", backend.backend_name)


class TestGetClient(unittest.TestCase):
    """Tests for the get_client() factory function."""

    def setUp(self):
        reset_client()

    def tearDown(self):
        reset_client()

    def test_returns_sqlite_when_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            client = get_client(force_new=True)
            self.assertIsInstance(client, SQLiteBackend)

    def test_returns_sqlite_when_spacetimedb_unreachable(self):
        with patch.dict(os.environ, {"SPACETIMEDB_TOKEN": "fake-token"}):
            with patch.object(
                SpacetimeDBBackend, "is_available", return_value=False
            ):
                client = get_client(force_new=True)
                self.assertIsInstance(client, SQLiteBackend)

    def test_returns_spacetimedb_when_available(self):
        with patch.dict(os.environ, {"SPACETIMEDB_TOKEN": "real-token"}):
            with patch.object(
                SpacetimeDBBackend, "is_available", return_value=True
            ):
                client = get_client(force_new=True)
                self.assertIsInstance(client, SpacetimeDBBackend)

    def test_caches_client_singleton(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            client1 = get_client(force_new=True)
            client2 = get_client()
            self.assertIs(client1, client2)

    def test_force_new_creates_fresh_client(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            client1 = get_client(force_new=True)
            client2 = get_client(force_new=True)
            self.assertIsNot(client1, client2)

    def test_reset_client_clears_cache(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            client1 = get_client(force_new=True)
            reset_client()
            client2 = get_client()
            self.assertIsNot(client1, client2)

    def test_sqlite_fallback_is_functional(self):
        """End-to-end test: get client without token, do CRUD operations."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SPACETIMEDB_TOKEN", None)
            client = get_client(force_new=True)
            self.assertIsInstance(client, SQLiteBackend)

            # Store
            client.store("test_ns", "key1", {"hello": "world"})

            # Retrieve
            result = client.retrieve("test_ns", "key1")
            self.assertEqual(result, {"hello": "world"})

            # List
            keys = client.list_keys("test_ns")
            self.assertIn("key1", keys)

            # Delete
            deleted = client.delete("test_ns", "key1")
            self.assertTrue(deleted)

            # Verify deleted
            result = client.retrieve("test_ns", "key1")
            self.assertIsNone(result)


class TestStorageBackendInterface(unittest.TestCase):
    """Verify that backends properly implement the abstract interface."""

    def test_sqlite_is_storage_backend(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            backend = SQLiteBackend(db_path=f.name)
            self.assertIsInstance(backend, StorageBackend)
            backend.close()
            os.unlink(f.name)

    def test_spacetimedb_is_storage_backend(self):
        backend = SpacetimeDBBackend(token="test")
        self.assertIsInstance(backend, StorageBackend)


if __name__ == "__main__":
    unittest.main()
