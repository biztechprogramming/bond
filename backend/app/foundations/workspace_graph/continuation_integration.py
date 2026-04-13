"""Continuation integration for workspace knowledge graph.

Design Doc 110, Phase 2: Enriches continuation checkpoints with
structural graph anchors so that plan resumption can be grounded
in workspace structure rather than only transcript summaries.

Integrates with backend/app/agent/continuation.py (Design Doc 034).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import GraphNode
from .repository import WorkspaceGraphRepository

logger = logging.getLogger(__name__)


@dataclass
class GraphCheckpointAnchors:
    """Structural anchors from the WKG to enrich a continuation checkpoint.

    These are attached to a checkpoint so that on resume, the agent
    can quickly re-orient using graph structure instead of re-reading
    many files.
    """

    anchor_node_ids: list[str] = field(default_factory=list)
    impacted_file_paths: list[str] = field(default_factory=list)
    related_test_paths: list[str] = field(default_factory=list)
    unresolved_deps: list[str] = field(default_factory=list)
    summary: str = ""


async def build_graph_anchors(
    repo: WorkspaceGraphRepository,
    workspace_id: str,
    changed_files: list[str],
    plan_item_title: str | None = None,
) -> GraphCheckpointAnchors:
    """Build graph-based checkpoint anchors from changed files.

    Called when creating or updating a continuation checkpoint.
    The anchors allow the continuation context builder to include
    a compact structural summary instead of full file contents.

    Args:
        repo: WKG repository instance.
        workspace_id: Current workspace ID.
        changed_files: Files changed during the current work session.
        plan_item_title: Title of the active plan item, if any.

    Returns:
        GraphCheckpointAnchors for embedding in checkpoint data.
    """
    anchors = GraphCheckpointAnchors()

    # Find graph nodes for changed files
    for fpath in changed_files:
        node = await repo.get_node(workspace_id, f"file:{fpath}")
        if not node:
            # Try with repo prefix patterns
            nodes_by_path = await _find_nodes_by_path(repo, workspace_id, fpath)
            if nodes_by_path:
                node = nodes_by_path[0]

        if node:
            anchors.anchor_node_ids.append(node.id)

    if not anchors.anchor_node_ids:
        return anchors

    # Expand to find impacted neighbors
    subgraph = await repo.impact_analysis(
        workspace_id, anchors.anchor_node_ids, max_depth=2
    )

    for node in subgraph.nodes.values():
        if not node.path:
            continue
        if node.node_type == "file" and node.path not in changed_files:
            anchors.impacted_file_paths.append(node.path)
        elif node.node_type == "test":
            anchors.related_test_paths.append(node.path)

    # Build summary
    parts = [f"{len(changed_files)} changed files"]
    if anchors.impacted_file_paths:
        parts.append(f"{len(anchors.impacted_file_paths)} impacted files")
    if anchors.related_test_paths:
        parts.append(f"{len(anchors.related_test_paths)} related tests")
    anchors.summary = f"Graph anchors: {', '.join(parts)}"

    return anchors


def format_graph_anchors_for_continuation(
    anchors: GraphCheckpointAnchors,
) -> str:
    """Format graph anchors as a context section for continuation prompts.

    Designed to be appended to the continuation context built by
    continuation.build_continuation_context().
    """
    if not anchors.anchor_node_ids and not anchors.impacted_file_paths:
        return ""

    lines = ["## Graph-Anchored Context"]

    if anchors.summary:
        lines.append(anchors.summary)

    if anchors.impacted_file_paths:
        lines.append(f"\nImpacted files to review ({len(anchors.impacted_file_paths)}):")
        for f in anchors.impacted_file_paths[:10]:
            lines.append(f"  - {f}")

    if anchors.related_test_paths:
        lines.append(f"\nTests to run ({len(anchors.related_test_paths)}):")
        for f in anchors.related_test_paths[:5]:
            lines.append(f"  - {f}")

    if anchors.unresolved_deps:
        lines.append(f"\nUnresolved dependencies:")
        for d in anchors.unresolved_deps[:5]:
            lines.append(f"  - {d}")

    return "\n".join(lines)


async def _find_nodes_by_path(
    repo: WorkspaceGraphRepository,
    workspace_id: str,
    path: str,
) -> list[GraphNode]:
    """Find nodes matching a file path using search as fallback."""
    try:
        results = await repo.search(workspace_id, path, node_types=["file"], limit=3)
        return [n for n in results if n.path and n.path.endswith(path)]
    except Exception:
        return []
