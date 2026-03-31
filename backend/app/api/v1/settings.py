"""Settings API — thin route handlers delegating to SettingsService.

SpacetimeDB is the source of truth for runtime settings.
SQLite holds static reference data (embedding_configs) and local crypto state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db
from backend.app.services.settings_service import (
    SettingsError,
    SettingsService,
    SettingsValidationError,
)

logger = logging.getLogger("bond.api.settings")

router = APIRouter(prefix="/settings", tags=["settings"])


def _service() -> SettingsService:
    return SettingsService()


# ── Request models ────────────────────────────────────────────


class SettingUpdate(BaseModel):
    value: str


class EmbeddingUpdate(BaseModel):
    model: str
    dimension: int
    execution_mode: str


# ── General settings CRUD ─────────────────────────────────────


@router.get("")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    """Return all settings as a key-value dict."""
    return await _service().get_all(db)


@router.get("/embedding/models")
async def get_embedding_models(db: AsyncSession = Depends(get_db)):
    """Return all available embedding models from the reference table."""
    models = await _service().get_embedding_models(db)
    return [asdict(m) for m in models]


@router.get("/embedding/current")
async def get_current_embedding(db: AsyncSession = Depends(get_db)):
    """Return the active embedding configuration."""
    config = await _service().get_embedding_current(db)
    return asdict(config)


@router.put("/embedding")
async def update_embedding(body: EmbeddingUpdate, db: AsyncSession = Depends(get_db)):
    """Validate and update embedding configuration."""
    try:
        return await _service().update_embedding(
            db, body.model, body.dimension, body.execution_mode,
        )
    except SettingsValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SettingsError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── LLM configuration ────────────────────────────────────────


@router.get("/llm/providers")
async def get_llm_providers():
    """Return enabled LLM providers."""
    providers = await _service().get_llm_providers()
    return [asdict(p) for p in providers]


@router.get("/llm/models")
async def get_llm_models():
    """Return available LLM models with litellm-compatible IDs."""
    return await _service().get_llm_models()


@router.get("/llm/current")
async def get_llm_current():
    """Return current LLM provider/model and which providers have keys."""
    current = await _service().get_llm_current()
    return asdict(current)


# ── Single-key endpoints (must come after /embedding/* and /llm/* routes) ──


@router.get("/{key:path}")
async def get_setting(key: str):
    """Return a single setting by key."""
    result = await _service().get(key)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")
    return result


@router.put("/{key:path}")
async def update_setting(
    key: str, body: SettingUpdate, request: Request, db: AsyncSession = Depends(get_db),
):
    """Create or update a single setting."""
    svc = _service()

    # LLM API keys go to provider_api_keys, not settings
    if key.startswith("llm.api_key."):
        provider_id = key.removeprefix("llm.api_key.")
        try:
            result = await svc.upsert_provider_api_key(provider_id, body.value)
        except SettingsValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except SettingsError as e:
            raise HTTPException(status_code=500, detail=str(e))

        # Trigger model catalog sync if scheduler is available
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler:
            asyncio.create_task(scheduler.trigger("sync_models"))

        return result

    try:
        return await svc.upsert(key, body.value)
    except SettingsError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Legacy helpers (re-exported for test compatibility) ───────


async def _get_decrypted(db: AsyncSession, key: str) -> str | None:
    """Thin wrapper kept for backward compatibility with existing tests."""
    return await SettingsService.get_decrypted(db, key)
