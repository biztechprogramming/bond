"""Tests for the PersistenceClient — api and sqlite modes."""

import asyncio
import json
import os
import pytest
import aiosqlite
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from backend.app.agent.persistence_client import (
    PersistenceClient,
    _resolve_gateway_url,
    _detect_persistence_mode,
)


# ---------- fixtures ----------


@pytest.fixture
def clean_env(monkeypatch):
    """Clear persistence-related env vars."""
    for key in [
        "BOND_PERSISTENCE_MODE",
        "BOND_GATEWAY_URL",
        "BOND_AGENT_ID",
        "BOND_AGENT_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
async def sqlite_db(tmp_path):
    """Create an in-memory-like sqlite DB with the required tables."""
    db_path = tmp_path / "test_agent.db"
    db = await aiosqlite.connect(str(db_path))
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tool_logs (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            input TEXT NOT NULL,
            output TEXT NOT NULL,
            duration INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    await db.commit()
    yield db
    await db.close()


# ---------- _resolve_gateway_url ----------


class TestResolveGatewayUrl:
    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("BOND_GATEWAY_URL", "http://custom:9999")
        assert _resolve_gateway_url() == "http://custom:9999"

    def test_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("BOND_GATEWAY_URL", "http://custom:9999/")
        assert _resolve_gateway_url() == "http://custom:9999"


# ---------- _detect_persistence_mode ----------


class TestDetectPersistenceMode:
    @pytest.mark.asyncio
    async def test_api_when_gateway_reachable(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            mode = await _detect_persistence_mode("http://localhost:18789")
            assert mode == "api"

    @pytest.mark.asyncio
    async def test_sqlite_when_gateway_unreachable(self):
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=Exception("connection refused"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            mode = await _detect_persistence_mode("http://localhost:18789")
            assert mode == "sqlite"


# ---------- PersistenceClient — init ----------


class TestClientInit:
    @pytest.mark.asyncio
    async def test_explicit_api_mode(self, clean_env):
        client = PersistenceClient(agent_id="test", mode="api", gateway_url="http://localhost:18789")
        await client.init()
        assert client.mode == "api"
        await client.close()

    @pytest.mark.asyncio
    async def test_explicit_sqlite_mode(self, clean_env):
        client = PersistenceClient(agent_id="test", mode="sqlite")
        await client.init()
        assert client.mode == "sqlite"
        await client.close()

    @pytest.mark.asyncio
    async def test_env_var_mode(self, monkeypatch):
        monkeypatch.setenv("BOND_PERSISTENCE_MODE", "API")
        client = PersistenceClient(agent_id="test", gateway_url="http://localhost:18789")
        await client.init()
        assert client.mode == "api"  # normalized to lowercase
        await client.close()

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self, clean_env):
        client = PersistenceClient(agent_id="test", mode="magic")
        with pytest.raises(ValueError, match="Invalid BOND_PERSISTENCE_MODE"):
            await client.init()

    @pytest.mark.asyncio
    async def test_init_idempotent(self, clean_env):
        client = PersistenceClient(agent_id="test", mode="sqlite")
        await client.init()
        await client.init()  # second call is a no-op
        assert client.mode == "sqlite"
        await client.close()

    @pytest.mark.asyncio
    async def test_mode_before_init_raises(self, clean_env):
        client = PersistenceClient(agent_id="test")
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = client.mode


# ---------- PersistenceClient — sqlite mode ----------


class TestSqliteMode:
    @pytest.mark.asyncio
    async def test_save_message(self, clean_env, sqlite_db):
        client = PersistenceClient(agent_id="agent-1", mode="sqlite")
        await client.init()

        result = await client.save_message(
            session_id="sess-1",
            role="user",
            content="Hello!",
            metadata={"source": "test"},
            agent_db=sqlite_db,
        )
        assert result is True

        # Verify the row
        async with sqlite_db.execute("SELECT * FROM messages") as cursor:
            rows = await cursor.fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row[1] == "agent-1"  # agent_id
        assert row[2] == "sess-1"  # session_id
        assert row[3] == "user"  # role
        assert row[4] == "Hello!"  # content
        assert json.loads(row[5]) == {"source": "test"}  # metadata

        await client.close()

    @pytest.mark.asyncio
    async def test_log_tool(self, clean_env, sqlite_db):
        client = PersistenceClient(agent_id="agent-1", mode="sqlite")
        await client.init()

        result = await client.log_tool(
            session_id="sess-1",
            tool_name="web_search",
            input={"query": "test"},
            output={"results": [1, 2]},
            duration=1.5,
            agent_db=sqlite_db,
        )
        assert result is True

        async with sqlite_db.execute("SELECT * FROM tool_logs") as cursor:
            rows = await cursor.fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row[1] == "agent-1"
        assert row[3] == "web_search"
        assert row[6] == 1500  # 1.5s → 1500ms

        await client.close()

    @pytest.mark.asyncio
    async def test_sqlite_requires_agent_db(self, clean_env):
        client = PersistenceClient(agent_id="agent-1", mode="sqlite")
        await client.init()

        with pytest.raises(RuntimeError, match="requires agent_db"):
            await client.save_message("sess", "user", "hello", agent_db=None)

        with pytest.raises(RuntimeError, match="requires agent_db"):
            await client.log_tool("sess", "tool", {}, {}, 0.0, agent_db=None)

        await client.close()


# ---------- PersistenceClient — api mode ----------


class TestApiMode:
    @pytest.mark.asyncio
    async def test_save_message_success(self, clean_env):
        client = PersistenceClient(
            agent_id="agent-1", mode="api", gateway_url="http://fake:18789"
        )
        await client.init()

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "test-id", "status": "saved"}

        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.save_message("sess-1", "user", "Hello!")
        assert result == {"id": "test-id", "status": "saved"}

        # Verify the POST payload
        call_args = client._client.post.call_args
        assert call_args[0][0] == "/messages"
        payload = call_args[1]["json"]
        assert payload["agentId"] == "agent-1"
        assert payload["role"] == "user"
        assert payload["content"] == "Hello!"

        await client.close()

    @pytest.mark.asyncio
    async def test_save_message_failure_raises(self, clean_env):
        client = PersistenceClient(
            agent_id="agent-1", mode="api", gateway_url="http://fake:18789"
        )
        await client.init()

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "internal error"

        client._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Gateway save_message failed"):
            await client.save_message("sess-1", "user", "Hello!")

        await client.close()

    @pytest.mark.asyncio
    async def test_log_tool_success(self, clean_env):
        client = PersistenceClient(
            agent_id="agent-1", mode="api", gateway_url="http://fake:18789"
        )
        await client.init()

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"id": "tool-id", "status": "logged"}

        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.log_tool("sess-1", "web_search", {"q": "test"}, {"r": 1}, 2.0)
        assert result == {"id": "tool-id", "status": "logged"}

        payload = client._client.post.call_args[1]["json"]
        assert payload["toolName"] == "web_search"
        assert payload["duration"] == 2.0

        await client.close()

    @pytest.mark.asyncio
    async def test_log_tool_failure_raises(self, clean_env):
        client = PersistenceClient(
            agent_id="agent-1", mode="api", gateway_url="http://fake:18789"
        )
        await client.init()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"

        client._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Gateway log_tool failed"):
            await client.log_tool("sess-1", "broken", {}, {}, 0.0)

        await client.close()

    @pytest.mark.asyncio
    async def test_auth_header_set_when_token_present(self, monkeypatch):
        monkeypatch.delenv("BOND_API_KEY", raising=False)
        monkeypatch.setenv("BOND_AGENT_TOKEN", "secret-token-123")

        client = PersistenceClient(
            agent_id="agent-1", mode="api", gateway_url="http://fake:18789"
        )
        await client.init()

        assert client._client.headers.get("authorization") == "Bearer secret-token-123"
        await client.close()
