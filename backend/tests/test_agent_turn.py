"""Tests for the agent turn endpoint."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _apply_sql(db: aiosqlite.Connection, sql_file: Path) -> None:
    sql = sql_file.read_text()
    await db.executescript(sql)


@pytest.fixture()
async def turn_client(_clear_settings_cache):
    """Client with fully migrated DB for agent turn tests."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_turn_test_")
    db_path = Path(tmpdir) / "test.db"
    os.environ["BOND_DATABASE_PATH"] = str(db_path)

    async with aiosqlite.connect(db_path) as db:
        from tests.conftest import apply_all_migrations
        await apply_all_migrations(db)

    from backend.app.config import get_settings
    get_settings.cache_clear()

    sess._engine = None
    sess._session_factory = None

    from backend.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    sess._engine = None
    sess._session_factory = None


@pytest.fixture()
def mock_llm():
    """Mock LLM for agent turn tests."""
    message = MagicMock()
    message.content = "Hello from Bond!"
    message.tool_calls = None
    message.model_dump.return_value = {"role": "assistant", "content": "Hello from Bond!"}
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]

    with (
        patch("backend.app.agent.loop.chat_completion", new_callable=AsyncMock) as mock_cc,
        patch("backend.app.agent.loop.litellm") as mock_litellm,
    ):
        mock_cc.return_value = "Hello from Bond!"
        mock_litellm.acompletion = AsyncMock(return_value=response)
        yield mock_cc


@pytest.mark.asyncio
async def test_agent_turn_returns_response(turn_client, mock_llm):
    resp = await turn_client.post(
        "/api/v1/agent/turn",
        json={"message": "Hi Bond"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Hello from Bond!"
    assert "conversation_id" in data
    assert "message_id" in data


@pytest.mark.asyncio
async def test_agent_turn_with_conversation_id(turn_client, mock_llm):
    # First turn creates conversation
    resp1 = await turn_client.post(
        "/api/v1/agent/turn",
        json={"message": "First message"},
    )
    conv_id = resp1.json()["conversation_id"]

    # Second turn uses existing conversation
    resp2 = await turn_client.post(
        "/api/v1/agent/turn",
        json={"message": "Follow up", "conversation_id": conv_id},
    )
    assert resp2.status_code == 200
    assert resp2.json()["response"] == "Hello from Bond!"
    assert resp2.json()["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_agent_turn_empty_request(turn_client):
    """Empty request (no message, no conversation) creates conv and returns empty."""
    resp = await turn_client.post("/api/v1/agent/turn", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "conversation_id" in data
