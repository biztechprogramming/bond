#!/usr/bin/env python3
"""Migrate API keys from the Vault file into provider_api_keys table.

Run once after migration 000019. Idempotent (uses INSERT OR IGNORE).

Usage:
    uv run python scripts/migrate_vault_keys.py
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.core.crypto import encrypt_value
from backend.app.core.vault import Vault
from backend.app.config import get_settings

# Vault key name → (provider_id, key_type detection)
_VAULT_KEYS = {
    "ANTHROPIC_API_KEY": "anthropic",
    "GOOGLE_API_KEY": "google",
    "OPENAI_API_KEY": "openai",
    "DEEPSEEK_API_KEY": "deepseek",
    "GROQ_API_KEY": "groq",
    "MISTRAL_API_KEY": "mistral",
    "XAI_API_KEY": "xai",
    "OPENROUTER_API_KEY": "openrouter",
}


def _detect_key_type(provider: str, value: str) -> str:
    if provider == "anthropic" and value.startswith("sk-ant-oat"):
        return "oauth_token"
    return "api_key"


def main():
    settings = get_settings()
    db_path = Path(settings.bond_home) / "data" / "knowledge.db"

    vault = Vault()
    db = sqlite3.connect(str(db_path))

    migrated = 0
    skipped = 0

    for vault_key, provider_id in _VAULT_KEYS.items():
        value = vault.get(vault_key)
        if not value:
            continue

        # Check if already migrated
        existing = db.execute(
            "SELECT 1 FROM provider_api_keys WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()

        if existing:
            print(f"  SKIP {provider_id}: already has a key in provider_api_keys")
            skipped += 1
            continue

        key_type = _detect_key_type(provider_id, value)
        encrypted = encrypt_value(value)

        db.execute(
            "INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type) "
            "VALUES (?, ?, ?)",
            (provider_id, encrypted, key_type),
        )
        migrated += 1
        print(f"  OK   {provider_id}: migrated from vault ({key_type})")

    db.commit()
    db.close()

    print(f"\nDone: {migrated} migrated, {skipped} skipped")


if __name__ == "__main__":
    main()
