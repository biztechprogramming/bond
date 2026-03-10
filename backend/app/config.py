"""Settings loader — bond.json + environment variable merge."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

BOND_HOME = Path(os.environ.get("BOND_HOME", Path.home() / ".bond"))
BOND_JSON_PATH = BOND_HOME / "bond.json"

# Defaults for a fresh install
_DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
    },
    "backend": {
        "host": "127.0.0.1",
        "port": 18790,
    },
    "gateway": {
        "host": "127.0.0.1",
        "port": 18789,
    },
    "frontend": {
        "port": 18788,
    },
    "database": {
        "path": str(BOND_HOME / "data" / "knowledge.db"),
    },
    "sandbox_backend": "legacy",
    "opensandbox": {
        "server_url": "http://localhost:8090",
        "api_key": "",
    },
}


class Settings(BaseModel):
    """Merged configuration from bond.json, env vars, and defaults."""

    bond_home: Path = BOND_HOME

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"

    # Backend
    backend_host: str = "127.0.0.1"
    backend_port: int = 18790

    # Gateway
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 18789

    # Frontend
    frontend_port: int = 18788

    # Database
    database_path: str = str(BOND_HOME / "data" / "knowledge.db")

    # Vault
    vault_path: str = str(BOND_HOME / "data" / "credentials.enc")

    # Sandbox backend: "legacy" (Docker direct) or "opensandbox"
    sandbox_backend: str = "legacy"

    # OpenSandbox settings (only used when sandbox_backend == "opensandbox")
    opensandbox_server_url: str = "http://localhost:8090"
    opensandbox_api_key: str = ""


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_bond_json() -> dict[str, Any]:
    """Load bond.json if it exists, merged with defaults."""
    config = _DEFAULTS.copy()
    if BOND_JSON_PATH.exists():
        with open(BOND_JSON_PATH) as f:
            user_config = json.load(f)
        config = _deep_merge(config, user_config)
    return config


@lru_cache
def get_settings() -> Settings:
    """Build Settings from bond.json + env overrides."""
    config = load_bond_json()

    llm = config.get("llm", {})
    backend = config.get("backend", {})
    gateway = config.get("gateway", {})
    frontend = config.get("frontend", {})
    database = config.get("database", {})

    opensandbox = config.get("opensandbox", {})

    return Settings(
        bond_home=Path(os.environ.get("BOND_HOME", BOND_HOME)),
        llm_provider=os.environ.get("BOND_LLM_PROVIDER", llm.get("provider", "anthropic")),
        llm_model=os.environ.get("BOND_LLM_MODEL", llm.get("model", "claude-sonnet-4-20250514")),
        backend_host=os.environ.get("BOND_BACKEND_HOST", backend.get("host", "127.0.0.1")),
        backend_port=int(os.environ.get("BOND_BACKEND_PORT", backend.get("port", 18790))),
        gateway_host=os.environ.get("BOND_GATEWAY_HOST", gateway.get("host", "127.0.0.1")),
        gateway_port=int(os.environ.get("BOND_GATEWAY_PORT", gateway.get("port", 18789))),
        frontend_port=int(os.environ.get("BOND_FRONTEND_PORT", frontend.get("port", 18788))),
        database_path=os.environ.get("BOND_DATABASE_PATH", database.get("path", str(BOND_HOME / "data" / "knowledge.db"))),
        vault_path=os.environ.get("BOND_VAULT_PATH", str(BOND_HOME / "data" / "credentials.enc")),
        sandbox_backend=os.environ.get("BOND_SANDBOX_BACKEND", config.get("sandbox_backend", "legacy")),
        opensandbox_server_url=os.environ.get("OPENSANDBOX_SERVER_URL", opensandbox.get("server_url", "http://localhost:8090")),
        opensandbox_api_key=os.environ.get("OPEN_SANDBOX_API_KEY", opensandbox.get("api_key", "")),
    )
