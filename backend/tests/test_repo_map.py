"""Tests for backend.app.agent.repo_map — Phase 0 repo map generation."""

from __future__ import annotations

import os
import subprocess

import pytest

from backend.app.agent.repo_map import build_repo_map, SKIP_EXTS, SKIP_NAMES


def _git_init(path: str):
    """Initialize a git repo and add all files."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)


def _create_file(root: str, relpath: str, content: str = "x"):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


@pytest.mark.asyncio
async def test_basic_tree(tmp_path):
    root = str(tmp_path)
    _create_file(root, "README.md")
    _create_file(root, "src/main.py")
    _create_file(root, "src/utils/helper.py")
    _git_init(root)

    tree = await build_repo_map(root)
    assert "README.md" in tree
    assert "src/" in tree
    assert "main.py" in tree
    assert "helper.py" in tree


@pytest.mark.asyncio
async def test_skip_exts(tmp_path):
    root = str(tmp_path)
    _create_file(root, "app.py")
    _create_file(root, "logo.png")
    _create_file(root, "photo.jpg")
    _create_file(root, "style.map")
    _git_init(root)

    tree = await build_repo_map(root)
    assert "app.py" in tree
    assert "logo.png" not in tree
    assert "photo.jpg" not in tree
    assert "style.map" not in tree


@pytest.mark.asyncio
async def test_skip_names(tmp_path):
    root = str(tmp_path)
    _create_file(root, "index.js")
    _create_file(root, "package-lock.json")
    _create_file(root, "bun.lock")
    _git_init(root)

    tree = await build_repo_map(root)
    assert "index.js" in tree
    assert "package-lock.json" not in tree
    assert "bun.lock" not in tree


@pytest.mark.asyncio
async def test_empty_file_filtered(tmp_path):
    root = str(tmp_path)
    _create_file(root, "real.py", "print('hi')")
    _create_file(root, "empty.py", "")  # 0 bytes
    _git_init(root)

    tree = await build_repo_map(root)
    assert "real.py" in tree
    assert "empty.py" not in tree


@pytest.mark.asyncio
async def test_collapse_dirs(tmp_path):
    root = str(tmp_path)
    _create_file(root, "app.py")
    _create_file(root, "gen/spacetime/a.ts")
    _create_file(root, "gen/spacetime/b.ts")
    _create_file(root, "gen/spacetime/c.ts")
    _git_init(root)

    tree = await build_repo_map(root, collapse_dirs={"gen/spacetime/"})
    assert "app.py" in tree
    assert "[generated: 3 files]" in tree
    assert "a.ts" not in tree


@pytest.mark.asyncio
async def test_directory_suffix(tmp_path):
    root = str(tmp_path)
    _create_file(root, "src/main.py")
    _git_init(root)

    tree = await build_repo_map(root)
    assert "src/" in tree


@pytest.mark.asyncio
async def test_non_git_returns_empty(tmp_path):
    root = str(tmp_path)
    _create_file(root, "file.py")
    # No git init

    tree = await build_repo_map(root)
    assert tree == ""
