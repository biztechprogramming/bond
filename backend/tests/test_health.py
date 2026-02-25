"""Tests for the health endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_200(async_client):
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "bond-backend"
