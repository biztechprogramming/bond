"""Workspace knowledge graph repository — CRUD, graph traversal, search.

DEPRECATED (2026-04-17): This SQLite-backed repository is superseded by SpacetimeDB
tables and reducers.  Migration 000030 is a no-op; the WKG schema now lives in
SpacetimeDB (see spacetimedb/spacetimedb/src/index.ts and Design Doc 018).
Do NOT invest further in this module — it will be removed once the SpacetimeDB
repository adapter is in place.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from .models import (
    FileState,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphSubgraph,
)

logger = logging.getLogger(__name__)


class WorkspaceGraphRepository:
    """Repository for workspace graph nodes, edges, runs, file state, and traversal."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Nodes ──

    async def upsert_node(self, node: GraphNode) -> GraphNode:
        """Insert or update a node by (workspace_id, stable_key)."""
        existing = await self._session.execute(
            text(
                "SELECT id FROM workspace_graph_nodes "
                "WHERE workspace_id = :ws AND stable_key = :sk"
            ),
            {"ws": node.workspace_id, "sk": node.stable_key},
        )
        row = existing.mappings().first()

        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(node.metadata)

        if row:
            node_id = row["id"]
            await self._session.execute(
                text(
                    "UPDATE workspace_graph_nodes SET "
                    "repo_id=:repo_id, node_type=:node_type, display_name=:display_name, "
                    "path=:path, language=:language, signature=:signature, "
                    "content_hash=:content_hash, is_deleted=:is_deleted, "
                    "metadata=:metadata "
                    "WHERE id=:id"
                ),
                {
                    "id": node_id,
                    "repo_id": node.repo_id,
                    "node_type": node.node_type,
                    "display_name": node.display_name,
                    "path": node.path,
                    "language": node.language,
                    "signature": node.signature,
                    "content_hash": node.content_hash,
                    "is_deleted": node.is_deleted,
                    "metadata": meta_json,
                },
            )
            await self._session.flush()
            node.id = node_id
        else:
            node_id = node.id or str(ULID())
            node.id = node_id
            await self._session.execute(
                text(
                    "INSERT INTO workspace_graph_nodes "
                    "(id, workspace_id, repo_id, node_type, stable_key, display_name, "
                    "path, language, signature, content_hash, is_deleted, metadata, "
                    "created_at, updated_at) "
                    "VALUES (:id, :ws, :repo_id, :node_type, :sk, :display_name, "
                    ":path, :language, :signature, :content_hash, :is_deleted, :metadata, "
                    ":created_at, :updated_at)"
                ),
                {
                    "id": node_id,
                    "ws": node.workspace_id,
                    "repo_id": node.repo_id,
                    "node_type": node.node_type,
                    "sk": node.stable_key,
                    "display_name": node.display_name,
                    "path": node.path,
                    "language": node.language,
                    "signature": node.signature,
                    "content_hash": node.content_hash,
                    "is_deleted": node.is_deleted,
                    "metadata": meta_json,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await self._session.flush()

        return await self.get_node(node.workspace_id, node.stable_key)

    async def upsert_nodes(self, nodes: list[GraphNode]) -> list[GraphNode]:
        """Batch upsert nodes."""
        results = []
        for node in nodes:
            results.append(await self.upsert_node(node))
        return results

    async def get_node(self, workspace_id: str, stable_key: str) -> GraphNode | None:
        """Get a node by workspace_id and stable_key."""
        result = await self._session.execute(
            text(
                "SELECT * FROM workspace_graph_nodes "
                "WHERE workspace_id = :ws AND stable_key = :sk AND is_deleted = 0"
            ),
            {"ws": workspace_id, "sk": stable_key},
        )
        row = result.mappings().first()
        return GraphNode.from_row(row) if row else None

    async def get_node_by_id(self, node_id: str) -> GraphNode | None:
        """Get a node by its primary ID."""
        result = await self._session.execute(
            text("SELECT * FROM workspace_graph_nodes WHERE id = :id AND is_deleted = 0"),
            {"id": node_id},
        )
        row = result.mappings().first()
        return GraphNode.from_row(row) if row else None

    async def get_nodes_by_type(
        self, workspace_id: str, node_type: str
    ) -> list[GraphNode]:
        """Get all non-deleted nodes of a given type in a workspace."""
        result = await self._session.execute(
            text(
                "SELECT * FROM workspace_graph_nodes "
                "WHERE workspace_id = :ws AND node_type = :nt AND is_deleted = 0"
            ),
            {"ws": workspace_id, "nt": node_type},
        )
        return [GraphNode.from_row(r) for r in result.mappings().all()]

    async def soft_delete_nodes_for_path(
        self, workspace_id: str, path: str
    ) -> int:
        """Soft-delete all nodes associated with a file path."""
        result = await self._session.execute(
            text(
                "UPDATE workspace_graph_nodes SET is_deleted = 1 "
                "WHERE workspace_id = :ws AND path = :path AND is_deleted = 0"
            ),
            {"ws": workspace_id, "path": path},
        )
        await self._session.flush()
        return result.rowcount

    # ── Edges ──

    async def upsert_edge(self, edge: GraphEdge) -> GraphEdge:
        """Insert or update an edge by unique constraint."""
        existing = await self._session.execute(
            text(
                "SELECT id FROM workspace_graph_edges "
                "WHERE workspace_id = :ws AND source_node_id = :src "
                "AND target_node_id = :tgt AND edge_type = :et AND source_kind = :sk"
            ),
            {
                "ws": edge.workspace_id,
                "src": edge.source_node_id,
                "tgt": edge.target_node_id,
                "et": edge.edge_type,
                "sk": edge.source_kind,
            },
        )
        row = existing.mappings().first()

        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(edge.metadata)

        if row:
            edge_id = row["id"]
            await self._session.execute(
                text(
                    "UPDATE workspace_graph_edges SET "
                    "mode=:mode, confidence=:confidence, run_id=:run_id, "
                    "is_deleted=:is_deleted, metadata=:metadata, "
                    "last_confirmed_at=:confirmed "
                    "WHERE id=:id"
                ),
                {
                    "id": edge_id,
                    "mode": edge.mode,
                    "confidence": edge.confidence,
                    "run_id": edge.run_id,
                    "is_deleted": edge.is_deleted,
                    "metadata": meta_json,
                    "confirmed": now,
                },
            )
            await self._session.flush()
            edge.id = edge_id
        else:
            edge_id = edge.id or str(ULID())
            edge.id = edge_id
            await self._session.execute(
                text(
                    "INSERT INTO workspace_graph_edges "
                    "(id, workspace_id, repo_id, source_node_id, target_node_id, "
                    "edge_type, mode, confidence, source_kind, run_id, is_deleted, "
                    "metadata, created_at, updated_at, last_confirmed_at) "
                    "VALUES (:id, :ws, :repo_id, :src, :tgt, :et, :mode, :conf, "
                    ":sk, :run_id, :is_deleted, :metadata, :now, :now, :confirmed)"
                ),
                {
                    "id": edge_id,
                    "ws": edge.workspace_id,
                    "repo_id": edge.repo_id,
                    "src": edge.source_node_id,
                    "tgt": edge.target_node_id,
                    "et": edge.edge_type,
                    "mode": edge.mode,
                    "conf": edge.confidence,
                    "sk": edge.source_kind,
                    "run_id": edge.run_id,
                    "is_deleted": edge.is_deleted,
                    "metadata": meta_json,
                    "now": now,
                    "confirmed": now,
                },
            )
            await self._session.flush()

        return edge

    async def upsert_edges(self, edges: list[GraphEdge]) -> list[GraphEdge]:
        """Batch upsert edges."""
        results = []
        for edge in edges:
            results.append(await self.upsert_edge(edge))
        return results

    async def get_edges_for_node(
        self,
        workspace_id: str,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
    ) -> list[GraphEdge]:
        """Get edges connected to a node."""
        params: dict = {"ws": workspace_id, "nid": node_id}
        clauses = ["workspace_id = :ws", "is_deleted = 0"]

        if direction == "outgoing":
            clauses.append("source_node_id = :nid")
        elif direction == "incoming":
            clauses.append("target_node_id = :nid")
        else:
            clauses.append("(source_node_id = :nid OR target_node_id = :nid)")

        if edge_types:
            placeholders = ", ".join(f":et{i}" for i in range(len(edge_types)))
            clauses.append(f"edge_type IN ({placeholders})")
            for i, et in enumerate(edge_types):
                params[f"et{i}"] = et

        where = " AND ".join(clauses)
        result = await self._session.execute(
            text(f"SELECT * FROM workspace_graph_edges WHERE {where}"), params
        )
        return [GraphEdge.from_row(r) for r in result.mappings().all()]

    # ── Graph traversal ──

    async def get_neighbors(
        self,
        workspace_id: str,
        node_id: str,
        edge_types: list[str] | None = None,
        depth: int = 1,
        min_confidence: float = 0.0,
    ) -> GraphSubgraph:
        """BFS traversal up to `depth` hops."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(node_id, 0)]
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        seen_edges: set[str] = set()

        while queue:
            current_id, current_depth = queue.pop(0)
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            node = await self.get_node_by_id(current_id)
            if node:
                nodes[current_id] = node

            if current_depth < depth:
                node_edges = await self.get_edges_for_node(
                    workspace_id, current_id, edge_types=edge_types
                )
                for e in node_edges:
                    if e.confidence >= min_confidence and e.id not in seen_edges:
                        seen_edges.add(e.id)
                        edges.append(e)
                        next_id = (
                            e.target_node_id
                            if e.source_node_id == current_id
                            else e.source_node_id
                        )
                        queue.append((next_id, current_depth + 1))

        return GraphSubgraph(nodes=nodes, edges=edges, center_id=node_id)

    async def find_path(
        self,
        workspace_id: str,
        source_node_id: str,
        target_node_id: str,
        max_depth: int = 4,
        edge_types: list[str] | None = None,
    ) -> list[GraphEdge] | None:
        """BFS shortest path between two nodes."""
        if source_node_id == target_node_id:
            return []

        visited: set[str] = set()
        queue: list[tuple[str, list[GraphEdge]]] = [(source_node_id, [])]

        while queue:
            current_id, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            if current_id in visited:
                continue
            visited.add(current_id)

            node_edges = await self.get_edges_for_node(
                workspace_id, current_id, edge_types=edge_types
            )
            for e in node_edges:
                next_id = (
                    e.target_node_id
                    if e.source_node_id == current_id
                    else e.source_node_id
                )
                if next_id == target_node_id:
                    return path + [e]
                if next_id not in visited:
                    queue.append((next_id, path + [e]))

        return None

    # ── Search ──

    async def search(
        self,
        workspace_id: str,
        query: str,
        *,
        node_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[GraphNode]:
        """FTS search over graph nodes."""
        # Use FTS5 match
        fts_result = await self._session.execute(
            text(
                "SELECT n.* FROM workspace_graph_nodes n "
                "JOIN workspace_graph_nodes_fts f ON n.rowid = f.rowid "
                "WHERE workspace_graph_nodes_fts MATCH :q "
                "AND n.workspace_id = :ws AND n.is_deleted = 0 "
                "ORDER BY rank LIMIT :limit"
            ),
            {"q": query, "ws": workspace_id, "limit": limit},
        )
        rows = fts_result.mappings().all()
        results = [GraphNode.from_row(r) for r in rows]

        if node_types:
            results = [r for r in results if r.node_type in node_types]

        return results

    # ── Runs ──

    async def record_run(self, run: GraphRun) -> str:
        """Insert a run record."""
        run_id = run.id or str(ULID())
        await self._session.execute(
            text(
                "INSERT INTO workspace_graph_runs "
                "(id, workspace_id, repo_id, run_type, status, trigger, "
                "files_scanned, nodes_written, edges_written, started_at, "
                "completed_at, error) "
                "VALUES (:id, :ws, :repo_id, :rt, :status, :trigger, "
                ":fs, :nw, :ew, :started, :completed, :error)"
            ),
            {
                "id": run_id,
                "ws": run.workspace_id,
                "repo_id": run.repo_id,
                "rt": run.run_type,
                "status": run.status,
                "trigger": run.trigger,
                "fs": run.files_scanned,
                "nw": run.nodes_written,
                "ew": run.edges_written,
                "started": run.started_at,
                "completed": run.completed_at,
                "error": run.error,
            },
        )
        await self._session.flush()
        return run_id

    async def update_run(
        self, run_id: str, status: str, **kwargs
    ) -> None:
        """Update run status and optional fields."""
        sets = ["status = :status"]
        params: dict = {"id": run_id, "status": status}
        for key in ("files_scanned", "nodes_written", "edges_written", "completed_at", "error"):
            if key in kwargs:
                sets.append(f"{key} = :{key}")
                params[key] = kwargs[key]
        sql = f"UPDATE workspace_graph_runs SET {', '.join(sets)} WHERE id = :id"
        await self._session.execute(text(sql), params)
        await self._session.flush()

    # ── File state ──

    async def upsert_file_state(self, fs: FileState) -> FileState:
        """Insert or update file state by (workspace_id, path)."""
        existing = await self._session.execute(
            text(
                "SELECT id FROM workspace_graph_file_state "
                "WHERE workspace_id = :ws AND path = :path"
            ),
            {"ws": fs.workspace_id, "path": fs.path},
        )
        row = existing.mappings().first()
        meta_json = json.dumps(fs.metadata)

        if row:
            fs_id = row["id"]
            await self._session.execute(
                text(
                    "UPDATE workspace_graph_file_state SET "
                    "repo_id=:repo_id, content_hash=:hash, language=:lang, "
                    "mtime_ns=:mtime, size_bytes=:size, last_indexed_at=:indexed, "
                    "last_run_id=:run_id, status=:status, last_error=:error, "
                    "metadata=:metadata "
                    "WHERE id=:id"
                ),
                {
                    "id": fs_id,
                    "repo_id": fs.repo_id,
                    "hash": fs.content_hash,
                    "lang": fs.language,
                    "mtime": fs.mtime_ns,
                    "size": fs.size_bytes,
                    "indexed": fs.last_indexed_at,
                    "run_id": fs.last_run_id,
                    "status": fs.status,
                    "error": fs.last_error,
                    "metadata": meta_json,
                },
            )
            fs.id = fs_id
        else:
            fs_id = fs.id or str(ULID())
            fs.id = fs_id
            await self._session.execute(
                text(
                    "INSERT INTO workspace_graph_file_state "
                    "(id, workspace_id, repo_id, path, content_hash, language, "
                    "mtime_ns, size_bytes, last_indexed_at, last_run_id, status, "
                    "last_error, metadata) "
                    "VALUES (:id, :ws, :repo_id, :path, :hash, :lang, :mtime, "
                    ":size, :indexed, :run_id, :status, :error, :metadata)"
                ),
                {
                    "id": fs_id,
                    "ws": fs.workspace_id,
                    "repo_id": fs.repo_id,
                    "path": fs.path,
                    "hash": fs.content_hash,
                    "lang": fs.language,
                    "mtime": fs.mtime_ns,
                    "size": fs.size_bytes,
                    "indexed": fs.last_indexed_at,
                    "run_id": fs.last_run_id,
                    "status": fs.status,
                    "error": fs.last_error,
                    "metadata": meta_json,
                },
            )
        await self._session.flush()
        return fs

    async def get_file_state(
        self, workspace_id: str, path: str
    ) -> FileState | None:
        """Get file state by workspace and path."""
        result = await self._session.execute(
            text(
                "SELECT * FROM workspace_graph_file_state "
                "WHERE workspace_id = :ws AND path = :path"
            ),
            {"ws": workspace_id, "path": path},
        )
        row = result.mappings().first()
        return FileState.from_row(row) if row else None

    async def get_changed_files(
        self, workspace_id: str, file_hashes: dict[str, str]
    ) -> list[str]:
        """Return paths whose content_hash differs from what's stored, or are new."""
        changed = []
        for path, new_hash in file_hashes.items():
            fs = await self.get_file_state(workspace_id, path)
            if fs is None or fs.content_hash != new_hash:
                changed.append(path)
        return changed
