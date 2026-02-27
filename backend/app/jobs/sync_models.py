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


_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "groq": "Groq",
    "mistral": "Mistral",
    "xai": "xAI",
    "openrouter": "OpenRouter",
}


def _parse_openai_compat(data: dict, prefix: str) -> list[tuple[str, str]]:
    """Parse OpenAI-compatible /v1/models response. Filter to chat models."""
    label = _PROVIDER_LABELS.get(prefix, prefix.title())
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
        models.append((litellm_id, _prefixed_name(label, name)))
    return models


# ── Provider-specific fetchers ────────────────────────────────


_ANTHROPIC_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models"


def _prefixed_name(provider_label: str, name: str) -> str:
    """Add provider prefix to display name: 'Anthropic — Claude Sonnet 4'."""
    return f"{provider_label} — {name}"


def _model_id_to_display(mid: str) -> str:
    """Convert a model ID like 'claude-opus-4-6' to 'Claude Opus 4.6'.

    Handles patterns:
      claude-opus-4-6       → Claude Opus 4.6
      claude-opus-4-20250514 → Claude Opus 4 (2025-05-14)
      claude-haiku-4-5-20251001 → Claude Haiku 4.5 (2025-10-01)
      claude-3-haiku-20240307 → Claude 3 Haiku (2024-03-07)
    """
    import re
    # Extract date suffix (8 digits)
    date_match = re.search(r'-(\d{4})(\d{2})(\d{2})$', mid)
    date_str = ""
    base = mid
    if date_match:
        date_str = f" ({date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)})"
        base = mid[:date_match.start()]

    parts = base.split("-")
    # Rebuild: words get title-cased, consecutive digit groups get joined with dots
    result = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.isdigit():
            # Collect consecutive digit parts → join with dots (e.g. 4-5 → 4.5)
            nums = [p]
            while i + 1 < len(parts) and parts[i + 1].isdigit() and len(parts[i + 1]) <= 2:
                i += 1
                nums.append(parts[i])
            result.append(".".join(nums))
        else:
            result.append(p.title())
        i += 1

    return " ".join(result) + date_str


async def _scrape_anthropic_models(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    """Scrape model IDs from Anthropic's docs page."""
    import re
    resp = await client.get(_ANTHROPIC_DOCS_URL, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    ids: set[str] = set()
    # Match model IDs like claude-opus-4-6, claude-sonnet-4-20250514, claude-3-haiku-20240307
    for m in re.finditer(r'claude-(?:opus|sonnet|haiku)-[\w.-]+', html):
        mid = m.group()
        if any(x in mid for x in ('prompting', 'analytics', 'code', 'microsoft', 'amazon', 'vertex', 'foundry')):
            continue
        ids.add(mid)
    for m in re.finditer(r'claude-[\d.]+-(?:opus|sonnet|haiku)(?:-[\d]+)?', html):
        ids.add(m.group())

    # Filter: keep short aliases and dated versions, skip -v1 Bedrock variants
    models = []
    for mid in sorted(ids):
        if mid.endswith("-v1"):
            continue
        display = _model_id_to_display(mid)
        models.append((f"anthropic/{mid}", _prefixed_name("Anthropic", display)))

    return models


async def _fetch_anthropic(client: httpx.AsyncClient, api_key: str, key_type: str = "api_key") -> list[tuple[str, str]]:
    """Fetch Anthropic models via API (api_key) or docs scrape (OAuth)."""
    if key_type == "api_key":
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
            models.append((f"anthropic/{mid}", _prefixed_name("Anthropic", display)))
        return models

    # OAuth token — scrape from docs
    logger.info("sync_models: Anthropic using OAuth token, scraping model list from docs")
    return await _scrape_anthropic_models(client)


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
        models.append((litellm_id, _prefixed_name("Google", display)))
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


async def _get_api_key(db: AsyncSession, setting_key: str, provider: str) -> tuple[str, str] | None:
    """Read an API key from settings DB, then vault, then environment.

    Returns (key_value, key_type) or None. key_type is 'api_key' or 'oauth_token'.
    """
    # 1. Check settings DB (encrypted) — includes key_type column
    result = await db.execute(
        text("SELECT value, key_type FROM settings WHERE key = :key"), {"key": setting_key}
    )
    row = result.fetchone()
    if row and row[0]:
        raw = row[0]
        key_type = row[1] if row[1] else "api_key"
        try:
            decrypted = decrypt_value(raw)
            if decrypted:
                return (decrypted, key_type)
        except Exception:
            if not is_encrypted(raw):
                return (raw, key_type)

    # 2. Check vault + environment via Vault
    try:
        vault = Vault()
        result = vault.get_api_key_with_type(provider)
        if result:
            return result
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
                result = await _get_api_key(db, provider_conf["setting_key"], provider_name)
                if not result:
                    continue
                api_key, key_type = result

                try:
                    fetch_fn = provider_conf["fetch"]
                    # Anthropic needs key_type to skip API call for OAuth tokens
                    if provider_name == "anthropic":
                        models = await fetch_fn(client, api_key, key_type)
                    else:
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
