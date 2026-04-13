"""Workspace graph extractor — Phase 1 deterministic extraction.

Uses workspace_map for workspace/repo discovery and repomap tags for symbol extraction.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID

from backend.app.agent.repomap.tags import Tag, extract_tags
from backend.app.agent.workspace_map import (
    SKIP_DIRS,
    SKIP_EXTS,
    SKIP_NAMES,
    DiscoveredRepo,
    discover_repos,
)

from .models import FileState, GraphEdge, GraphNode, GraphRun, Provenance

logger = logging.getLogger(__name__)


def _hash_file(path: str) -> str:
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _should_skip(name: str) -> bool:
    if name.startswith("."):
        return True
    if name in SKIP_DIRS or name in SKIP_NAMES:
        return True
    ext = os.path.splitext(name)[1].lower()
    return ext in SKIP_EXTS


def _enumerate_files(repo_path: str, max_files: int = 5000) -> list[str]:
    """Walk a repo and return relative file paths worth indexing."""
    files: list[str] = []
    for root, dirs, filenames in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not _should_skip(d)]
        for fname in filenames:
            if _should_skip(fname):
                continue
            rel = os.path.relpath(os.path.join(root, fname), repo_path)
            files.append(rel)
            if len(files) >= max_files:
                return files
    return files


class WorkspaceGraphExtractor:
    """Phase 1 deterministic extractor using workspace_map + repomap tags."""

    def extract_workspace(
        self,
        workspace_root: str,
        workspace_id: str,
    ) -> ExtractionResult:
        """Extract a full workspace into nodes, edges, and file states."""
        repos = discover_repos(workspace_root)
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        file_states: list[FileState] = []
        provenance: list[Provenance] = []

        # Workspace node
        ws_node = GraphNode(
            id=str(ULID()),
            workspace_id=workspace_id,
            repo_id=None,
            node_type="workspace",
            stable_key=f"workspace:{os.path.basename(workspace_root)}",
            display_name=os.path.basename(workspace_root),
            path=None,
        )
        nodes.append(ws_node)

        for repo in repos:
            repo_result = self.extract_repo(
                workspace_id=workspace_id,
                workspace_node_id=ws_node.id,
                repo=repo,
            )
            nodes.extend(repo_result.nodes)
            edges.extend(repo_result.edges)
            file_states.extend(repo_result.file_states)
            provenance.extend(repo_result.provenance)

        return ExtractionResult(
            nodes=nodes,
            edges=edges,
            file_states=file_states,
            files_scanned=len(file_states),
            provenance=provenance,
        )

    def extract_repo(
        self,
        workspace_id: str,
        workspace_node_id: str,
        repo: DiscoveredRepo,
    ) -> ExtractionResult:
        """Extract a single repo into nodes, edges, file states."""
        repo_id = repo.name
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        file_states: list[FileState] = []
        provenance: list[Provenance] = []

        # Repository node
        repo_node = GraphNode(
            id=str(ULID()),
            workspace_id=workspace_id,
            repo_id=repo_id,
            node_type="repository",
            stable_key=f"repo:{repo.name}",
            display_name=repo.name,
            path=None,
        )
        nodes.append(repo_node)

        # workspace -> contains -> repo
        edges.append(GraphEdge(
            id=str(ULID()),
            workspace_id=workspace_id,
            repo_id=None,
            source_node_id=workspace_node_id,
            target_node_id=repo_node.id,
            edge_type="contains",
            mode="extracted",
            source_kind="workspace_map",
        ))

        # Enumerate files
        rel_paths = _enumerate_files(repo.path)

        for rel_path in rel_paths:
            abs_path = os.path.join(repo.path, rel_path)
            content_hash = _hash_file(abs_path)
            if not content_hash:
                continue

            stat = os.stat(abs_path)
            ext = os.path.splitext(rel_path)[1].lower()

            # File node
            file_stable_key = f"file:{repo.name}/{rel_path}"
            file_node = GraphNode(
                id=str(ULID()),
                workspace_id=workspace_id,
                repo_id=repo_id,
                node_type="file",
                stable_key=file_stable_key,
                display_name=rel_path,
                path=rel_path,
                language=ext.lstrip(".") if ext else None,
                content_hash=content_hash,
            )
            nodes.append(file_node)

            # repo -> contains -> file
            edges.append(GraphEdge(
                id=str(ULID()),
                workspace_id=workspace_id,
                repo_id=repo_id,
                source_node_id=repo_node.id,
                target_node_id=file_node.id,
                edge_type="contains",
                mode="extracted",
                source_kind="workspace_map",
            ))

            # File state
            file_states.append(FileState(
                id=str(ULID()),
                workspace_id=workspace_id,
                repo_id=repo_id,
                path=rel_path,
                content_hash=content_hash,
                language=ext.lstrip(".") if ext else None,
                mtime_ns=int(stat.st_mtime_ns) if hasattr(stat, "st_mtime_ns") else int(stat.st_mtime * 1e9),
                size_bytes=stat.st_size,
                status="indexed",
            ))

            # Extract symbols via repomap tags
            tags = extract_tags(abs_path, rel_path)
            symbol_ids: dict[str, str] = {}  # name -> node_id for defs

            for tag in tags:
                if tag.kind == "def":
                    sym_stable_key = f"symbol:{repo.name}/{rel_path}::{tag.name}"
                    sym_node = GraphNode(
                        id=str(ULID()),
                        workspace_id=workspace_id,
                        repo_id=repo_id,
                        node_type="symbol",
                        stable_key=sym_stable_key,
                        display_name=tag.name,
                        path=rel_path,
                        language=ext.lstrip(".") if ext else None,
                        signature=tag.signature,
                        metadata={"line": tag.line},
                    )
                    nodes.append(sym_node)
                    symbol_ids[tag.name] = sym_node.id

                    # file -> defines -> symbol
                    define_edge = GraphEdge(
                        id=str(ULID()),
                        workspace_id=workspace_id,
                        repo_id=repo_id,
                        source_node_id=file_node.id,
                        target_node_id=sym_node.id,
                        edge_type="defines",
                        mode="extracted",
                        source_kind="ast",
                        metadata={"line": tag.line},
                    )
                    edges.append(define_edge)

                    # Provenance for the definition edge
                    provenance.append(Provenance(
                        workspace_id=workspace_id,
                        provenance_type="ast_extraction",
                        edge_id=define_edge.id,
                        node_id=sym_node.id,
                        source_path=rel_path,
                        source_line_start=tag.line,
                        excerpt=tag.signature or tag.name,
                    ))

            # Second pass: references
            for tag in tags:
                if tag.kind == "ref" and tag.name in symbol_ids:
                    # file -> references -> symbol
                    edges.append(GraphEdge(
                        id=str(ULID()),
                        workspace_id=workspace_id,
                        repo_id=repo_id,
                        source_node_id=file_node.id,
                        target_node_id=symbol_ids[tag.name],
                        edge_type="references",
                        mode="extracted",
                        source_kind="ast",
                        metadata={"line": tag.line},
                    ))

        return ExtractionResult(
            nodes=nodes,
            edges=edges,
            file_states=file_states,
            files_scanned=len(file_states),
            provenance=provenance,
        )


class ExtractionResult:
    """Result of a workspace/repo extraction."""

    def __init__(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        file_states: list[FileState],
        files_scanned: int = 0,
        provenance: list[Provenance] | None = None,
    ):
        self.nodes = nodes
        self.edges = edges
        self.file_states = file_states
        self.files_scanned = files_scanned
        self.provenance = provenance or []
