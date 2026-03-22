"""Centralized OAuth token handling for Anthropic (Claude Max).

Single source of truth for:
  - Detecting OAuth tokens (sk-ant-oat prefix)
  - Detecting key_type from raw values
  - Extra HTTP headers required by the Anthropic OAuth API
  - Refreshing expired tokens via the Gateway (which uses pi-ai)

All Python code that touches OAuth tokens should import from here.
The actual refresh logic lives in the Gateway (TypeScript / pi-ai);
this module calls the Gateway's /provider-api-keys/:providerId endpoint
which handles refresh transparently.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OAUTH_TOKEN_PREFIX = "sk-ant-oat"

# Extra headers required to use an OAuth token with the Anthropic Messages API.
# Must stay in sync with gateway/src/oauth/provider-oauth.ts buildOAuthHeaders().
OAUTH_EXTRA_HEADERS: dict[str, str] = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-cli/2.1.81",
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def is_oauth_token(value: str) -> bool:
    """Detect an OAuth token by its ``sk-ant-oat`` prefix."""
    return value.startswith(OAUTH_TOKEN_PREFIX)


def detect_key_type(provider_key: str, value: str) -> str:
    """Classify a raw API-key value as ``"api_key"`` or ``"oauth_token"``.

    Used when storing a new key to persist the correct key_type metadata.

    For Anthropic keys:
      - ``sk-ant-api*`` → ``"api_key"``
      - ``sk-ant-oat*`` → ``"oauth_token"``
      - Unknown prefix  → ``"oauth_token"`` (safe default — avoids raw API calls)

    All other providers default to ``"api_key"``.
    """
    if provider_key == "llm.api_key.anthropic":
        if is_oauth_token(value):
            return "oauth_token"
        if value.startswith("sk-ant-"):
            return "api_key"
        return "oauth_token"  # Unknown format, assume OAuth to avoid failed calls
    return "api_key"


def get_oauth_extra_headers(api_key: str) -> dict[str, str]:
    """Return the extra headers dict if *api_key* is an OAuth token, else empty."""
    if is_oauth_token(api_key):
        return dict(OAUTH_EXTRA_HEADERS)
    return {}


# ---------------------------------------------------------------------------
# Gateway-based token refresh
# ---------------------------------------------------------------------------


def _resolve_gateway_url() -> str:
    """Resolve the Gateway URL (shared logic with persistence_client)."""
    explicit = os.environ.get("BOND_GATEWAY_URL")
    if explicit:
        return explicit.rstrip("/")

    import platform
    system = platform.system().lower()
    if system in ("darwin", "windows") or "microsoft" in platform.release().lower():
        return "http://host.docker.internal:18789"
    else:
        return "http://172.17.0.1:18789"


async def resolve_provider_key_via_gateway(
    provider_id: str,
    *,
    gateway_url: str | None = None,
    timeout: float = 15.0,
) -> tuple[str, str] | None:
    """Resolve an API key through the Gateway, which handles OAuth refresh.

    The Gateway's ``GET /api/v1/provider-api-keys/:providerId`` endpoint
    detects OAuth credentials, refreshes them via pi-ai if expired,
    and returns a valid access token.

    Returns ``(api_key, key_type)`` or ``None`` if not found.
    ``key_type`` is ``"oauth_token"`` when the Gateway performed a refresh,
    or the stored key_type otherwise.
    """
    url = gateway_url or _resolve_gateway_url()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/v1/provider-api-keys/{provider_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            api_key = data.get("encryptedValue", "")
            key_type = data.get("keyType", "api_key")
            auth_mode = resp.headers.get("x-auth-mode", "api-key")
            if auth_mode == "oauth":
                key_type = "oauth_token"
            return (api_key, key_type)
    except Exception as e:
        logger.warning("resolve_provider_key_via_gateway(%s) failed: %s", provider_id, e)
        return None
