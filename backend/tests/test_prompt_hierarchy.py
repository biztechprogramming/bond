"""Tests for the prompt hierarchy system (Design Doc 021)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.app.agent.tools.dynamic_loader import (
    generate_manifest,
    load_context_fragments,
    load_universal_fragments,
)


@pytest.fixture()
def prompts_dir(tmp_path: Path) -> Path:
    """Create a realistic prompts directory tree for testing."""
    # universal/
    uni = tmp_path / "universal"
    uni.mkdir()
    (uni / "communication.md").write_text("## Communication\nBe concise.")
    (uni / "safety.md").write_text("## Safety\nDon't break things.")
    (uni / "reasoning.md").write_text("## Reasoning\nThink first.")

    # engineering/
    eng = tmp_path / "engineering"
    eng.mkdir()
    (eng / "engineering.md").write_text("## Engineering\nSOLID, DRY, YAGNI.")

    # engineering/git/
    git = eng / "git"
    git.mkdir()
    (git / "git.md").write_text("## Git\nBranching strategy.")

    # engineering/git/commits/
    commits = git / "commits"
    commits.mkdir()
    (commits / "commits.md").write_text("## Commits\nAtomic commits.")

    # engineering/git/pull-requests/
    prs = git / "pull-requests"
    prs.mkdir()
    (prs / "pull-requests.md").write_text("## Pull Requests\nKeep PRs small.")

    # engineering/code-quality/
    cq = eng / "code-quality"
    cq.mkdir()
    (cq / "code-quality.md").write_text("## Code Quality\nCorrectness over cleverness.")

    # engineering/code-quality/must-compile/
    mc = cq / "must-compile"
    mc.mkdir()
    (mc / "must-compile.md").write_text("## Must Compile\nZero warnings.")

    # infrastructure/
    infra = tmp_path / "infrastructure"
    infra.mkdir()
    (infra / "infrastructure.md").write_text("## Infrastructure\nIaC principles.")

    # infrastructure/docker/
    docker = infra / "docker"
    docker.mkdir()
    (docker / "docker.md").write_text("## Docker\nSmall images.")

    # infrastructure/docker/sandbox/
    sandbox = docker / "sandbox"
    sandbox.mkdir()
    (sandbox / "sandbox.md").write_text("## Sandbox\nContainer rules.")

    return tmp_path


class TestGenerateManifest:
    def test_finds_leaf_nodes(self, prompts_dir: Path):
        manifest = generate_manifest(prompts_dir)
        assert "engineering" in manifest
        assert "engineering.git" in manifest
        assert "engineering.git.commits" in manifest
        assert "engineering.git.pull-requests" in manifest
        assert "engineering.code-quality" in manifest
        assert "engineering.code-quality.must-compile" in manifest
        assert "infrastructure" in manifest
        assert "infrastructure.docker" in manifest
        assert "infrastructure.docker.sandbox" in manifest

    def test_excludes_universal(self, prompts_dir: Path):
        manifest = generate_manifest(prompts_dir)
        # Universal should never appear as a selectable category
        assert "universal" not in manifest

    def test_leaf_only_no_stray_files(self, prompts_dir: Path):
        """Only files matching dirname/dirname.md pattern appear."""
        # Add a stray file that doesn't match the pattern
        (prompts_dir / "engineering" / "notes.md").write_text("Random notes")
        manifest = generate_manifest(prompts_dir)
        # notes.md should not appear since its stem != parent name
        assert "notes" not in manifest.lower() or "engineering.notes" not in manifest

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        empty = tmp_path / "empty_prompts"
        empty.mkdir()
        manifest = generate_manifest(empty)
        assert manifest == ""

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path):
        manifest = generate_manifest(tmp_path / "nonexistent")
        assert manifest == ""


class TestLoadContextFragments:
    def test_loads_ancestor_chain_only(self, prompts_dir: Path):
        """load_context loads only the specific category chain — NOT universal.
        Universal fragments are injected into the system prompt at startup."""
        result = load_context_fragments("engineering.git.commits", prompts_dir)
        # Should NOT contain universal fragments (those go in system prompt)
        assert "Communication" not in result
        assert "Safety" not in result
        assert "Reasoning" not in result
        # Should contain the ancestor chain
        assert "Engineering" in result
        assert "Git" in result  # engineering/git/git.md
        assert "Atomic commits" in result  # engineering/git/commits/commits.md

    def test_does_not_load_sibling_fragments(self, prompts_dir: Path):
        result = load_context_fragments("engineering.git.commits", prompts_dir)
        # Should NOT contain pull-requests (sibling of commits)
        assert "Pull Requests" not in result
        # Should NOT contain code-quality (sibling of git)
        assert "Code Quality" not in result

    def test_infrastructure_path(self, prompts_dir: Path):
        result = load_context_fragments("infrastructure.docker.sandbox", prompts_dir)
        assert "Communication" not in result  # universal NOT loaded here
        assert "Infrastructure" in result  # infrastructure.md
        assert "Docker" in result  # docker.md
        assert "Container rules" in result  # sandbox.md
        # Should NOT contain engineering content
        assert "Engineering" not in result

    def test_unknown_category_returns_error(self, prompts_dir: Path):
        result = load_context_fragments("nonexistent.path", prompts_dir)
        assert "Error:" in result or "unknown category" in result.lower()

    def test_partial_path_loads_available(self, prompts_dir: Path):
        """Loading just 'engineering' should work — it's a valid node."""
        result = load_context_fragments("engineering", prompts_dir)
        assert "Communication" not in result  # universal NOT loaded here
        assert "Engineering" in result  # engineering.md
        # Should NOT contain deeper fragments
        assert "Atomic commits" not in result

    def test_empty_category_returns_error(self, prompts_dir: Path):
        """Empty category is invalid — should return an error."""
        result = load_context_fragments("", prompts_dir)
        assert "Error:" in result

    def test_separator_joined_by_dividers(self, prompts_dir: Path):
        result = load_context_fragments("engineering.git", prompts_dir)
        assert "---" in result

    def test_nonexistent_prompts_dir(self, tmp_path: Path):
        result = load_context_fragments("engineering", tmp_path / "nope")
        assert "Error:" in result


class TestLoadUniversalFragments:
    def test_loads_all_universal_files(self, prompts_dir: Path):
        result = load_universal_fragments(prompts_dir)
        assert "Communication" in result
        assert "Safety" in result
        assert "Reasoning" in result

    def test_returns_empty_for_missing_universal_dir(self, tmp_path: Path):
        # No universal/ subdirectory
        (tmp_path / "engineering").mkdir()
        result = load_universal_fragments(tmp_path)
        assert result == ""

    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path):
        result = load_universal_fragments(tmp_path / "nope")
        assert result == ""

    def test_fragments_separated_by_dividers(self, prompts_dir: Path):
        result = load_universal_fragments(prompts_dir)
        assert "---" in result
