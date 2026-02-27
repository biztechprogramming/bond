"""Sync available LLM models from configured providers into the llm_models table."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.app.core.crypto import decrypt_value, is_encrypted
from backend.app.core.vault import Vault

logger = logging.getLogger(__name__)

# Provider definitions: settings key → (API URL builder, auth builder, response parser, litellm prefix)
# Each parser returns list of (model_id_raw, display_name) tuples.


def _bearer_auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _parse_openai_compat(data: dict, prefix: str) -> list[tuple[str, str]]:
    """Parse OpenAI-compatible /v1/models response. Filter to chat models."""
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        # Skip embedding, tts, whisper, image, moderation models
        skip_patterns = ("embed", "tts", "whisper", "dall-e", "davinci", "babbage",
                         "moderation", "search", "similarity", "code-", "text-",
                         "curie", "ada")
        if any(p in mid.lower() for p in skip_patterns):
            continue
        litellm_id = f"{prefix}/{mid}" if prefix else mid
        name = m.get("name", mid)
        # Use model id as name if no display name
        if name == mid:
            name = mid.replace("-", " ").title()
        models.append((litellm_id, name))
    return models


# ── Provider-specific fetchers ────────────────────────────────


async def _fetch_anthropic(client: httpx.AsyncClient, api_key: str) -> list[tuple[str, str]]:
    """Fetch Anthropic models."""
    resp = await client.get(
        "https://api.anthropic.com/v1/models?limit=100",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        display = m.get("display_name", mid)
        # Anthropic models: use bare ID for utility (litellm handles it),
        # and anthropic/ prefix for explicit chat usage
        models.append((mid, display))
        models.append((f"anthropic/{mid}", f"{display} (anthropic/)"))
    return models


async def _fetch_google(client: httpx.AsyncClient, api_key: str) -> list[tuple[str, str]]:
    """Fetch Google/Gemini models."""
    resp = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
    )
    resp.raise_for_status()
    data = resp.json()
    models = []
    for m in data.get("models", []):
        # name format: "models/gemini-2.5-flash"
        raw_name = m.get("name", "")
        mid = raw_name.replace("models/", "")
        display = m.get("displayName", mid)
        # Only include generateContent-capable models
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        litellm_id = f"gemini/{mid}"
        models.append((litellm_id, display))
    return models


async def _fetch_openai_compat(
    client: httpx.AsyncClient,
    api_key: str,
    url: str,
    prefix: str,
) -> list[tuple[str, str]]:
    """Fetch from any OpenAI-compatible /v1/models endpoint."""
    resp = await client.get(url, headers=_bearer_auth(api_key))
    resp.raise_for_status()
    return _parse_openai_compat(resp.json(), prefix)


# ── Provider registry ─────────────────────────────────────────

_PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "setting_key": "llm.api_key.anthropic",
        "fetch": _fetch_anthropic,
    },
    "google": {
        "setting_key": "llm.api_key.google",
        "fetch": _fetch_google,
    },
    "openai": {
        "setting_key": "llm.api_key.openai",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://api.openai.com/v1/models", "openai"),
    },
    "deepseek": {
        "setting_key": "llm.api_key.deepseek",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://api.deepseek.com/models", "deepseek"),
    },
    "groq": {
        "setting_key": "llm.api_key.groq",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://api.groq.com/openai/v1/models", "groq"),
    },
    "mistral": {
        "setting_key": "llm.api_key.mistral",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://api.mistral.ai/v1/models", "mistral"),
    },
    "xai": {
        "setting_key": "llm.api_key.xai",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://api.x.ai/v1/models", "xai"),
    },
    "openrouter": {
        "setting_key": "llm.api_key.openrouter",
        "fetch": lambda c, k: _fetch_openai_compat(c, k, "https://openrouter.ai/api/v1/models", "openrouter"),
    },
}


async def _get_api_key(db: AsyncSession, setting_key: str, provider: str) -> str | None:
    """Read an API key from settings DB, then vault, then environment."""
    # 1. Check settings DB (encrypted)
    result = await db.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": setting_key}
    )
    row = result.fetchone()
    if row and row[0]:
        raw = row[0]
        try:
            decrypted = decrypt_value(raw)
            if decrypted:
                return decrypted
        except Exception:
            if not is_encrypted(raw):
                return raw

    # 2. Check vault + environment via Vault.get_api_key()
    try:
        vault = Vault()
        key = vault.get_api_key(provider)
        if key:
            return key
    except Exception:
        pass

    return None


async def sync_models(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Sync LLM model catalog from all configured providers."""
    total_synced = 0
    total_errors = 0

    async with session_factory() as db:
        # Ensure table exists (in case migration hasn't run yet)
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS llm_models ("
            "id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_id TEXT NOT NULL, "
            "name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'chat', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "UNIQUE(provider, model_id))"
        ))
        await db.commit()

        async with httpx.AsyncClient(timeout=30.0) as client:
            for provider_name, provider_conf in _PROVIDERS.items():
                api_key = await _get_api_key(db, provider_conf["setting_key"], provider_name)
                if not api_key:
                    continue

                try:
                    fetch_fn = provider_conf["fetch"]
                    models = await fetch_fn(client, api_key)

                    if not models:
                        logger.info("sync_models: %s returned 0 models", provider_name)
                        continue

                    # Upsert models
                    for model_id, name in models:
                        composite_id = f"{provider_name}:{model_id}"
                        await db.execute(
                            text(
                                "INSERT INTO llm_models (id, provider, model_id, name) "
                                "VALUES (:id, :provider, :model_id, :name) "
                                "ON CONFLICT(provider, model_id) DO UPDATE SET "
                                "name = :name, updated_at = CURRENT_TIMESTAMP"
                            ),
                            {
                                "id": composite_id,
                                "provider": provider_name,
                                "model_id": model_id,
                                "name": name,
                            },
                        )

                    await db.commit()
                    total_synced += len(models)
                    logger.info(
                        "sync_models: %s — %d models synced", provider_name, len(models)
                    )

                except httpx.HTTPStatusError as e:
                    total_errors += 1
                    logger.warning(
                        "sync_models: %s HTTP %d: %s",
                        provider_name, e.response.status_code, e.response.text[:200],
                    )
                except Exception:
                    total_errors += 1
                    logger.exception("sync_models: %s failed", provider_name)

    logger.info(
        "sync_models complete: %d models synced, %d provider errors",
        total_synced, total_errors,
    )
