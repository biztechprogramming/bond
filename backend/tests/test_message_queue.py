"""Tests for message queue, interrupt system, and SSE streaming."""

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
async def mq_client(_clear_settings_cache):
    """Client with fully migrated DB including message queue migration."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_mq_test_")
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


# -- Message queue endpoint --


@pytest.mark.asyncio
async def test_queue_message(mq_client):
    """Should queue a message and return message_id + position."""
    # Create conversation first
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    res = await mq_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello", "role": "user"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "queued"
    assert "message_id" in data
    assert data["queue_position"] >= 1


@pytest.mark.asyncio
async def test_queue_multiple_messages(mq_client):
    """Should queue multiple messages with increasing positions."""
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    res1 = await mq_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "First"},
    )
    res2 = await mq_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Second"},
    )

    assert res1.json()["queue_position"] == 1
    assert res2.json()["queue_position"] == 2


@pytest.mark.asyncio
async def test_queue_message_not_found(mq_client):
    """Should 404 for nonexistent conversation."""
    res = await mq_client.post(
        "/api/v1/conversations/nonexistent/messages",
        json={"content": "Hello"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_queue_updates_message_count(mq_client):
    """Should increment message_count when queueing."""
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    await mq_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello"},
    )

    conv = await mq_client.get(f"/api/v1/conversations/{conv_id}")
    assert conv.json()["message_count"] == 1


# -- Interrupt endpoint --


@pytest.mark.asyncio
async def test_interrupt_no_active_turn(mq_client):
    """Should return no_active_turn when no turn is running."""
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    res = await mq_client.post(f"/api/v1/conversations/{conv_id}/interrupt")
    assert res.status_code == 200
    assert res.json()["status"] == "no_active_turn"


@pytest.mark.asyncio
async def test_interrupt_not_found(mq_client):
    """Should 404 for nonexistent conversation."""
    res = await mq_client.post("/api/v1/conversations/nonexistent/interrupt")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_interrupt_with_active_turn(mq_client):
    """Should send interrupt when a turn is active."""
    from backend.app.agent.interrupts import register_turn, unregister_turn

    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    register_turn(conv_id)
    try:
        res = await mq_client.post(f"/api/v1/conversations/{conv_id}/interrupt")
        assert res.status_code == 200
        assert res.json()["status"] == "interrupt_sent"
    finally:
        unregister_turn(conv_id)


# -- Interrupt module unit tests --


def test_interrupt_flags():
    """Test interrupt register/set/check/unregister lifecycle."""
    from backend.app.agent.interrupts import (
        register_turn, unregister_turn, set_interrupt,
        check_interrupt, is_turn_active,
    )

    conv_id = "test-conv-1"

    assert not is_turn_active(conv_id)
    assert not check_interrupt(conv_id)

    register_turn(conv_id)
    assert is_turn_active(conv_id)
    assert not check_interrupt(conv_id)

    assert set_interrupt(conv_id)
    assert check_interrupt(conv_id)
    # After checking, flag should be cleared
    assert not check_interrupt(conv_id)

    unregister_turn(conv_id)
    assert not is_turn_active(conv_id)
    assert not set_interrupt(conv_id)


# -- Agent turn with queue --


@pytest.mark.asyncio
async def test_turn_with_queued_messages(mq_client, mock_llm):
    """Turn should pick up queued messages."""
    # Create conversation
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    # Queue a message
    await mq_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Hello from queue"},
    )

    # Start turn (without message — it should pick up queued)
    res = await mq_client.post(
        "/api/v1/agent/turn",
        json={"conversation_id": conv_id},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["response"] == "Hello from Bond!"
    assert data["queued_count"] == 0


@pytest.mark.asyncio
async def test_turn_queued_count_in_response(mq_client, mock_llm):
    """Turn response should include queued_count."""
    res = await mq_client.post(
        "/api/v1/agent/turn",
        json={"message": "Hello"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "queued_count" in data
    assert data["queued_count"] == 0


# -- SSE streaming turn --


@pytest.mark.asyncio
async def test_sse_streaming_turn(mq_client, mock_llm):
    """SSE streaming turn should return event stream."""
    conv_res = await mq_client.post("/api/v1/conversations", json={})
    conv_id = conv_res.json()["id"]

    res = await mq_client.post(
        "/api/v1/agent/turn",
        json={"message": "Hello", "conversation_id": conv_id, "stream": True},
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")

    body = res.text
    assert "event: status" in body
    assert "event: chunk" in body
    assert "event: done" in body
    assert "Hello from Bond!" in body
