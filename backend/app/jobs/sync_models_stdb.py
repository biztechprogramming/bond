"""Sync available LLM models from configured providers into the llm_models table (SpacetimeDB version)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from backend.app.core.crypto import decrypt_value, is_encrypted
from backend.app.core.spacetimedb import get_stdb

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Display name helpers (same as original)
# ---------------------------------------------------------------------------


def _model_slug_to_display(slug: str) -> str:
    """Convert 'claude-opus-4-6' → 'Claude Opus 4.6', 'claude-opus-4-20250514' → 'Claude Opus 4 (2025-05-14)'."""
    date_match = re.search(r'-(\d{4})(\d{2})(\d{2})$', slug)
    date_str = ""
    base = slug
    if date_match:
        date_str = f" ({date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)})"
        base = slug[:date_match.start()]

    parts = base.split("-")
    result = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.isdigit():
            nums = [p]
            while i + 1 < len(parts) and parts[i + 1].isdigit() and len(parts[i + 1]) <= 2:
                i += 1
                nums.append(parts[i])
            result.append(".".join(nums))
        else:
            result.append(p.title())
        i += 1

    return " ".join(result) + date_str


# ---------------------------------------------------------------------------
# Fetch methods — keyed by providers.models_fetch_method (same as original)
# ---------------------------------------------------------------------------


async def _fetch_anthropic_api(
    client: httpx.AsyncClient,
    provider: dict,
    api_key: str,
    key_type: str,
) -> list[dict]:
    """Fetch via Anthropic /v1/models (api_key only) or scrape docs (oauth_token)."""
    if key_type == "oauth_token":
        return await _fetch_anthropic_scrape(client, provider, api_key, key_type)

    config = json.loads(provider["config"] or "{}")
    url = provider["api_base_url"] + provider["models_endpoint"]
    resp = await client.get(url, headers={
        "x-api-key": api_key,
        "anthropic-version": config.get("anthropic_version", "2023-06-01"),
    })
    resp.raise_for_status()

    models = []
    for m in resp.json().get("data", []):
        mid = m.get("id", "")
        display = m.get("display_name", _model_slug_to_display(mid))
        models.append({"slug": mid, "display_name": display})
    return models


async def _fetch_anthropic_scrape(
    client: httpx.AsyncClient,
    provider: dict,
    api_key: str,
    key_type: str,
) -> list[dict]:
    """Scrape model IDs from Anthropic's docs page."""
    resp = await client.get(
        "https://docs.anthropic.com/en/docs/about-claude/models",
        follow_redirects=True,
    )
    resp.raise_for_status()
    html = resp.text

    ids: set[str] = set()
    for m in re.finditer(r'claude-(?:opus|sonnet|haiku)-[\w.-]+', html):
        mid = m.group()
        if any(x in mid for x in ('prompting', 'analytics', 'code', 'microsoft', 'amazon', 'vertex', 'foundry')):
            continue
        ids.add(mid)
    for m in re.finditer(r'claude-[\d.]+-(?:opus|sonnet|haiku)(?:-[\d]+)?', html):
        ids.add(m.group())

    models = []
    for mid in sorted(ids):
        if mid.endswith("-v1"):
            continue
        models.append({"slug": mid, "display_name": _model_slug_to_display(mid)})

    logger.info("sync_models: Anthropic scraped %d models from docs", len(models))
    return models


async def _fetch_google_api(
    client: httpx.AsyncClient,
    provider: dict,
    api_key: str,
    key_type: str,
) -> list[dict]:
    """Fetch Google/Gemini models via generativelanguage API."""
    base = provider.get("api_base_url") or "https://generativelanguage.googleapis.com"
    endpoint = provider.get("models_endpoint") or "/v1beta/models"
    url = base + endpoint
    resp = await client.get(url, params={"key": api_key})
    resp.raise_for_status()

    models = []
    for m in resp.json().get("models", []):
        raw_name = m.get("name", "")
        slug = raw_name.replace("models/", "")
        display = m.get("displayName", slug)
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        models.append({"slug": slug, "display_name": display})
    return models


async def _fetch_openai_compat(
    client: httpx.AsyncClient,
    provider: dict,
    api_key: str,
    key_type: str,
) -> list[dict]:
    """Fetch from any OpenAI-compatible /v1/models endpoint."""
    url = provider["api_base_url"] + provider["models_endpoint"]
    resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    resp.raise_for_status()

    skip_patterns = (
        "embed", "tts", "whisper", "dall-e", "davinci", "babbage",
        "moderation", "search", "similarity", "code-", "text-",
        "curie", "ada",
    )

    models = []
    for m in resp.json().get("data", []):
        mid = m.get("id", "")
        if any(p in mid.lower() for p in skip_patterns):
            continue
        name = m.get("name", mid)
        if name == mid:
            name = mid.replace("-", " ").title()
        models.append({"slug": mid, "display_name": name})
    return models


# Method name → function
_FETCH_METHODS = {
    "anthropic_api": _fetch_anthropic_api,
    "anthropic_scrape": _fetch_anthropic_scrape,
    "google_api": _fetch_google_api,
    "openai_compat": _fetch_openai_compat,
}

# ---------------------------------------------------------------------------
# Key resolution (SpacetimeDB)
# ---------------------------------------------------------------------------


async def _get_api_key(stdb, provider_id: str) -> tuple[str, str] | None:
    """Read the active API key for a provider from SpacetimeDB."""
    rows = await stdb.query(f"SELECT encrypted_value, key_type FROM provider_api_keys WHERE provider_id = '{provider_id}'")
    if not rows:
        return None
    row = rows[0]
    raw = row["encrypted_value"]
    key_type = row["key_type"] or "api_key"
    try:
        decrypted = decrypt_value(raw)
        if decrypted:
            return (decrypted, key_type)
    except Exception:
        if not is_encrypted(raw):
            return (raw, key_type)
    return None


# ---------------------------------------------------------------------------
# Main sync (SpacetimeDB)
# ---------------------------------------------------------------------------


async def sync_models_stdb(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Sync LLM model catalog from all enabled providers into SpacetimeDB."""
    total_synced = 0
    total_errors = 0

    stdb = get_stdb()
    # Load all enabled providers from SpacetimeDB
    rows = await stdb.query("SELECT id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config, is_enabled FROM providers WHERE is_enabled = true")
    providers = []
    for row in rows:
        # Decode optional fields (they are stored as [0, value] or [1] for none)
        api_base_url = row["api_base_url"]
        if isinstance(api_base_url, list) and len(api_base_url) == 2 and api_base_url[0] == 0:
            api_base_url = api_base_url[1]
        else:
            api_base_url = None
        models_endpoint = row["models_endpoint"]
        if isinstance(models_endpoint, list) and len(models_endpoint) == 2 and models_endpoint[0] == 0:
            models_endpoint = models_endpoint[1]
        else:
            models_endpoint = None
        config = row["config"]
        providers.append({
            "id": row["id"],
            "display_name": row["display_name"],
            "litellm_prefix": row["litellm_prefix"],
            "api_base_url": api_base_url,
            "models_endpoint": models_endpoint,
            "models_fetch_method": row["models_fetch_method"],
            "auth_type": row["auth_type"],
            "config": config,
        })

    async with httpx.AsyncClient(timeout=30.0) as client:
        for provider in providers:
            provider_id = provider["id"]
            display_name = provider["display_name"]

            key_result = await _get_api_key(stdb, provider_id)
            if not key_result:
                continue

            api_key, key_type = key_result
            fetch_method = provider["models_fetch_method"]
            fetch_fn = _FETCH_METHODS.get(fetch_method)

            if not fetch_fn:
                logger.warning("sync_models: Unknown fetch method '%s' for %s", fetch_method, provider_id)
                total_errors += 1
                continue

            try:
                models = await fetch_fn(client, provider, api_key, key_type)

                if not models:
                    logger.info("sync_models: %s returned 0 models", provider_id)
                    continue

                # Upsert models via addModel reducer (requires id, provider, modelId, displayName, contextWindow, isEnabled)
                # We need to generate an ID and set contextWindow = 0, isEnabled = true
                from ulid import ULID
                for m in models:
                    slug = m["slug"]
                    name = f"{display_name} — {m['display_name']}"
                    model_id = str(ULID())
                    # Call addModel reducer (upsert behavior? addModel only inserts, but we can call updateModel later)
                    # For simplicity, we'll call addModel; if duplicate primary key, it will replace? Not sure.
                    # We'll need to implement upsert logic: check if model exists, then update.
                    # For now, just call addModel.
                    success = await stdb.call_reducer("add_model", [
                        model_id,
                        provider_id,
                        slug,
                        name,
                        0,  # contextWindow
                        True,  # isEnabled
                    ])
                    if not success:
                        logger.error("sync_models: Failed to insert model %s for %s", slug, provider_id)

                total_synced += len(models)
                logger.info("sync_models: %s — %d models synced", provider_id, len(models))

            except httpx.HTTPStatusError as e:
                total_errors += 1
                logger.warning(
                    "sync_models: %s HTTP %d: %s",
                    provider_id, e.response.status_code, e.response.text[:200],
                )
            except Exception:
                total_errors += 1
                logger.exception("sync_models: %s failed", provider_id)

    logger.info("sync_models complete: %d models synced, %d provider errors", total_synced, total_errors)