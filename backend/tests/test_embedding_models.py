"""Diagnostic tests for embedding model seeding, querying, and column-name alignment."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.settings_service import SettingsService

# Mirror of _EMBEDDING_SEED from settings_service.py (can't import directly due to
# /bond vs /workspace/bond sys.path resolution — the installed copy may be stale).
_EMBEDDING_SEED = [
    ("voyage-4-nano", "voyage4", "voyage", 2048, "[256,512,1024,2048]", True, False, True),
    ("voyage-4-lite", "voyage4", "voyage", 2048, "[256,512,1024,2048]", False, True, False),
    ("voyage-4", "voyage4", "voyage", 2048, "[256,512,1024,2048]", False, True, False),
    ("voyage-4-large", "voyage4", "voyage", 2048, "[256,512,1024,2048]", False, True, False),
    ("Qwen3-Embedding-0.6B", "qwen3", "huggingface", 1024, "[256,512,1024]", True, False, False),
    ("Qwen3-Embedding-4B", "qwen3", "huggingface", 2560, "[256,512,1024,2560]", True, False, False),
    ("Qwen3-Embedding-8B", "qwen3", "huggingface", 4096, "[256,512,1024,4096]", True, False, False),
    ("gemini-embedding-001", "gemini", "google", 768, "[768]", False, True, False),
]


# ── Helpers ──


def _make_stdb_sql_response(rows: list[dict], columns: list[str]) -> list:
    """Build a SpacetimeDB HTTP SQL response payload.

    SpacetimeDB returns column names inside an Option wrapper:
      {"name": {"some": "column_name"}, "algebraic_type": ...}
    """
    return [
        {
            "schema": {
                "elements": [
                    {"name": {"some": col}, "algebraic_type": {}} for col in columns
                ]
            },
            "rows": [[row[col] for col in columns] for row in rows],
        }
    ]


EMBEDDING_COLUMNS = [
    "model_name",
    "family",
    "provider",
    "max_dimension",
    "supported_dimensions",
    "supports_local",
    "supports_api",
    "is_default",
]


@pytest.mark.asyncio
async def test_stdb_client_parses_snake_case_columns():
    """StdbClient.query() correctly parses snake_case column names from SpacetimeDB."""
    from backend.app.core.spacetimedb import StdbClient

    sample_rows = [
        {
            "model_name": "voyage-4-nano",
            "family": "voyage4",
            "provider": "voyage",
            "max_dimension": 2048,
            "supported_dimensions": "[256,512,1024,2048]",
            "supports_local": True,
            "supports_api": False,
            "is_default": True,
        }
    ]

    payload = _make_stdb_sql_response(sample_rows, EMBEDDING_COLUMNS)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    client = StdbClient(base_url="http://fake", module_name="test", token="tok")
    # httpx AsyncClient.post is async, so mock it as a coroutine
    async def _fake_post(*args, **kwargs):
        return mock_resp

    with patch.object(client._client, "post", side_effect=_fake_post):
        rows = await client.query("SELECT * FROM embedding_models")

    assert len(rows) == 1
    row = rows[0]
    assert row["model_name"] == "voyage-4-nano"
    assert row["max_dimension"] == 2048
    assert row["supports_local"] is True
    assert row["supports_api"] is False
    assert row["is_default"] is True


@pytest.mark.asyncio
async def test_get_embedding_models_returns_parsed_models(mock_stdb):
    """SettingsService.get_embedding_models() returns all seeded models."""
    svc = SettingsService.__new__(SettingsService)
    svc._stdb = mock_stdb

    models = await svc.get_embedding_models()
    assert len(models) == 8

    names = {m.model_name for m in models}
    assert "voyage-4-nano" in names
    assert "Qwen3-Embedding-0.6B" in names
    assert "gemini-embedding-001" in names

    # Check default
    defaults = [m for m in models if m.is_default]
    assert len(defaults) == 1
    assert defaults[0].model_name == "voyage-4-nano"


@pytest.mark.asyncio
async def test_seed_embedding_models_calls_reducer(mock_stdb):
    """seed_embedding_models() calls set_embedding_model reducer with correct arg order."""
    # Clear pre-seeded data so seed actually runs
    mock_stdb.embedding_models = []

    svc = SettingsService.__new__(SettingsService)
    svc._stdb = mock_stdb

    await svc.seed_embedding_models()

    assert len(mock_stdb.embedding_models) == len(_EMBEDDING_SEED)

    # Verify first seed entry (voyage-4-nano) matches expected structure
    nano = next(m for m in mock_stdb.embedding_models if m["model_name"] == "voyage-4-nano")
    assert nano["family"] == "voyage4"
    assert nano["provider"] == "voyage"
    assert nano["max_dimension"] == 2048
    assert nano["supported_dimensions"] == "[256,512,1024,2048]"
    assert nano["supports_local"] is True
    assert nano["supports_api"] is False
    assert nano["is_default"] is True


@pytest.mark.asyncio
async def test_seed_skips_when_models_exist(mock_stdb):
    """seed_embedding_models() is a no-op when models already exist."""
    svc = SettingsService.__new__(SettingsService)
    svc._stdb = mock_stdb

    original_count = len(mock_stdb.embedding_models)
    await svc.seed_embedding_models()
    assert len(mock_stdb.embedding_models) == original_count


@pytest.mark.asyncio
async def test_reducer_arg_order_matches_schema():
    """Verify _EMBEDDING_SEED arg positions match the setEmbeddingModel reducer params.

    Reducer signature (from index.ts):
      modelName, family, provider, maxDimension, supportedDimensions,
      supportsLocal, supportsApi, isDefault
    """
    for entry in _EMBEDDING_SEED:
        name, family, provider, max_dim, dims, local, api, default = entry
        assert isinstance(name, str), f"modelName should be str, got {type(name)}"
        assert isinstance(family, str), f"family should be str, got {type(family)}"
        assert isinstance(provider, str), f"provider should be str, got {type(provider)}"
        assert isinstance(max_dim, int), f"maxDimension should be int, got {type(max_dim)}"
        assert isinstance(dims, str), f"supportedDimensions should be str, got {type(dims)}"
        assert isinstance(local, bool), f"supportsLocal should be bool, got {type(local)}"
        assert isinstance(api, bool), f"supportsApi should be bool, got {type(api)}"
        assert isinstance(default, bool), f"isDefault should be bool, got {type(default)}"
        # Validate dims is valid JSON array
        parsed = json.loads(dims)
        assert isinstance(parsed, list)


@pytest.mark.asyncio
async def test_query_error_raises():
    """StdbClient.query() raises on HTTP errors instead of silently returning []."""
    from backend.app.core.spacetimedb import StdbClient

    client = StdbClient(base_url="http://fake", module_name="test", token="tok")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    async def _fake_post(*args, **kwargs):
        return mock_resp

    with patch.object(client._client, "post", side_effect=_fake_post):
        with pytest.raises(RuntimeError, match="SpacetimeDB SQL failed"):
            await client.query("SELECT * FROM embedding_models")
