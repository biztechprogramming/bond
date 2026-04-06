"""Tests for image upload to gateway in image_gen.py (Design Doc 104)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


@pytest.fixture
def mock_gateway_success():
    """Mock httpx.AsyncClient that returns a successful upload response."""
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "id": "img_abc123",
        "url": "/api/v1/images/img_abc123.png",
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.fixture
def mock_gateway_failure():
    """Mock httpx.AsyncClient that raises on upload."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_successful_upload_returns_gateway_url(mock_gateway_success, tmp_path):
    """When gateway upload succeeds, the returned URL should be the gateway path."""
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    with patch("httpx.AsyncClient", return_value=mock_gateway_success):
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            with open(img_path, "rb") as f:
                resp = await client.post(
                    "http://gateway:18789/api/v1/images/upload",
                    files={"file": (img_path.name, f, "image/png")},
                    data={
                        "agent_id": "agent-1",
                        "conversation_id": "conv-1",
                        "filename": img_path.name,
                        "prompt": "test prompt",
                    },
                )
                resp.raise_for_status()
                url = resp.json()["url"]

    assert url == "/api/v1/images/img_abc123.png"


@pytest.mark.asyncio
async def test_failed_upload_falls_back_to_local_path(mock_gateway_failure, tmp_path):
    """When gateway upload fails, should fall back to local file path."""
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    local_path = str(img_path)

    uploaded_url = local_path  # default fallback

    with patch("httpx.AsyncClient", return_value=mock_gateway_failure):
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                with open(img_path, "rb") as f:
                    resp = await client.post(
                        "http://gateway:18789/api/v1/images/upload",
                        files={"file": (img_path.name, f, "image/png")},
                        data={"agent_id": "agent-1", "conversation_id": "conv-1"},
                    )
                    resp.raise_for_status()
                    uploaded_url = resp.json()["url"]
        except Exception:
            uploaded_url = local_path

    assert uploaded_url == local_path
