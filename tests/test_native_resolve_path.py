"""Tests for _resolve_path fallback logic in native tool handlers."""

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def workspace_layout(tmp_path):
    """Create a mock container layout with /bond and /workspace equivalents."""
    bond_root = tmp_path / "bond"
    workspace_root = tmp_path / "workspace"

    # Bond repo structure
    (bond_root / "backend" / "app" / "agent").mkdir(parents=True)
    (bond_root / "backend" / "app" / "agent" / "context_pipeline.py").write_text("# pipeline")
    (bond_root / "backend" / "app" / "worker.py").write_text("# worker")

    # Workspace with user content only
    (workspace_root / "design").mkdir(parents=True)
    (workspace_root / "design" / "spec.md").write_text("# spec")

    return bond_root, workspace_root


def test_resolve_relative_path_from_bond(workspace_layout):
    """Relative path found under /bond fallback when cwd is /workspace."""
    bond_root, workspace_root = workspace_layout

    from backend.app.agent.tools.native import _resolve_path, _FALLBACK_ROOTS

    original_roots = list(_FALLBACK_ROOTS)
    try:
        # Patch fallback roots to our test dirs
        import backend.app.agent.tools.native as mod
        mod._FALLBACK_ROOTS = [bond_root, workspace_root]

        old_cwd = os.getcwd()
        os.chdir(workspace_root)
        try:
            result = _resolve_path("backend/app/agent/context_pipeline.py")
            assert result.exists()
            assert str(result) == str(bond_root / "backend/app/agent/context_pipeline.py")
        finally:
            os.chdir(old_cwd)
    finally:
        mod._FALLBACK_ROOTS = original_roots


def test_resolve_relative_path_from_cwd(workspace_layout):
    """Relative path found under cwd takes precedence over fallbacks."""
    bond_root, workspace_root = workspace_layout

    from backend.app.agent.tools.native import _resolve_path
    import backend.app.agent.tools.native as mod

    original_roots = list(mod._FALLBACK_ROOTS)
    try:
        mod._FALLBACK_ROOTS = [bond_root, workspace_root]

        old_cwd = os.getcwd()
        os.chdir(workspace_root)
        try:
            result = _resolve_path("design/spec.md")
            assert result.exists()
            # Should resolve against cwd (workspace), not fallback to bond
            assert "bond" not in str(result.resolve())
        finally:
            os.chdir(old_cwd)
    finally:
        mod._FALLBACK_ROOTS = original_roots


def test_resolve_absolute_path(workspace_layout):
    """Absolute paths are returned as-is."""
    bond_root, workspace_root = workspace_layout

    from backend.app.agent.tools.native import _resolve_path

    abs_path = str(bond_root / "backend" / "app" / "worker.py")
    result = _resolve_path(abs_path)
    assert str(result) == abs_path


def test_resolve_nonexistent_returns_original(workspace_layout):
    """Non-existent relative path returns the original Path (no crash)."""
    bond_root, workspace_root = workspace_layout

    from backend.app.agent.tools.native import _resolve_path
    import backend.app.agent.tools.native as mod

    original_roots = list(mod._FALLBACK_ROOTS)
    try:
        mod._FALLBACK_ROOTS = [bond_root, workspace_root]

        result = _resolve_path("totally/fake/path.py")
        assert not result.exists()
        assert str(result).endswith("totally/fake/path.py")
    finally:
        mod._FALLBACK_ROOTS = original_roots
