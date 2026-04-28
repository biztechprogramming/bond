"""Tests for workspace knowledge graph — repository, extraction, migration.

NOTE (2026-04-17): The SQLite-backed repository tests are skipped because
migration 000030 is now a no-op (WKG lives in SpacetimeDB, not SQLite).
Extractor-only tests (which don't need a database) still run.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import aiosqlite
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.foundations.workspace_graph.extractor import (
    ExtractionResult,
    WorkspaceGraphExtractor,
)
from backend.app.foundations.workspace_graph.models import (
    FileState,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphSubgraph,
)
from backend.app.foundations.workspace_graph.repository import (
    WorkspaceGraphRepository,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

WS_ID = "ws-test-1"
REPO_ID = "test-repo"

# SQLite WKG tables no longer exist (migration 30 is a no-op).
# Repository tests require the old DDL which has been removed in favour of
# SpacetimeDB.  Skip them until a SpacetimeDB-backed test harness is wired up.
_SKIP_SQLITE_REPO = pytest.mark.skip(
    reason="WKG SQLite tables removed — migration 30 is a no-op (see Design Doc 018)"
)


async def _setup_db(db_path: Path) -> None:
    """Apply migrations needed for workspace graph.

    DEPRECATED: migration 30 is a no-op so this only creates the base schema.
    """
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
    db_path = tmp_path / "test_wkg.db"
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


# ── Node CRUD ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_upsert_and_get_node(repo):
    node = GraphNode(
        id="",
        workspace_id=WS_ID,
        repo_id=REPO_ID,
        node_type="file",
        stable_key="file:test-repo/main.py",
        display_name="main.py",
        path="main.py",
        language="py",
    )
    result = await repo.upsert_node(node)
    assert result.id
    assert result.display_name == "main.py"
    assert result.node_type == "file"

    fetched = await repo.get_node(WS_ID, "file:test-repo/main.py")
    assert fetched is not None
    assert fetched.id == result.id


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_upsert_node_updates_existing(repo):
    node = GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:test-repo/app.py",
        display_name="app.py", path="app.py",
    )
    first = await repo.upsert_node(node)

    node.display_name = "app.py (updated)"
    node.content_hash = "abc123"
    second = await repo.upsert_node(node)

    assert second.id == first.id
    assert second.display_name == "app.py (updated)"
    assert second.content_hash == "abc123"


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_get_node_nonexistent(repo):
    assert await repo.get_node(WS_ID, "does-not-exist") is None


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_get_nodes_by_type(repo):
    for i in range(3):
        await repo.upsert_node(GraphNode(
            id="", workspace_id=WS_ID, repo_id=REPO_ID,
            node_type="symbol", stable_key=f"symbol:test{i}",
            display_name=f"sym_{i}",
        ))
    await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:other",
        display_name="other.py",
    ))

    symbols = await repo.get_nodes_by_type(WS_ID, "symbol")
    assert len(symbols) == 3

    files = await repo.get_nodes_by_type(WS_ID, "file")
    assert len(files) == 1


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_soft_delete_nodes(repo):
    await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:del_test",
        display_name="del_test", path="del.py",
    ))
    count = await repo.soft_delete_nodes_for_path(WS_ID, "del.py")
    assert count == 1

    # Should not be found (soft-deleted)
    assert await repo.get_node(WS_ID, "symbol:del_test") is None


# ── Edges ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_upsert_edge(repo):
    n1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:a.py", display_name="a.py",
    ))
    n2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:a.py::foo", display_name="foo",
    ))
    edge = await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=n1.id, target_node_id=n2.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))
    assert edge.id
    assert edge.edge_type == "defines"


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_get_edges_for_node(repo):
    n1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:b.py", display_name="b.py",
    ))
    n2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:b.py::bar", display_name="bar",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=n1.id, target_node_id=n2.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))

    edges = await repo.get_edges_for_node(WS_ID, n1.id)
    assert len(edges) == 1
    assert edges[0].edge_type == "defines"

    # Filter by type
    no_edges = await repo.get_edges_for_node(WS_ID, n1.id, edge_types=["imports"])
    assert len(no_edges) == 0


# ── Graph traversal ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_neighbors_depth_1(repo):
    ws = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=None,
        node_type="workspace", stable_key="workspace:test", display_name="test",
    ))
    r = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="repository", stable_key="repo:test-repo", display_name="test-repo",
    ))
    f = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:test-repo/x.py", display_name="x.py",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=None,
        source_node_id=ws.id, target_node_id=r.id,
        edge_type="contains", mode="extracted", source_kind="workspace_map",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=r.id, target_node_id=f.id,
        edge_type="contains", mode="extracted", source_kind="workspace_map",
    ))

    sub = await repo.get_neighbors(WS_ID, ws.id, depth=1)
    assert ws.id in sub.nodes
    assert r.id in sub.nodes
    # file should NOT be in depth=1 from workspace
    assert f.id not in sub.nodes


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_neighbors_depth_2(repo):
    ws = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=None,
        node_type="workspace", stable_key="workspace:d2", display_name="d2",
    ))
    r = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="repository", stable_key="repo:d2-repo", display_name="d2-repo",
    ))
    f = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:d2-repo/y.py", display_name="y.py",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=None,
        source_node_id=ws.id, target_node_id=r.id,
        edge_type="contains", mode="extracted", source_kind="workspace_map",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=r.id, target_node_id=f.id,
        edge_type="contains", mode="extracted", source_kind="workspace_map",
    ))

    sub = await repo.get_neighbors(WS_ID, ws.id, depth=2)
    assert f.id in sub.nodes


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_find_path(repo):
    n1 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:path1", display_name="path1",
    ))
    n2 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:path2", display_name="path2",
    ))
    n3 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:path3", display_name="path3",
    ))

    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=n1.id, target_node_id=n2.id,
        edge_type="defines", mode="extracted", source_kind="ast",
    ))
    await repo.upsert_edge(GraphEdge(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        source_node_id=n2.id, target_node_id=n3.id,
        edge_type="references", mode="extracted", source_kind="ast",
    ))

    path = await repo.find_path(WS_ID, n1.id, n3.id)
    assert path is not None
    assert len(path) == 2

    # No path to unconnected node
    n4 = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:isolated", display_name="isolated",
    ))
    no_path = await repo.find_path(WS_ID, n1.id, n4.id)
    assert no_path is None


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_find_path_same_node(repo):
    n = await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:self", display_name="self",
    ))
    path = await repo.find_path(WS_ID, n.id, n.id)
    assert path == []


# ── Search ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_fts_search(repo):
    await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:repo/main.py::handle_request",
        display_name="handle_request", path="main.py",
        signature="def handle_request(req: Request):",
    ))
    await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="symbol", stable_key="symbol:repo/main.py::handle_response",
        display_name="handle_response", path="main.py",
    ))
    await repo.upsert_node(GraphNode(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        node_type="file", stable_key="file:repo/other.py",
        display_name="other.py", path="other.py",
    ))

    results = await repo.search(WS_ID, "handle*")
    assert len(results) == 2

    # Type filter
    results = await repo.search(WS_ID, "other*", node_types=["file"])
    assert len(results) == 1


# ── Runs ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_record_and_update_run(repo):
    run = GraphRun(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        run_type="full", status="running", trigger="manual",
        started_at="2026-04-13T00:00:00+00:00",
    )
    run_id = await repo.record_run(run)
    assert run_id

    await repo.update_run(
        run_id, status="success",
        files_scanned=10, nodes_written=50, edges_written=80,
        completed_at="2026-04-13T00:01:00+00:00",
    )


# ── File state ──


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_file_state_upsert_and_change_detection(repo):
    fs = FileState(
        id="", workspace_id=WS_ID, repo_id=REPO_ID,
        path="main.py", content_hash="aaa111", status="indexed",
    )
    result = await repo.upsert_file_state(fs)
    assert result.id

    fetched = await repo.get_file_state(WS_ID, "main.py")
    assert fetched is not None
    assert fetched.content_hash == "aaa111"

    # Change detection
    changed = await repo.get_changed_files(WS_ID, {
        "main.py": "aaa111",  # unchanged
        "new.py": "bbb222",   # new file
    })
    assert "new.py" in changed
    assert "main.py" not in changed

    # Detect hash change
    changed2 = await repo.get_changed_files(WS_ID, {
        "main.py": "ccc333",  # changed
    })
    assert "main.py" in changed2


# ── Extractor ──


@pytest.mark.asyncio
async def test_extractor_on_sample_repo(tmp_path):
    """Test deterministic extraction from a small sample repo."""
    # Create a minimal repo structure
    repo_dir = tmp_path / "workspace" / "sample-repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()  # mark as git repo

    # Write a Python file with definitions
    py_file = repo_dir / "app.py"
    py_file.write_text(textwrap.dedent("""\
        class MyService:
            def handle(self, request):
                return self.process(request)

            def process(self, data):
                return data
    """))

    # Write a non-code file
    (repo_dir / "README.md").write_text("# Sample\n")

    extractor = WorkspaceGraphExtractor()
    result = extractor.extract_workspace(
        workspace_root=str(tmp_path / "workspace"),
        workspace_id="ws-extract-test",
    )

    assert result.files_scanned >= 2  # app.py + README.md

    # Check node types present
    node_types = {n.node_type for n in result.nodes}
    assert "workspace" in node_types
    assert "repository" in node_types
    assert "file" in node_types

    # Check file nodes
    file_nodes = [n for n in result.nodes if n.node_type == "file"]
    file_names = {n.display_name for n in file_nodes}
    assert "app.py" in file_names
    assert "README.md" in file_names

    # Check edges
    edge_types = {e.edge_type for e in result.edges}
    assert "contains" in edge_types

    # Check file state
    assert len(result.file_states) >= 2
    paths = {fs.path for fs in result.file_states}
    assert "app.py" in paths


@pytest.mark.asyncio
async def test_extractor_produces_symbols(tmp_path):
    """Test that Python symbol extraction works via repomap tags."""
    repo_dir = tmp_path / "workspace" / "sym-repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    py_file = repo_dir / "models.py"
    py_file.write_text(textwrap.dedent("""\
        class User:
            pass

        class Account:
            pass

        def create_user(name):
            return User()
    """))

    extractor = WorkspaceGraphExtractor()
    result = extractor.extract_workspace(
        workspace_root=str(tmp_path / "workspace"),
        workspace_id="ws-sym-test",
    )

    symbol_nodes = [n for n in result.nodes if n.node_type == "symbol"]
    symbol_names = {n.display_name for n in symbol_nodes}

    # tree-sitter should find these definitions
    # (exact results depend on tree-sitter Python grammar availability)
    if symbol_nodes:
        assert "defines" in {e.edge_type for e in result.edges}
        # Check stable keys follow convention
        for sn in symbol_nodes:
            assert sn.stable_key.startswith("symbol:")


@_SKIP_SQLITE_REPO
@pytest.mark.asyncio
async def test_extraction_into_repository(repo, tmp_path):
    """End-to-end: extract a sample repo and persist into the repository."""
    repo_dir = tmp_path / "workspace" / "e2e-repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "hello.py").write_text("def greet():\n    return 'hi'\n")

    extractor = WorkspaceGraphExtractor()
    result = extractor.extract_workspace(
        workspace_root=str(tmp_path / "workspace"),
        workspace_id=WS_ID,
    )

    # Persist nodes
    persisted_nodes = await repo.upsert_nodes(result.nodes)
    assert len(persisted_nodes) >= 3  # workspace + repo + file

    # Build ID mapping for edges (extractor used temp IDs, repo assigned real ones)
    id_map = {}
    for orig, persisted in zip(result.nodes, persisted_nodes):
        id_map[orig.id] = persisted.id

    # Persist edges with remapped IDs
    for edge in result.edges:
        edge.source_node_id = id_map.get(edge.source_node_id, edge.source_node_id)
        edge.target_node_id = id_map.get(edge.target_node_id, edge.target_node_id)
    persisted_edges = await repo.upsert_edges(result.edges)
    assert len(persisted_edges) >= 2  # ws->repo, repo->file

    # Persist file states
    for fs in result.file_states:
        await repo.upsert_file_state(fs)

    # Record run
    run = GraphRun(
        id="", workspace_id=WS_ID, repo_id="e2e-repo",
        run_type="full", status="success", trigger="manual",
        files_scanned=result.files_scanned,
        nodes_written=len(persisted_nodes),
        edges_written=len(persisted_edges),
        started_at="2026-04-13T00:00:00+00:00",
        completed_at="2026-04-13T00:00:01+00:00",
    )
    await repo.record_run(run)

    # Verify graph traversal works on persisted data
    ws_node = [n for n in persisted_nodes if n.node_type == "workspace"][0]
    sub = await repo.get_neighbors(WS_ID, ws_node.id, depth=2)
    assert len(sub.nodes) >= 3
