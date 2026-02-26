"""Tests for file read/write tools with path traversal prevention."""

from __future__ import annotations

import os
import tempfile

import pytest

from backend.app.agent.tools.files import (
    handle_file_read,
    handle_file_write,
    _resolve_and_check,
    _translate_container_to_host,
)


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


# ---------------------------------------------------------------------------
# Unit tests for _translate_container_to_host
# ---------------------------------------------------------------------------

EXAMPLE_MOUNTS = [
    {
        "mount_name": "ecoinspector-portal",
        "container_path": "/workspace/ecoinspector-portal",
        "host_path": "/mnt/c/dev/ecoinspector/ecoinspector-portal",
    },
]


def test_translate_container_absolute_path():
    """Container path should be translated to host path."""
    result = _translate_container_to_host(
        "/workspace/ecoinspector-portal/Makefile", EXAMPLE_MOUNTS
    )
    assert result == "/mnt/c/dev/ecoinspector/ecoinspector-portal/Makefile"


def test_translate_container_dotenv():
    """Dotfile container path should translate correctly."""
    result = _translate_container_to_host(
        "/workspace/ecoinspector-portal/.env", EXAMPLE_MOUNTS
    )
    assert result == "/mnt/c/dev/ecoinspector/ecoinspector-portal/.env"


def test_translate_relative_path_simple():
    """Relative path should prepend first mount's host_path."""
    result = _translate_container_to_host("Makefile", EXAMPLE_MOUNTS)
    assert result == "/mnt/c/dev/ecoinspector/ecoinspector-portal/Makefile"


def test_translate_relative_path_nested():
    """Nested relative path should prepend first mount's host_path."""
    result = _translate_container_to_host("src/foo.py", EXAMPLE_MOUNTS)
    assert result == "/mnt/c/dev/ecoinspector/ecoinspector-portal/src/foo.py"


def test_translate_no_matching_mount():
    """Path with no matching mount should be returned unchanged."""
    result = _translate_container_to_host("/other/place/file.txt", EXAMPLE_MOUNTS)
    assert result == "/other/place/file.txt"


def test_translate_multiple_mounts_picks_correct():
    """Should pick the mount whose container_path matches."""
    mounts = [
        {
            "mount_name": "alpha",
            "container_path": "/workspace/alpha",
            "host_path": "/host/alpha",
        },
        {
            "mount_name": "beta",
            "container_path": "/workspace/beta",
            "host_path": "/host/beta",
        },
    ]
    result = _translate_container_to_host("/workspace/beta/readme.md", mounts)
    assert result == "/host/beta/readme.md"


def test_translate_empty_mounts():
    """Empty mounts list should return path unchanged."""
    result = _translate_container_to_host("/workspace/project/file.txt", [])
    assert result == "/workspace/project/file.txt"


def test_translate_empty_mounts_relative():
    """Relative path with empty mounts should return unchanged."""
    result = _translate_container_to_host("Makefile", [])
    assert result == "Makefile"


# ---------------------------------------------------------------------------
# Integration tests: handle_file_read / handle_file_write with mounts context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_write_with_mounts_translates_path():
    """Write to a container path with mounts should write to host path."""
    with tempfile.TemporaryDirectory(prefix="bond_mount_test_") as tmpdir:
        mounts = [
            {
                "mount_name": "project",
                "container_path": "/workspace/project",
                "host_path": tmpdir,
            },
        ]
        result = await handle_file_write(
            {"path": "/workspace/project/test.txt", "content": "hello from container"},
            {"workspace_mounts": mounts, "workspace_dirs": [tmpdir]},
        )
        assert result["status"] == "written"
        host_file = os.path.join(tmpdir, "test.txt")
        assert os.path.exists(host_file)
        with open(host_file) as f:
            assert f.read() == "hello from container"


@pytest.mark.asyncio
async def test_file_read_with_mounts_translates_path():
    """Read from a container path with mounts should read from host path."""
    with tempfile.TemporaryDirectory(prefix="bond_mount_test_") as tmpdir:
        host_file = os.path.join(tmpdir, "test.txt")
        with open(host_file, "w") as f:
            f.write("host content")

        mounts = [
            {
                "mount_name": "project",
                "container_path": "/workspace/project",
                "host_path": tmpdir,
            },
        ]
        result = await handle_file_read(
            {"path": "/workspace/project/test.txt"},
            {"workspace_mounts": mounts, "workspace_dirs": [tmpdir]},
        )
        assert result["content"] == "host content"


# ---------------------------------------------------------------------------
# Docker container integration test (skipped when Docker unavailable)
# ---------------------------------------------------------------------------

import shutil
import subprocess

_docker_available = shutil.which("docker") is not None


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
def test_docker_mount_path_translation():
    """Create a container with a mount, write inside it, verify on host."""
    with tempfile.TemporaryDirectory(prefix="bond_docker_test_") as tmpdir:
        container_name = "bond_test_path_xlate"
        mount_src = tmpdir
        mount_dst = "/workspace/test"

        try:
            # Start a minimal container with the temp dir mounted
            subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", container_name,
                    "-v", f"{mount_src}:{mount_dst}",
                    "alpine:latest",
                    "sleep", "30",
                ],
                check=True,
                capture_output=True,
            )

            # Write a file inside the container
            subprocess.run(
                [
                    "docker", "exec", container_name,
                    "sh", "-c", f"echo 'written inside' > {mount_dst}/from_container.txt",
                ],
                check=True,
                capture_output=True,
            )

            # Translate the container path to host path
            mounts = [
                {
                    "mount_name": "test",
                    "container_path": mount_dst,
                    "host_path": mount_src,
                },
            ]
            host_path = _translate_container_to_host(
                f"{mount_dst}/from_container.txt", mounts
            )
            assert host_path == os.path.join(mount_src, "from_container.txt")
            assert os.path.exists(host_path)
            with open(host_path) as f:
                assert f.read().strip() == "written inside"

        finally:
            # Clean up container
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )
