"""Tests for project_search tool — multi-strategy file discovery (Doc 029)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.agent.tools.shell_utils import handle_project_search


@pytest.fixture(autouse=True)
def project_tree(tmp_path):
    """Create a realistic project tree for testing."""
    # Design docs with zero-padded numeric prefixes
    design_dir = tmp_path / "docs" / "design"
    design_dir.mkdir(parents=True)
    (design_dir / "001-knowledge-store.md").write_text("# Design Doc 001: Knowledge Store")
    (design_dir / "027-fragment-selection-roadmap.md").write_text(
        "# Design Doc 027: Fragment Selection Roadmap\nPhase 1, Phase 2, Phase 3"
    )
    (design_dir / "028-checkbox-removal.md").write_text("# Design Doc 028")

    # Source files
    src_dir = tmp_path / "backend" / "app" / "agent"
    src_dir.mkdir(parents=True)
    (src_dir / "manifest.py").write_text("# Manifest loading\nclass Manifest: pass")
    (src_dir / "lifecycle.py").write_text("# Lifecycle hooks\nclass Lifecycle: pass")
    (src_dir / "worker.py").write_text("# Worker entrypoint\nasync def run_loop(): pass")

    # Tests
    test_dir = tmp_path / "backend" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_worker.py").write_text("def test_worker(): pass")
    (test_dir / "test_manifest.py").write_text("def test_manifest(): pass")

    # Noise directories (should be excluded)
    (tmp_path / "node_modules" / "foo").mkdir(parents=True)
    (tmp_path / "node_modules" / "foo" / "027-something.md").write_text("noise")
    (tmp_path / ".git" / "objects").mkdir(parents=True)

    return tmp_path


async def test_finds_design_doc_by_number(project_tree):
    """'design doc 27' should find 027-fragment-selection-roadmap.md via zero-padded filename."""
    result = await handle_project_search(
        {"query": "design doc 27", "path": str(project_tree)}, {}
    )
    all_files = result["filename_matches"] + result["content_matches"] + result["path_matches"]
    paths = [os.path.basename(f) for f in all_files]
    assert "027-fragment-selection-roadmap.md" in paths, f"Expected 027 doc in results: {result}"


async def test_finds_by_content_search(project_tree):
    """Should find files by content when filename doesn't match."""
    result = await handle_project_search(
        {"query": "Fragment Selection Roadmap", "path": str(project_tree)}, {}
    )
    all_files = result["filename_matches"] + result["content_matches"] + result["path_matches"]
    all_basenames = [os.path.basename(f) for f in all_files]
    assert any("027" in name or "fragment" in name.lower() for name in all_basenames), \
        f"Expected fragment doc in results: {result}"


async def test_finds_by_path_component(project_tree):
    """Should find files in directories matching query terms."""
    result = await handle_project_search(
        {"query": "design documents", "path": str(project_tree)}, {}
    )
    total = result["total_results"]
    assert total > 0, f"Expected results for 'design documents': {result}"


async def test_excludes_noise_directories(project_tree):
    """Should not return files from node_modules or .git."""
    result = await handle_project_search(
        {"query": "027", "path": str(project_tree)}, {}
    )
    all_files = result["filename_matches"] + result["content_matches"] + result["path_matches"]
    for f in all_files:
        assert "node_modules" not in f, f"Should exclude node_modules: {f}"
        assert ".git" not in f, f"Should exclude .git: {f}"


async def test_respects_include_filter(project_tree):
    """File type filter should work."""
    result = await handle_project_search(
        {"query": "worker", "path": str(project_tree), "include": "*.py"}, {}
    )
    all_files = result["filename_matches"] + result["content_matches"] + result["path_matches"]
    for f in all_files:
        assert f.endswith(".py"), f"Expected only .py files: {f}"


async def test_empty_query_returns_error(project_tree):
    """Empty query should return an error."""
    result = await handle_project_search({"query": "", "path": str(project_tree)}, {})
    assert "error" in result


async def test_no_results_gives_suggestion(project_tree):
    """When nothing is found, include a suggestion."""
    result = await handle_project_search(
        {"query": "xyznonexistent123", "path": str(project_tree)}, {}
    )
    assert result["total_results"] == 0
    assert "suggestion" in result


async def test_zero_padding_finds_numeric_docs(project_tree):
    """Query with number '1' should find '001-knowledge-store.md'."""
    result = await handle_project_search(
        {"query": "doc 1", "path": str(project_tree)}, {}
    )
    all_files = result["filename_matches"] + result["content_matches"] + result["path_matches"]
    paths = [os.path.basename(f) for f in all_files]
    assert "001-knowledge-store.md" in paths, f"Expected 001 doc in results: {result}"
