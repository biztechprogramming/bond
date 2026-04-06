"""Tests for OAuth token flow through the Python backend to litellm.

Covers: detection, header injection, ApiKeyResolver integration,
end-to-end litellm.acompletion kwargs, header consistency with gateway,
and gateway token refresh.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.core.oauth import (
    OAUTH_EXTRA_HEADERS,
    OAUTH_SYSTEM_PROMPT_PREFIX,
    OAUTH_TOKEN_PREFIX,
    detect_key_type,
    ensure_oauth_system_prefix,
    get_oauth_extra_headers,
    is_oauth_token,
    resolve_provider_key_via_gateway,
)
from backend.app.agent.api_key_resolver import ApiKeyResolver


# ── Fixtures ──────────────────────────────────────────────────────────────


FAKE_OAUTH_TOKEN = "sk-ant-oat-abc123-fake-oauth-token"
FAKE_API_KEY = "sk-ant-api01-regular-api-key"
RANDOM_STRING = "some-random-string-not-a-key"

# Expected headers — must match gateway/src/oauth/provider-oauth.ts buildOAuthHeaders()
EXPECTED_HEADERS = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-cli/2.1.81",
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
}


def _make_resolver(injected: dict[str, str] | None = None) -> ApiKeyResolver:
    """Create an ApiKeyResolver with injected keys and no persistence."""
    return ApiKeyResolver(
        injected_keys=injected or {},
        provider_aliases={"anthropic": "anthropic"},
        litellm_prefixes={},
        persistence=None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. OAuth token detection
# ═══════════════════════════════════════════════════════════════════════════


class TestIsOAuthToken:
    def test_detects_oauth_token(self):
        assert is_oauth_token(FAKE_OAUTH_TOKEN) is True

    def test_rejects_regular_api_key(self):
        assert is_oauth_token(FAKE_API_KEY) is False

    def test_rejects_random_string(self):
        assert is_oauth_token(RANDOM_STRING) is False

    def test_rejects_empty_string(self):
        assert is_oauth_token("") is False

    def test_prefix_constant(self):
        assert OAUTH_TOKEN_PREFIX == "sk-ant-oat"


class TestDetectKeyType:
    def test_oauth_token_detected(self):
        assert detect_key_type("llm.api_key.anthropic", FAKE_OAUTH_TOKEN) == "oauth_token"

    def test_api_key_detected(self):
        assert detect_key_type("llm.api_key.anthropic", FAKE_API_KEY) == "api_key"

    def test_unknown_prefix_defaults_to_oauth(self):
        assert detect_key_type("llm.api_key.anthropic", "unknown-prefix-key") == "oauth_token"

    def test_non_anthropic_provider_defaults_to_api_key(self):
        assert detect_key_type("llm.api_key.openai", FAKE_OAUTH_TOKEN) == "api_key"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Extra headers injection
# ═══════════════════════════════════════════════════════════════════════════


class TestGetOAuthExtraHeaders:
    def test_returns_headers_for_oauth_token(self):
        headers = get_oauth_extra_headers(FAKE_OAUTH_TOKEN)
        assert headers == EXPECTED_HEADERS

    def test_returns_empty_for_regular_key(self):
        assert get_oauth_extra_headers(FAKE_API_KEY) == {}

    def test_returns_empty_for_random_string(self):
        assert get_oauth_extra_headers(RANDOM_STRING) == {}

    def test_returns_copy_not_reference(self):
        h1 = get_oauth_extra_headers(FAKE_OAUTH_TOKEN)
        h2 = get_oauth_extra_headers(FAKE_OAUTH_TOKEN)
        assert h1 is not h2
        assert h1 is not OAUTH_EXTRA_HEADERS


# ═══════════════════════════════════════════════════════════════════════════
# 3. ApiKeyResolver.resolve_all() integration
# ═══════════════════════════════════════════════════════════════════════════


class TestApiKeyResolverOAuth:
    @pytest.mark.asyncio
    async def test_oauth_primary_injects_extra_headers(self):
        resolver = _make_resolver({"anthropic": FAKE_OAUTH_TOKEN})
        _model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )
        assert "extra_headers" in extra_kwargs
        assert extra_kwargs["extra_headers"] == EXPECTED_HEADERS
        assert extra_kwargs["api_key"] == FAKE_OAUTH_TOKEN

    @pytest.mark.asyncio
    async def test_regular_key_no_extra_headers(self):
        resolver = _make_resolver({"anthropic": FAKE_API_KEY})
        _model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )
        assert "extra_headers" not in extra_kwargs
        assert extra_kwargs["api_key"] == FAKE_API_KEY

    @pytest.mark.asyncio
    async def test_oauth_utility_injects_extra_headers(self):
        resolver = _make_resolver({"anthropic": FAKE_OAUTH_TOKEN})
        _model, _extra_kwargs, utility_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )
        assert "extra_headers" in utility_kwargs
        assert utility_kwargs["extra_headers"] == EXPECTED_HEADERS

    @pytest.mark.asyncio
    async def test_mixed_primary_oauth_utility_regular(self):
        """Primary is OAuth, utility is regular — only primary gets headers."""
        resolver = ApiKeyResolver(
            injected_keys={"anthropic": FAKE_OAUTH_TOKEN, "openai": FAKE_API_KEY},
            provider_aliases={"anthropic": "anthropic", "openai": "openai"},
            litellm_prefixes={},
            persistence=None,
        )
        _model, extra_kwargs, utility_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "openai/gpt-4o",
        )
        assert "extra_headers" in extra_kwargs
        assert "extra_headers" not in utility_kwargs

    @pytest.mark.asyncio
    async def test_get_extra_headers_after_resolve(self):
        resolver = _make_resolver({"anthropic": FAKE_OAUTH_TOKEN})
        await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )
        assert resolver.get_extra_headers() == EXPECTED_HEADERS

    @pytest.mark.asyncio
    async def test_get_extra_headers_empty_for_regular_key(self):
        resolver = _make_resolver({"anthropic": FAKE_API_KEY})
        await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )
        assert resolver.get_extra_headers() == {}


# ═══════════════════════════════════════════════════════════════════════════
# 4. Headers actually reach litellm.acompletion()
# ═══════════════════════════════════════════════════════════════════════════


class TestHeadersReachLitellm:
    """Verify the full chain: resolver → extra_kwargs → **kwargs → litellm.acompletion."""

    @pytest.mark.asyncio
    async def test_oauth_headers_passed_to_acompletion(self):
        """Mock litellm.acompletion and verify extra_headers arrives in kwargs.

        This simulates the worker pattern:
            _iter_kwargs = extra_kwargs  (from resolver)
            litellm.acompletion(model=..., messages=..., **_iter_kwargs)
        """
        resolver = _make_resolver({"anthropic": FAKE_OAUTH_TOKEN})
        _model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )

        # Simulate litellm.acompletion(**_iter_kwargs) — capture the kwargs
        captured_kwargs: dict = {}
        async def fake_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "test"
            return resp

        await fake_acompletion(
            model="anthropic/claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
            **extra_kwargs,
        )

        assert "extra_headers" in captured_kwargs
        assert captured_kwargs["extra_headers"] == EXPECTED_HEADERS
        assert captured_kwargs["api_key"] == FAKE_OAUTH_TOKEN

    @pytest.mark.asyncio
    async def test_regular_key_no_headers_to_acompletion(self):
        """Regular API key should NOT pass extra_headers."""
        resolver = _make_resolver({"anthropic": FAKE_API_KEY})
        _model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )

        captured_kwargs: dict = {}
        async def fake_acompletion(**kwargs):
            captured_kwargs.update(kwargs)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            return resp

        await fake_acompletion(
            model="anthropic/claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
            **extra_kwargs,
        )

        assert "extra_headers" not in captured_kwargs
        assert captured_kwargs["api_key"] == FAKE_API_KEY

    @pytest.mark.asyncio
    async def test_kwargs_spread_matches_worker_pattern(self):
        """Verify that extra_kwargs can be spread with ** just like the worker does.

        In worker.py line ~951: response = await _cancellable_llm_call(..., **_iter_kwargs)
        And _cancellable_llm_call passes **kwargs to litellm.acompletion(**kwargs).
        """
        resolver = _make_resolver({"anthropic": FAKE_OAUTH_TOKEN})
        _model, extra_kwargs, _util_kwargs, _util_model = await resolver.resolve_all(
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5-20251001",
        )

        # The worker builds kwargs like this:
        final_kwargs = dict(
            model="anthropic/claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=8192,
        )
        final_kwargs.update(extra_kwargs)

        assert "extra_headers" in final_kwargs
        assert final_kwargs["extra_headers"] == EXPECTED_HEADERS
        assert final_kwargs["api_key"] == FAKE_OAUTH_TOKEN


# ═══════════════════════════════════════════════════════════════════════════
# 5. Header consistency with gateway
# ═══════════════════════════════════════════════════════════════════════════


class TestHeaderConsistencyWithGateway:
    """OAUTH_EXTRA_HEADERS must match gateway/src/oauth/provider-oauth.ts buildOAuthHeaders()."""

    def test_has_all_required_keys(self):
        required = {
            "anthropic-beta",
            "user-agent",
            "x-app",
            "anthropic-dangerous-direct-browser-access",
        }
        assert set(OAUTH_EXTRA_HEADERS.keys()) == required

    def test_anthropic_beta_value(self):
        assert OAUTH_EXTRA_HEADERS["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"

    def test_user_agent_value(self):
        assert OAUTH_EXTRA_HEADERS["user-agent"] == "claude-cli/2.1.81"

    def test_x_app_value(self):
        assert OAUTH_EXTRA_HEADERS["x-app"] == "cli"

    def test_dangerous_direct_browser_access(self):
        assert OAUTH_EXTRA_HEADERS["anthropic-dangerous-direct-browser-access"] == "true"

    def test_exact_match(self):
        assert OAUTH_EXTRA_HEADERS == EXPECTED_HEADERS


# ═══════════════════════════════════════════════════════════════════════════
# 6. Gateway token refresh (unit tests with mocks)
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveProviderKeyViaGateway:
    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        mock_resp = httpx.Response(404, request=httpx.Request("GET", "http://test/"))
        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=1,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_key_and_type_from_response(self):
        mock_resp = httpx.Response(
            200,
            json={"encryptedValue": FAKE_API_KEY, "keyType": "api_key"},
            headers={"x-auth-mode": "api-key"},
            request=httpx.Request("GET", "http://test/"),
        )
        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=1,
            )
            assert result == (FAKE_API_KEY, "api_key")

    @pytest.mark.asyncio
    async def test_oauth_auth_mode_overrides_key_type(self):
        mock_resp = httpx.Response(
            200,
            json={"encryptedValue": FAKE_OAUTH_TOKEN, "keyType": "api_key"},
            headers={"x-auth-mode": "oauth"},
            request=httpx.Request("GET", "http://test/"),
        )
        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(return_value=mock_resp)
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=1,
            )
            assert result is not None
            api_key, key_type = result
            assert api_key == FAKE_OAUTH_TOKEN
            assert key_type == "oauth_token"

    @pytest.mark.asyncio
    async def test_retries_on_connect_error(self):
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("Connection refused")
            return httpx.Response(
                200,
                json={"encryptedValue": FAKE_API_KEY, "keyType": "api_key"},
                headers={"x-auth-mode": "api-key"},
                request=httpx.Request("GET", url),
            )

        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient, \
             patch("backend.app.core.oauth.asyncio.sleep", new_callable=AsyncMock):
            client = AsyncMock()
            client.get = mock_get
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=5, retry_delay=0.01,
            )
            assert result == (FAKE_API_KEY, "api_key")
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_after_exhausting_retries(self):
        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient, \
             patch("backend.app.core.oauth.asyncio.sleep", new_callable=AsyncMock):
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=2, retry_delay=0.01,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_non_connect_error(self):
        with patch("backend.app.core.oauth.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client

            result = await resolve_provider_key_via_gateway(
                "anthropic", gateway_url="http://test", retries=3,
            )
            assert result is None


# ── ensure_oauth_system_prefix ────────────────────────────────────────────


class TestEnsureOauthSystemPrefix:
    """Tests for the centralized OAuth system prompt prefix injection."""

    def test_no_op_for_non_oauth(self):
        msgs = [{"role": "system", "content": "Hello"}, {"role": "user", "content": "Hi"}]
        result = ensure_oauth_system_prefix(msgs, api_key="sk-ant-api-regular")
        assert result[0]["content"] == "Hello"

    def test_no_op_without_api_key(self):
        msgs = [{"role": "user", "content": "Hi"}]
        original_len = len(msgs)
        ensure_oauth_system_prefix(msgs)
        assert len(msgs) == original_len

    def test_injects_prefix_string_system(self):
        msgs = [{"role": "system", "content": "My system prompt"}, {"role": "user", "content": "Hi"}]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        assert OAUTH_SYSTEM_PROMPT_PREFIX in msgs[0]["content"]
        assert "My system prompt" in msgs[0]["content"]

    def test_injects_prefix_list_system(self):
        msgs = [{
            "role": "system",
            "content": [{"type": "text", "text": "My prompt", "cache_control": {"type": "ephemeral"}}],
        }]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["text"] == OAUTH_SYSTEM_PROMPT_PREFIX
        assert content[1]["text"] == "My prompt"

    def test_inserts_system_message_when_missing(self):
        msgs = [{"role": "user", "content": "Hi"}]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == OAUTH_SYSTEM_PROMPT_PREFIX

    def test_idempotent(self):
        msgs = [{"role": "system", "content": "My prompt"}]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        content_after_first = msgs[0]["content"]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        assert msgs[0]["content"] == content_after_first

    def test_idempotent_list_format(self):
        msgs = [{
            "role": "system",
            "content": [{"type": "text", "text": "My prompt"}],
        }]
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        length_after_first = len(msgs[0]["content"])
        ensure_oauth_system_prefix(msgs, api_key=FAKE_OAUTH_TOKEN)
        assert len(msgs[0]["content"]) == length_after_first

    def test_detects_oauth_from_extra_kwargs(self):
        msgs = [{"role": "user", "content": "Hi"}]
        extra = {"api_key": FAKE_OAUTH_TOKEN, "extra_headers": OAUTH_EXTRA_HEADERS}
        ensure_oauth_system_prefix(msgs, extra_kwargs=extra)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"

    def test_no_inject_when_extra_kwargs_has_regular_key(self):
        msgs = [{"role": "user", "content": "Hi"}]
        extra = {"api_key": "sk-ant-api-regular"}
        ensure_oauth_system_prefix(msgs, extra_kwargs=extra)
        assert len(msgs) == 1
