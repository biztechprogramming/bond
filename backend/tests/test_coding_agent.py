"""Tests for coding_agent tool — Design doc 037 §9.

Tests process lifecycle, timeout, kill, error handling, and output truncation.
Uses mock processes to avoid needing actual Claude/Codex/Pi binaries.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure bond root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.agent.tools.coding_agent import (
    AGENT_COMMANDS,
    CodingAgentProcess,
    _active_processes,
    _validate_working_directory,
    handle_coding_agent,
    kill_all_coding_agents,
    kill_coding_agent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> str:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


@pytest.fixture(autouse=True)
def _cleanup_active_processes():
    """Ensure active processes dict is clean between tests."""
    _active_processes.clear()
    yield
    _active_processes.clear()


# ---------------------------------------------------------------------------
# _validate_working_directory
# ---------------------------------------------------------------------------


class TestValidateWorkingDirectory:
    def test_nonexistent_directory(self) -> None:
        err = _validate_working_directory("/nonexistent/path/xyz")
        assert err is not None
        assert "not found" in err.lower()

    def test_valid_directory(self, tmp_workspace: str) -> None:
        with patch.dict(os.environ, {"BOND_ALLOWED_WORKSPACE_ROOTS": "/tmp"}):
            err = _validate_working_directory(tmp_workspace)
            assert err is None

    def test_directory_outside_allowed_roots(self, tmp_workspace: str) -> None:
        with patch.dict(os.environ, {"BOND_ALLOWED_WORKSPACE_ROOTS": "/opt/restricted"}):
            err = _validate_working_directory(tmp_workspace)
            assert err is not None
            assert "not under any allowed" in err.lower()


# ---------------------------------------------------------------------------
# CodingAgentProcess
# ---------------------------------------------------------------------------


class TestCodingAgentProcess:
    @pytest.mark.asyncio
    async def test_unknown_agent_type(self, tmp_workspace: str) -> None:
        cap = CodingAgentProcess("unknown_agent", "task", tmp_workspace)
        with pytest.raises(ValueError, match="Unknown agent type"):
            await cap.start()

    @pytest.mark.asyncio
    async def test_missing_binary(self, tmp_workspace: str) -> None:
        with patch("shutil.which", return_value=None):
            cap = CodingAgentProcess("claude", "task", tmp_workspace)
            with pytest.raises(FileNotFoundError, match="not found in PATH"):
                await cap.start()

    @pytest.mark.asyncio
    async def test_output_collection(self, tmp_workspace: str) -> None:
        """Spawn a real process (echo) and verify output is collected."""
        cap = CodingAgentProcess("claude", "hello", tmp_workspace, timeout_minutes=1)

        # Mock the command to use echo instead of claude
        with patch.dict(AGENT_COMMANDS, {
            "claude": {"binary": "echo", "args": [], "needs_pty": False},
        }):
            await cap.start()
            lines = []
            async for line in cap.stream_output():
                lines.append(line)
            exit_code = await cap.wait()

        assert exit_code == 0
        assert any("hello" in line for line in lines)
        assert cap.elapsed > 0

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self, tmp_workspace: str) -> None:
        """Process that exceeds timeout should be killed."""
        cap = CodingAgentProcess("claude", "task", tmp_workspace, timeout_minutes=0)
        # timeout_minutes=0 → 0 seconds timeout

        with patch.dict(AGENT_COMMANDS, {
            "claude": {"binary": "sleep", "args": ["60"], "needs_pty": False},
        }):
            # Override task in command (sleep ignores extra args)
            cap.task = ""
            await cap.start()
            exit_code = await cap.wait()

        assert exit_code == -1  # timed out
        assert cap._killed

    @pytest.mark.asyncio
    async def test_kill(self, tmp_workspace: str) -> None:
        """Explicit kill should terminate the process."""
        cap = CodingAgentProcess("claude", "task", tmp_workspace, timeout_minutes=5)

        with patch.dict(AGENT_COMMANDS, {
            "claude": {"binary": "sleep", "args": ["300"], "needs_pty": False},
        }):
            cap.task = ""
            await cap.start()
            assert cap.process is not None
            await cap.kill()

        assert cap._killed

    def test_get_output_truncation(self) -> None:
        """Output longer than token cap should be truncated."""
        cap = CodingAgentProcess("claude", "task", "/tmp", timeout_minutes=1)
        # Simulate 500 lines of 100 chars each
        cap.output_lines = [f"line-{i}: " + "x" * 90 for i in range(500)]
        output = cap.get_output(last_n=300)
        # Should be truncated (first 20 + last 100 + omission notice)
        assert "[... " in output and "lines omitted" in output

    def test_get_output_short(self) -> None:
        """Short output should not be truncated."""
        cap = CodingAgentProcess("claude", "task", "/tmp", timeout_minutes=1)
        cap.output_lines = ["line 1", "line 2", "line 3"]
        output = cap.get_output()
        assert output == "line 1\nline 2\nline 3"


# ---------------------------------------------------------------------------
# handle_coding_agent
# ---------------------------------------------------------------------------


class TestHandleCodingAgent:
    @pytest.mark.asyncio
    async def test_missing_task(self) -> None:
        result = await handle_coding_agent(
            {"working_directory": "/tmp"},
            {"agent_id": "test"},
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_working_directory(self) -> None:
        result = await handle_coding_agent(
            {"task": "do something"},
            {"agent_id": "test"},
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_agent_type(self) -> None:
        result = await handle_coding_agent(
            {"task": "do something", "working_directory": "/tmp", "agent_type": "gpt"},
            {"agent_id": "test"},
        )
        assert "error" in result
        assert "Unknown agent_type" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_api_key(self, tmp_workspace: str) -> None:
        with patch.dict(os.environ, {}, clear=False):
            # Remove ANTHROPIC_API_KEY if present
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch.dict(os.environ, {"BOND_ALLOWED_WORKSPACE_ROOTS": "/tmp"}):
                result = await handle_coding_agent(
                    {"task": "do something", "working_directory": tmp_workspace},
                    {"agent_id": "test"},
                )
        assert "error" in result
        assert "ANTHROPIC_API_KEY" in result["error"]

    @pytest.mark.asyncio
    async def test_nonexistent_directory(self) -> None:
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            result = await handle_coding_agent(
                {"task": "do something", "working_directory": "/nonexistent/path"},
                {"agent_id": "test"},
            )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_successful_execution(self, tmp_workspace: str) -> None:
        """Use echo as a mock coding agent to test the full flow."""
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key",
            "BOND_ALLOWED_WORKSPACE_ROOTS": "/tmp",
        }):
            with patch.dict(AGENT_COMMANDS, {
                "claude": {"binary": "echo", "args": ["completed:"], "needs_pty": False},
            }):
                result = await handle_coding_agent(
                    {"task": "test task", "working_directory": tmp_workspace},
                    {"agent_id": "test"},
                )

        assert result["status"] == "completed"
        assert result["exit_code"] == 0
        assert result["agent_type"] == "claude"
        assert "elapsed_seconds" in result
        assert "output" in result

    @pytest.mark.asyncio
    async def test_git_branch_checkout(self, tmp_workspace: str) -> None:
        """Branch checkout should happen before agent starts."""
        # Initialize a git repo
        proc = await asyncio.create_subprocess_exec(
            "git", "init", cwd=tmp_workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # Configure git identity for the test repo
        for cmd in [
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ]:
            p = await asyncio.create_subprocess_exec(
                *cmd, cwd=tmp_workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await p.communicate()
        # Create initial commit
        readme = Path(tmp_workspace) / "README.md"
        readme.write_text("# Test")
        for cmd in [
            ["git", "add", "."],
            ["git", "commit", "-m", "init"],
        ]:
            p = await asyncio.create_subprocess_exec(
                *cmd, cwd=tmp_workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await p.communicate()

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "test-key",
            "BOND_ALLOWED_WORKSPACE_ROOTS": "/tmp",
        }):
            with patch.dict(AGENT_COMMANDS, {
                "claude": {"binary": "echo", "args": ["done"], "needs_pty": False},
            }):
                result = await handle_coding_agent(
                    {
                        "task": "test task",
                        "working_directory": tmp_workspace,
                        "branch": "feature/test-branch",
                    },
                    {"agent_id": "test"},
                )

        assert result["status"] == "completed"
        assert result.get("branch") == "feature/test-branch"

        # Verify branch was created
        p = await asyncio.create_subprocess_exec(
            "git", "branch", "--show-current", cwd=tmp_workspace,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await p.communicate()
        assert "feature/test-branch" in stdout.decode().strip()


# ---------------------------------------------------------------------------
# kill functions
# ---------------------------------------------------------------------------


class TestKillFunctions:
    @pytest.mark.asyncio
    async def test_kill_coding_agent_no_process(self) -> None:
        result = await kill_coding_agent("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_kill_coding_agent_with_process(self, tmp_workspace: str) -> None:
        cap = CodingAgentProcess("claude", "task", tmp_workspace, timeout_minutes=5)
        with patch.dict(AGENT_COMMANDS, {
            "claude": {"binary": "sleep", "args": ["300"], "needs_pty": False},
        }):
            cap.task = ""
            await cap.start()
        _active_processes["test-agent"] = cap

        result = await kill_coding_agent("test-agent")
        assert result is True
        assert "test-agent" not in _active_processes

    @pytest.mark.asyncio
    async def test_kill_all_coding_agents(self, tmp_workspace: str) -> None:
        for i in range(3):
            cap = CodingAgentProcess("claude", "task", tmp_workspace, timeout_minutes=5)
            with patch.dict(AGENT_COMMANDS, {
                "claude": {"binary": "sleep", "args": ["300"], "needs_pty": False},
            }):
                cap.task = ""
                await cap.start()
            _active_processes[f"agent-{i}"] = cap

        count = await kill_all_coding_agents()
        assert count == 3
        assert len(_active_processes) == 0
