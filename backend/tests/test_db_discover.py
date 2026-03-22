"""Tests for db_discover tool handler."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.agent.tools.db_discover import (
    CACHE_TTL,
    _cache_key,
    _get_cached,
    _maybe_summarize,
    _redact_connection_string,
    _set_cache,
    handle_db_discover,
)


class TestCacheKey:
    def test_deterministic(self):
        conn = "postgres://user:pass@host:5432/db"
        assert _cache_key(conn) == _cache_key(conn)

    def test_different_for_different_strings(self):
        a = _cache_key("postgres://user:pass@host:5432/db1")
        b = _cache_key("postgres://user:pass@host:5432/db2")
        assert a != b

    def test_hex_string(self):
        key = _cache_key("sqlite:///test.db")
        assert len(key) == 64  # sha256 hex digest


class TestRedactConnectionString:
    def test_redacts_password(self):
        conn = "postgres://myuser:secretpass@host:5432/db"
        redacted = _redact_connection_string(conn)
        assert "secretpass" not in redacted
        assert "myuser" in redacted
        assert "***" in redacted

    def test_no_credentials(self):
        conn = "sqlite:///path/to/db.sqlite3"
        assert _redact_connection_string(conn) == conn

    def test_complex_password(self):
        conn = "mysql://admin:p@ss:w0rd!@host:3306/db"
        redacted = _redact_connection_string(conn)
        assert "p@ss:w0rd!" not in redacted
        assert "***" in redacted


class TestMaybeSummarize:
    def test_small_schema_passes_through(self):
        schema = {
            "name": "test",
            "tables": [{"name": "t1", "columns": [{"name": "id"}]}],
            "relations": [],
        }
        result = _maybe_summarize(schema)
        assert result is schema

    def test_large_schema_summarized(self):
        # Build a schema that exceeds the token threshold
        big_tables = []
        for i in range(200):
            big_tables.append({
                "name": f"table_{i}",
                "type": "TABLE",
                "columns": [
                    {"name": f"col_{j}", "type": "varchar(255)", "nullable": True}
                    for j in range(50)
                ],
                "indexes": [{"name": f"idx_{i}", "def": "x" * 200, "columns": [f"col_0"]}],
            })
        schema = {
            "name": "bigdb",
            "tables": big_tables,
            "relations": [],
            "_metadata": {"discovered_at": "2024-01-01T00:00:00Z"},
        }
        result = _maybe_summarize(schema)
        assert "_note" in result
        assert result["table_count"] == 200
        # Summarized tables should have column names but not full column objects
        assert "columns" in result["tables"][0]
        assert isinstance(result["tables"][0]["columns"][0], str)


class TestCacheHitMiss:
    def test_cache_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )
        conn = "postgres://u:p@h:5432/db"
        data = {"name": "db", "tables": []}
        _set_cache(conn, data)
        cached = _get_cached(conn)
        assert cached == data

    def test_cache_miss_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )
        assert _get_cached("postgres://u:p@h:5432/db") is None

    def test_cache_miss_when_expired(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )
        conn = "postgres://u:p@h:5432/db"
        _set_cache(conn, {"name": "db"})
        # Backdate the file
        cache_file = tmp_path / f"{_cache_key(conn)}.json"
        old_time = time.time() - CACHE_TTL - 100
        import os
        os.utime(cache_file, (old_time, old_time))
        assert _get_cached(conn) is None


class TestHandleDbDiscover:
    @pytest.mark.asyncio
    async def test_missing_connection_string(self):
        result = await handle_db_discover({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cache_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )
        conn = "postgres://u:p@h:5432/db"
        schema = {"name": "db", "tables": [], "relations": []}
        _set_cache(conn, schema)

        result = await handle_db_discover({"connection_string": conn})
        assert result.get("_from_cache") is True

    @pytest.mark.asyncio
    async def test_tbls_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )

        async def fake_exec(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await handle_db_discover({"connection_string": "sqlite:///test.db"})
        assert "not installed" in result["error"]

    @pytest.mark.asyncio
    async def test_tbls_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )

        schema_output = json.dumps({
            "name": "testdb",
            "tables": [{"name": "users", "columns": [{"name": "id"}]}],
            "relations": [],
        })

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(schema_output.encode(), b"")
        )

        async def fake_exec(*args, **kwargs):
            return mock_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await handle_db_discover({"connection_string": "sqlite:///test.db"})
        assert result["name"] == "testdb"
        assert "_metadata" in result
        assert "***" not in result["_metadata"]["connection"]  # sqlite has no password

    @pytest.mark.asyncio
    async def test_tbls_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"connection refused")
        )

        async def fake_exec(*args, **kwargs):
            return mock_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await handle_db_discover({"connection_string": "postgres://u:p@h:5432/db"})
        assert "error" in result
        assert "connection refused" in result["error"]
        assert "***" in result["connection"]

    @pytest.mark.asyncio
    async def test_refresh_bypasses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "backend.app.agent.tools.db_discover._cache_dir", lambda: tmp_path
        )
        conn = "postgres://u:p@h:5432/db"
        _set_cache(conn, {"name": "old", "tables": [], "relations": []})

        schema_output = json.dumps({
            "name": "new",
            "tables": [],
            "relations": [],
        })

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(schema_output.encode(), b"")
        )

        async def fake_exec(*args, **kwargs):
            return mock_proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await handle_db_discover({"connection_string": conn, "refresh": True})
        assert result["name"] == "new"
        assert "_from_cache" not in result
