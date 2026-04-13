"""Tests for workspace knowledge graph Phase 2.

Covers: GraphifyAdapter, Phase 2 extractors (routes, tests, docs, config,
migrations), context pipeline integration, continuation integration,
and Phase 1 gaps (provenance, impact_analysis).
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.foundations.workspace_graph.adapter import (
    ExtractionEdge,
    ExtractionNode,
    GraphifyAdapter,
    GraphifyExtractionBatch,
    ImportSummary,
    _map_confidence,
)
from backend.app.foundations.workspace_graph.context_integration import (
    GraphContextHint,
    format_graph_hint_for_prompt,
    graph_context_for_files,
)
from backend.app.foundations.workspace_graph.continuation_integration import (
    GraphCheckpointAnchors,
    build_graph_anchors,
    format_graph_anchors_for_continuation,
)
from backend.app.foundations.workspace_graph.models import (
    FileState,
    GraphEdge,
    GraphNode,
    GraphRun,
    Provenance,
)
from backend.app.foundations.workspace_graph.phase2_extractors import (
    extract_phase2_artifacts,
    _guess_tested_file,
    _is_test_file,
)
from backend.app.foundations.workspace_graph.repository import (
    WorkspaceGraphRepository,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

WS_ID = "ws-phase2-test"
REPO_ID = "phase2-repo"


async def _setup_db(db_path: Path) -> None:
    """Apply migrations needed for workspace graph."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        for name in [
            "000001_init.up.sql",
            "000030_workspace_knowledge_graph.up.sql",
        ]:
            sql = (MIGRATIONS_DIR / name).read_text()
            await db.executescript(sql)


@pytest.fixture()
async def repo(tmp_path):
    """Create a WorkspaceGraphRepository with migrated schema."""
    db_path = tmp_path / "test_wkg_phase2.db"
    await _setup_db(db_path)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys = ON"))
        yield WorkspaceGraphRepository(session)
    await engine.dispose()


# ============================================================================
# Phase 1 gap: Provenance
# ============================================================================


@pytest.mark.asyncio
async def test_record_provenance(repo):
    node = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:test::foo",
        display_name="foo", path="test.py",
    ))

    prov_id = await repo.record_provenance(
        WS_ID,
        node_id=node.id,
        provenance_type="ast_extraction",
        source_path="test.py",
        source_line_start=10,
        source_line_end=20,
        excerpt="def foo():",
    )
    assert prov_id


@pytest.mark.asyncio
async def test_record_provenance_for_edge(repo):
    n1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:prov_a.py", display_name="prov_a.py",
    ))
    n2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:prov_a::bar", display_name="bar",
    ))
    edge = await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=n1.id, target_node_id=n2.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))

    prov_id = await repo.record_provenance(
        WS_ID,
        edge_id=edge.id,
        provenance_type="ast_extraction",
        source_path="prov_a.py",
        source_line_start=5,
        excerpt="def bar(x):",
    )
    assert prov_id


# ============================================================================
# Phase 1 gap: Impact analysis
# ============================================================================


@pytest.mark.asyncio
async def test_impact_analysis(repo):
    # Create a small graph: file -> defines -> symbol, symbol <- references <- file2
    f1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:impact_a.py", display_name="impact_a.py",
    ))
    sym = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:impact_a::func", display_name="func",
    ))
    f2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:impact_b.py", display_name="impact_b.py",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=f1.id, target_node_id=sym.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=f2.id, target_node_id=sym.id,
        edge_type="references", mode="extracted", source_kind="ast",
    ))

    result = await repo.impact_analysis(WS_ID, [f1.id], max_depth=2)
    assert f1.id in result.nodes
    assert sym.id in result.nodes
    assert f2.id in result.nodes
    assert len(result.edges) == 2


@pytest.mark.asyncio
async def test_impact_analysis_empty_seeds(repo):
    result = await repo.impact_analysis(WS_ID, [], max_depth=2)
    assert len(result.nodes) == 0


# ============================================================================
# Phase 2: Confidence mapping
# ============================================================================


def test_confidence_mapping():
    assert _map_confidence("EXTRACTED") == ("extracted", 1.0)
    assert _map_confidence("INFERRED") == ("inferred", 0.7)
    assert _map_confidence("AMBIGUOUS") == ("ambiguous", 0.4)
    assert _map_confidence("unknown") == ("extracted", 1.0)


# ============================================================================
# Phase 2: GraphifyAdapter
# ============================================================================


def test_adapter_graphify_not_available():
    adapter = GraphifyAdapter()
    # Graphify is not installed in this test env
    assert adapter.graphify_available is False


@pytest.mark.asyncio
async def test_adapter_extract_workspace(tmp_path):
    """Test that adapter produces a batch from a sample repo."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Create a Python file with a route
    (repo_dir / "app.py").write_text(textwrap.dedent("""\
        from fastapi import FastAPI
        app = FastAPI()

        @app.get("/users")
        def list_users():
            return []

        @app.post("/users")
        def create_user():
            pass
    """))

    # Create a test file
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(textwrap.dedent("""\
        def test_list_users():
            assert True

        def test_create_user():
            assert True
    """))

    # Create a migration
    migrations_dir = repo_dir / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_create_users.up.sql").write_text(
        "CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT);\n"
    )

    # Create a doc
    (repo_dir / "README.md").write_text("# My App\n\nA sample app.\n")

    # Create a config
    (repo_dir / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")

    adapter = GraphifyAdapter()
    batch = await adapter.extract_workspace(repo_dir)

    node_types = {n.node_type for n in batch.nodes}
    assert "route" in node_types
    assert "test" in node_types
    assert "migration" in node_types
    assert "document" in node_types
    assert "config_key" in node_types

    # Check route details
    route_nodes = [n for n in batch.nodes if n.node_type == "route"]
    route_labels = {n.label for n in route_nodes}
    assert "GET /users" in route_labels
    assert "POST /users" in route_labels

    # Check test details
    test_nodes = [n for n in batch.nodes if n.node_type == "test"]
    test_names = {n.label for n in test_nodes}
    assert "test_list_users" in test_names
    assert "test_create_user" in test_names

    # Check migration extracted table reference
    table_nodes = [n for n in batch.nodes if n.node_type == "table"]
    assert any(n.label == "users" for n in table_nodes)

    # Check edges
    edge_relations = {e.relation for e in batch.edges}
    assert "handles" in edge_relations
    assert "writes_to" in edge_relations


@pytest.mark.asyncio
async def test_adapter_import_batch():
    """Test that import_batch converts a batch into Bond graph objects."""
    adapter = GraphifyAdapter()

    batch = GraphifyExtractionBatch(
        nodes=[
            ExtractionNode(
                id="n1", label="GET /api", node_type="route",
                source_file="routes.py",
                source_location={"line_start": 5},
                attributes={"stable_key": "route:routes.py:GET:/api"},
            ),
            ExtractionNode(
                id="n2", label="test_api", node_type="test",
                source_file="test_routes.py",
                attributes={"stable_key": "test:test_routes.py::test_api"},
            ),
        ],
        edges=[
            ExtractionEdge(source="n1", target="n2", relation="covered_by"),
            ExtractionEdge(source="n1", target="dangling", relation="calls"),
        ],
    )

    nodes, edges, provenance, summary = adapter.import_batch(
        "ws-1", "repo-1", batch, "run-1"
    )

    assert summary.nodes_imported == 2
    assert summary.edges_imported == 1  # one edge skipped (dangling)
    assert summary.dangling_edges_skipped == 1
    assert summary.provenance_recorded == 2
    assert len(nodes) == 2
    assert len(edges) == 1
    assert edges[0].edge_type == "covered_by"


# ============================================================================
# Phase 2: Phase2 extractors
# ============================================================================


def test_is_test_file():
    assert _is_test_file("tests/test_foo.py", ".py", "test_foo.py")
    assert _is_test_file("src/foo.test.ts", ".ts", "foo.test.ts")
    assert _is_test_file("spec/bar.spec.js", ".js", "bar.spec.js")
    assert not _is_test_file("src/foo.py", ".py", "foo.py")


def test_guess_tested_file():
    assert _guess_tested_file("tests/test_foo.py") is not None
    assert _guess_tested_file("src/components/__tests__/Button.test.tsx") is not None
    assert _guess_tested_file("src/app.py") is None


def test_extract_routes_python(tmp_path):
    (tmp_path / "routes.py").write_text(textwrap.dedent("""\
        @router.get("/items")
        def get_items():
            pass

        @router.post("/items")
        def create_item():
            pass
    """))

    batch = extract_phase2_artifacts(tmp_path)
    routes = [n for n in batch.nodes if n.node_type == "route"]
    assert len(routes) == 2
    methods = {n.attributes.get("method") for n in routes}
    assert methods == {"GET", "POST"}


def test_extract_routes_express(tmp_path):
    (tmp_path / "server.js").write_text(textwrap.dedent("""\
        app.get('/api/users', (req, res) => {});
        app.post('/api/users', (req, res) => {});
        router.delete('/api/users/:id', handler);
    """))

    batch = extract_phase2_artifacts(tmp_path)
    routes = [n for n in batch.nodes if n.node_type == "route"]
    assert len(routes) == 3


def test_extract_tests_pytest(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(textwrap.dedent("""\
        class TestCore:
            def test_add(self):
                assert 1 + 1 == 2

        def test_subtract():
            assert 2 - 1 == 1

        async def test_async_op():
            pass
    """))

    batch = extract_phase2_artifacts(tmp_path)
    tests = [n for n in batch.nodes if n.node_type == "test"]
    names = {n.label for n in tests}
    assert "TestCore" in names
    assert "test_add" in names
    assert "test_subtract" in names
    assert "test_async_op" in names


def test_extract_tests_jest(tmp_path):
    (tmp_path / "app.test.ts").write_text(textwrap.dedent("""\
        describe('App', () => {
            it('should render', () => {});
            test('handles click', () => {});
        });
    """))

    batch = extract_phase2_artifacts(tmp_path)
    tests = [n for n in batch.nodes if n.node_type == "test"]
    names = {n.label for n in tests}
    assert "App" in names
    assert "should render" in names
    assert "handles click" in names


def test_extract_docs(tmp_path):
    (tmp_path / "README.md").write_text("# Project Docs\n\nSome content.\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## v1.0\n")

    batch = extract_phase2_artifacts(tmp_path)
    docs = [n for n in batch.nodes if n.node_type == "document"]
    assert len(docs) == 2
    labels = {n.label for n in docs}
    assert "Project Docs" in labels
    assert "Changelog" in labels


def test_extract_config(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    (tmp_path / "tsconfig.json").write_text("{}\n")
    (tmp_path / ".env").write_text("KEY=val\n")

    batch = extract_phase2_artifacts(tmp_path)
    configs = [n for n in batch.nodes if n.node_type == "config_key"]
    names = {n.label for n in configs}
    assert "pyproject.toml" in names
    assert "tsconfig.json" in names
    assert ".env" in names


def test_extract_migrations(tmp_path):
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_init.up.sql").write_text(
        "CREATE TABLE users (id TEXT PRIMARY KEY);\n"
        "CREATE TABLE orders (id TEXT PRIMARY KEY, user_id TEXT);\n"
    )

    batch = extract_phase2_artifacts(tmp_path)
    migrations = [n for n in batch.nodes if n.node_type == "migration"]
    assert len(migrations) == 1

    tables = [n for n in batch.nodes if n.node_type == "table"]
    table_names = {n.label for n in tables}
    assert "users" in table_names
    assert "orders" in table_names

    writes_to = [e for e in batch.edges if e.relation == "writes_to"]
    assert len(writes_to) == 2


def test_extract_file_subset(tmp_path):
    (tmp_path / "a.py").write_text("@app.get('/a')\ndef a(): pass\n")
    (tmp_path / "b.py").write_text("@app.get('/b')\ndef b(): pass\n")

    batch = extract_phase2_artifacts(tmp_path, file_subset=["a.py"])
    routes = [n for n in batch.nodes if n.node_type == "route"]
    assert len(routes) == 1
    assert routes[0].attributes.get("path") == "/a"


def test_extraction_batch_dangling_count():
    batch = GraphifyExtractionBatch(
        nodes=[ExtractionNode(id="n1", label="x", node_type="file")],
        edges=[
            ExtractionEdge(source="n1", target="n2", relation="calls"),
        ],
    )
    assert batch.dangling_edge_count == 1


# ============================================================================
# Phase 2: Context pipeline integration
# ============================================================================


@pytest.mark.asyncio
async def test_graph_context_for_files(repo):
    """Test context integration with a small graph."""
    # Build a graph: file -> defines -> symbol <- references <- test_file
    f1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:src/app.py",
        display_name="app.py", path="src/app.py",
    ))
    sym = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:src/app.py::handler",
        display_name="handler", path="src/app.py",
    ))
    t1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:tests/test_app.py",
        display_name="test_app.py", path="tests/test_app.py",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=f1.id, target_node_id=sym.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=t1.id, target_node_id=sym.id,
        edge_type="references", mode="extracted", source_kind="ast",
    ))

    hint = await graph_context_for_files(
        repo, WS_ID, ["src/app.py"]
    )

    # test_app.py should show up as a candidate file
    assert "tests/test_app.py" in hint.candidate_files
    assert hint.relationship_summary


@pytest.mark.asyncio
async def test_graph_context_no_matches(repo):
    hint = await graph_context_for_files(repo, WS_ID, ["nonexistent.py"])
    assert hint.candidate_files == []
    assert hint.relationship_summary == ""


def test_format_graph_hint_empty():
    hint = GraphContextHint()
    assert format_graph_hint_for_prompt(hint) == ""


def test_format_graph_hint():
    hint = GraphContextHint(
        candidate_files=["a.py", "b.py"],
        related_tests=["test_a.py"],
        related_docs=["README.md"],
        relationship_summary="Graph context: 5 nodes, 4 edges",
    )
    output = format_graph_hint_for_prompt(hint)
    assert "## Workspace Graph Context" in output
    assert "a.py" in output
    assert "test_a.py" in output
    assert "README.md" in output


# ============================================================================
# Phase 2: Continuation integration
# ============================================================================


@pytest.mark.asyncio
async def test_build_graph_anchors(repo):
    f1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:modified.py",
        display_name="modified.py", path="modified.py",
    ))
    f2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:neighbor.py",
        display_name="neighbor.py", path="neighbor.py",
    ))
    sym = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:modified.py::func",
        display_name="func", path="modified.py",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=f1.id, target_node_id=sym.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=f2.id, target_node_id=sym.id,
        edge_type="references", mode="extracted", source_kind="ast",
    ))

    anchors = await build_graph_anchors(
        repo, WS_ID, ["modified.py"]
    )

    assert len(anchors.anchor_node_ids) >= 1
    assert "neighbor.py" in anchors.impacted_file_paths
    assert "Graph anchors:" in anchors.summary


@pytest.mark.asyncio
async def test_build_graph_anchors_no_matches(repo):
    anchors = await build_graph_anchors(repo, WS_ID, ["nonexistent.py"])
    assert anchors.anchor_node_ids == []


def test_format_graph_anchors_empty():
    anchors = GraphCheckpointAnchors()
    assert format_graph_anchors_for_continuation(anchors) == ""


def test_format_graph_anchors():
    anchors = GraphCheckpointAnchors(
        anchor_node_ids=["id1"],
        impacted_file_paths=["a.py", "b.py"],
        related_test_paths=["test_a.py"],
        summary="Graph anchors: 1 changed files, 2 impacted files",
    )
    output = format_graph_anchors_for_continuation(anchors)
    assert "## Graph-Anchored Context" in output
    assert "a.py" in output
    assert "test_a.py" in output


# ============================================================================
# Phase 2: Extractor provenance
# ============================================================================


@pytest.mark.asyncio
async def test_extractor_produces_provenance(tmp_path):
    """Test that Phase 1 extractor now produces provenance records."""
    from backend.app.foundations.workspace_graph.extractor import WorkspaceGraphExtractor

    repo_dir = tmp_path / "workspace" / "prov-repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "mod.py").write_text("def hello():\n    pass\n")

    extractor = WorkspaceGraphExtractor()
    result = extractor.extract_workspace(
        workspace_root=str(tmp_path / "workspace"),
        workspace_id="ws-prov",
    )

    # If tree-sitter found the symbol, we should have provenance
    if any(n.node_type == "symbol" for n in result.nodes):
        assert len(result.provenance) > 0
        prov = result.provenance[0]
        assert prov.provenance_type == "ast_extraction"
        assert prov.source_path is not None
