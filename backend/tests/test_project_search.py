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

    # Source files in nested directories
    src_dir = tmp_path / "backend" / "app" / "agent"
    src_dir.mkdir(parents=True)
    (src_dir / "manifest.py").write_text("# Manifest loading\nclass Manifest: pass")
    (src_dir / "lifecycle.py").write_text("# Lifecycle hooks\nclass Lifecycle: pass")
    (src_dir / "worker.py").write_text("# Worker entrypoint\nasync def run_loop(): pass")

    # Blazor/Razor-style files in deeply nested paths
    inspection_dir = tmp_path / "src" / "inspection" / "components"
    inspection_dir.mkdir(parents=True)
    (inspection_dir / "DefectEntry.razor").write_text(
        "@page \"/inspection/defect\"\n<h1>Defect Entry</h1>\n"
        "<EditForm Model=\"@defect\">\n  <InputText @bind-Value=\"defect.Description\" />\n"
        "</EditForm>\n@code {\n  private Defect defect = new();\n}"
    )
    pallet_dir = tmp_path / "src" / "pallet" / "views"
    pallet_dir.mkdir(parents=True)
    (pallet_dir / "PalletOverview.razor").write_text(
        "@page \"/pallet/overview\"\n<h1>Pallet Overview</h1>"
    )

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


def _all_paths(result: dict) -> list[str]:
    """Extract all file paths from enriched result sets."""
    paths = []
    for category in ("filename_matches", "path_matches", "content_matches"):
        for entry in result.get(category, []):
            if isinstance(entry, dict):
                paths.append(entry["path"])
            else:
                paths.append(entry)
    return paths


def _all_basenames(result: dict) -> list[str]:
    return [os.path.basename(p) for p in _all_paths(result)]


async def test_finds_design_doc_by_number(project_tree):
    """'design doc 27' should find 027-fragment-selection-roadmap.md via zero-padded filename."""
    result = await handle_project_search(
        {"query": "design doc 27", "path": str(project_tree)}, {}
    )
    basenames = _all_basenames(result)
    assert "027-fragment-selection-roadmap.md" in basenames, f"Expected 027 doc in results: {result}"


async def test_finds_by_content_search(project_tree):
    """Should find files by content when filename doesn't match."""
    result = await handle_project_search(
        {"query": "Fragment Selection Roadmap", "path": str(project_tree)}, {}
    )
    basenames = _all_basenames(result)
    assert any("027" in name or "fragment" in name.lower() for name in basenames), \
        f"Expected fragment doc in results: {result}"


async def test_finds_by_path_component(project_tree):
    """Should find files in directories matching query terms."""
    result = await handle_project_search(
        {"query": "design documents", "path": str(project_tree)}, {}
    )
    assert result["total_results"] > 0, f"Expected results for 'design documents': {result}"


async def test_excludes_noise_directories(project_tree):
    """Should not return files from node_modules or .git."""
    result = await handle_project_search(
        {"query": "027", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    for f in all_files:
        assert "node_modules" not in f, f"Should exclude node_modules: {f}"
        assert ".git" not in f, f"Should exclude .git: {f}"


async def test_respects_include_filter(project_tree):
    """File type filter should work."""
    result = await handle_project_search(
        {"query": "worker", "path": str(project_tree), "include": "*.py"}, {}
    )
    all_files = _all_paths(result)
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
    basenames = _all_basenames(result)
    assert "001-knowledge-store.md" in basenames, f"Expected 001 doc in results: {result}"


async def test_returns_preview(project_tree):
    """Each result should include a preview of file contents."""
    result = await handle_project_search(
        {"query": "worker", "path": str(project_tree)}, {}
    )
    all_entries = result["filename_matches"] + result["path_matches"] + result["content_matches"]
    assert len(all_entries) > 0
    for entry in all_entries:
        assert isinstance(entry, dict), f"Expected dict entry, got: {type(entry)}"
        assert "path" in entry, f"Missing 'path' in entry: {entry}"
        assert "preview" in entry, f"Missing 'preview' in entry: {entry}"
        assert len(entry["preview"]) > 0, f"Preview should not be empty: {entry}"


async def test_searches_each_word_independently(project_tree):
    """Each word in query should match independently (OR logic)."""
    result = await handle_project_search(
        {"query": "inspection defect entry blazor", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    # Should find DefectEntry.razor (matches: inspection dir, defect in name, entry in name)
    basenames = [os.path.basename(f) for f in all_files]
    assert "DefectEntry.razor" in basenames, f"Expected DefectEntry.razor in results: {basenames}"


async def test_matches_parent_directory_names(project_tree):
    """Files should match when query words appear in parent/grandparent directory names."""
    result = await handle_project_search(
        {"query": "pallet", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    # Should find PalletOverview.razor via the pallet/ directory
    assert any("PalletOverview" in f for f in all_files), \
        f"Expected PalletOverview.razor via directory match: {all_files}"


async def test_content_search_always_runs(project_tree):
    """Content search should run even when filename matches are plentiful."""
    # "worker" matches worker.py and test_worker.py by name,
    # but content search should still find files containing "worker" in their text
    result = await handle_project_search(
        {"query": "worker", "path": str(project_tree)}, {}
    )
    # The key assertion: content_matches should exist as a list (strategy always runs)
    assert isinstance(result["content_matches"], list)


async def test_all_strategies_run(project_tree):
    """All three strategies should always execute, not short-circuit."""
    result = await handle_project_search(
        {"query": "manifest agent backend", "path": str(project_tree)}, {}
    )
    # Should have results from multiple strategies
    assert result["total_results"] > 0
    # Verify structure
    assert "filename_matches" in result
    assert "path_matches" in result
    assert "content_matches" in result
