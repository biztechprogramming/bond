"""Tests for the embeddings engine and providers."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


# ── Provider stubs ──


@pytest.mark.asyncio
async def test_local_provider_returns_zero_vectors():
    from backend.app.foundations.embeddings.local import LocalEmbeddingProvider

    provider = LocalEmbeddingProvider(model_name="voyage-4-nano", dimension=128)
    assert provider.model_name == "voyage-4-nano"
    assert provider.dimension == 128

    vectors = await provider.embed(["hello", "world"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 128
    assert all(v == 0.0 for v in vectors[0])


@pytest.mark.asyncio
async def test_local_provider_embed_query():
    from backend.app.foundations.embeddings.local import LocalEmbeddingProvider

    provider = LocalEmbeddingProvider(dimension=64)
    vec = await provider.embed_query("test query")
    assert len(vec) == 64
    assert all(v == 0.0 for v in vec)


@pytest.mark.asyncio
async def test_voyage_provider_no_key_returns_zeros():
    from backend.app.foundations.embeddings.voyage import VoyageAPIProvider

    provider = VoyageAPIProvider(model_name="voyage-4-nano", dimension=256, api_key=None)
    assert provider.model_name == "voyage-4-nano"
    assert provider.dimension == 256

    vectors = await provider.embed(["hello"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 256
    assert all(v == 0.0 for v in vectors[0])

    vec = await provider.embed_query("test")
    assert len(vec) == 256


@pytest.mark.asyncio
async def test_gemini_provider_returns_zeros():
    from backend.app.foundations.embeddings.gemini import GeminiAPIProvider

    provider = GeminiAPIProvider(dimension=768)
    assert provider.model_name == "gemini-embedding-001"

    vectors = await provider.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 768 for v in vectors)

    vec = await provider.embed_query("query")
    assert len(vec) == 768


# ── Engine initialization ──


def _full_settings(**overrides):
    """Build a complete settings dict with all required keys."""
    defaults = {
        "embedding.model": "voyage-4-nano",
        "embedding.output_dimension": "1024",
        "embedding.execution_mode": "local",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_engine_errors_on_missing_settings(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine, EmbeddingConfigError

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    # Empty settings should raise
    with pytest.raises(EmbeddingConfigError, match="embedding.model is not configured"):
        EmbeddingEngine(settings={}, db_engine=engine)

    # Missing dimension
    with pytest.raises(EmbeddingConfigError, match="embedding.output_dimension is not configured"):
        EmbeddingEngine(settings={"embedding.model": "test"}, db_engine=engine)

    # Missing execution_mode
    with pytest.raises(EmbeddingConfigError, match="embedding.execution_mode is not configured"):
        EmbeddingEngine(
            settings={"embedding.model": "test", "embedding.output_dimension": "512"},
            db_engine=engine,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_errors_on_api_without_key(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine, EmbeddingConfigError

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    with pytest.raises(EmbeddingConfigError, match="no Voyage API key"):
        EmbeddingEngine(settings=_full_settings(**{"embedding.execution_mode": "api"}), db_engine=engine)

    with pytest.raises(EmbeddingConfigError, match="no Gemini API key"):
        EmbeddingEngine(settings=_full_settings(**{"embedding.execution_mode": "gemini"}), db_engine=engine)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_errors_on_unknown_execution_mode(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine, EmbeddingConfigError

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    with pytest.raises(EmbeddingConfigError, match="Unknown execution_mode 'bogus'"):
        EmbeddingEngine(settings=_full_settings(**{"embedding.execution_mode": "bogus"}), db_engine=engine)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_selects_local(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.local import LocalEmbeddingProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    emb_engine = EmbeddingEngine(settings=_full_settings(), db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.requested_dimension == 1024

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_selects_voyage_with_api_key(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.voyage import VoyageAPIProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    settings = _full_settings(**{
        "embedding.api_key.voyage": "test-key-123",
        "embedding.model": "voyage-4-lite",
        "embedding.output_dimension": "512",
        "embedding.execution_mode": "api",
    })
    emb_engine = EmbeddingEngine(settings=settings, db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, VoyageAPIProvider)
    assert provider.dimension == 512
    assert provider.model_name == "voyage-4-lite"

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_selects_gemini_provider(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.gemini import GeminiAPIProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    settings = _full_settings(**{
        "embedding.execution_mode": "gemini",
        "embedding.api_key.gemini": "test-gemini-key",
        "embedding.model": "gemini-embedding-001",
    })
    emb_engine = EmbeddingEngine(settings=settings, db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, GeminiAPIProvider)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_auto_prefers_voyage(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.voyage import VoyageAPIProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    settings = _full_settings(**{
        "embedding.execution_mode": "auto",
        "embedding.api_key.voyage": "voyage-key",
        "embedding.api_key.gemini": "gemini-key",
    })
    emb_engine = EmbeddingEngine(settings=settings, db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, VoyageAPIProvider)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_auto_falls_to_gemini_without_voyage(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.gemini import GeminiAPIProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    settings = _full_settings(**{
        "embedding.execution_mode": "auto",
        "embedding.api_key.gemini": "gemini-key",
    })
    emb_engine = EmbeddingEngine(settings=settings, db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, GeminiAPIProvider)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_auto_falls_to_local_without_keys(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine
    from backend.app.foundations.embeddings.local import LocalEmbeddingProvider

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    settings = _full_settings(**{"embedding.execution_mode": "auto"})
    emb_engine = EmbeddingEngine(settings=settings, db_engine=engine)
    provider = emb_engine.get_provider()
    assert isinstance(provider, LocalEmbeddingProvider)

    await engine.dispose()


@pytest.mark.asyncio
async def test_engine_embed_delegates_to_provider(tmp_path):
    from backend.app.foundations.embeddings.engine import EmbeddingEngine

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )

    emb_engine = EmbeddingEngine(settings=_full_settings(), db_engine=engine)
    vectors = await emb_engine.embed(["hello", "world"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024

    query_vec = await emb_engine.embed_query("test")
    assert len(query_vec) == 1024

    await engine.dispose()
