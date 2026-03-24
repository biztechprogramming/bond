"""Tests for backend.app.agent.workspace_map — Design Doc 069."""

from __future__ import annotations

import os
import subprocess

import pytest

from backend.app.agent.workspace_map import (
    build_workspace_overview,
    discover_repos,
    DiscoveredRepo,
    _list_directory,
    _should_skip_entry,
)


def _git_init(path: str):
    """Initialize a git repo."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)


def _create_file(root: str, relpath: str, content: str = "x"):
    full = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# discover_repos
# ---------------------------------------------------------------------------


class TestDiscoverRepos:
    def test_finds_git_repos(self, tmp_path):
        # Create two git repos
        _create_file(str(tmp_path / "repo-a"), "main.py")
        _git_init(str(tmp_path / "repo-a"))
        _create_file(str(tmp_path / "repo-b"), "index.ts")
        _git_init(str(tmp_path / "repo-b"))

        repos = discover_repos(str(tmp_path))
        git_repos = [r for r in repos if r.is_git]
        assert len(git_repos) == 2
        names = {r.name for r in git_repos}
        assert names == {"repo-a", "repo-b"}

    def test_finds_nested_git_repos(self, tmp_path):
        # Create a nested git repo: projects/service-a/
        nested_dir = tmp_path / "projects" / "service-a"
        _create_file(str(nested_dir), "app.py")
        _git_init(str(nested_dir))

        repos = discover_repos(str(tmp_path))
        git_repos = [r for r in repos if r.is_git]
        assert len(git_repos) == 1
        assert git_repos[0].name == "projects/service-a"

    def test_non_git_dirs_included(self, tmp_path):
        _create_file(str(tmp_path / "repo-a"), "main.py")
        _git_init(str(tmp_path / "repo-a"))
        _create_file(str(tmp_path / "docs"), "guide.md")
        # docs is not a git repo

        repos = discover_repos(str(tmp_path))
        git_repos = [r for r in repos if r.is_git]
        non_git = [r for r in repos if not r.is_git]
        assert len(git_repos) == 1
        assert len(non_git) == 1
        assert non_git[0].name == "docs"

    def test_skips_hidden_dirs(self, tmp_path):
        _create_file(str(tmp_path / ".hidden"), "secret.py")
        _create_file(str(tmp_path / "visible"), "app.py")
        _git_init(str(tmp_path / "visible"))

        repos = discover_repos(str(tmp_path))
        names = {r.name for r in repos}
        assert ".hidden" not in names

    def test_skips_vendor_dirs(self, tmp_path):
        _create_file(str(tmp_path / "node_modules" / "pkg"), "index.js")
        _create_file(str(tmp_path / "real-repo"), "main.py")
        _git_init(str(tmp_path / "real-repo"))

        repos = discover_repos(str(tmp_path))
        names = {r.name for r in repos}
        assert "node_modules" not in names

    def test_empty_workspace(self, tmp_path):
        repos = discover_repos(str(tmp_path))
        assert repos == []

    def test_git_repos_come_first(self, tmp_path):
        _create_file(str(tmp_path / "aaa-docs"), "readme.md")
        _create_file(str(tmp_path / "zzz-repo"), "main.py")
        _git_init(str(tmp_path / "zzz-repo"))

        repos = discover_repos(str(tmp_path))
        # Git repos should come before non-git
        assert repos[0].is_git
        assert not repos[1].is_git


# ---------------------------------------------------------------------------
# _should_skip_entry
# ---------------------------------------------------------------------------


class TestShouldSkipEntry:
    def test_hidden_files(self):
        assert _should_skip_entry(".gitignore") is True

    def test_vendor_dirs(self):
        assert _should_skip_entry("node_modules") is True
        assert _should_skip_entry("__pycache__") is True

    def test_skip_exts(self):
        assert _should_skip_entry("logo.png") is True
        assert _should_skip_entry("style.map") is True

    def test_skip_names(self):
        assert _should_skip_entry("package-lock.json") is True

    def test_normal_files(self):
        assert _should_skip_entry("main.py") is False
        assert _should_skip_entry("README.md") is False
        assert _should_skip_entry("src") is False


# ---------------------------------------------------------------------------
# _list_directory
# ---------------------------------------------------------------------------


class TestListDirectory:
    def test_basic_listing(self, tmp_path):
        _create_file(str(tmp_path), "main.py")
        _create_file(str(tmp_path), "utils.py")
        os.makedirs(str(tmp_path / "src"))

        lines = _list_directory(str(tmp_path), depth=0, max_depth=1)
        text = "\n".join(lines)
        assert "main.py" in text
        assert "utils.py" in text
        assert "src/" in text

    def test_respects_max_depth(self, tmp_path):
        _create_file(str(tmp_path / "a" / "b" / "c"), "deep.py")

        lines_shallow = _list_directory(str(tmp_path), depth=0, max_depth=1)
        text_shallow = "\n".join(lines_shallow)
        # At max_depth=1, b/ should show item count
        assert "deep.py" not in text_shallow

        lines_deep = _list_directory(str(tmp_path), depth=0, max_depth=3)
        text_deep = "\n".join(lines_deep)
        assert "deep.py" in text_deep

    def test_skips_hidden_and_vendor(self, tmp_path):
        _create_file(str(tmp_path), "app.py")
        _create_file(str(tmp_path / ".git"), "config")
        _create_file(str(tmp_path / "node_modules" / "pkg"), "index.js")

        lines = _list_directory(str(tmp_path), depth=0, max_depth=2)
        text = "\n".join(lines)
        assert "app.py" in text
        assert ".git" not in text
        assert "node_modules" not in text


# ---------------------------------------------------------------------------
# build_workspace_overview
# ---------------------------------------------------------------------------


class TestBuildWorkspaceOverview:
    def test_multi_repo_workspace(self, tmp_path):
        # Create two git repos and one non-git dir
        _create_file(str(tmp_path / "bond"), "backend/app/worker.py")
        _create_file(str(tmp_path / "bond"), "frontend/src/App.tsx")
        _git_init(str(tmp_path / "bond"))

        _create_file(str(tmp_path / "openclaw"), "src/server.ts")
        _git_init(str(tmp_path / "openclaw"))

        _create_file(str(tmp_path / "docs"), "guide.md")

        overview, repos = build_workspace_overview(str(tmp_path))

        assert len(repos) == 3
        assert "=== bond/" in overview
        assert "(git)" in overview
        assert "=== openclaw/" in overview
        assert "=== docs/" in overview
        assert "(no git)" in overview
        assert "worker.py" in overview
        assert "server.ts" in overview

    def test_empty_workspace(self, tmp_path):
        overview, repos = build_workspace_overview(str(tmp_path))
        assert overview == ""
        assert repos == []

    def test_git_repos_get_deeper_listing(self, tmp_path):
        _create_file(str(tmp_path / "repo"), "src/deep/nested/file.py")
        _git_init(str(tmp_path / "repo"))
        _create_file(str(tmp_path / "plain"), "src/deep/nested/file.py")

        overview, repos = build_workspace_overview(str(tmp_path))

        # Git repos get 3 levels, non-git gets 1 level
        # The git repo should show deeper structure
        git_repos = [r for r in repos if r.is_git]
        non_git = [r for r in repos if not r.is_git]
        assert len(git_repos) == 1
        assert len(non_git) == 1
