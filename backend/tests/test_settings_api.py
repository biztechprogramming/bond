"""Tests for the settings API endpoints."""

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
async def settings_client(_clear_settings_cache):
    """Client with fully migrated DB (settings + embedding_configs tables)."""
    import backend.app.db.session as sess

    # Reset DB globals
    sess._engine = None
    sess._session_factory = None

    tmpdir = tempfile.mkdtemp(prefix="bond_settings_test_")
    db_path = Path(tmpdir) / "test.db"
    os.environ["BOND_DATABASE_PATH"] = str(db_path)

    # Apply migrations to create tables
    async with aiosqlite.connect(db_path) as db:
        await _apply_sql(db, MIGRATIONS_DIR / "000001_init.up.sql")
        await _apply_sql(db, MIGRATIONS_DIR / "000002_knowledge_store.up.sql")

    # Clear settings cache again after env change
    from backend.app.config import get_settings
    get_settings.cache_clear()

    # Reset engine to pick up new DB path
    sess._engine = None
    sess._session_factory = None

    from backend.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    # Cleanup
    sess._engine = None
    sess._session_factory = None


# ── CRUD for settings ──


@pytest.mark.asyncio
async def test_get_all_settings_empty(settings_client):
    resp = await settings_client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_put_and_get_setting(settings_client):
    resp = await settings_client.put(
        "/api/v1/settings/test.key",
        json={"value": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "test.key"
    assert data["value"] == "hello"

    # GET it back
    resp = await settings_client.get("/api/v1/settings/test.key")
    assert resp.status_code == 200
    assert resp.json()["value"] == "hello"


@pytest.mark.asyncio
async def test_get_setting_not_found(settings_client):
    resp = await settings_client.get("/api/v1/settings/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_existing_setting(settings_client):
    await settings_client.put("/api/v1/settings/k1", json={"value": "v1"})
    await settings_client.put("/api/v1/settings/k1", json={"value": "v2"})
    resp = await settings_client.get("/api/v1/settings/k1")
    assert resp.json()["value"] == "v2"


@pytest.mark.asyncio
async def test_get_all_settings_returns_multiple(settings_client):
    await settings_client.put("/api/v1/settings/a", json={"value": "1"})
    await settings_client.put("/api/v1/settings/b", json={"value": "2"})
    resp = await settings_client.get("/api/v1/settings")
    data = resp.json()
    assert data["a"] == "1"
    assert data["b"] == "2"


# ── API key masking ──


@pytest.mark.asyncio
async def test_api_key_masking_voyage(settings_client):
    await settings_client.put(
        "/api/v1/settings/embedding.api_key.voyage",
        json={"value": "sk-voyage-1234567890abcdef"},
    )
    resp = await settings_client.get("/api/v1/settings/embedding.api_key.voyage")
    masked = resp.json()["value"]
    assert masked.endswith("cdef")
    assert masked.startswith("*")
    assert "sk-voyage" not in masked


@pytest.mark.asyncio
async def test_api_key_masking_gemini(settings_client):
    await settings_client.put(
        "/api/v1/settings/embedding.api_key.gemini",
        json={"value": "AIzaSyAbcdefghijklmnop"},
    )
    resp = await settings_client.get("/api/v1/settings/embedding.api_key.gemini")
    masked = resp.json()["value"]
    assert masked.endswith("mnop")
    assert "AIzaSy" not in masked


@pytest.mark.asyncio
async def test_api_key_masking_in_all_settings(settings_client):
    await settings_client.put(
        "/api/v1/settings/embedding.api_key.voyage",
        json={"value": "sk-voyage-secret123"},
    )
    resp = await settings_client.get("/api/v1/settings")
    data = resp.json()
    assert data["embedding.api_key.voyage"].endswith("t123")
    assert "sk-voyage" not in data["embedding.api_key.voyage"]


# ── Embedding model listing ──


@pytest.mark.asyncio
async def test_get_embedding_models(settings_client):
    resp = await settings_client.get("/api/v1/settings/embedding/models")
    assert resp.status_code == 200
    models = resp.json()
    assert len(models) == 8  # 4 voyage + 3 qwen + 1 gemini

    # Check structure
    m = models[0]
    assert "model_name" in m
    assert "family" in m
    assert "provider" in m
    assert "max_dimension" in m
    assert "supported_dimensions" in m
    assert isinstance(m["supported_dimensions"], list)
    assert "supports_local" in m
    assert "supports_api" in m
    assert "is_default" in m


@pytest.mark.asyncio
async def test_embedding_models_families(settings_client):
    resp = await settings_client.get("/api/v1/settings/embedding/models")
    models = resp.json()
    families = {m["family"] for m in models}
    assert families == {"voyage4", "qwen3", "gemini"}


@pytest.mark.asyncio
async def test_embedding_models_default(settings_client):
    resp = await settings_client.get("/api/v1/settings/embedding/models")
    defaults = [m for m in resp.json() if m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["model_name"] == "voyage-4-nano"


# ── Embedding current config ──


@pytest.mark.asyncio
async def test_get_current_embedding_defaults(settings_client):
    resp = await settings_client.get("/api/v1/settings/embedding/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "voyage-4-nano"
    assert data["dimension"] == 1024
    assert data["execution_mode"] == "auto"
    assert data["has_voyage_key"] is False
    assert data["has_gemini_key"] is False


# ── Embedding config update ──


@pytest.mark.asyncio
async def test_update_embedding_valid(settings_client):
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-nano", "dimension": 512, "execution_mode": "local"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"] == "voyage-4-nano"
    assert data["dimension"] == 512
    assert data["execution_mode"] == "local"


@pytest.mark.asyncio
async def test_update_embedding_invalid_model(settings_client):
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "nonexistent-model", "dimension": 512, "execution_mode": "auto"},
    )
    assert resp.status_code == 400
    assert "Unknown model" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_embedding_invalid_dimension(settings_client):
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-nano", "dimension": 9999, "execution_mode": "auto"},
    )
    assert resp.status_code == 400
    assert "not supported" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_embedding_invalid_execution_mode(settings_client):
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-nano", "dimension": 1024, "execution_mode": "invalid"},
    )
    assert resp.status_code == 400
    assert "execution_mode" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_embedding_local_not_supported(settings_client):
    # voyage-4-lite does not support local
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-lite", "dimension": 1024, "execution_mode": "local"},
    )
    assert resp.status_code == 400
    assert "does not support local" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_embedding_api_not_supported(settings_client):
    # Qwen3-Embedding-0.6B does not support API
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "Qwen3-Embedding-0.6B", "dimension": 512, "execution_mode": "api"},
    )
    assert resp.status_code == 400
    assert "does not support API" in resp.json()["detail"]


# ── Family switch warning ──


@pytest.mark.asyncio
async def test_family_switch_warning(settings_client):
    # First set a voyage model
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-nano", "dimension": 1024, "execution_mode": "local"},
    )
    assert resp.status_code == 200
    assert "warning" not in resp.json()

    # Switch to qwen3 family
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "Qwen3-Embedding-0.6B", "dimension": 512, "execution_mode": "local"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "warning" in data
    assert "voyage4" in data["warning"]
    assert "qwen3" in data["warning"]
    assert "re-generated" in data["warning"]


@pytest.mark.asyncio
async def test_same_family_no_warning(settings_client):
    # Set voyage-4-nano
    await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4-nano", "dimension": 1024, "execution_mode": "local"},
    )
    # Switch to voyage-4 (same family)
    resp = await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "voyage-4", "dimension": 1024, "execution_mode": "api"},
    )
    assert resp.status_code == 200
    assert "warning" not in resp.json()


@pytest.mark.asyncio
async def test_embedding_persists_after_update(settings_client):
    await settings_client.put(
        "/api/v1/settings/embedding",
        json={"model": "Qwen3-Embedding-4B", "dimension": 2560, "execution_mode": "local"},
    )
    resp = await settings_client.get("/api/v1/settings/embedding/current")
    data = resp.json()
    assert data["model"] == "Qwen3-Embedding-4B"
    assert data["dimension"] == 2560
    assert data["execution_mode"] == "local"
