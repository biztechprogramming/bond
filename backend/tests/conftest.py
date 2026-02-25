"""Shared test fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Set BOND_HOME to a temp dir before any config import
_tmpdir = tempfile.mkdtemp(prefix="bond_test_")
os.environ["BOND_HOME"] = _tmpdir
os.environ["BOND_DATABASE_PATH"] = str(Path(_tmpdir) / "test.db")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the lru_cache on get_settings so tests get fresh settings."""
    from backend.app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def _reset_db_globals():
    """Reset DB engine/session globals so each test uses its own DB."""
    import backend.app.db.session as sess

    sess._engine = None
    sess._session_factory = None
    yield
    sess._engine = None
    sess._session_factory = None


@pytest.fixture()
async def async_client(_reset_db_globals):
    """Provide an httpx AsyncClient wired to the FastAPI app."""
    from backend.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture()
def mock_chat_completion():
    """Mock the LLM chat_completion so tests don't call real APIs."""
    with patch("backend.app.agent.loop.chat_completion", new_callable=AsyncMock) as mock:
        mock.return_value = "Hello from Bond!"
        yield mock
