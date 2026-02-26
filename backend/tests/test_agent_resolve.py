"""Tests for the agent resolve endpoint."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def _apply_sql(db: aiosqlite.Connection, sql_file: Path) -> None:
    sql = sql_file.read_text()
    await db.executescript(sql)


@pytest.fixture()
async def resolve_client(_clear_settings_cache):
    """Client with fully migrated DB including agents + conversations tables."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_resolve_test_")
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


@pytest.mark.asyncio
async def test_resolve_host_mode_agent(resolve_client):
    """Default agent has no sandbox_image, should resolve as host mode."""
    res = await resolve_client.get(
        "/api/v1/agent/resolve",
        params={"agent_id": "01JBOND0000000000000DEFAULT"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "host"
    assert data["agent_id"] == "01JBOND0000000000000DEFAULT"
    assert "conversation_id" in data


@pytest.mark.asyncio
async def test_resolve_container_mode_agent(resolve_client):
    """Agent with sandbox_image should resolve as container mode."""
    # Create a containerized agent
    async with aiosqlite.connect(os.environ["BOND_DATABASE_PATH"]) as db:
        await db.execute(
            "INSERT INTO agents (id, name, display_name, system_prompt, model, sandbox_image, tools, is_default, is_active) "
            "VALUES ('agent-container-1', 'container-agent', 'Container Agent', 'test', 'anthropic/claude-sonnet-4-20250514', 'bond-sandbox:latest', '[]', 0, 1)"
        )
        await db.commit()

    # Mock sandbox_manager.ensure_running
    with patch("backend.app.api.v1.agent.get_sandbox_manager") as mock_mgr:
        mock_instance = mock_mgr.return_value
        mock_instance.ensure_running = AsyncMock(return_value={
            "worker_url": "http://localhost:18793",
            "container_id": "abc123",
        })

        res = await resolve_client.get(
            "/api/v1/agent/resolve",
            params={"agent_id": "agent-container-1"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["mode"] == "container"
        assert data["worker_url"] == "http://localhost:18793"
        assert data["agent_id"] == "agent-container-1"


@pytest.mark.asyncio
async def test_resolve_creates_conversation_if_needed(resolve_client):
    """Should create a conversation if agent_id is provided but no conversation_id."""
    res = await resolve_client.get(
        "/api/v1/agent/resolve",
        params={"agent_id": "01JBOND0000000000000DEFAULT"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["conversation_id"]  # Should be a new ULID


@pytest.mark.asyncio
async def test_resolve_nonexistent_conversation_returns_400(resolve_client):
    """Should return 400 if conversation doesn't exist and no agent_id provided."""
    res = await resolve_client.get(
        "/api/v1/agent/resolve",
        params={"conversation_id": "nonexistent-conv"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_resolve_nonexistent_agent_returns_404(resolve_client):
    """Should return 404 if agent doesn't exist."""
    res = await resolve_client.get(
        "/api/v1/agent/resolve",
        params={"agent_id": "nonexistent-agent"},
    )
    assert res.status_code == 404
