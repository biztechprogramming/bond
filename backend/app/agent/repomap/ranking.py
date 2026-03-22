"""PageRank-based importance scoring for files.

Builds a directed graph from symbol definitions and references,
then runs PageRank to score files by structural importance.
No networkx dependency -- PageRank is implemented inline.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from .tags import Tag


def rank_files(
    tags: list[Tag],
    focus_files: list[str] | None = None,
) -> dict[str, float]:
    """Score files by structural importance using PageRank.

    Args:
        tags: All extracted tags (defs and refs) across the repo.
        focus_files: Optional list of relative file paths to boost.

    Returns:
        Dict mapping relative file paths to importance scores.
    """
    # Build def/ref maps
    defines: dict[str, set[str]] = defaultdict(set)  # symbol -> set of files that define it
    references: dict[str, list[str]] = defaultdict(list)  # symbol -> list of files that reference it

    all_files: set[str] = set()

    for tag in tags:
        all_files.add(tag.rel_fname)
        if tag.kind == "def":
            defines[tag.name].add(tag.rel_fname)
        elif tag.kind == "ref":
            references[tag.name].append(tag.rel_fname)

    if not all_files:
        return {}

    # If no references found, use defines as references (some query files only give defs)
    if not references:
        references = {k: list(v) for k, v in defines.items()}

    # Build weighted edge graph: edges[src] = {dst: weight}
    edges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    idents = set(defines.keys()) & set(references.keys())

    for ident in idents:
        definers = defines[ident]

        # Weight heuristics from aider
        mul = 1.0
        is_meaningful = (
            ("_" in ident or "-" in ident or any(c.isupper() for c in ident))
            and len(ident) >= 8
        )
        if is_meaningful:
            mul *= 10.0
        if ident.startswith("_"):
            mul *= 0.1
        if len(definers) > 5:
            mul *= 0.1

        for referencer, num_refs in Counter(references[ident]).items():
            for definer in definers:
                weight = mul * math.sqrt(num_refs)
                edges[referencer][definer] += weight

    # Collect all nodes that appear in the graph
    nodes: set[str] = set()
    for src, dsts in edges.items():
        nodes.add(src)
        nodes.update(dsts.keys())

    # Add files that have no edges (so they still appear in results)
    nodes.update(all_files)

    if not nodes:
        return {}

    # Personalization vector
    personalization: dict[str, float] = {}
    if focus_files:
        focus_set = set(focus_files)
        base = 1.0 / len(nodes)
        boost = 100.0 / len(nodes)
        for node in nodes:
            personalization[node] = boost if node in focus_set else base
    else:
        personalization = {node: 1.0 / len(nodes) for node in nodes}

    # PageRank iteration (no networkx)
    damping = 0.85
    scores = {node: 1.0 / len(nodes) for node in nodes}

    for _ in range(20):
        new_scores: dict[str, float] = {}
        for node in nodes:
            # Teleport component
            rank = (1 - damping) * personalization.get(node, 1.0 / len(nodes))
            # Link component
            for src in nodes:
                if node in edges.get(src, {}):
                    total_out = sum(edges[src].values())
                    if total_out > 0:
                        rank += damping * scores[src] * edges[src][node] / total_out
            new_scores[node] = rank
        scores = new_scores

    return scores
