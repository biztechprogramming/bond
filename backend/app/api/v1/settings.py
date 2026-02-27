"""Settings API — CRUD for app settings, embedding and LLM configuration."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.core.crypto import decrypt_value, encrypt_value, is_encrypted
from backend.app.db.session import get_db

router = APIRouter(prefix="/settings", tags=["settings"])

# Keys that must be encrypted at rest and masked on read
_ENCRYPTED_KEYS = {
    "embedding.api_key.voyage",
    "embedding.api_key.gemini",
    "llm.api_key.anthropic",
    "llm.api_key.openai",
    "llm.api_key.google",
    "llm.api_key.deepseek",
    "llm.api_key.groq",
    "llm.api_key.mistral",
    "llm.api_key.openrouter",
    "llm.api_key.xai",
}

# providers.yaml lives next to the agent code
_PROVIDERS_PATH = Path(__file__).resolve().parent.parent.parent / "app" / "agent" / "providers.yaml"


def _mask_value(value: str) -> str:
    """Mask a sensitive value, showing only last 4 chars."""
    if value and len(value) > 4:
        return "*" * (len(value) - 4) + value[-4:]
    return value


def _read_value(key: str, raw: str) -> str:
    """Read a stored value — decrypt + mask if it's a secret key."""
    if key in _ENCRYPTED_KEYS:
        plaintext = decrypt_value(raw)
        return _mask_value(plaintext)
    return raw


def _write_value(key: str, value: str) -> str:
    """Prepare a value for storage — encrypt if it's a secret key."""
    if key in _ENCRYPTED_KEYS:
        return encrypt_value(value)
    return value


async def _get_decrypted(db: AsyncSession, key: str) -> str | None:
    """Internal helper: read and decrypt a setting value (no masking)."""
    result = await db.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": key}
    )
    row = result.fetchone()
    if not row or not row[0]:
        return None
    raw = row[0]
    if key in _ENCRYPTED_KEYS:
        plaintext = decrypt_value(raw)
        # Migrate legacy plaintext: re-encrypt and persist
        if not is_encrypted(raw):
            encrypted = encrypt_value(plaintext)
            await db.execute(
                text(
                    "UPDATE settings SET value = :value, updated_at = CURRENT_TIMESTAMP "
                    "WHERE key = :key"
                ),
                {"key": key, "value": encrypted},
            )
            await db.commit()
        return plaintext
    return raw


# ── General settings CRUD ─────────────────────────────────────


@router.get("")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    """Return all settings as a key-value dict."""
    result = await db.execute(text("SELECT key, value FROM settings"))
    rows = result.fetchall()
    return {row[0]: _read_value(row[0], row[1]) for row in rows}


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
    raw_map = {row[0]: row[1] for row in result.fetchall()}

    # Decrypt API keys to check presence (but don't return values)
    voyage_raw = raw_map.get("embedding.api_key.voyage", "")
    gemini_raw = raw_map.get("embedding.api_key.gemini", "")
    has_voyage = bool(voyage_raw and decrypt_value(voyage_raw))
    has_gemini = bool(gemini_raw and decrypt_value(gemini_raw))

    return {
        "model": raw_map.get("embedding.model", "voyage-4-nano"),
        "dimension": int(raw_map.get("embedding.output_dimension", "1024")),
        "execution_mode": raw_map.get("embedding.execution_mode", "auto"),
        "has_voyage_key": has_voyage,
        "has_gemini_key": has_gemini,
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


# ── LLM configuration endpoints ──────────────────────────────


@router.get("/llm/providers")
async def get_llm_providers():
    """Return the list of chat providers from providers.yaml."""
    providers_path = Path(__file__).resolve().parent.parent.parent / "agent" / "providers.yaml"
    with open(providers_path) as f:
        data = yaml.safe_load(f)
    chat = data.get("chat", {})
    return [
        {"id": pid, "name": pconf.get("name", pid)}
        for pid, pconf in chat.items()
    ]


@router.get("/llm/models")
async def get_llm_models(db: AsyncSession = Depends(get_db)):
    """Return available LLM models from the synced catalog."""
    result = await db.execute(
        text(
            "SELECT model_id, name, provider, category "
            "FROM llm_models ORDER BY provider, name"
        )
    )
    return [
        {"id": row[0], "name": row[1], "provider": row[2], "category": row[3]}
        for row in result.fetchall()
    ]


@router.get("/llm/current")
async def get_llm_current(db: AsyncSession = Depends(get_db)):
    """Return current LLM provider, model, and which API keys are configured."""
    settings = get_settings()

    # Check which LLM keys are set (bool per provider)
    providers_with_keys = [
        "anthropic", "openai", "google", "deepseek", "groq", "mistral", "openrouter", "xai",
    ]
    keys_set: dict[str, bool] = {}
    for provider in providers_with_keys:
        setting_key = f"llm.api_key.{provider}"
        result = await db.execute(
            text("SELECT value FROM settings WHERE key = :key"), {"key": setting_key}
        )
        row = result.fetchone()
        has_key = bool(row and row[0] and decrypt_value(row[0]))
        keys_set[provider] = has_key

    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "keys_set": keys_set,
    }


# ── Single-key endpoints (must come after /embedding/* and /llm/* routes) ──


@router.get("/{key:path}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    """Return a single setting by key."""
    result = await db.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": key}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")
    return {"key": key, "value": _read_value(key, row[0])}


class SettingUpdate(BaseModel):
    value: str


@router.put("/{key:path}")
async def update_setting(
    key: str, body: SettingUpdate, request: Request, db: AsyncSession = Depends(get_db),
):
    """Create or update a single setting."""
    stored = _write_value(key, body.value)
    await db.execute(
        text(
            "INSERT INTO settings (key, value) VALUES (:key, :value) "
            "ON CONFLICT(key) DO UPDATE SET value = :value, "
            "updated_at = CURRENT_TIMESTAMP"
        ),
        {"key": key, "value": stored},
    )
    await db.commit()

    # Trigger model catalog sync when an LLM API key changes
    if key.startswith("llm.api_key."):
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler:
            asyncio.create_task(scheduler.trigger("sync_models"))

    return {"key": key, "value": _read_value(key, stored)}
