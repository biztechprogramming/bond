"""Integration tests for OAuth-via-litellm flow with REAL API calls.

These tests hit the local Gateway and the Anthropic API to prove the
OAuth token flow works end-to-end. They are skipped automatically when
the gateway is unreachable or no OAuth token is available.

Run with:  pytest backend/tests/test_oauth_litellm_integration.py -v -s -m integration
"""

from __future__ import annotations

import os

import httpx
import litellm
import pytest
import pytest_asyncio

from backend.app.core.oauth import OAUTH_EXTRA_HEADERS, OAUTH_SYSTEM_PROMPT_PREFIX, is_oauth_token
from backend.app.agent.api_key_resolver import ApiKeyResolver

GATEWAY_URL = "http://localhost:18789"
GATEWAY_ENDPOINT = f"{GATEWAY_URL}/api/v1/provider-api-keys/anthropic"
CHEAP_MODEL = "anthropic/claude-haiku-4-5-20251001"
GATEWAY_TIMEOUT = 5.0
API_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_oauth_token() -> tuple[str, str] | None:
    """Fetch an OAuth token from the gateway. Returns (token, auth_mode) or None."""
    try:
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
            resp = await client.get(GATEWAY_ENDPOINT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            token = data.get("encryptedValue", "")
            auth_mode = resp.headers.get("x-auth-mode", "")
            return (token, auth_mode)
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return None


def _gateway_unavailable_reason() -> str | None:
    """Return a skip reason if the gateway is unreachable, else None."""
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(_fetch_oauth_token())
    if result is None:
        return "Gateway not reachable or no Anthropic key configured"
    token, auth_mode = result
    if auth_mode != "oauth":
        return "Gateway did not return an OAuth token (x-auth-mode != oauth)"
    if not is_oauth_token(token):
        return f"Token does not start with sk-ant-oat (got: {token[:15]}...)"
    return None


# Cache the result so we only probe once per session
_skip_reason: str | None = None
_skip_checked = False


def _get_skip_reason() -> str | None:
    global _skip_reason, _skip_checked
    if not _skip_checked:
        _skip_reason = _gateway_unavailable_reason()
        _skip_checked = True
    return _skip_reason


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def oauth_token() -> str:
    """Fetch a fresh OAuth token from the gateway, skip if unavailable."""
    result = await _fetch_oauth_token()
    if result is None:
        pytest.skip("Gateway not reachable")
    token, auth_mode = result
    if auth_mode != "oauth" or not is_oauth_token(token):
        pytest.skip(f"No OAuth token available (auth_mode={auth_mode})")
    return token


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_gateway_returns_oauth_token():
    """GET the gateway endpoint and verify it returns an OAuth token."""
    result = await _fetch_oauth_token()
    if result is None:
        pytest.skip("Gateway not reachable or no Anthropic key configured")

    token, auth_mode = result
    print(f"\n  auth_mode: {auth_mode}")
    print(f"  token prefix: {token[:20]}...")
    print(f"  token length: {len(token)}")

    assert auth_mode == "oauth", f"Expected x-auth-mode: oauth, got: {auth_mode}"
    assert is_oauth_token(token), f"Token should start with sk-ant-oat, got: {token[:15]}"


@pytest.mark.asyncio
async def test_raw_litellm_acompletion_with_oauth(oauth_token: str):
    """Call litellm.acompletion with a real OAuth token + extra headers."""
    print(f"\n  Using OAuth token: {oauth_token[:20]}...")
    print(f"  Extra headers: {OAUTH_EXTRA_HEADERS}")

    response = await litellm.acompletion(
        model=CHEAP_MODEL,
        api_key=oauth_token,
        extra_headers=OAUTH_EXTRA_HEADERS,
        messages=[{"role": "user", "content": "Say PONG"}],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Response: {content}")
    assert content is not None and len(content) > 0, "Expected non-empty response"


@pytest.mark.asyncio
async def test_litellm_handles_oauth_natively_without_extra_headers(oauth_token: str):
    """Verify litellm v1.82+ handles OAuth tokens natively (no extra_headers needed).

    DISCOVERY (2026-03-22): litellm.llms.anthropic.common_utils.optionally_handle_anthropic_oauth()
    detects sk-ant-oat tokens and automatically:
    - Switches from x-api-key to Authorization: Bearer
    - Adds anthropic-beta: oauth-2025-04-20
    - Adds anthropic-dangerous-direct-browser-access: true

    This means our OAUTH_EXTRA_HEADERS injection is redundant (but harmless).
    If this test FAILS, litellm has regressed and we need the extra headers again.
    """
    print(f"\n  Calling WITHOUT extra_headers (litellm should handle natively)...")

    response = await litellm.acompletion(
        model=CHEAP_MODEL,
        api_key=oauth_token,
        messages=[{"role": "user", "content": "Say PONG"}],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Response (no extra_headers): {content}")
    assert content is not None and len(content) > 0, (
        "litellm failed to handle OAuth natively — "
        "OAUTH_EXTRA_HEADERS injection is still required"
    )


@pytest.mark.asyncio
async def test_resolver_to_litellm_full_chain(oauth_token: str):
    """ApiKeyResolver with real OAuth token → resolve_all → litellm.acompletion."""
    resolver = ApiKeyResolver(
        injected_keys={"anthropic": oauth_token},
        provider_aliases={"anthropic": "anthropic"},
        litellm_prefixes={},
        persistence=None,
    )

    normalized_model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
        CHEAP_MODEL, CHEAP_MODEL,
    )

    print(f"\n  Normalized model: {normalized_model}")
    print(f"  extra_kwargs keys: {list(extra_kwargs.keys())}")
    assert "extra_headers" in extra_kwargs, "Resolver should inject extra_headers for OAuth token"
    assert extra_kwargs["extra_headers"] == OAUTH_EXTRA_HEADERS

    response = await litellm.acompletion(
        model=normalized_model,
        messages=[{"role": "user", "content": "Say PONG"}],
        max_tokens=16,
        timeout=API_TIMEOUT,
        **extra_kwargs,
    )

    content = response.choices[0].message.content
    print(f"  Response: {content}")
    assert content is not None and len(content) > 0, "Expected non-empty response"


@pytest.mark.asyncio
async def test_regular_api_key_still_works():
    """If ANTHROPIC_API_KEY env var is set, verify it works without extra headers."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    if is_oauth_token(api_key):
        pytest.skip("ANTHROPIC_API_KEY is an OAuth token, not a regular key")

    print(f"\n  Using regular API key: {api_key[:10]}...")

    response = await litellm.acompletion(
        model=CHEAP_MODEL,
        api_key=api_key,
        messages=[{"role": "user", "content": "Say PONG"}],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Response: {content}")
    assert content is not None and len(content) > 0, "Expected non-empty response"


# ---------------------------------------------------------------------------
# Model sync — /v1/models with OAuth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_models_endpoint_with_oauth(oauth_token: str):
    """/v1/models works with OAuth Bearer auth — returns only accessible models.

    This is the ROOT CAUSE of the 400 errors: model sync was scraping docs
    instead of calling /v1/models with OAuth, so users saw models their
    Claude Max subscription can't actually use.
    """
    headers = {
        **OAUTH_EXTRA_HEADERS,
        "authorization": f"Bearer {oauth_token}",
        "anthropic-version": "2023-06-01",
    }

    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
        resp = await client.get("https://api.anthropic.com/v1/models", headers=headers)

    print(f"\n  /v1/models status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    models = [m["id"] for m in data.get("data", [])]
    print(f"  Accessible models ({len(models)}):")
    for m in sorted(models):
        print(f"    {m}")

    assert len(models) > 0, "Expected at least one accessible model"
    # Verify these are real model IDs, not scraped junk
    assert all(m.startswith("claude-") for m in models), "All models should start with 'claude-'"


@pytest.mark.asyncio
async def test_model_sync_oauth_returns_only_accessible_models(oauth_token: str):
    """Model sync with OAuth should return ONLY models the account can access.

    This reproduces the root cause: the old code scraped docs (20+ models),
    users picked an inaccessible one, and got 400. The /v1/models API with
    OAuth Bearer auth returns only the ~9 models Claude Max can actually use.

    We call the API directly here (same logic as the fixed _fetch_anthropic_api)
    to avoid importing the jobs module which pulls in sqlalchemy.
    """
    headers = {
        **OAUTH_EXTRA_HEADERS,
        "authorization": f"Bearer {oauth_token}",
        "anthropic-version": "2023-06-01",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get("https://api.anthropic.com/v1/models", headers=headers)

    assert resp.status_code == 200
    models = resp.json().get("data", [])
    model_ids = [m["id"] for m in models]

    print(f"\n  API returns {len(model_ids)} accessible models:")
    for m in sorted(model_ids):
        print(f"    {m}")

    assert len(model_ids) > 0, "Expected at least one model"
    assert len(model_ids) < 20, (
        f"Got {len(model_ids)} models — suspiciously high, might still be scraping"
    )

    # Verify a model works with the Claude Code identity system prompt
    test_model = model_ids[0]
    print(f"\n  Verifying model '{test_model}' works with Claude Code identity...")
    response = await litellm.acompletion(
        model=f"anthropic/{test_model}",
        api_key=oauth_token,
        extra_headers=OAUTH_EXTRA_HEADERS,
        messages=[
            {"role": "system", "content": [
                {"type": "text", "text": OAUTH_SYSTEM_PROMPT_PREFIX},
                {"type": "text", "text": "You are a helpful assistant."},
            ]},
            {"role": "user", "content": "Say OK"},
        ],
        max_tokens=8,
        timeout=API_TIMEOUT,
    )
    content = response.choices[0].message.content
    print(f"  {test_model} responded: {content}")
    assert content is not None


# ---------------------------------------------------------------------------
# Claude Code identity — the critical fix for Sonnet/Opus with OAuth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sonnet_fails_without_claude_code_identity(oauth_token: str):
    """Sonnet 400s with OAuth when the Claude Code identity is missing.

    This is the ROOT CAUSE discovered 2026-03-22: the Anthropic API requires
    'You are Claude Code, Anthropic's official CLI for Claude.' in the system
    prompt for Claude Max OAuth tokens when calling Sonnet/Opus models.
    """
    print("\n  Calling Sonnet WITHOUT Claude Code identity...")

    try:
        await litellm.acompletion(
            model="anthropic/claude-sonnet-4-6",
            api_key=oauth_token,
            extra_headers=OAUTH_EXTRA_HEADERS,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say OK"},
            ],
            max_tokens=16,
            timeout=API_TIMEOUT,
        )
        pytest.fail("Expected 400 BadRequestError but call succeeded")
    except Exception as e:
        print(f"  Got expected error: {type(e).__name__}")
        assert "400" in str(e) or "BadRequest" in type(e).__name__, (
            f"Expected 400 BadRequestError, got: {e}"
        )


@pytest.mark.asyncio
async def test_sonnet_succeeds_with_claude_code_identity(oauth_token: str):
    """Sonnet works with OAuth when the Claude Code identity IS present.

    The fix: prepend OAUTH_SYSTEM_PROMPT_PREFIX to the system prompt.
    """
    print("\n  Calling Sonnet WITH Claude Code identity...")

    response = await litellm.acompletion(
        model="anthropic/claude-sonnet-4-6",
        api_key=oauth_token,
        extra_headers=OAUTH_EXTRA_HEADERS,
        messages=[
            {"role": "system", "content": [
                {"type": "text", "text": OAUTH_SYSTEM_PROMPT_PREFIX},
                {"type": "text", "text": "You are a helpful assistant."},
            ]},
            {"role": "user", "content": "Say OK"},
        ],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Sonnet responded: {content}")
    assert content is not None and len(content) > 0


@pytest.mark.asyncio
async def test_opus_succeeds_with_claude_code_identity(oauth_token: str):
    """Opus works with OAuth when the Claude Code identity IS present."""
    print("\n  Calling Opus WITH Claude Code identity...")

    response = await litellm.acompletion(
        model="anthropic/claude-opus-4-6",
        api_key=oauth_token,
        extra_headers=OAUTH_EXTRA_HEADERS,
        messages=[
            {"role": "system", "content": [
                {"type": "text", "text": OAUTH_SYSTEM_PROMPT_PREFIX},
                {"type": "text", "text": "You are a helpful assistant."},
            ]},
            {"role": "user", "content": "Say OK"},
        ],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Opus responded: {content}")
    assert content is not None and len(content) > 0


@pytest.mark.asyncio
async def test_haiku_works_without_claude_code_identity(oauth_token: str):
    """Haiku works with OAuth even WITHOUT the Claude Code identity.

    Haiku has looser restrictions — this confirms only Sonnet/Opus need the fix.
    """
    print("\n  Calling Haiku WITHOUT Claude Code identity...")

    response = await litellm.acompletion(
        model=CHEAP_MODEL,
        api_key=oauth_token,
        extra_headers=OAUTH_EXTRA_HEADERS,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say OK"},
        ],
        max_tokens=16,
        timeout=API_TIMEOUT,
    )

    content = response.choices[0].message.content
    print(f"  Haiku responded: {content}")
    assert content is not None and len(content) > 0
