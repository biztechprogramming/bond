"""Workspace Knowledge Graph — Phase 1 + Phase 2.

Deterministic structural graph for workspaces, repositories, files, and symbols.
Phase 2 adds: adapter abstraction, enhanced extractors (routes, tests, docs, config,
migrations), context pipeline integration, and continuation integration.

Design Doc 110.
"""

from .models import (
    FileState,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphSubgraph,
    Provenance,
)
from .repository import WorkspaceGraphRepository
from .extractor import ExtractionResult, WorkspaceGraphExtractor
from .adapter import (
    ExtractionEdge,
    ExtractionNode,
    GraphifyAdapter,
    GraphifyExtractionBatch,
    ImportSummary,
)
from .context_integration import (
    GraphContextHint,
    format_graph_hint_for_prompt,
    graph_context_for_files,
)
from .continuation_integration import (
    GraphCheckpointAnchors,
    build_graph_anchors,
    format_graph_anchors_for_continuation,
)

__all__ = [
    # Phase 1
    "FileState",
    "GraphEdge",
    "GraphNode",
    "GraphRun",
    "GraphSubgraph",
    "Provenance",
    "WorkspaceGraphRepository",
    "ExtractionResult",
    "WorkspaceGraphExtractor",
    # Phase 2 — Adapter
    "ExtractionEdge",
    "ExtractionNode",
    "GraphifyAdapter",
    "GraphifyExtractionBatch",
    "ImportSummary",
    # Phase 2 — Context pipeline
    "GraphContextHint",
    "format_graph_hint_for_prompt",
    "graph_context_for_files",
    # Phase 2 — Continuation
    "GraphCheckpointAnchors",
    "build_graph_anchors",
    "format_graph_anchors_for_continuation",
]
