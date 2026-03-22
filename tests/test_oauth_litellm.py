"""Integration test: OAuth token + litellm.

Proves that a Claude Max OAuth token (sk-ant-oat01-*) can be used with
litellm.acompletion when the correct extra headers are supplied.
"""

import json
import os
from pathlib import Path

import pytest


def _read_oauth_token() -> str:
    """Read fresh OAuth access token from ~/.claude/.credentials.json."""
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        pytest.skip("No ~/.claude/.credentials.json found")
    creds = json.loads(creds_path.read_text())
    token = creds.get("claudeAiOauth", {}).get("accessToken")
    if not token or not token.startswith("sk-ant-oat"):
        pytest.skip("No valid OAuth token in credentials file")
    return token


OAUTH_HEADERS = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-cli/2.1.81",
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
}


@pytest.mark.asyncio
async def test_litellm_with_oauth_token():
    """Prove litellm can call Anthropic with an OAuth token + extra headers."""
    import litellm

    token = _read_oauth_token()

    # litellm needs the token as api_key and the OAuth headers as extra_headers.
    # OAuth tokens use Bearer auth, which litellm handles when the key starts
    # with sk-ant-oat (via its anthropic oauth handling).
    response = await litellm.acompletion(
        model="anthropic/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "Say hello in one word."}],
        max_tokens=32,
        api_key=token,
        extra_headers=OAUTH_HEADERS,
    )

    content = response.choices[0].message.content
    print(f"litellm response: {content}")
    assert content, "Expected non-empty response"
    assert len(content) > 0


@pytest.mark.asyncio
async def test_oauth_token_detection():
    """Verify we can detect OAuth tokens by prefix."""
    token = _read_oauth_token()
    assert token.startswith("sk-ant-oat")


@pytest.mark.asyncio
async def test_oauth_headers_are_correct():
    """Verify the header constants match what Anthropic expects."""
    assert "oauth-2025-04-20" in OAUTH_HEADERS["anthropic-beta"]
    assert "claude-code" in OAUTH_HEADERS["anthropic-beta"]
    assert OAUTH_HEADERS["x-app"] == "cli"
