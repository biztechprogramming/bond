"""Settings service — business logic for app settings, embedding, and LLM configuration.

Keeps data-access and validation logic out of route handlers.
SpacetimeDB is the source of truth for runtime settings and embedding model config.
SQLite holds local crypto state.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.core.crypto import decrypt_value, encrypt_value, is_encrypted
from backend.app.core.oauth import detect_key_type
from backend.app.core.spacetimedb import get_stdb

logger = logging.getLogger("bond.services.settings")

# ── Constants ─────────────────────────────────────────────────

# Keys that must be encrypted at rest and masked on read
ENCRYPTED_KEYS = frozenset({
    "embedding.api_key.voyage",
    "embedding.api_key.gemini",
})

_EMBEDDING_DEFAULTS = {
    "embedding.model": "voyage-4-nano",
    "embedding.output_dimension": "1024",
    "embedding.execution_mode": "auto",
}

# Seed data for embedding models in SpacetimeDB
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


# ── Value helpers ─────────────────────────────────────────────


def mask_value(value: str) -> str:
    """Mask a sensitive value, showing only last 4 chars."""
    if value and len(value) > 4:
        return "*" * (len(value) - 4) + value[-4:]
    return value


def read_value(key: str, raw: str) -> str:
    """Read a stored value — decrypt + mask if it's a secret key."""
    if key in ENCRYPTED_KEYS:
        return mask_value(decrypt_value(raw))
    return raw


def write_value(key: str, value: str) -> str:
    """Prepare a value for storage — encrypt if it's a secret key."""
    if key in ENCRYPTED_KEYS:
        return encrypt_value(value)
    return value


# ── Data classes ──────────────────────────────────────────────


@dataclass
class EmbeddingConfig:
    model: str
    dimension: int
    execution_mode: str
    has_voyage_key: bool
    has_gemini_key: bool


@dataclass
class EmbeddingModel:
    model_name: str
    family: str
    provider: str
    max_dimension: int
    supported_dimensions: list[int]
    supports_local: bool
    supports_api: bool
    is_default: bool


@dataclass
class LlmProvider:
    id: str
    name: str
    is_enabled: bool


@dataclass
class LlmCurrent:
    provider: str
    model: str
    keys_set: dict[str, bool]


# ── Generic settings (SpacetimeDB) ───────────────────────────


class SettingsService:
    """Facade for all settings operations."""

    def __init__(self) -> None:
        self._stdb = get_stdb()

    # ── Generic key-value ─────────────────────────────────────

    async def get_all(self, db: AsyncSession) -> dict[str, str]:
        """Return all settings as a key→value dict (masked where appropriate)."""
        result = await db.execute(text("SELECT key, value FROM settings"))
        return {row[0]: read_value(row[0], row[1]) for row in result.fetchall()}

    async def get(self, key: str) -> dict[str, str]:
        """Return a single setting by key."""
        rows = await self._stdb.query(
            f"SELECT value FROM settings WHERE key = '{_escape(key)}'"
        )
        if not rows:
            return None
        return {"key": key, "value": read_value(key, rows[0]["value"])}

    async def upsert(self, key: str, value: str) -> dict[str, str]:
        """Create or update a single setting in SpacetimeDB."""
        stored = write_value(key, value)
        await self._stdb.call_reducer("set_setting", [key, stored])
        return {"key": key, "value": read_value(key, stored)}

    # ── Provider API keys ─────────────────────────────────────

    async def upsert_provider_api_key(
        self, provider_id: str, raw_key: str,
    ) -> dict[str, str]:
        """Encrypt and store a provider API key."""
        # Verify provider exists
        prov_rows = await self._stdb.query(
            f"SELECT id FROM providers WHERE id = '{_escape(provider_id)}'"
        )
        if not prov_rows:
            raise SettingsValidationError(f"Unknown provider: {provider_id}")

        encrypted = encrypt_value(raw_key)
        key_type = detect_key_type(f"llm.api_key.{provider_id}", raw_key)
        now = int(time.time() * 1000)

        await self._stdb.call_reducer(
            "set_provider_api_key", [provider_id, encrypted, key_type, now, now],
        )

        return {"key": f"llm.api_key.{provider_id}", "value": mask_value(raw_key)}

    # ── Embedding config (SpacetimeDB) ────────────────────────

    async def seed_embedding_models(self) -> None:
        """Seed embedding models into SpacetimeDB if the table is empty."""
        rows = await self._stdb.query(
            "SELECT model_name FROM embedding_models LIMIT 1"
        )
        if rows:
            return
        logger.info("Seeding %d embedding models into SpacetimeDB", len(_EMBEDDING_SEED))
        for name, family, provider, max_dim, dims, local, api, default in _EMBEDDING_SEED:
            try:
                await self._stdb.call_reducer("set_embedding_model", [
                    name, family, provider, max_dim, dims, local, api, default,
                ])
            except Exception:
                logger.error("Failed to seed embedding model: %s", name, exc_info=True)
                raise

    async def get_embedding_models(self) -> list[EmbeddingModel]:
        """Return all available embedding models from SpacetimeDB."""
        rows = await self._stdb.query(
            "SELECT model_name, family, provider, max_dimension, "
            "supported_dimensions, supports_local, supports_api, is_default "
            "FROM embedding_models ORDER BY family, model_name"
        )
        if not rows:
            logger.warning("No embedding models found in SpacetimeDB — was seed_embedding_models() called?")
        return [
            EmbeddingModel(
                model_name=r["model_name"],
                family=r["family"],
                provider=r["provider"],
                max_dimension=r["max_dimension"],
                supported_dimensions=json.loads(r["supported_dimensions"]),
                supports_local=bool(r["supports_local"]),
                supports_api=bool(r["supports_api"]),
                is_default=bool(r["is_default"]),
            )
            for r in rows
        ]

    async def get_embedding_current(self) -> EmbeddingConfig:
        """Return the active embedding configuration, seeding defaults if needed."""
        # Read current settings from SpacetimeDB
        rows = await self._stdb.query(
            "SELECT key, value FROM settings WHERE key LIKE 'embedding.%'"
        )
        raw_map = {r["key"]: r["value"] for r in rows}

        # Seed missing defaults into SpacetimeDB
        missing = {k: v for k, v in _EMBEDDING_DEFAULTS.items() if k not in raw_map}
        if missing:
            for key, value in missing.items():
                await self._stdb.call_reducer("set_setting", [key, value])
                raw_map[key] = value

        # Check API key presence
        has_voyage, has_gemini = await self._check_api_key_presence(raw_map)

        return EmbeddingConfig(
            model=raw_map.get("embedding.model", _EMBEDDING_DEFAULTS["embedding.model"]),
            dimension=int(raw_map.get("embedding.output_dimension", _EMBEDDING_DEFAULTS["embedding.output_dimension"])),
            execution_mode=raw_map.get("embedding.execution_mode", _EMBEDDING_DEFAULTS["embedding.execution_mode"]),
            has_voyage_key=has_voyage,
            has_gemini_key=has_gemini,
        )

    async def update_embedding(
        self, model: str, dimension: int, execution_mode: str,
    ) -> dict[str, Any]:
        """Validate and update embedding configuration."""
        # Validate model exists in SpacetimeDB
        rows = await self._stdb.query(
            "SELECT family, supported_dimensions, supports_local, supports_api "
            f"FROM embedding_models WHERE model_name = '{_escape(model)}'"
        )
        if not rows:
            raise SettingsValidationError(f"Unknown model: {model}")

        model_row = rows[0]
        new_family = model_row["family"]
        supported_dims = json.loads(model_row["supported_dimensions"])
        supports_local = bool(model_row["supports_local"])
        supports_api = bool(model_row["supports_api"])

        # Validate dimension
        if dimension not in supported_dims:
            raise SettingsValidationError(
                f"Dimension {dimension} not supported. Valid: {supported_dims}"
            )

        # Validate execution mode
        if execution_mode not in ("local", "api", "auto"):
            raise SettingsValidationError("execution_mode must be local, api, or auto")
        if execution_mode == "local" and not supports_local:
            raise SettingsValidationError(f"Model {model} does not support local execution")
        if execution_mode == "api" and not supports_api:
            raise SettingsValidationError(f"Model {model} does not support API execution")

        # Detect family switch
        warning = await self._detect_family_switch(model, new_family)

        # Persist to SpacetimeDB (source of truth for the worker)
        for key, value in [
            ("embedding.model", model),
            ("embedding.output_dimension", str(dimension)),
            ("embedding.execution_mode", execution_mode),
        ]:
            await self._stdb.call_reducer("set_setting", [key, value])

        result = {"status": "ok", "model": model, "dimension": dimension, "execution_mode": execution_mode}
        if warning:
            result["warning"] = warning
        return result

    # ── LLM configuration ────────────────────────────────────

    async def get_llm_providers(self) -> list[LlmProvider]:
        """Return enabled LLM providers."""
        rows = await self._stdb.query(
            "SELECT id, display_name, is_enabled FROM providers"
        )
        providers = [
            LlmProvider(id=r["id"], name=r["display_name"], is_enabled=bool(r["is_enabled"]))
            for r in rows
        ]
        providers.sort(key=lambda p: p.name)
        return providers

    async def get_llm_models(self) -> list[dict[str, Any]]:
        """Return available LLM models with litellm-compatible IDs."""
        models = await self._stdb.query(
            "SELECT model_id, display_name, provider, is_enabled "
            "FROM llm_models WHERE is_enabled = true"
        )
        providers = await self._stdb.query(
            "SELECT id, litellm_prefix, display_name "
            "FROM providers WHERE is_enabled = true"
        )
        provider_map = {p["id"]: p for p in providers}

        result = []
        for m in models:
            provider = provider_map.get(m["provider"])
            if provider:
                result.append({
                    "id": f"{provider['litellm_prefix']}/{m['model_id']}",
                    "name": m["display_name"],
                    "provider": m["provider"],
                    "category": "chat",
                })

        result.sort(key=lambda x: (
            provider_map.get(x["provider"], {}).get("display_name", ""),
            x["name"],
        ))
        return result

    async def get_llm_current(self) -> LlmCurrent:
        """Return current LLM provider/model and which providers have keys."""
        settings = get_settings()
        providers = await self._stdb.query(
            "SELECT id FROM providers WHERE is_enabled = true"
        )
        keys = await self._stdb.query("SELECT provider_id FROM provider_api_keys")
        key_set = {row["provider_id"] for row in keys}

        return LlmCurrent(
            provider=settings.llm_provider,
            model=settings.llm_model,
            keys_set={row["id"]: row["id"] in key_set for row in providers},
        )

    # ── SQLite crypto helpers (used by tests and legacy paths) ─

    @staticmethod
    async def get_decrypted(db: AsyncSession, key: str) -> str | None:
        """Read and decrypt a setting value from SQLite (no masking)."""
        result = await db.execute(
            text("SELECT value FROM settings WHERE key = :key"), {"key": key}
        )
        row = result.fetchone()
        if not row or not row[0]:
            return None

        raw = row[0]
        if key in ENCRYPTED_KEYS:
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

    # ── Private helpers ───────────────────────────────────────

    async def _check_api_key_presence(
        self, raw_map: dict[str, str],
    ) -> tuple[bool, bool]:
        """Check whether Voyage and Gemini API keys are configured."""
        voyage_raw = raw_map.get("embedding.api_key.voyage", "")
        gemini_raw = raw_map.get("embedding.api_key.gemini", "")
        has_voyage = bool(voyage_raw and decrypt_value(voyage_raw))
        has_gemini = bool(gemini_raw and decrypt_value(gemini_raw))

        if not has_voyage or not has_gemini:
            try:
                pkeys = await self._stdb.query(
                    "SELECT provider_id, key_type FROM provider_api_keys "
                    "WHERE provider_id = 'voyage' OR provider_id = 'gemini'"
                )
                for pk in pkeys:
                    if pk["provider_id"] == "voyage":
                        has_voyage = True
                    if pk["provider_id"] == "gemini":
                        has_gemini = True
            except Exception:
                pass

        return has_voyage, has_gemini

    async def _detect_family_switch(
        self, new_model: str, new_family: str,
    ) -> str | None:
        """Return a warning message if switching embedding families, else None."""
        # Read current model from SpacetimeDB settings
        rows = await self._stdb.query(
            "SELECT value FROM settings WHERE key = 'embedding.model'"
        )
        if not rows:
            return None
        current_model = rows[0]["value"]

        # Look up current model's family
        family_rows = await self._stdb.query(
            f"SELECT family FROM embedding_models WHERE model_name = '{_escape(current_model)}'"
        )
        if family_rows and family_rows[0]["family"] != new_family:
            return (
                f"Switching from {family_rows[0]['family']} to {new_family} family. "
                "All existing embeddings will need to be re-generated."
            )
        return None


# ── Exceptions ────────────────────────────────────────────────


class SettingsError(Exception):
    """Internal / storage error."""


class SettingsValidationError(Exception):
    """Validation / user-input error."""


# ── Helpers ───────────────────────────────────────────────────


def _escape(value: str) -> str:
    """Escape single quotes for SQL literals."""
    return value.replace("'", "''")
