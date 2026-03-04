"""Tests for agent turn with conversation persistence."""

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
async def persist_client(_clear_settings_cache):
    """Client with fully migrated DB for persistence tests."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_persist_test_")
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


# -- Turn creates conversation --


@pytest.mark.asyncio
async def test_turn_creates_conversation(persist_client, mock_llm):
    """Turn should create a new conversation when none provided."""
    res = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "Hello!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "conversation_id" in data
    assert "message_id" in data
    assert data["response"] == "Hello from Bond!"
    assert len(data["conversation_id"]) > 0


@pytest.mark.asyncio
async def test_turn_uses_existing_conversation(persist_client, mock_llm):
    """Turn should reuse an existing conversation."""
    # First turn: creates conversation
    res1 = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "First message"},
    )
    conv_id = res1.json()["conversation_id"]

    # Second turn: reuses same conversation
    res2 = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "Second message", "conversation_id": conv_id},
    )
    assert res2.status_code == 200
    assert res2.json()["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_messages_persisted_in_db(persist_client, mock_llm):
    """Messages should be stored in conversation_messages."""
    res = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "Test persistence"},
    )
    conv_id = res.json()["conversation_id"]

    # Fetch messages via API
    msgs_res = await persist_client.get(f"/api/v1/conversations/{conv_id}/messages")
    assert msgs_res.status_code == 200
    messages = msgs_res.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Test persistence"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hello from Bond!"


@pytest.mark.asyncio
async def test_history_loaded_on_subsequent_turns(persist_client, mock_llm):
    """History should be loaded from DB on subsequent turns."""
    # First turn
    res1 = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "First"},
    )
    conv_id = res1.json()["conversation_id"]

    # Second turn
    res2 = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "Second", "conversation_id": conv_id},
    )
    assert res2.status_code == 200

    # Verify 4 messages total (2 per turn)
    msgs_res = await persist_client.get(f"/api/v1/conversations/{conv_id}/messages")
    messages = msgs_res.json()
    assert len(messages) == 4


@pytest.mark.asyncio
async def test_auto_title_set_on_first_exchange(persist_client, mock_llm):
    """Title should be set from first user message."""
    res = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "What is the meaning of life?"},
    )
    conv_id = res.json()["conversation_id"]

    conv_res = await persist_client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res.status_code == 200
    assert conv_res.json()["title"] == "What is the meaning of life?"


@pytest.mark.asyncio
async def test_auto_title_truncated(persist_client, mock_llm):
    """Long messages should be truncated for title."""
    long_msg = "A" * 100
    res = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": long_msg},
    )
    conv_id = res.json()["conversation_id"]

    conv_res = await persist_client.get(f"/api/v1/conversations/{conv_id}")
    title = conv_res.json()["title"]
    assert len(title) <= 53  # 50 chars + "..."
    assert title.endswith("...")


@pytest.mark.asyncio
async def test_message_count_updated(persist_client, mock_llm):
    """Conversation message_count should be updated."""
    res = await persist_client.post(
        "/api/v1/agent/turn",
        json={"message": "Count test"},
    )
    conv_id = res.json()["conversation_id"]

    conv_res = await persist_client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res.json()["message_count"] == 2
