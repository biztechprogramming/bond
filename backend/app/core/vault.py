"""Secret vault — Fernet-encrypted credential storage.

Stores API keys and other secrets in an encrypted JSON file.
The encryption key is derived from a master password or stored
in the OS keychain (future enhancement).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet

from backend.app.config import get_settings


def _get_or_create_key() -> bytes:
    """Get the Fernet key from BOND_VAULT_KEY env var or generate one."""
    settings = get_settings()
    key_path = Path(settings.bond_home) / "data" / ".vault_key"

    env_key = os.environ.get("BOND_VAULT_KEY")
    if env_key:
        return env_key.encode()

    if key_path.exists():
        return key_path.read_bytes()

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


class Vault:
    """Encrypted credential store backed by a Fernet-encrypted JSON file."""

    def __init__(self, vault_path: str | Path | None = None) -> None:
        if vault_path is None:
            settings = get_settings()
            vault_path = settings.vault_path
        self.path = Path(vault_path)
        self._fernet = Fernet(_get_or_create_key())

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        encrypted = self.path.read_bytes()
        decrypted = self._fernet.decrypt(encrypted)
        return json.loads(decrypted)

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet.encrypt(json.dumps(data).encode())
        self.path.write_bytes(encrypted)
        self.path.chmod(0o600)

    def get(self, key: str) -> str | None:
        """Retrieve a secret by key."""
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        """Store a secret."""
        data = self._load()
        data[key] = value
        self._save(data)

    def delete(self, key: str) -> None:
        """Remove a secret."""
        data = self._load()
        data.pop(key, None)
        self._save(data)

    def list_keys(self) -> list[str]:
        """List all stored secret keys."""
        return list(self._load().keys())

    def get_api_key(self, provider: str) -> str | None:
        """Get an API key for a specific LLM provider.

        Checks vault first, then falls back to environment variables.
        Provider key format in vault: ``{PROVIDER}_API_KEY``
        """
        key_name = f"{provider.upper()}_API_KEY"
        vault_value = self.get(key_name)
        if vault_value:
            return vault_value
        return os.environ.get(key_name)
