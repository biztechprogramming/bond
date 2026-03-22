"""Integration test: verify Claude Code can authenticate inside the agent container.

This test builds the agent Docker image, starts a container with the host's
Claude Code credentials mounted, and runs `claude --print` to confirm the
OAuth tokens work end-to-end.

Requirements:
  - Docker running
  - ~/.claude/.credentials.json present on host (valid OAuth tokens)
  - Network access to Anthropic API

Run:
  pytest backend/tests/test_claude_code_auth.py -v -s
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
CLAUDE_JSON_PATH = Path.home() / ".claude.json"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = PROJECT_ROOT / "Dockerfile.agent"
IMAGE_NAME = "bond-agent-worker:test"
CONTAINER_NAME = "bond-claude-auth-test"


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _credentials_valid() -> bool:
    """Check that credentials file exists and has an access token."""
    if not CREDENTIALS_PATH.exists():
        return False
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        return bool(data.get("claudeAiOauth", {}).get("accessToken"))
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available"
)
skip_no_creds = pytest.mark.skipif(
    not _credentials_valid(), reason="No valid Claude Code credentials at ~/.claude/.credentials.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def agent_image():
    """Build the agent Docker image (cached across tests in this module)."""
    print(f"\n[setup] Building {IMAGE_NAME} from {DOCKERFILE}...")
    result = subprocess.run(
        ["docker", "build", "-f", str(DOCKERFILE), "-t", IMAGE_NAME, str(PROJECT_ROOT)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"Docker build failed:\n{result.stderr}")
    return IMAGE_NAME


@pytest.fixture()
def claude_container(agent_image):
    """Start a container with Claude credentials mounted, yield, then clean up."""
    # Remove any stale test container
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        # Override entrypoint so we get a plain shell (no worker startup needed)
        "--entrypoint", "sleep",
    ]

    # Mount credentials (rw so Claude Code can refresh expired OAuth tokens)
    if CREDENTIALS_PATH.exists():
        cmd.extend(["-v", f"{CREDENTIALS_PATH}:/home/bond-agent/.claude/.credentials.json:rw"])
    if CLAUDE_JSON_PATH.exists():
        cmd.extend(["-v", f"{CLAUDE_JSON_PATH}:/home/bond-agent/.claude.json:ro"])
    if SETTINGS_PATH.exists():
        cmd.extend(["-v", f"{SETTINGS_PATH}:/home/bond-agent/.claude/settings.json:ro"])

    cmd.extend([agent_image, "3600"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Container start failed:\n{result.stderr}")

    container_id = result.stdout.strip()[:12]
    # Brief pause to let the container settle
    time.sleep(1)

    yield container_id

    # Cleanup
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_no_docker
@skip_no_creds
class TestClaudeCodeAuth:
    """Verify Claude Code works inside the agent container."""

    def test_credentials_mounted(self, claude_container):
        """Credentials file is visible inside the container."""
        result = subprocess.run(
            ["docker", "exec", "-u", "bond-agent", claude_container,
             "test", "-f", "/home/bond-agent/.claude/.credentials.json"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, "Credentials file not found in container"

    def test_credentials_readable(self, claude_container):
        """bond-agent user can read the credentials file."""
        result = subprocess.run(
            ["docker", "exec", "-u", "bond-agent", claude_container,
             "cat", "/home/bond-agent/.claude/.credentials.json"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"Cannot read credentials: {result.stderr}"
        data = json.loads(result.stdout)
        assert "claudeAiOauth" in data, "Credentials file missing claudeAiOauth key"
        assert data["claudeAiOauth"].get("accessToken"), "No access token in credentials"

    def test_claude_code_installed(self, claude_container):
        """claude CLI is available in the container."""
        result = subprocess.run(
            ["docker", "exec", "-u", "bond-agent", claude_container,
             "claude", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, f"claude --version failed: {result.stderr}"
        assert "Claude Code" in result.stdout or result.stdout.strip(), \
            f"Unexpected version output: {result.stdout}"

    def test_claude_code_can_connect(self, claude_container):
        """claude --print actually connects to the API and gets a response.

        Tries ANTHROPIC_API_KEY env var first (preferred for long-lived tokens),
        falls back to mounted OAuth credentials.
        """
        env_args = []
        # If ANTHROPIC_API_KEY is set on the host or in env, inject it
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            env_args = ["-e", f"ANTHROPIC_API_KEY={api_key}"]

        base_cmd = ["docker", "exec"] + env_args + ["-u", "bond-agent", claude_container]
        result = subprocess.run(
            base_cmd + ["claude", "--print", "--max-turns", "1",
             "Respond with exactly the word PONG and nothing else."],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"claude --print failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "PONG" in result.stdout.upper(), (
            f"Expected PONG in response, got: {result.stdout}"
        )
