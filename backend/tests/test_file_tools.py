"""Tests for file read/write tools with path traversal prevention."""

from __future__ import annotations

import os
import tempfile

import pytest

from backend.app.agent.tools.files import handle_file_read, handle_file_write, _resolve_and_check


@pytest.fixture
def workspace():
    """Create a temporary workspace directory."""
    tmpdir = tempfile.mkdtemp(prefix="bond_file_test_")
    # Create a test file
    test_file = os.path.join(tmpdir, "hello.txt")
    with open(test_file, "w") as f:
        f.write("Hello, world!")
    return tmpdir


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
