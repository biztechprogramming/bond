"""Tests for the agents CRUD API endpoints."""

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
async def agents_client(_clear_settings_cache):
    """Client with fully migrated DB including agents tables."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_agents_test_")
    db_path = Path(tmpdir) / "test.db"
    os.environ["BOND_DATABASE_PATH"] = str(db_path)

    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000003_entity_graph.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000004_audit_log.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000005_agents.up.sql")

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


# ── List agents ──


@pytest.mark.asyncio
async def test_list_agents(agents_client):
    """Should list the default seeded agent."""
    res = await agents_client.get("/api/v1/agents")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Default agent should be first
    assert data[0]["name"] == "bond"
    assert data[0]["is_default"] is True


# ── Get single agent ──


@pytest.mark.asyncio
async def test_get_agent(agents_client):
    """Should get the default agent by ID."""
    res = await agents_client.get("/api/v1/agents/01JBOND0000000000000DEFAULT")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "bond"
    assert data["display_name"] == "Bond"
    assert isinstance(data["tools"], list)
    assert isinstance(data["workspace_mounts"], list)
    assert isinstance(data["channels"], list)
    assert len(data["channels"]) >= 1


@pytest.mark.asyncio
async def test_get_agent_not_found(agents_client):
    res = await agents_client.get("/api/v1/agents/nonexistent")
    assert res.status_code == 404


# ── Create agent ──


@pytest.mark.asyncio
async def test_create_agent(agents_client):
    """Should create a new agent with mounts and channels."""
    body = {
        "name": "test-agent",
        "display_name": "Test Agent",
        "system_prompt": "You are a test agent.",
        "model": "openai/gpt-4o",
        "tools": ["respond", "search_memory"],
        "max_iterations": 10,
        "auto_rag": False,
        "auto_rag_limit": 3,
        "workspace_mounts": [
            {"host_path": "/tmp/test", "mount_name": "test", "readonly": True}
        ],
        "channels": [
            {"channel": "webchat", "enabled": True},
            {"channel": "telegram", "enabled": True},
        ],
    }
    res = await agents_client.post("/api/v1/agents", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "test-agent"
    assert data["model"] == "openai/gpt-4o"
    assert data["tools"] == ["respond", "search_memory"]
    assert data["auto_rag"] is False
    assert len(data["workspace_mounts"]) == 1
    assert data["workspace_mounts"][0]["readonly"] is True
    assert len(data["channels"]) == 2


# ── Update agent ──


@pytest.mark.asyncio
async def test_update_agent(agents_client):
    """Should update agent fields and replace mounts/channels."""
    # Create an agent first
    body = {
        "name": "update-test",
        "display_name": "Update Test",
        "system_prompt": "Original prompt.",
        "model": "openai/gpt-4o",
        "tools": ["respond"],
        "channels": [{"channel": "webchat", "enabled": True}],
    }
    create_res = await agents_client.post("/api/v1/agents", json=body)
    agent_id = create_res.json()["id"]

    # Update
    update_body = {
        "display_name": "Updated Name",
        "tools": ["respond", "search_memory", "file_read"],
        "channels": [
            {"channel": "signal", "enabled": True},
        ],
    }
    res = await agents_client.put(f"/api/v1/agents/{agent_id}", json=update_body)
    assert res.status_code == 200
    data = res.json()
    assert data["display_name"] == "Updated Name"
    assert data["tools"] == ["respond", "search_memory", "file_read"]
    assert len(data["channels"]) == 1
    assert data["channels"][0]["channel"] == "signal"


# ── Delete agent ──


@pytest.mark.asyncio
async def test_delete_agent(agents_client):
    """Should delete a non-default agent."""
    body = {
        "name": "delete-me",
        "display_name": "Delete Me",
        "system_prompt": "Temporary.",
        "model": "openai/gpt-4o",
        "tools": [],
    }
    create_res = await agents_client.post("/api/v1/agents", json=body)
    agent_id = create_res.json()["id"]

    res = await agents_client.delete(f"/api/v1/agents/{agent_id}")
    assert res.status_code == 200

    # Verify deleted
    get_res = await agents_client.get(f"/api/v1/agents/{agent_id}")
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_delete_default_agent_rejected(agents_client):
    """Should reject deleting the default agent."""
    res = await agents_client.delete("/api/v1/agents/01JBOND0000000000000DEFAULT")
    assert res.status_code == 400
    assert "default" in res.json()["detail"].lower()


# ── Set default ──


@pytest.mark.asyncio
async def test_set_default_agent(agents_client):
    """Should change the default agent."""
    body = {
        "name": "new-default",
        "display_name": "New Default",
        "system_prompt": "I am the new default.",
        "model": "openai/gpt-4o",
        "tools": ["respond"],
    }
    create_res = await agents_client.post("/api/v1/agents", json=body)
    agent_id = create_res.json()["id"]

    res = await agents_client.post(f"/api/v1/agents/{agent_id}/default")
    assert res.status_code == 200
    assert res.json()["is_default"] is True

    # Original default should no longer be default
    old_res = await agents_client.get("/api/v1/agents/01JBOND0000000000000DEFAULT")
    assert old_res.json()["is_default"] is False


# ── Tools listing ──


@pytest.mark.asyncio
async def test_list_tools(agents_client):
    """Should list all 14 available tools."""
    res = await agents_client.get("/api/v1/agents/tools")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 14
    names = {t["name"] for t in data}
    assert "respond" in names
    assert "search_memory" in names
    assert "code_execute" in names


# ── Sandbox images ──


@pytest.mark.asyncio
async def test_list_sandbox_images(agents_client):
    """Should return a list (possibly empty if docker unavailable)."""
    res = await agents_client.get("/api/v1/agents/sandbox-images")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
