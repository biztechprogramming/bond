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

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("bond.core.oauth")

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

# System prompt identity prefix required by the Anthropic API for Claude Max
# OAuth tokens when calling Sonnet/Opus models.  Without this prefix the API
# returns 400 invalid_request_error.  Must stay in sync with pi-ai's
# buildParams() in @mariozechner/pi-ai/dist/providers/anthropic.js.
OAUTH_SYSTEM_PROMPT_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)


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


def ensure_oauth_system_prefix(
    messages: list[dict],
    api_key: str | None = None,
    *,
    extra_kwargs: dict | None = None,
) -> list[dict]:
    """Ensure the OAuth system prompt prefix is present when using an OAuth token.

    Claude Max OAuth tokens require a "You are Claude Code..." identity prefix
    in the system prompt.  Without it the Anthropic API returns 400.

    This function is idempotent — calling it multiple times is safe.

    Detection: either pass *api_key* directly, or pass the *extra_kwargs* dict
    that was built by the API-key resolver (checks for ``extra_headers`` +
    ``api_key`` inside that dict).

    Returns the (possibly modified) messages list.
    """
    # Detect whether we're in an OAuth context
    _is_oauth = False
    if api_key and is_oauth_token(api_key):
        _is_oauth = True
    elif extra_kwargs:
        _has_headers = "extra_headers" in extra_kwargs
        _key = extra_kwargs.get("api_key", "")
        if _has_headers and _key and is_oauth_token(_key):
            _is_oauth = True

    if not _is_oauth:
        return messages

    # Check if the prefix is already present
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content", "")
        # Handle both string and list-of-blocks formats
        if isinstance(content, str):
            if OAUTH_SYSTEM_PROMPT_PREFIX in content:
                return messages
            # Prepend as string
            messages[0] = {
                **messages[0],
                "content": OAUTH_SYSTEM_PROMPT_PREFIX + "\n\n" + content,
            }
        elif isinstance(content, list):
            # Check if prefix block already exists
            for block in content:
                if isinstance(block, dict) and OAUTH_SYSTEM_PROMPT_PREFIX in block.get("text", ""):
                    return messages
            # Prepend as a new block
            messages[0] = {
                **messages[0],
                "content": [
                    {
                        "type": "text",
                        "text": OAUTH_SYSTEM_PROMPT_PREFIX,
                        "cache_control": {"type": "ephemeral"},
                    },
                    *content,
                ],
            }
    else:
        # No system message at all — insert one at position 0
        messages.insert(0, {
            "role": "system",
            "content": OAUTH_SYSTEM_PROMPT_PREFIX,
        })

    return messages


# ---------------------------------------------------------------------------
# Gateway-based token refresh
# ---------------------------------------------------------------------------


def _is_inside_docker() -> bool:
    """Detect whether we're running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _resolve_gateway_url() -> str:
    """Resolve the Gateway URL.

    When running natively (not in Docker), use localhost.
    When running inside a container, use Docker networking heuristics.
    """
    explicit = os.environ.get("BOND_GATEWAY_URL")
    if explicit:
        return explicit.rstrip("/")

    if not _is_inside_docker():
        return "http://localhost:18789"

    # Inside a Docker container — use host networking
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
    retries: int = 5,
    retry_delay: float = 2.0,
) -> tuple[str, str] | None:
    """Resolve an API key through the Gateway, which handles OAuth refresh.

    The Gateway's ``GET /api/v1/provider-api-keys/:providerId`` endpoint
    detects OAuth credentials, refreshes them via pi-ai if expired,
    and returns a valid access token.

    Retries on connection errors (e.g. Gateway still starting up).

    Returns ``(api_key, key_type)`` or ``None`` if not found.
    ``key_type`` is ``"oauth_token"`` when the Gateway performed a refresh,
    or the stored key_type otherwise.
    """
    url = gateway_url or _resolve_gateway_url()
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            headers: dict[str, str] = {}
            bond_api_key = os.environ.get("BOND_API_KEY", "")
            if bond_api_key:
                headers["Authorization"] = f"Bearer {bond_api_key}"
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    f"{url}/api/v1/provider-api-keys/{provider_id}",
                    headers=headers,
                )
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
        except httpx.ConnectError as e:
            last_err = e
            if attempt < retries:
                logger.info(
                    "resolve_provider_key_via_gateway(%s): Gateway not ready, "
                    "retry %d/%d in %.0fs",
                    provider_id, attempt, retries, retry_delay,
                )
                await asyncio.sleep(retry_delay)
            continue
        except Exception as e:
            logger.warning(
                "resolve_provider_key_via_gateway(%s) failed: %s: %s",
                provider_id, type(e).__name__, e,
            )
            return None

    logger.warning(
        "resolve_provider_key_via_gateway(%s) failed after %d retries: %s: %s",
        provider_id, retries, type(last_err).__name__, last_err,
    )
    return None


async def get_provider_api_key(
    provider_id: str,
    *,
    gateway_url: str | None = None,
) -> tuple[str, str] | None:
    """Get a ready-to-use API key for a provider.

    Resolves through the Gateway (which handles OAuth refresh via pi-ai),
    then decrypts non-OAuth keys that are stored encrypted in SpacetimeDB.

    Returns ``(api_key, key_type)`` or ``None`` if not found.
    """
    from backend.app.core.crypto import decrypt_value

    result = await resolve_provider_key_via_gateway(
        provider_id, gateway_url=gateway_url,
    )
    if not result:
        return None

    api_key, key_type = result
    if not api_key:
        return None

    # OAuth keys are already decrypted/refreshed by the Gateway.
    # Non-OAuth keys come back encrypted — decrypt them.
    if key_type != "oauth_token":
        try:
            decrypted = decrypt_value(api_key)
            if decrypted:
                api_key = decrypted
        except Exception:
            pass  # Not encrypted, use as-is

    return (api_key, key_type)
