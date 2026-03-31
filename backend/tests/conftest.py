"""Shared test fixtures."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

# Set BOND_HOME to a temp dir before any config import
_tmpdir = tempfile.mkdtemp(prefix="bond_test_")
os.environ["BOND_HOME"] = _tmpdir
os.environ["BOND_DATABASE_PATH"] = str(Path(_tmpdir) / "test.db")

# Create a minimal bond.json so config loading doesn't fail when Path.exists is patched
_bond_json = Path(_tmpdir) / "bond.json"
_bond_json.write_text(json.dumps({}))

# Path to all migration files, in order
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
_ALL_MIGRATIONS = sorted(_MIGRATIONS_DIR.glob("*.up.sql"))


async def apply_all_migrations(db: aiosqlite.Connection) -> None:
    """Apply every migration in order to the given aiosqlite connection."""
    for sql_file in _ALL_MIGRATIONS:
        sql = sql_file.read_text()
        await db.executescript(sql)


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


class _InMemoryStdb:
    """In-memory SpacetimeDB mock that stores settings and provider keys in dicts."""

    def __init__(self) -> None:
        self.settings: dict[str, str] = {}
        self.provider_api_keys: dict[str, dict] = {}
        self.providers: list[dict] = [
            {"id": "anthropic", "display_name": "Anthropic", "is_enabled": True, "litellm_prefix": "anthropic"},
            {"id": "openai", "display_name": "OpenAI", "is_enabled": True, "litellm_prefix": "openai"},
            {"id": "google", "display_name": "Google", "is_enabled": True, "litellm_prefix": "google"},
        ]
        self.llm_models: list[dict] = []

    async def query(self, sql: str) -> list[dict]:
        sql_upper = sql.strip().upper()
        if "FROM SETTINGS" in sql_upper:
            if "WHERE KEY =" in sql_upper:
                # Extract key value
                key = sql.split("'")[1]
                if key in self.settings:
                    return [{"key": key, "value": self.settings[key]}]
                return []
            return [{"key": k, "value": v} for k, v in self.settings.items()]
        if "FROM PROVIDERS" in sql_upper:
            return self.providers
        if "FROM PROVIDER_API_KEYS" in sql_upper:
            return [{"provider_id": k, "key_type": v.get("key_type", "")} for k, v in self.provider_api_keys.items()]
        if "FROM LLM_MODELS" in sql_upper:
            return self.llm_models
        return []

    async def call_reducer(self, reducer: str, args: list) -> bool:
        if reducer == "set_setting":
            self.settings[args[0]] = args[1]
        elif reducer == "set_provider_api_key":
            self.provider_api_keys[args[0]] = {
                "encrypted": args[1], "key_type": args[2],
                "created_at": args[3], "updated_at": args[4],
            }
        elif reducer == "delete_setting":
            self.settings.pop(args[0], None)
        return True


@pytest.fixture()
def mock_stdb():
    """Patch get_stdb to return an in-memory store for all SpacetimeDB calls."""
    stdb = _InMemoryStdb()
    with patch("backend.app.core.spacetimedb.get_stdb", return_value=stdb):
        yield stdb


@pytest.fixture()
def mock_chat_completion():
    """Mock the LLM so tests don't call real APIs.

    Patches both the simple chat_completion path and litellm.acompletion
    for the tool-use loop path.
    """
    from unittest.mock import MagicMock

    # Build a mock litellm response for the tool-use path
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
