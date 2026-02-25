"""Tests for SandboxManager and HostExecutor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.sandbox.host import HostExecutor
from backend.app.sandbox.manager import SandboxManager


# ── HostExecutor tests ──


@pytest.mark.asyncio
async def test_host_executor_python():
    """Should execute Python code on host."""
    executor = HostExecutor()
    result = await executor.execute("python", "print('hello')", timeout=10)
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_host_executor_shell():
    """Should execute shell code on host."""
    executor = HostExecutor()
    result = await executor.execute("shell", "echo hello", timeout=10)
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_host_executor_unsupported_language():
    """Should return error for unsupported language."""
    executor = HostExecutor()
    result = await executor.execute("ruby", "puts 'hi'", timeout=10)
    assert "error" in result


@pytest.mark.asyncio
async def test_host_executor_timeout():
    """Should timeout long-running code."""
    executor = HostExecutor()
    result = await executor.execute("python", "import time; time.sleep(60)", timeout=1)
    assert "error" in result
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_host_executor_stderr():
    """Should capture stderr."""
    executor = HostExecutor()
    result = await executor.execute("python", "import sys; sys.stderr.write('oops')", timeout=10)
    assert "oops" in result["stderr"]


# ── SandboxManager tests (mocked docker) ──


@pytest.mark.asyncio
async def test_sandbox_manager_execute_mocked():
    """Should execute code via docker exec (mocked)."""
    manager = SandboxManager()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc

        result = await manager.execute("container123", "python", "print('hi')", timeout=10)
        assert result["exit_code"] == 0
        assert result["stdout"] == "output"


@pytest.mark.asyncio
async def test_sandbox_manager_execute_timeout():
    """Should handle timeout in docker exec."""
    manager = SandboxManager()

    mock_proc = MagicMock()
    mock_proc.kill = MagicMock()

    async def slow_communicate():
        await asyncio.sleep(10)
        return (b"", b"")

    mock_proc.communicate = slow_communicate

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc

        result = await manager.execute("container123", "python", "import time; time.sleep(60)", timeout=1)
        assert "error" in result
        assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_sandbox_manager_unsupported_language():
    """Should return error for unsupported language."""
    manager = SandboxManager()
    result = await manager.execute("container123", "ruby", "puts 'hi'")
    assert "error" in result


@pytest.mark.asyncio
async def test_sandbox_get_or_create_new_container():
    """Should create a new container when none exists."""
    manager = SandboxManager()

    # Mock: no existing container found, then create succeeds
    call_count = 0

    async def mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        proc = MagicMock()
        if call_count == 1:
            # docker ps -aq: no existing container
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
        else:
            # docker run: return container ID
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"abc123def456\n", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        container_id = await manager.get_or_create_container(
            "test-agent", "python:3.12-slim"
        )
        assert container_id == "abc123def456"


@pytest.mark.asyncio
async def test_sandbox_cleanup_idle():
    """Should clean up idle containers."""
    manager = SandboxManager()
    # Manually add an old container
    manager._containers["bond-sandbox-old"] = {
        "container_id": "old123",
        "last_used": 0,  # epoch = very old
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_proc
        count = await manager.cleanup_idle(max_idle_seconds=1)
        assert count == 1
        assert "bond-sandbox-old" not in manager._containers
