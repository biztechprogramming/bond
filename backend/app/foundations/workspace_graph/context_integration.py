"""Context pipeline integration for workspace knowledge graph.

Design Doc 110, Phase 2: Integrates WKG with Bond's context pipeline
(context_pipeline.py, pre_gather.py, context_builder.py) so the agent
can use graph structure to narrow file selection, improve pre-gather
planning, and provide graph-aware context summaries.

This module provides hooks that existing pipeline stages can call
without requiring a hard dependency on WKG being initialized.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import GraphNode, GraphSubgraph
from .repository import WorkspaceGraphRepository

logger = logging.getLogger(__name__)


@dataclass
class GraphContextHint:
    """A hint from the WKG to the context pipeline about relevant files/artifacts."""

    candidate_files: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    related_docs: list[str] = field(default_factory=list)
    related_configs: list[str] = field(default_factory=list)
    relationship_summary: str = ""
    staleness_warning: str | None = None


async def graph_context_for_files(
    repo: WorkspaceGraphRepository,
    workspace_id: str,
    file_paths: list[str],
    *,
    max_depth: int = 2,
    include_tests: bool = True,
    include_docs: bool = True,
) -> GraphContextHint:
    """Query the WKG for context relevant to a set of files.

    Intended to be called from pre_gather or context_builder to enrich
    the agent's context with graph-derived file relationships.

    Args:
        repo: WKG repository instance.
        workspace_id: Current workspace ID.
        file_paths: Files the agent is currently working with.
        max_depth: How many hops to traverse from seed files.
        include_tests: Whether to include related test files.
        include_docs: Whether to include related documents.

    Returns:
        GraphContextHint with candidate files and relationship info.
    """
    hint = GraphContextHint()

    # Find seed node IDs for the given file paths
    seed_ids: list[str] = []
    for fpath in file_paths:
        # Try common stable key patterns
        for prefix in ("file:", f"file:{workspace_id}/"):
            node = await repo.get_node(workspace_id, f"{prefix}{fpath}")
            if node:
                seed_ids.append(node.id)
                break

    if not seed_ids:
        return hint

    # Run impact analysis from seed nodes
    subgraph = await repo.impact_analysis(
        workspace_id, seed_ids, max_depth=max_depth
    )

    # Categorize discovered nodes
    seen_files: set[str] = set(file_paths)
    for node in subgraph.nodes.values():
        path = node.path
        if not path or path in seen_files:
            continue

        if node.node_type == "file":
            hint.candidate_files.append(path)
            seen_files.add(path)
        elif node.node_type == "test" and include_tests:
            hint.related_tests.append(path)
        elif node.node_type == "document" and include_docs:
            hint.related_docs.append(path)
        elif node.node_type == "config_key":
            hint.related_configs.append(path)

    # Build a short relationship summary for prompt inclusion
    if subgraph.edges:
        edge_types = {}
        for e in subgraph.edges:
            edge_types[e.edge_type] = edge_types.get(e.edge_type, 0) + 1
        parts = [f"{count} {etype}" for etype, count in sorted(edge_types.items())]
        hint.relationship_summary = (
            f"Graph context: {len(subgraph.nodes)} nodes, "
            f"{len(subgraph.edges)} edges ({', '.join(parts)})"
        )

    return hint


def format_graph_hint_for_prompt(hint: GraphContextHint, *, max_files: int = 10) -> str:
    """Format a GraphContextHint as a compact prompt section.

    Designed to be injected into the system prompt or pre-gather plan
    to guide file selection without dumping raw graph data.
    """
    if not hint.candidate_files and not hint.related_tests and not hint.related_docs:
        return ""

    lines = ["## Workspace Graph Context"]

    if hint.relationship_summary:
        lines.append(hint.relationship_summary)

    if hint.candidate_files:
        lines.append(f"\nRelated files ({len(hint.candidate_files)}):")
        for f in hint.candidate_files[:max_files]:
            lines.append(f"  - {f}")
        if len(hint.candidate_files) > max_files:
            lines.append(f"  ... and {len(hint.candidate_files) - max_files} more")

    if hint.related_tests:
        lines.append(f"\nRelated tests ({len(hint.related_tests)}):")
        for f in hint.related_tests[:5]:
            lines.append(f"  - {f}")

    if hint.related_docs:
        lines.append(f"\nRelated docs ({len(hint.related_docs)}):")
        for f in hint.related_docs[:5]:
            lines.append(f"  - {f}")

    if hint.related_configs:
        lines.append(f"\nRelated config ({len(hint.related_configs)}):")
        for f in hint.related_configs[:3]:
            lines.append(f"  - {f}")

    if hint.staleness_warning:
        lines.append(f"\n{hint.staleness_warning}")

    return "\n".join(lines)
