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
async def test_cancellable_llm_call_with_tool_history(oauth_token: str):
    """Simulate _cancellable_llm_call with a realistic agent-loop payload.

    Reproduces the scenario: Opus + OAuth + system prompt in Anthropic block
    format + tool calls in history + tool results. This is what the agent loop
    builds by the time a coding_agent tool call has been made and the next
    LLM iteration fires.
    """
    import asyncio
    import json
    from backend.app.core.oauth import OAUTH_SYSTEM_PROMPT_PREFIX, ensure_oauth_system_prefix

    print("\n  Building realistic agent-loop message payload...")

    # 1. System message in Anthropic block format (as built by _run_agent_loop)
    system_prompt = (
        "You are a helpful AI assistant.\n\n"
        "## Tools\nYou have access to tools for reading and writing files."
    )
    messages = [{
        "role": "system",
        "content": [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ],
    }]

    # 2. OAuth prefix injection (same as worker.py line 638-639)
    extra_kwargs = {
        "api_key": oauth_token,
        "extra_headers": dict(OAUTH_EXTRA_HEADERS),
    }
    ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)
    print(f"  System message has {len(messages[0]['content'])} content blocks")

    # 3. Simulate conversation history with multiple turns
    messages.append({"role": "user", "content": "Please create a coding agent to fix the bug."})
    messages.append({
        "role": "assistant",
        "content": "I'll create a coding agent to fix that bug.",
        "tool_calls": [{
            "id": "call_001",
            "type": "function",
            "function": {
                "name": "coding_agent",
                "arguments": json.dumps({"task": "Fix the bug in auth.py", "working_directory": "/tmp"}),
            },
        }],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": "call_001",
        "content": json.dumps({
            "status": "started",
            "agent_type": "claude-code",
            "working_directory": "/tmp",
            "baseline_commit": "abc12345",
            "message": "Coding agent (claude-code) started in /tmp.",
        }),
    })

    # 4. Add a few more turns to simulate depth (52 messages in the real error)
    for i in range(5):
        messages.append({"role": "user", "content": f"SYSTEM: Turn {i+2}/10 budget note"})
        messages.append({
            "role": "assistant",
            "content": f"Continuing analysis, iteration {i+2}.",
            "tool_calls": [{
                "id": f"call_{i+10:03d}",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": f"/tmp/file{i}.py"}),
                },
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{i+10:03d}",
            "content": json.dumps({"content": f"# File {i} content\ndef func_{i}(): pass\n", "path": f"/tmp/file{i}.py"}),
        })

    # Final user message that triggers the failing LLM call
    messages.append({"role": "user", "content": "What's the status of the coding agent?"})

    print(f"  Total messages: {len(messages)}")
    print(f"  Model: anthropic/claude-opus-4-6")

    # 5. Tool definitions (simplified but valid)
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "coding_agent",
                "description": "Spawn a background coding agent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "working_directory": {"type": "string"},
                    },
                    "required": ["task", "working_directory"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    ]

    # 6. Call litellm.acompletion exactly as _cancellable_llm_call does
    #    (_cancellable_llm_call is just: litellm.acompletion(**kwargs) with interrupt support)
    try:
        response = await litellm.acompletion(
            model="anthropic/claude-opus-4-6",
            messages=messages,
            tools=tool_defs,
            temperature=0.7,
            max_tokens=8192,
            **extra_kwargs,
        )

        assert response is not None, "Expected a response from litellm.acompletion"
        content = response.choices[0].message.content
        print(f"  Response: {(content or '')[:100]}")
        print("  ✅ Realistic agent-loop payload succeeded")
    except Exception as e:
        print(f"  ❌ FAILED: {type(e).__name__}: {e}")
        raise


@pytest.mark.asyncio
async def test_cancellable_with_model_dump_and_lifecycle(oauth_token: str):
    """Simulate with model_dump() output and lifecycle injection.

    model_dump() includes null fields (function_call: null, provider_specific_fields: null)
    which might trip up litellm's Anthropic translation. Also tests lifecycle injection
    appending to the OAuth prefix block (block 0), which is what happens in production.
    """
    import json
    from backend.app.core.oauth import OAUTH_SYSTEM_PROMPT_PREFIX, ensure_oauth_system_prefix

    print("\n  Building payload with model_dump() style messages + lifecycle injection...")

    # 1. System prompt in Anthropic block format
    system_prompt = "You are a helpful AI assistant with tools."
    messages = [{
        "role": "system",
        "content": [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
        ],
    }]

    # 2. OAuth prefix injection
    extra_kwargs = {
        "api_key": oauth_token,
        "extra_headers": dict(OAUTH_EXTRA_HEADERS),
    }
    ensure_oauth_system_prefix(messages, extra_kwargs=extra_kwargs)

    # 3. Simulate lifecycle injection into the LAST text block (the actual system prompt)
    #    The FIX: lifecycle must target the last text block, NOT block 0 (OAuth prefix).
    #    Block 0 is "You are Claude Code..." which Anthropic's OAuth validation checks strictly.
    for block in reversed(messages[0]["content"]):
        if isinstance(block, dict) and block.get("type") == "text":
            block["text"] += (
                "\n\n## Current Phase: IMPLEMENTING\n"
                "Focus on writing correct, tested code. Follow existing patterns."
            )
            break
    print(f"  Block 0 (OAuth prefix) untouched: {messages[0]['content'][0]['text'][:60]!r}")
    print(f"  Block 1 (system prompt) has lifecycle: {'IMPLEMENTING' in messages[0]['content'][1]['text']}")

    # 4. Build 50+ messages using model_dump()-style dicts (with null fields)
    messages.append({"role": "user", "content": "Fix the authentication bug in auth.py"})

    for i in range(24):
        # Assistant message in model_dump() format (includes null fields)
        messages.append({
            "content": f"I'll read file{i}.py to understand the code." if i % 3 != 2 else None,
            "role": "assistant",
            "tool_calls": [{
                "function": {
                    "arguments": json.dumps({"path": f"/tmp/file{i}.py"}),
                    "name": "read_file",
                },
                "id": f"call_{i:03d}",
                "type": "function",
            }],
            "function_call": None,  # model_dump() includes this
            "provider_specific_fields": None,  # model_dump() includes this
        })
        # Tool result
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{i:03d}",
            "content": json.dumps({
                "content": f"def func_{i}():\n    return {i}\n",
                "path": f"/tmp/file{i}.py",
            }),
        })

    # Final user message
    messages.append({"role": "user", "content": "Now fix the bug."})

    print(f"  Total messages: {len(messages)}")

    tool_defs = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }]

    try:
        response = await litellm.acompletion(
            model="anthropic/claude-opus-4-6",
            messages=messages,
            tools=tool_defs,
            temperature=0.7,
            max_tokens=8192,
            **extra_kwargs,
        )

        content = response.choices[0].message.content
        print(f"  Response: {(content or '')[:100]}")
        print("  ✅ 50+ messages with model_dump() nulls + lifecycle injection succeeded")
    except Exception as e:
        print(f"  ❌ FAILED: {type(e).__name__}: {e}")
        # Dump first few messages for debugging
        for i, m in enumerate(messages[:5]):
            print(f"  msg[{i}] role={m.get('role')} keys={list(m.keys())}")
        raise


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
