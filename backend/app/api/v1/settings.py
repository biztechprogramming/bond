"""Settings API — CRUD for app settings and embedding configuration."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_db

router = APIRouter(prefix="/settings", tags=["settings"])

# Keys whose values should be masked on read
_MASKED_KEYS = {"embedding.api_key.voyage", "embedding.api_key.gemini"}


def _mask_value(key: str, value: str) -> str:
    """Mask sensitive values, showing only last 4 chars."""
    if key in _MASKED_KEYS and value and len(value) > 4:
        return "*" * (len(value) - 4) + value[-4:]
    return value


# ── General settings CRUD ─────────────────────────────────────


@router.get("")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    """Return all settings as a key-value dict."""
    result = await db.execute(text("SELECT key, value FROM settings"))
    rows = result.fetchall()
    return {row[0]: _mask_value(row[0], row[1]) for row in rows}


@router.get("/embedding/models")
async def get_embedding_models(db: AsyncSession = Depends(get_db)):
    """Return all rows from embedding_configs table."""
    result = await db.execute(
        text(
            "SELECT model_name, family, provider, max_dimension, "
            "supported_dimensions, supports_local, supports_api, is_default "
            "FROM embedding_configs ORDER BY family, model_name"
        )
    )
    rows = result.fetchall()
    return [
        {
            "model_name": r[0],
            "family": r[1],
            "provider": r[2],
            "max_dimension": r[3],
            "supported_dimensions": json.loads(r[4]),
            "supports_local": bool(r[5]),
            "supports_api": bool(r[6]),
            "is_default": bool(r[7]),
        }
        for r in rows
    ]


@router.get("/embedding/current")
async def get_current_embedding(db: AsyncSession = Depends(get_db)):
    """Return the current embedding configuration."""
    keys = [
        "embedding.model",
        "embedding.output_dimension",
        "embedding.execution_mode",
        "embedding.api_key.voyage",
        "embedding.api_key.gemini",
    ]
    placeholders = ", ".join(f"'{k}'" for k in keys)
    result = await db.execute(
        text(f"SELECT key, value FROM settings WHERE key IN ({placeholders})")
    )
    settings_map = {row[0]: row[1] for row in result.fetchall()}

    voyage_key = settings_map.get("embedding.api_key.voyage", "")
    gemini_key = settings_map.get("embedding.api_key.gemini", "")

    return {
        "model": settings_map.get("embedding.model", "voyage-4-nano"),
        "dimension": int(settings_map.get("embedding.output_dimension", "1024")),
        "execution_mode": settings_map.get("embedding.execution_mode", "auto"),
        "has_voyage_key": bool(voyage_key),
        "has_gemini_key": bool(gemini_key),
    }


class EmbeddingUpdate(BaseModel):
    model: str
    dimension: int
    execution_mode: str


@router.put("/embedding")
async def update_embedding(body: EmbeddingUpdate, db: AsyncSession = Depends(get_db)):
    """Update embedding settings with validation."""
    # Validate model exists
    result = await db.execute(
        text(
            "SELECT family, supported_dimensions, supports_local, supports_api "
            "FROM embedding_configs WHERE model_name = :model"
        ),
        {"model": body.model},
    )
    model_row = result.fetchone()
    if not model_row:
        raise HTTPException(status_code=400, detail=f"Unknown model: {body.model}")

    new_family = model_row[0]
    supported_dims = json.loads(model_row[1])
    supports_local = bool(model_row[2])
    supports_api = bool(model_row[3])

    # Validate dimension
    if body.dimension not in supported_dims:
        raise HTTPException(
            status_code=400,
            detail=f"Dimension {body.dimension} not supported. Valid: {supported_dims}",
        )

    # Validate execution mode
    if body.execution_mode not in ("local", "api", "auto"):
        raise HTTPException(status_code=400, detail="execution_mode must be local, api, or auto")
    if body.execution_mode == "local" and not supports_local:
        raise HTTPException(status_code=400, detail=f"Model {body.model} does not support local execution")
    if body.execution_mode == "api" and not supports_api:
        raise HTTPException(status_code=400, detail=f"Model {body.model} does not support API execution")

    # Check for family switch
    warning = None
    result = await db.execute(
        text("SELECT value FROM settings WHERE key = 'embedding.model'")
    )
    current_row = result.fetchone()
    if current_row:
        current_model = current_row[0]
        result2 = await db.execute(
            text("SELECT family FROM embedding_configs WHERE model_name = :model"),
            {"model": current_model},
        )
        old_family_row = result2.fetchone()
        if old_family_row and old_family_row[0] != new_family:
            warning = (
                f"Switching from {old_family_row[0]} to {new_family} family. "
                "All existing embeddings will need to be re-generated."
            )

    # Upsert settings
    for key, value in [
        ("embedding.model", body.model),
        ("embedding.output_dimension", str(body.dimension)),
        ("embedding.execution_mode", body.execution_mode),
    ]:
        await db.execute(
            text(
                "INSERT INTO settings (key, value) VALUES (:key, :value) "
                "ON CONFLICT(key) DO UPDATE SET value = :value, "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {"key": key, "value": value},
        )
    await db.commit()

    resp = {"status": "ok", "model": body.model, "dimension": body.dimension, "execution_mode": body.execution_mode}
    if warning:
        resp["warning"] = warning
    return resp


# ── Single-key endpoints (must come after /embedding/* routes) ──


@router.get("/{key:path}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    """Return a single setting by key."""
    result = await db.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": key}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")
    return {"key": key, "value": _mask_value(key, row[0])}


class SettingUpdate(BaseModel):
    value: str


@router.put("/{key:path}")
async def update_setting(key: str, body: SettingUpdate, db: AsyncSession = Depends(get_db)):
    """Create or update a single setting."""
    await db.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:key, :value) "
            "ON CONFLICT(key) DO UPDATE SET value = :value, "
            "updated_at = CURRENT_TIMESTAMP"
        ),
        {"key": key, "value": body.value},
    )
    await db.commit()
    return {"key": key, "value": _mask_value(key, body.value)}
