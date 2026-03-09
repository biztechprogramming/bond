"""Tests for Tier 3 semantic fragment router (Doc 027 Phase 3).

Covers:
- Route layer construction from manifest
- Similarity-based fragment selection
- Irrelevant queries returning empty/different results
- Cache rebuild
- Edge cases: no manifest, empty Tier 3, uninitialized router
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.manifest import FragmentMeta, invalidate_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset both manifest and router caches between tests."""
    invalidate_cache()
    import backend.app.agent.fragment_router as fr

    fr._router = None
    fr._encoder = None
    fr._route_to_fragment = {}
    yield
    invalidate_cache()
    fr._router = None
    fr._encoder = None
    fr._route_to_fragment = {}


@pytest.fixture
def tmp_prompts(tmp_path: Path) -> Path:
    """Create a minimal prompts dir with manifest and Tier 3 files."""
    manifest = textwrap.dedent("""\
        tier1/always.md:
          tier: 1

        backend/python.md:
          tier: 3
          utterances:
            - "Python code"
            - "write Python"
            - "Python script"

        database/sql.md:
          tier: 3
          utterances:
            - "SQL query"
            - "database schema"
            - "relational database"

        frontend/react.md:
          tier: 3
          utterances:
            - "React component"
            - "frontend UI"
            - "JSX template"
    """)

    (tmp_path / "manifest.yaml").write_text(manifest)

    # Create the fragment files
    (tmp_path / "tier1").mkdir()
    (tmp_path / "tier1" / "always.md").write_text("Always-on content")

    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "python.md").write_text("Python coding guidelines and best practices.")

    (tmp_path / "database").mkdir()
    (tmp_path / "database" / "sql.md").write_text("SQL database design patterns and query optimization.")

    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "react.md").write_text("React component architecture and hooks patterns.")

    return tmp_path


# ---------------------------------------------------------------------------
# Build route layer tests
# ---------------------------------------------------------------------------


class TestBuildRouteLayer:
    """Tests for build_route_layer()."""

    def test_builds_from_manifest(self, tmp_prompts: Path):
        import backend.app.agent.fragment_router as fr

        fr.build_route_layer(tmp_prompts)

        assert fr._router is not None
        # Should have 3 Tier 3 routes (not the Tier 1 entry)
        assert len(fr._route_to_fragment) == 3
        assert "backend/python.md" in fr._route_to_fragment
        assert "database/sql.md" in fr._route_to_fragment
        assert "frontend/react.md" in fr._route_to_fragment

    def test_cached_after_first_build(self, tmp_prompts: Path):
        import backend.app.agent.fragment_router as fr

        fr.build_route_layer(tmp_prompts)
        router_ref = fr._router
        fr.build_route_layer(tmp_prompts)  # Should be no-op
        assert fr._router is router_ref

    def test_no_manifest(self, tmp_path: Path):
        """Missing manifest.yaml should result in empty router."""
        import backend.app.agent.fragment_router as fr

        fr.build_route_layer(tmp_path)

        assert fr._router is not None
        assert len(fr._route_to_fragment) == 0

    def test_no_tier3_fragments(self, tmp_path: Path):
        """Manifest with only Tier 1 entries should produce empty router."""
        manifest = textwrap.dedent("""\
            tier1/always.md:
              tier: 1
        """)
        (tmp_path / "manifest.yaml").write_text(manifest)
        (tmp_path / "tier1").mkdir()
        (tmp_path / "tier1" / "always.md").write_text("Always on")

        import backend.app.agent.fragment_router as fr

        fr.build_route_layer(tmp_path)

        assert fr._router is not None
        assert len(fr._route_to_fragment) == 0

    def test_empty_utterances_skipped(self, tmp_path: Path):
        """Tier 3 entries with empty utterances should be skipped."""
        manifest = textwrap.dedent("""\
            backend/empty.md:
              tier: 3
              utterances: []

            backend/good.md:
              tier: 3
              utterances:
                - "Python code"
        """)
        (tmp_path / "manifest.yaml").write_text(manifest)
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "empty.md").write_text("Empty utterances")
        (tmp_path / "backend" / "good.md").write_text("Good fragment")

        import backend.app.agent.fragment_router as fr

        fr.build_route_layer(tmp_path)

        assert len(fr._route_to_fragment) == 1
        assert "backend/good.md" in fr._route_to_fragment


# ---------------------------------------------------------------------------
# Fragment selection tests
# ---------------------------------------------------------------------------


class TestSelectFragments:
    """Tests for select_fragments_by_similarity()."""

    @pytest.mark.asyncio
    async def test_relevant_query_returns_matches(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, select_fragments_by_similarity

        build_route_layer(tmp_prompts)

        results = await select_fragments_by_similarity("Write a Python function to parse JSON")
        assert len(results) >= 1
        paths = [f.path for f in results]
        assert "backend/python.md" in paths

    @pytest.mark.asyncio
    async def test_database_query_returns_sql(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, select_fragments_by_similarity

        build_route_layer(tmp_prompts)

        results = await select_fragments_by_similarity("Design a SQL database schema for users")
        assert len(results) >= 1
        paths = [f.path for f in results]
        assert "database/sql.md" in paths

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, select_fragments_by_similarity

        build_route_layer(tmp_prompts)

        results = await select_fragments_by_similarity("code", top_k=1)
        assert len(results) <= 1

    @pytest.mark.asyncio
    async def test_empty_message_returns_empty(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, select_fragments_by_similarity

        build_route_layer(tmp_prompts)

        results = await select_fragments_by_similarity("")
        assert results == []

        results = await select_fragments_by_similarity("   ")
        assert results == []

    @pytest.mark.asyncio
    async def test_router_not_initialized_returns_empty(self):
        from backend.app.agent.fragment_router import select_fragments_by_similarity

        results = await select_fragments_by_similarity("Python code")
        assert results == []

    @pytest.mark.asyncio
    async def test_fragments_have_content(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, select_fragments_by_similarity

        build_route_layer(tmp_prompts)

        results = await select_fragments_by_similarity("Write Python code")
        for f in results:
            assert f.content, f"Fragment {f.path} has no content"
            assert f.token_estimate > 0


# ---------------------------------------------------------------------------
# Rebuild tests
# ---------------------------------------------------------------------------


class TestRebuildRoutes:
    """Tests for rebuild_routes()."""

    def test_rebuild_refreshes_cache(self, tmp_prompts: Path):
        from backend.app.agent.fragment_router import build_route_layer, rebuild_routes

        build_route_layer(tmp_prompts)

        import backend.app.agent.fragment_router as fr

        old_router = fr._router

        rebuild_routes(tmp_prompts)
        assert fr._router is not old_router
        assert len(fr._route_to_fragment) == 3


# ---------------------------------------------------------------------------
# Audit metadata tests
# ---------------------------------------------------------------------------


class TestTier3Meta:
    """Tests for get_tier3_meta()."""

    def test_meta_format(self):
        from backend.app.agent.fragment_router import get_tier3_meta

        frags = [
            FragmentMeta(
                path="backend/python.md",
                tier=3,
                content="content",
                token_estimate=100,
            ),
        ]
        meta = get_tier3_meta(frags)
        assert len(meta) == 1
        assert meta[0]["source"] == "semantic-router-tier3"
        assert meta[0]["path"] == "backend/python.md"
        assert meta[0]["name"] == "python"
        assert meta[0]["tokenEstimate"] == 100
