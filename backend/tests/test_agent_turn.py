"""Tests for the agent turn endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agent_turn_returns_response(async_client, mock_chat_completion):
    resp = await async_client.post(
        "/api/v1/agent/turn",
        json={"message": "Hi Bond"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Hello from Bond!"
    mock_chat_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_turn_with_history(async_client, mock_chat_completion):
    resp = await async_client.post(
        "/api/v1/agent/turn",
        json={
            "message": "Follow up",
            "history": [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "First reply"},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["response"] == "Hello from Bond!"


@pytest.mark.asyncio
async def test_agent_turn_validation_error(async_client):
    resp = await async_client.post("/api/v1/agent/turn", json={})
    assert resp.status_code == 422
