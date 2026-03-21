"""Tests for workspace cloner (Design Doc 057)."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.sandbox.workspace_cloner import (
    ClonePlan,
    CopySpec,
    RepoCloneSpec,
    _apply_instance_overrides,
    _copytree_filtered,
    _refresh_clone,
    _should_skip,
    cleanup_workspace_clones,
    copy_env_files,
    detect_lockfiles,
    detect_workspace_type,
    execute_clone_plan,
    generate_clone_plan,
    generate_dep_install_script,
)


# ---------------------------------------------------------------------------
# Detection algorithm
# ---------------------------------------------------------------------------


class TestDetectWorkspaceType:
    @pytest.mark.asyncio
    async def test_case1_git_repo_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        result = await detect_workspace_type(str(tmp_path))
        assert result["case"] == 1
        assert result["repo_root"] == str(tmp_path)
        assert result["needs_prompt"] is False

    @pytest.mark.asyncio
    async def test_case2_inside_git_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "frontend"
        subdir.mkdir()
        result = await detect_workspace_type(str(subdir))
        assert result["case"] == 2
        assert result["repo_root"] == str(tmp_path)
        assert result["needs_prompt"] is True
        assert "repo rooted at" in result["prompt_message"]

    @pytest.mark.asyncio
    async def test_case3_contains_git_repos(self, tmp_path):
        (tmp_path / "api" / ".git").mkdir(parents=True)
        (tmp_path / "web" / ".git").mkdir(parents=True)
        (tmp_path / "README.md").touch()
        result = await detect_workspace_type(str(tmp_path))
        assert result["case"] == 3
        assert len(result["sub_repos"]) == 2
        assert result["needs_prompt"] is False

    @pytest.mark.asyncio
    async def test_case3_respects_depth_limit(self, tmp_path):
        # Create a repo at depth 4 — should NOT be found
        deep = tmp_path / "a" / "b" / "c" / "repo"
        (deep / ".git").mkdir(parents=True)
        result = await detect_workspace_type(str(tmp_path))
        # Depth limit is 3, so scanning a/b/c/repo/.git requires 4 levels
        assert result["case"] == 4

    @pytest.mark.asyncio
    async def test_case4_no_git(self, tmp_path):
        (tmp_path / "file.txt").touch()
        result = await detect_workspace_type(str(tmp_path))
        assert result["case"] == 4
        assert result["needs_prompt"] is True
        assert "not a git repo" in result["prompt_message"]


# ---------------------------------------------------------------------------
# Clone plan generation
# ---------------------------------------------------------------------------


class TestGenerateClonePlan:
    @pytest.mark.asyncio
    async def test_case1_plan(self, tmp_path):
        (tmp_path / ".git").mkdir()

        with patch("backend.app.sandbox.workspace_cloner._get_current_branch",
                    new_callable=AsyncMock, return_value="feature/test"), \
             patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            plan = await generate_clone_plan(
                str(tmp_path), "agent-123", "project",
            )

        assert plan.case == 1
        assert len(plan.repos) == 1
        assert plan.repos[0].branch == "feature/test"
        assert plan.repos[0].remote == f"file://{tmp_path}"
        assert not plan.direct_mount

    @pytest.mark.asyncio
    async def test_case2_direct_mount(self, tmp_path):
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "sub"
        subdir.mkdir()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            plan = await generate_clone_plan(
                str(subdir), "agent-123", "sub",
            )

        assert plan.direct_mount is True

    @pytest.mark.asyncio
    async def test_case3_plan(self, tmp_path):
        (tmp_path / "api" / ".git").mkdir(parents=True)
        (tmp_path / "web" / ".git").mkdir(parents=True)
        (tmp_path / "README.md").touch()

        with patch("backend.app.sandbox.workspace_cloner._get_current_branch",
                    new_callable=AsyncMock, return_value="main"), \
             patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            plan = await generate_clone_plan(
                str(tmp_path), "agent-123", "projects",
            )

        assert plan.case == 3
        assert len(plan.repos) == 2
        assert len(plan.copies) == 1  # README.md
        assert not plan.direct_mount


# ---------------------------------------------------------------------------
# Clone execution
# ---------------------------------------------------------------------------


class TestExecuteClonePlan:
    @pytest.mark.asyncio
    async def test_direct_mount_noop(self):
        plan = ClonePlan(case=2, direct_mount=True)
        await execute_clone_plan(plan)  # should not raise

    @pytest.mark.asyncio
    async def test_git_clone_called(self, tmp_path):
        target = tmp_path / "clone"
        plan = ClonePlan(
            case=1,
            repos=[RepoCloneSpec(
                repo_root="/src/repo",
                remote="file:///src/repo",
                branch="main",
                target_path=str(target),
            )],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await execute_clone_plan(plan)

        call_args = mock_exec.call_args[0]
        assert "git" in call_args
        assert "clone" in call_args
        assert "--depth" in call_args
        assert "1" in call_args
        assert "--branch" in call_args
        assert "main" in call_args

    @pytest.mark.asyncio
    async def test_copy_skips_build_artifacts(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "dist").mkdir()
        (src / "dist" / "bundle.js").touch()
        (src / "__pycache__").mkdir()
        (src / "app.py").write_text("hello")

        target = tmp_path / "target"
        plan = ClonePlan(
            case=3,
            repos=[],
            copies=[
                CopySpec(source=str(src / "dist"), target=str(target / "dist")),
                CopySpec(source=str(src / "__pycache__"), target=str(target / "__pycache__")),
                CopySpec(source=str(src / "app.py"), target=str(target / "app.py")),
            ],
        )

        await execute_clone_plan(plan)

        assert not (target / "dist").exists()
        assert not (target / "__pycache__").exists()
        assert (target / "app.py").exists()

    @pytest.mark.asyncio
    async def test_cloneignore_respected(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / ".cloneignore").write_text("secret_data\n")
        (src / "secret_data").mkdir()
        (src / "secret_data" / "keys.txt").touch()
        (src / "normal.txt").write_text("ok")

        target = tmp_path / "target"
        plan = ClonePlan(
            case=3,
            repos=[],
            copies=[
                CopySpec(source=str(src / "secret_data"), target=str(target / "secret_data")),
                CopySpec(source=str(src / "normal.txt"), target=str(target / "normal.txt")),
            ],
        )

        await execute_clone_plan(plan)

        assert not (target / "secret_data").exists()
        assert (target / "normal.txt").exists()


# ---------------------------------------------------------------------------
# Env file handling
# ---------------------------------------------------------------------------


class TestCopyEnvFiles:
    @pytest.mark.asyncio
    async def test_copies_env_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        tgt = tmp_path / "tgt"
        tgt.mkdir()

        (src / ".env").write_text("PORT=3000\nDATABASE_URL=db.sqlite")
        (src / ".env.local").write_text("SECRET=abc")
        (src / "app.py").write_text("not an env file")

        await copy_env_files(str(src), str(tgt), instance_index=2)

        assert (tgt / ".env").exists()
        assert (tgt / ".env.local").exists()
        assert not (tgt / "app.py").exists()

    @pytest.mark.asyncio
    async def test_port_increment(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        tgt = tmp_path / "tgt"
        tgt.mkdir()

        (src / ".env").write_text("PORT=3000\nAPI_PORT=8080")

        await copy_env_files(str(src), str(tgt), instance_index=3)

        content = (tgt / ".env").read_text()
        assert "PORT=3003" in content
        assert "API_PORT=8083" in content

    @pytest.mark.asyncio
    async def test_instance_id_injected(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        tgt = tmp_path / "tgt"
        tgt.mkdir()

        await copy_env_files(str(src), str(tgt), instance_index=5)

        content = (tgt / ".env.local").read_text()
        assert "CONTAINER_INSTANCE_ID=5" in content

    @pytest.mark.asyncio
    async def test_db_path_suffix(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        tgt = tmp_path / "tgt"
        tgt.mkdir()

        (src / ".env").write_text("DB_PATH=data/app.db")

        await copy_env_files(str(src), str(tgt), instance_index=1)

        content = (tgt / ".env").read_text()
        assert "DB_PATH=data/app_instance1.db" in content


# ---------------------------------------------------------------------------
# Lockfile detection
# ---------------------------------------------------------------------------


class TestDetectLockfiles:
    def test_bun_lock(self, tmp_path):
        (tmp_path / "bun.lock").touch()
        result = detect_lockfiles(str(tmp_path))
        assert any("bun install" in cmd for _, cmd in result)

    def test_npm_lockfile(self, tmp_path):
        (tmp_path / "package-lock.json").touch()
        result = detect_lockfiles(str(tmp_path))
        assert any("npm ci" in cmd for _, cmd in result)

    def test_uv_lock(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / "uv.lock").touch()
        result = detect_lockfiles(str(tmp_path))
        assert any("uv sync" in cmd for _, cmd in result)

    def test_no_lockfiles(self, tmp_path):
        assert detect_lockfiles(str(tmp_path)) == []

    def test_multiple_lockfiles(self, tmp_path):
        (tmp_path / "bun.lock").touch()
        (tmp_path / "requirements.txt").touch()
        result = detect_lockfiles(str(tmp_path))
        assert len(result) == 2

    def test_go_sum(self, tmp_path):
        (tmp_path / "go.sum").touch()
        result = detect_lockfiles(str(tmp_path))
        assert any("go mod download" in cmd for _, cmd in result)


# ---------------------------------------------------------------------------
# Build artifact skipping
# ---------------------------------------------------------------------------


class TestBuildArtifactSkipping:
    def test_skip_dist(self):
        assert _should_skip("dist", ["dist", ".next", "__pycache__"])

    def test_skip_pycache(self):
        assert _should_skip("__pycache__", ["dist", ".next", "__pycache__"])

    def test_skip_pyc_wildcard(self):
        assert _should_skip("foo.pyc", ["*.pyc"])

    def test_no_skip_normal(self):
        assert not _should_skip("src", ["dist", ".next"])

    def test_copytree_filters(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "dist").mkdir()
        (src / "dist" / "out.js").touch()
        (src / "app.py").write_text("code")
        (src / "foo.pyc").touch()

        target = tmp_path / "target"
        _copytree_filtered(src, target, ["dist", "*.pyc"])

        assert (target / "app.py").exists()
        assert not (target / "dist").exists()
        assert not (target / "foo.pyc").exists()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_workspaces_dir(self, tmp_path):
        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            ws_dir = tmp_path / "data" / "agents" / "agent-123" / "workspaces"
            ws_dir.mkdir(parents=True)
            (ws_dir / "project" / ".git").mkdir(parents=True)
            (ws_dir / "project" / "file.txt").write_text("hello")

            await cleanup_workspace_clones("agent-123")

            assert not ws_dir.exists()

    @pytest.mark.asyncio
    async def test_cleanup_noop_if_missing(self, tmp_path):
        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            await cleanup_workspace_clones("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# Clone cache / refresh
# ---------------------------------------------------------------------------


class TestRefreshClone:
    @pytest.mark.asyncio
    async def test_refresh_succeeds_with_existing_git(self, tmp_path):
        """When .git exists and fetch+reset succeed, refresh returns True."""
        target = tmp_path / "clone"
        (target / ".git").mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        repo = RepoCloneSpec(
            repo_root="/src", remote="file:///src",
            branch="main", target_path=str(target),
        )

        with patch("asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=mock_proc):
            result = await _refresh_clone(repo)

        assert result is True

    @pytest.mark.asyncio
    async def test_refresh_fails_no_git_dir(self, tmp_path):
        """When .git doesn't exist, refresh returns False."""
        target = tmp_path / "clone"
        target.mkdir()

        repo = RepoCloneSpec(
            repo_root="/src", remote="file:///src",
            branch="main", target_path=str(target),
        )
        result = await _refresh_clone(repo)
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_falls_back_on_fetch_failure(self, tmp_path):
        """When fetch fails, refresh returns False (caller does full clone)."""
        target = tmp_path / "clone"
        (target / ".git").mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.returncode = 128
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: error"))

        repo = RepoCloneSpec(
            repo_root="/src", remote="file:///src",
            branch="main", target_path=str(target),
        )

        with patch("asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=mock_proc):
            result = await _refresh_clone(repo)

        assert result is False


# ---------------------------------------------------------------------------
# Parallel cloning
# ---------------------------------------------------------------------------


class TestParallelCloning:
    @pytest.mark.asyncio
    async def test_execute_clone_plan_uses_gather(self):
        """Multiple repos are cloned in parallel via asyncio.gather."""
        plan = ClonePlan(
            case=3,
            repos=[
                RepoCloneSpec("/src/a", "file:///src/a", "main", "/tmp/a"),
                RepoCloneSpec("/src/b", "file:///src/b", "main", "/tmp/b"),
            ],
        )

        with patch("backend.app.sandbox.workspace_cloner._clone_repo",
                    new_callable=AsyncMock) as mock_clone, \
             patch("asyncio.gather", new_callable=AsyncMock) as mock_gather:
            mock_gather.return_value = [None, None]
            await execute_clone_plan(plan)

        # asyncio.gather was called (parallel execution)
        mock_gather.assert_called_once()


# ---------------------------------------------------------------------------
# Dependency install script
# ---------------------------------------------------------------------------


class TestGenerateDepInstallScript:
    def test_returns_none_no_lockfiles(self, tmp_path):
        assert generate_dep_install_script(str(tmp_path)) is None

    def test_generates_script_bun(self, tmp_path):
        (tmp_path / "bun.lock").touch()
        script = generate_dep_install_script(str(tmp_path))
        assert script is not None
        assert "bun install" in script
        assert f"cd {tmp_path}" in script
        assert script.startswith("#!/bin/sh")

    def test_generates_script_multiple(self, tmp_path):
        (tmp_path / "bun.lock").touch()
        (tmp_path / "requirements.txt").touch()
        script = generate_dep_install_script(str(tmp_path))
        assert "bun install" in script
        assert "pip install -r requirements.txt" in script


# ---------------------------------------------------------------------------
# ensure_deps_installed
# ---------------------------------------------------------------------------


class TestEnsureDepsInstalled:
    @pytest.mark.asyncio
    async def test_installs_deps(self):
        from backend.app.sandbox.manager import SandboxManager

        manager = SandboxManager()
        manager._containers["bond-sandbox-agent-123"] = {
            "container_id": "cont1",
            "dep_install_script": "#!/bin/sh\nbun install\n",
            "deps_installed": False,
        }

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"installed", b""))

        with patch("asyncio.create_subprocess_exec",
                    new_callable=AsyncMock, return_value=mock_proc):
            result = await manager.ensure_deps_installed("agent-123")

        assert result["installed"] is True
        assert manager._containers["bond-sandbox-agent-123"]["deps_installed"] is True

    @pytest.mark.asyncio
    async def test_skips_if_already_installed(self):
        from backend.app.sandbox.manager import SandboxManager

        manager = SandboxManager()
        manager._containers["bond-sandbox-agent-123"] = {
            "container_id": "cont1",
            "dep_install_script": "#!/bin/sh\nbun install\n",
            "deps_installed": True,
        }

        result = await manager.ensure_deps_installed("agent-123")
        assert result["installed"] is False
        assert "Already" in result["output"]

    @pytest.mark.asyncio
    async def test_no_script_returns_no_deps(self):
        from backend.app.sandbox.manager import SandboxManager

        manager = SandboxManager()
        manager._containers["bond-sandbox-agent-123"] = {
            "container_id": "cont1",
            "dep_install_script": None,
            "deps_installed": False,
        }

        result = await manager.ensure_deps_installed("agent-123")
        assert result["installed"] is False

    @pytest.mark.asyncio
    async def test_no_container_found(self):
        from backend.app.sandbox.manager import SandboxManager

        manager = SandboxManager()
        result = await manager.ensure_deps_installed("nonexistent")
        assert result["installed"] is False
