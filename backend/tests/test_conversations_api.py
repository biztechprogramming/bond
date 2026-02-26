"""Tests for the conversations CRUD API endpoints."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _apply_sql(db: aiosqlite.Connection, sql_file: Path) -> None:
    sql = sql_file.read_text()
    await db.executescript(sql)


@pytest.fixture()
async def conv_client(_clear_settings_cache):
    """Client with fully migrated DB including conversations tables."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_conv_test_")
    db_path = Path(tmpdir) / "test.db"
    os.environ["BOND_DATABASE_PATH"] = str(db_path)

    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000004_audit_log.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000005_agents.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000006_conversations.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000007_mount_container_path.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000008_message_queue.up.sql")

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


# -- Create conversation --


@pytest.mark.asyncio
async def test_create_conversation(conv_client):
    """Should create a conversation with default agent."""
    res = await conv_client.post("/api/v1/conversations", json={})
    assert res.status_code == 200
    data = res.json()
    assert "id" in data
    assert data["agent_name"] == "Bond"
    assert data["channel"] == "webchat"
    assert data["message_count"] == 0


@pytest.mark.asyncio
async def test_create_conversation_with_agent(conv_client):
    """Should create a conversation with a specific agent."""
    res = await conv_client.post(
        "/api/v1/conversations",
        json={"agent_id": "01JBOND0000000000000DEFAULT"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["agent_id"] == "01JBOND0000000000000DEFAULT"


@pytest.mark.asyncio
async def test_create_conversation_bad_agent(conv_client):
    """Should 404 with nonexistent agent."""
    res = await conv_client.post(
        "/api/v1/conversations",
        json={"agent_id": "nonexistent"},
    )
    assert res.status_code == 404


# -- List conversations --


@pytest.mark.asyncio
async def test_list_conversations(conv_client):
    """Should list conversations newest first."""
    # Create two conversations
    await conv_client.post("/api/v1/conversations", json={"title": "First"})
    await conv_client.post("/api/v1/conversations", json={"title": "Second"})

    res = await conv_client.get("/api/v1/conversations")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 2
    titles = {d["title"] for d in data}
    assert titles == {"First", "Second"}


@pytest.mark.asyncio
async def test_list_conversations_empty(conv_client):
    """Should return empty list when no conversations."""
    res = await conv_client.get("/api/v1/conversations")
    assert res.status_code == 200
    assert res.json() == []


# -- Get conversation with messages --


@pytest.mark.asyncio
async def test_get_conversation_with_messages(conv_client):
    """Should get conversation including messages."""
    # Create conversation
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.get(f"/api/v1/conversations/{conv_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == conv_id
    assert "messages" in data
    assert isinstance(data["messages"], list)


@pytest.mark.asyncio
async def test_get_conversation_not_found(conv_client):
    res = await conv_client.get("/api/v1/conversations/nonexistent")
    assert res.status_code == 404


# -- Get messages with pagination --


@pytest.mark.asyncio
async def test_get_messages_pagination(conv_client):
    """Should paginate messages."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.get(
        f"/api/v1/conversations/{conv_id}/messages?limit=10&offset=0"
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_get_messages_not_found(conv_client):
    res = await conv_client.get("/api/v1/conversations/nonexistent/messages")
    assert res.status_code == 404


# -- Update title --


@pytest.mark.asyncio
async def test_update_conversation_title(conv_client):
    """Should update conversation title."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.put(
        f"/api/v1/conversations/{conv_id}",
        json={"title": "Updated Title"},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "Updated Title"


# -- Delete conversation --


@pytest.mark.asyncio
async def test_delete_conversation(conv_client):
    """Should delete conversation and cascade messages."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.delete(f"/api/v1/conversations/{conv_id}")
    assert res.status_code == 200

    # Verify deleted
    get_res = await conv_client.get(f"/api/v1/conversations/{conv_id}")
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_delete_conversation_not_found(conv_client):
    res = await conv_client.delete("/api/v1/conversations/nonexistent")
    assert res.status_code == 404


# -- Save assistant message --


@pytest.mark.asyncio
async def test_save_assistant_message(conv_client):
    """Should save an assistant message as delivered."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "Hello from the agent!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "message_id" in data
    assert data["conversation_id"] == conv_id


@pytest.mark.asyncio
async def test_save_assistant_message_increments_count(conv_client):
    """Saving assistant message should increment conversation message_count."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]
    assert create_res.json()["message_count"] == 0

    await conv_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "response 1"},
    )

    conv_res = await conv_client.get(f"/api/v1/conversations/{conv_id}")
    assert conv_res.json()["message_count"] == 1


@pytest.mark.asyncio
async def test_save_assistant_message_nonexistent_conversation_404(conv_client):
    """Should 404 for nonexistent conversation."""
    res = await conv_client.post(
        "/api/v1/conversations/nonexistent/messages",
        json={"role": "assistant", "content": "test"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_save_non_assistant_or_user_role_rejected(conv_client):
    """Should reject roles other than user or assistant."""
    create_res = await conv_client.post("/api/v1/conversations", json={})
    conv_id = create_res.json()["id"]

    res = await conv_client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"role": "system", "content": "test"},
    )
    assert res.status_code == 400
