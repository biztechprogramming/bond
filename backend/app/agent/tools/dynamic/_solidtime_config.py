"""Shared SolidTime config loader for dynamic tools.

Reads integration config from gateway/data/integrations.json.
"""
from __future__ import annotations

import json
from pathlib import Path


def _find_integrations_file() -> Path:
    """Locate the integrations.json file relative to the project root."""
    # Try common locations — /data/shared is the container mount from project_root/data/shared/
    candidates = [
        Path("/data/shared/integrations.json"),
        Path("/bond/gateway/data/integrations.json"),
        Path("/workspace/bond/gateway/data/integrations.json"),
        Path.home() / "bond" / "gateway" / "data" / "integrations.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Fallback: walk up from this file
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "gateway" / "data" / "integrations.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Cannot find gateway/data/integrations.json")


def load_solidtime_config() -> dict:
    """Load SolidTime config. Raises if not configured."""
    path = _find_integrations_file()
    data = json.loads(path.read_text())
    config = data.get("solidtime")
    if not config or not config.get("enabled"):
        raise RuntimeError("SolidTime integration is not configured. Ask the user to set it up in Settings > Channels.")
    return config


def solidtime_request(method: str, path: str, config: dict | None = None, **kwargs) -> dict:
    """Make an authenticated request to the SolidTime API."""
    import requests

    if config is None:
        config = load_solidtime_config()

    url = f"{config['url']}/api/v1/organizations/{config['organizationId']}{path}"
    headers = {
        "Authorization": config["apiToken"],
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    resp.raise_for_status()

    if resp.status_code == 204:
        return {"ok": True}
    return resp.json()
