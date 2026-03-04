"""Tests for Design Doc 020 — Agent Repo Autonomy.

Tests:
  - Named volume creation when launching container
  - handle_repo_pr error when /bond is not a git repo
  - handle_repo_pr push-only message when no GITHUB_TOKEN
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.sandbox.manager import SandboxManager, _PROJECT_ROOT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _make_agent(**overrides) -> dict:
    defaults = {
        "id": "agent-test-020",
        "name": "tester",
        "sandbox_image": "bond-agent-worker:latest",
        "model": "claude-sonnet-4-20250514",
        "utility_model": "claude-sonnet-4-6",
        "system_prompt": "You are helpful.",
        "tools": ["respond"],
        "max_iterations": 5,
        "api_keys": {},
        "provider_aliases": {},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Test: Named volume is created when launching container
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_named_volume_created_on_launch():
    """_create_worker_container should call 'docker volume create bond-clone-{id}'."""
    mgr = SandboxManager()
    agent = _make_agent()

    volume_create_called = False
    original_agent_id = agent["id"]

    async def fake_subprocess(*args, **kwargs):
        nonlocal volume_create_called
        cmd = list(args)
        # Detect volume create call
        if len(cmd) >= 4 and cmd[0] == "docker" and cmd[1] == "volume" and cmd[2] == "create":
            volume_name = cmd[3]
            assert volume_name == f"bond-clone-{original_agent_id}"
            volume_create_called = True
            return _mock_proc(0, b"")
        # docker rm -f (cleanup)
        if len(cmd) >= 3 and cmd[0] == "docker" and cmd[1] == "rm":
            return _mock_proc(0, b"")
        # docker run
        if len(cmd) >= 3 and cmd[0] == "docker" and cmd[1] == "run":
            return _mock_proc(0, b"abc123deadbeef\n")
        return _mock_proc(0, b"")

    config_path = _PROJECT_ROOT / "data" / "agent-configs" / f"{agent['id']}.json"

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        with patch("backend.app.config.get_settings") as mock_settings:
            mock_settings.return_value.bond_home = str(_PROJECT_ROOT / "data" / "test-bond-home")
            try:
                container_id = await mgr._create_worker_container(
                    agent, f"bond-tester-{agent['id']}", 18800, config_path,
                )
            except Exception:
                pass  # May fail on vault import etc — we only care about volume create

    assert volume_create_called, "docker volume create was not called"


# ---------------------------------------------------------------------------
# Test: handle_repo_pr returns error when /bond is not a git repo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repo_pr_no_git_repo(tmp_path):
    """handle_repo_pr should return error when /bond/.git doesn't exist."""
    from backend.app.agent.tools.native import handle_repo_pr

    # Patch Path("/bond") to point to tmp_path (no .git)
    with patch("backend.app.agent.tools.native.Path") as MockPath:
        # Make Path("/bond") return tmp_path, but keep other paths working
        def path_side_effect(p):
            if p == "/bond":
                return tmp_path
            return Path(p)

        MockPath.side_effect = path_side_effect

        result = await handle_repo_pr(
            arguments={
                "branch": "feat/test",
                "title": "Test PR",
                "body": "Testing",
                "files": {"test.txt": "hello"},
                "commit_message": "test commit",
            },
            context={},
        )

    assert "error" in result
    assert "not a git repository" in result["error"]


# ---------------------------------------------------------------------------
# Test: handle_repo_pr with no GITHUB_TOKEN returns push-only message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repo_pr_no_github_token(tmp_path):
    """handle_repo_pr should push but report no PR when GITHUB_TOKEN is empty."""
    from backend.app.agent.tools.native import handle_repo_pr

    # Create fake .git dir
    (tmp_path / ".git").mkdir()

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = b"some changes"
        result.stderr = b""
        return result

    with patch("backend.app.agent.tools.native.Path") as MockPath:
        def path_side_effect(p):
            if p == "/bond":
                return tmp_path
            return Path(p)
        MockPath.side_effect = path_side_effect

        with patch("subprocess.run", side_effect=fake_run):
            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
                result = await handle_repo_pr(
                    arguments={
                        "branch": "feat/test-tool",
                        "title": "Add test tool",
                        "body": "Adds a test tool",
                        "files": {"test.py": "print('hi')"},
                        "commit_message": "feat: add test tool",
                    },
                    context={},
                )

    assert result.get("status") == "pushed"
    assert "GITHUB_TOKEN" in result.get("message", "")
