"""Tests for the shared memory stub endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
async def memory_client(_clear_settings_cache, _reset_db_globals):
    """Client for memory endpoint tests."""
    from backend.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_promote_memory_returns_202(memory_client):
    """Should return 202 Accepted for valid memory promotion."""
    res = await memory_client.post(
        "/api/v1/shared-memories",
        json={
            "agent_id": "agent-abc123",
            "memory_id": "mem-001",
            "type": "fact",
            "content": "User prefers dark mode",
            "summary": "User prefers dark mode",
            "source_type": "agent",
            "entities": [],
        },
    )
    assert res.status_code == 202
    data = res.json()
    assert data["status"] == "accepted"
    assert "shared_memory_id" in data


@pytest.mark.asyncio
async def test_promote_memory_validates_required_fields(memory_client):
    """Should reject requests missing required fields."""
    res = await memory_client.post(
        "/api/v1/shared-memories",
        json={"agent_id": "agent-abc123"},
    )
    assert res.status_code == 422  # Pydantic validation error


@pytest.mark.asyncio
async def test_promote_memory_logs_event(memory_client, caplog):
    """Should log the promotion event."""
    import logging

    with caplog.at_level(logging.INFO, logger="bond.api.memory"):
        await memory_client.post(
            "/api/v1/shared-memories",
            json={
                "agent_id": "agent-abc123",
                "memory_id": "mem-002",
                "type": "fact",
                "content": "User is a developer",
                "summary": "User is a developer",
                "source_type": "agent",
                "entities": [],
            },
        )

    assert any("Memory promotion received" in r.message for r in caplog.records)
