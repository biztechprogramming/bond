"""Tests for project_search tool — multi-strategy file discovery (Doc 029).

Tests cover:
- .gitignore respect (git repos never return ignored files)
- Relevance scoring (more matched tokens = higher rank)
- Backward-compatible category lists
- Edge cases (empty query, no results, zero-padding)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.app.agent.tools.shell_utils import handle_project_search


@pytest.fixture(autouse=True)
def project_tree(tmp_path):
    """Create a realistic project tree inside a git repo with .gitignore."""
    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    # .gitignore — the key part
    (tmp_path / ".gitignore").write_text(
        "bin/\nobj/\nnode_modules/\n.venv/\n*.dll\npublish/\n"
    )

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
    (inspection_dir / "InspectionForm.razor").write_text(
        "@page \"/inspection/form\"\n<h1>Inspection Form</h1>\n"
        "<EditForm Model=\"@inspection\">\n</EditForm>"
    )
    pallet_dir = tmp_path / "src" / "pallet" / "views"
    pallet_dir.mkdir(parents=True)
    (pallet_dir / "PalletOverview.razor").write_text(
        "@page \"/pallet/overview\"\n<h1>Pallet Overview</h1>"
    )
    (pallet_dir / "PalletDefectReport.razor").write_text(
        "@page \"/pallet/defect-report\"\n<h1>Pallet Defect Report</h1>\n"
        "<DefectEntry />"
    )

    # More .razor files to create realistic noise
    shared_dir = tmp_path / "src" / "shared" / "components"
    shared_dir.mkdir(parents=True)
    for name in ["NavMenu", "Header", "Footer", "Sidebar", "Layout", "ErrorBoundary"]:
        (shared_dir / f"{name}.razor").write_text(f"<h1>{name}</h1>")

    # bin/ and obj/ — these MUST be ignored
    bin_dir = tmp_path / "src" / "inspection" / "bin" / "Debug" / "net8.0"
    bin_dir.mkdir(parents=True)
    (bin_dir / "DefectEntry.razor.g.cs").write_text("// generated code for DefectEntry")
    (bin_dir / "InspectionForm.dll").write_text("binary garbage")

    obj_dir = tmp_path / "src" / "inspection" / "obj" / "Debug"
    obj_dir.mkdir(parents=True)
    (obj_dir / "DefectEntry.razor.g.cs").write_text("// obj generated code")

    # node_modules (ignored)
    nm_dir = tmp_path / "node_modules" / "foo"
    nm_dir.mkdir(parents=True)
    (nm_dir / "027-something.md").write_text("noise")

    # Tests
    test_dir = tmp_path / "backend" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_worker.py").write_text("def test_worker(): pass")
    (test_dir / "test_manifest.py").write_text("def test_manifest(): pass")

    # git add tracked files (not ignored ones)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    return tmp_path


def _all_paths(result: dict) -> list[str]:
    """Extract all file paths from the ranked results."""
    return [e["path"] for e in result.get("results", [])]


def _all_basenames(result: dict) -> list[str]:
    return [os.path.basename(p) for p in _all_paths(result)]


# -------------------------------------------------------------------
# .gitignore respect
# -------------------------------------------------------------------

async def test_never_returns_gitignored_bin_files(project_tree):
    """Files in bin/ (gitignored) must NEVER appear in results."""
    result = await handle_project_search(
        {"query": "DefectEntry", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    for f in all_files:
        assert "/bin/" not in f, f"gitignored bin/ file returned: {f}"


async def test_never_returns_gitignored_obj_files(project_tree):
    """Files in obj/ (gitignored) must NEVER appear in results."""
    result = await handle_project_search(
        {"query": "DefectEntry", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    for f in all_files:
        assert "/obj/" not in f, f"gitignored obj/ file returned: {f}"


async def test_never_returns_node_modules(project_tree):
    """node_modules is gitignored and must not appear."""
    result = await handle_project_search(
        {"query": "027", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    for f in all_files:
        assert "node_modules" not in f, f"gitignored node_modules file returned: {f}"
        assert ".git" not in f or ".gitignore" in f, f"Should exclude .git: {f}"


# -------------------------------------------------------------------
# Relevance scoring
# -------------------------------------------------------------------

async def test_multi_token_match_ranks_higher(project_tree):
    """A file matching more query tokens should rank above one matching fewer."""
    result = await handle_project_search(
        {"query": "inspection defect razor", "path": str(project_tree)}, {}
    )
    results = result.get("results", [])
    assert len(results) > 0
    # DefectEntry.razor matches: inspection (path), defect (filename), razor (filename)
    # NavMenu.razor matches only: razor (filename)
    top_basenames = [os.path.basename(r["path"]) for r in results[:4]]
    assert "DefectEntry.razor" in top_basenames, \
        f"DefectEntry.razor should be in top results: {top_basenames}"
    # Check DefectEntry ranks above generic .razor files
    defect_idx = None
    navmenu_idx = None
    for i, r in enumerate(results):
        bn = os.path.basename(r["path"])
        if bn == "DefectEntry.razor":
            defect_idx = i
        elif bn == "NavMenu.razor":
            navmenu_idx = i
    if defect_idx is not None and navmenu_idx is not None:
        assert defect_idx < navmenu_idx, \
            f"DefectEntry.razor (idx={defect_idx}) should rank above NavMenu.razor (idx={navmenu_idx})"


async def test_pallet_defect_razor_ranks_high(project_tree):
    """PalletDefectReport.razor matches pallet+defect+razor — should rank highly."""
    result = await handle_project_search(
        {"query": "pallet defect razor", "path": str(project_tree)}, {}
    )
    results = result.get("results", [])
    top_basenames = [os.path.basename(r["path"]) for r in results[:3]]
    assert "PalletDefectReport.razor" in top_basenames, \
        f"PalletDefectReport.razor should be top-ranked: {top_basenames}"


# -------------------------------------------------------------------
# Basic functionality (ported from original tests)
# -------------------------------------------------------------------

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
    all_entries = result.get("results", [])
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
    basenames = _all_basenames(result)
    assert "DefectEntry.razor" in basenames, f"Expected DefectEntry.razor in results: {basenames}"


async def test_matches_parent_directory_names(project_tree):
    """Files should match when query words appear in parent/grandparent directory names."""
    result = await handle_project_search(
        {"query": "pallet", "path": str(project_tree)}, {}
    )
    all_files = _all_paths(result)
    assert any("PalletOverview" in f for f in all_files), \
        f"Expected PalletOverview.razor via directory match: {all_files}"


async def test_backward_compatible_category_lists(project_tree):
    """Result should still include filename_matches, path_matches, content_matches."""
    result = await handle_project_search(
        {"query": "manifest agent backend", "path": str(project_tree)}, {}
    )
    assert result["total_results"] > 0
    assert "filename_matches" in result
    assert "path_matches" in result
    assert "content_matches" in result
    assert "results" in result  # new ranked list


async def test_inspection_form_razor_search(project_tree):
    """The exact failing scenario: 'InspectionForm razor' should find InspectionForm.razor
    without needing shell_find."""
    result = await handle_project_search(
        {"query": "InspectionForm razor", "path": str(project_tree)}, {}
    )
    basenames = _all_basenames(result)
    assert "InspectionForm.razor" in basenames, \
        f"InspectionForm.razor must be found: {basenames}"
    # It should be the TOP result (matches both tokens in filename)
    assert basenames[0] == "InspectionForm.razor", \
        f"InspectionForm.razor should be #1, got: {basenames[0]}"
    # And NO bin/obj files
    for f in _all_paths(result):
        assert "/bin/" not in f, f"gitignored bin/ file leaked: {f}"
        assert "/obj/" not in f, f"gitignored obj/ file leaked: {f}"
