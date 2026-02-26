"""Tests for file read/write tools — host mode and sandbox (docker exec) mode."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.agent.tools.files import (
    handle_file_read,
    handle_file_write,
    _resolve_and_check,
)


# ---------------------------------------------------------------------------
# _resolve_and_check unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace():
    """Create a temporary workspace directory."""
    tmpdir = tempfile.mkdtemp(prefix="bond_file_test_")
    test_file = os.path.join(tmpdir, "hello.txt")
    with open(test_file, "w") as f:
        f.write("Hello, world!")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_resolve_and_check_allowed(workspace):
    """Should allow paths within the workspace."""
    path = os.path.join(workspace, "hello.txt")
    result = _resolve_and_check(path, [workspace])
    assert result is not None
    assert str(result).startswith(workspace)


def test_resolve_and_check_rejected(workspace):
    """Should reject paths outside the workspace."""
    result = _resolve_and_check("/etc/passwd", [workspace])
    assert result is None


def test_resolve_and_check_traversal(workspace):
    """Should reject path traversal attempts."""
    path = os.path.join(workspace, "..", "..", "etc", "passwd")
    result = _resolve_and_check(path, [workspace])
    assert result is None


def test_resolve_and_check_no_dirs():
    """Should reject when no allowed dirs configured."""
    result = _resolve_and_check("/tmp/foo", [])
    assert result is None


# ---------------------------------------------------------------------------
# Host-mode file_read tests (no sandbox)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_read_success(workspace):
    """Should read a file within the workspace."""
    path = os.path.join(workspace, "hello.txt")
    result = await handle_file_read(
        {"path": path},
        {"workspace_dirs": [workspace]},
    )
    assert result["content"] == "Hello, world!"


@pytest.mark.asyncio
async def test_file_read_outside_workspace(workspace):
    """Should reject reading files outside workspace."""
    result = await handle_file_read(
        {"path": "/etc/hostname"},
        {"workspace_dirs": [workspace]},
    )
    assert "error" in result
    assert "outside" in result["error"].lower()


@pytest.mark.asyncio
async def test_file_read_no_workspace():
    """Should reject when no workspace dirs configured."""
    result = await handle_file_read(
        {"path": "/tmp/anything"},
        {"workspace_dirs": []},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_file_read_not_found(workspace):
    """Should return error for missing files."""
    path = os.path.join(workspace, "nonexistent.txt")
    result = await handle_file_read(
        {"path": path},
        {"workspace_dirs": [workspace]},
    )
    assert "error" in result
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# Host-mode file_write tests (no sandbox)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_write_success(workspace):
    """Should write a file within the workspace."""
    path = os.path.join(workspace, "output.txt")
    result = await handle_file_write(
        {"path": path, "content": "New content"},
        {"workspace_dirs": [workspace]},
    )
    assert result["status"] == "written"
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read() == "New content"


@pytest.mark.asyncio
async def test_file_write_creates_dirs(workspace):
    """Should create parent directories as needed."""
    path = os.path.join(workspace, "sub", "dir", "file.txt")
    result = await handle_file_write(
        {"path": path, "content": "nested"},
        {"workspace_dirs": [workspace]},
    )
    assert result["status"] == "written"
    assert os.path.exists(path)


@pytest.mark.asyncio
async def test_file_write_outside_workspace(workspace):
    """Should reject writing files outside workspace."""
    result = await handle_file_write(
        {"path": "/tmp/evil.txt", "content": "bad"},
        {"workspace_dirs": [workspace]},
    )
    assert "error" in result
    assert "outside" in result["error"].lower()


@pytest.mark.asyncio
async def test_file_write_traversal(workspace):
    """Should reject path traversal in writes."""
    path = os.path.join(workspace, "..", "evil.txt")
    result = await handle_file_write(
        {"path": path, "content": "bad"},
        {"workspace_dirs": [workspace]},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Sandbox-mode tests (mock docker exec)
# ---------------------------------------------------------------------------

def _mock_sandbox_container(container_id: str = "abc123"):
    """Patch _get_sandbox_container to return a fake container ID."""
    return patch(
        "backend.app.agent.tools.files._get_sandbox_container",
        new_callable=AsyncMock,
        return_value=container_id,
    )


def _make_fake_process(returncode=0, stdout=b"", stderr=b""):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


@pytest.mark.asyncio
async def test_sandbox_file_read_calls_docker_exec():
    """Sandbox read should call docker exec cat."""
    fake_proc = _make_fake_process(stdout=b"file content here")

    with _mock_sandbox_container("ctr42"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc) as mock_exec, \
         patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"file content here", b"")) as mock_wait:
        # wait_for wraps proc.communicate; make it return the right thing
        mock_wait.return_value = (b"file content here", b"")

        result = await handle_file_read(
            {"path": "/workspace/project/readme.md"},
            {"sandbox_image": "node:20", "agent_id": "a1"},
        )

    # Verify docker exec cat was called
    mock_exec.assert_called_once_with(
        "docker", "exec", "ctr42", "cat", "/workspace/project/readme.md",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert result["content"] == "file content here"
    assert result["path"] == "/workspace/project/readme.md"


@pytest.mark.asyncio
async def test_sandbox_file_read_not_found():
    """Sandbox read should return error when file doesn't exist."""
    fake_proc = _make_fake_process(returncode=1, stderr=b"cat: /workspace/x: No such file or directory")

    with _mock_sandbox_container("ctr42"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=fake_proc), \
         patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"", b"cat: /workspace/x: No such file or directory")):
        result = await handle_file_read(
            {"path": "/workspace/x"},
            {"sandbox_image": "node:20", "agent_id": "a1"},
        )

    assert "error" in result
    assert "No such file" in result["error"]


@pytest.mark.asyncio
async def test_sandbox_file_write_calls_docker_exec_tee():
    """Sandbox write should call docker exec -i tee with stdin."""
    mkdir_proc = _make_fake_process()
    tee_proc = _make_fake_process()

    call_count = 0
    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call is mkdir, second is tee
        if call_count == 1:
            return mkdir_proc
        return tee_proc

    async def fake_wait_for(coro, timeout):
        return await coro

    with _mock_sandbox_container("ctr42"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec, \
         patch("asyncio.wait_for", side_effect=fake_wait_for):
        result = await handle_file_write(
            {"path": "/workspace/project/out.txt", "content": "hello world"},
            {"sandbox_image": "node:20", "agent_id": "a1"},
        )

    assert result["status"] == "written"
    assert result["path"] == "/workspace/project/out.txt"

    # Verify the tee call (second call)
    calls = mock_exec.call_args_list
    assert len(calls) == 2

    # First call: mkdir -p
    assert calls[0].args == ("docker", "exec", "ctr42", "mkdir", "-p", "/workspace/project")

    # Second call: tee with stdin
    assert calls[1].args == ("docker", "exec", "-i", "ctr42", "tee", "/workspace/project/out.txt")
    assert calls[1].kwargs.get("stdin") == asyncio.subprocess.PIPE

    # Verify content was piped via stdin
    tee_proc.communicate.assert_called_once_with(input=b"hello world")


@pytest.mark.asyncio
async def test_sandbox_file_write_special_chars():
    """Sandbox write should handle content with special chars (quotes, newlines, etc)."""
    content = "line1\nline2\n'single' \"double\" $var `backtick` \\backslash\nBONDEOF\nEOF\n"
    tee_proc = _make_fake_process()
    mkdir_proc = _make_fake_process()

    call_count = 0
    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mkdir_proc
        return tee_proc

    async def fake_wait_for(coro, timeout):
        return await coro

    with _mock_sandbox_container("ctr42"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("asyncio.wait_for", side_effect=fake_wait_for):
        result = await handle_file_write(
            {"path": "/workspace/test.sh", "content": content},
            {"sandbox_image": "alpine", "agent_id": "a1"},
        )

    assert result["status"] == "written"
    # Content piped via stdin — no escaping issues
    tee_proc.communicate.assert_called_once_with(input=content.encode("utf-8"))


@pytest.mark.asyncio
async def test_sandbox_fallback_to_host_when_no_sandbox_image(workspace):
    """Without sandbox_image in context, should use host filesystem."""
    path = os.path.join(workspace, "hello.txt")
    result = await handle_file_read(
        {"path": path},
        {"workspace_dirs": [workspace]},  # No sandbox_image
    )
    assert result["content"] == "Hello, world!"


# ---------------------------------------------------------------------------
# Docker integration test (skipped when Docker unavailable)
# ---------------------------------------------------------------------------

_docker_available = shutil.which("docker") is not None


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.asyncio
async def test_docker_sandbox_file_roundtrip():
    """Start alpine container, write file via tool, read it back, verify on host."""
    with tempfile.TemporaryDirectory(prefix="bond_docker_integ_") as tmpdir:
        container_name = "bond_test_file_integ"
        mount_dst = "/workspace/test"

        try:
            # Start alpine container with temp dir mounted
            proc = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", container_name,
                    "-v", f"{tmpdir}:{mount_dst}",
                    "alpine:latest",
                    "sleep", "30",
                ],
                check=True,
                capture_output=True,
            )
            container_id = proc.stdout.decode().strip()[:12]

            # Build context that simulates sandbox mode
            context = {
                "sandbox_image": "alpine:latest",
                "agent_id": "test-integ",
                "workspace_mounts": [
                    {
                        "mount_name": "test",
                        "container_path": mount_dst,
                        "host_path": tmpdir,
                    },
                ],
            }

            # Patch _get_sandbox_container to return our real container
            with patch(
                "backend.app.agent.tools.files._get_sandbox_container",
                new_callable=AsyncMock,
                return_value=container_id,
            ):
                # Write a file with special characters
                test_content = "Hello from sandbox!\n'quotes' \"doubles\" $vars `ticks`\nBONDEOF\nline4"
                write_result = await handle_file_write(
                    {"path": f"{mount_dst}/special.txt", "content": test_content},
                    context,
                )
                assert write_result["status"] == "written", f"Write failed: {write_result}"

                # Read it back via the tool
                read_result = await handle_file_read(
                    {"path": f"{mount_dst}/special.txt"},
                    context,
                )
                assert read_result["content"] == test_content, f"Read mismatch: {read_result}"

            # Verify the file appeared on host filesystem (via bind mount)
            host_file = os.path.join(tmpdir, "special.txt")
            assert os.path.exists(host_file), "File not visible on host via bind mount"
            with open(host_file) as f:
                assert f.read() == test_content

        finally:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )
