"""Data classes for the workspace knowledge graph."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class GraphNode:
    """A node in the workspace knowledge graph."""

    id: str
    workspace_id: str
    repo_id: str | None
    node_type: str
    stable_key: str
    display_name: str
    path: str | None = None
    language: str | None = None
    signature: str | None = None
    content_hash: str | None = None
    is_deleted: int = 0
    metadata: dict = field(default_factory=dict)
    embedding_model: str | None = None
    processed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row) -> GraphNode:
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            repo_id=row["repo_id"],
            node_type=row["node_type"],
            stable_key=row["stable_key"],
            display_name=row["display_name"],
            path=row["path"],
            language=row["language"],
            signature=row["signature"],
            content_hash=row["content_hash"],
            is_deleted=row["is_deleted"],
            metadata=meta,
            embedding_model=row["embedding_model"],
            processed_at=row["processed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class GraphEdge:
    """An edge in the workspace knowledge graph."""

    id: str
    workspace_id: str
    repo_id: str | None
    source_node_id: str
    target_node_id: str
    edge_type: str
    mode: str  # extracted | inferred | ambiguous
    confidence: float = 1.0
    source_kind: str = "ast"
    run_id: str | None = None
    is_deleted: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_confirmed_at: str | None = None

    @classmethod
    def from_row(cls, row) -> GraphEdge:
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            repo_id=row["repo_id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            edge_type=row["edge_type"],
            mode=row["mode"],
            confidence=float(row["confidence"]),
            source_kind=row["source_kind"],
            run_id=row["run_id"],
            is_deleted=row["is_deleted"],
            metadata=meta,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_confirmed_at=row["last_confirmed_at"],
        )


@dataclass
class GraphRun:
    """A record of an indexing run."""

    id: str
    workspace_id: str
    repo_id: str | None
    run_type: str
    status: str
    trigger: str
    files_scanned: int = 0
    nodes_written: int = 0
    edges_written: int = 0
    started_at: str = ""
    completed_at: str | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row) -> GraphRun:
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            repo_id=row["repo_id"],
            run_type=row["run_type"],
            status=row["status"],
            trigger=row["trigger"],
            files_scanned=row["files_scanned"],
            nodes_written=row["nodes_written"],
            edges_written=row["edges_written"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
        )


@dataclass
class FileState:
    """Tracks per-file indexing state for incremental updates."""

    id: str
    workspace_id: str
    repo_id: str | None
    path: str
    content_hash: str
    language: str | None = None
    mtime_ns: int | None = None
    size_bytes: int | None = None
    last_indexed_at: str | None = None
    last_run_id: str | None = None
    status: str = "indexed"
    last_error: str | None = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row) -> FileState:
        meta = row["metadata"] if row["metadata"] else "{}"
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(
            id=row["id"],
            workspace_id=row["workspace_id"],
            repo_id=row["repo_id"],
            path=row["path"],
            content_hash=row["content_hash"],
            language=row["language"],
            mtime_ns=row["mtime_ns"],
            size_bytes=row["size_bytes"],
            last_indexed_at=row["last_indexed_at"],
            last_run_id=row["last_run_id"],
            status=row["status"],
            last_error=row["last_error"],
            metadata=meta,
        )


@dataclass
class GraphSubgraph:
    """A subgraph result from neighborhood or path queries."""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    center_id: str = ""
