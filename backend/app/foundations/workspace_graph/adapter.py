"""GraphifyAdapter — Phase 2 adapter abstraction for workspace graph extraction.

Design Doc 110, Phase 2: Provides a Bond-native adapter abstraction that can
optionally delegate to Graphify for broader multi-language extraction. When
Graphify is not available, falls back to Bond's native extractor pipeline
with enhanced regex/heuristic extractors for routes, tests, docs, config,
and migrations.

The adapter produces GraphifyExtractionBatch objects that the import_batch
method converts into Bond workspace_graph_* table records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ulid import ULID

from .models import GraphEdge, GraphNode, Provenance

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extraction batch — the normalized exchange format
# ---------------------------------------------------------------------------

@dataclass
class ExtractionNode:
    """A node produced by an extraction adapter."""

    id: str
    label: str
    node_type: str
    source_file: str | None = None
    source_location: dict | None = None  # {"line_start": int, "line_end": int}
    attributes: dict = field(default_factory=dict)


@dataclass
class ExtractionEdge:
    """An edge produced by an extraction adapter."""

    source: str
    target: str
    relation: str
    confidence: str = "EXTRACTED"  # EXTRACTED | INFERRED | AMBIGUOUS
    attributes: dict = field(default_factory=dict)


@dataclass
class GraphifyExtractionBatch:
    """Normalized extraction output, compatible with Graphify's schema.

    This is the exchange format between extraction adapters and the
    Bond workspace graph import pipeline.
    """

    nodes: list[ExtractionNode] = field(default_factory=list)
    edges: list[ExtractionEdge] = field(default_factory=list)
    hyperedges: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)  # extractor version, root, cache info

    @property
    def dangling_edge_count(self) -> int:
        node_ids = {n.id for n in self.nodes}
        return sum(
            1 for e in self.edges
            if e.source not in node_ids or e.target not in node_ids
        )


@dataclass
class ImportSummary:
    """Result of importing an extraction batch into the workspace graph."""

    nodes_imported: int = 0
    edges_imported: int = 0
    provenance_recorded: int = 0
    dangling_edges_skipped: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Confidence mapping: Graphify -> Bond
# ---------------------------------------------------------------------------

_CONFIDENCE_MAP = {
    "EXTRACTED": ("extracted", 1.0),
    "INFERRED": ("inferred", 0.7),
    "AMBIGUOUS": ("ambiguous", 0.4),
}


def _map_confidence(graphify_confidence: str) -> tuple[str, float]:
    """Map Graphify confidence label to Bond (mode, confidence) tuple."""
    return _CONFIDENCE_MAP.get(
        graphify_confidence.upper(), ("extracted", 1.0)
    )


# ---------------------------------------------------------------------------
# GraphifyAdapter
# ---------------------------------------------------------------------------

class GraphifyAdapter:
    """Adapter that bridges extraction engines to Bond's workspace graph.

    When Graphify is available in the environment, extract_workspace delegates
    to it. Otherwise, falls back to Bond-native enhanced extraction (Phase 2
    extractors for routes, tests, docs, config, migrations).

    Usage:
        adapter = GraphifyAdapter()
        batch = await adapter.extract_workspace(root)
        summary = adapter.import_batch(workspace_id, repo_id, batch, run_id)
    """

    def __init__(self) -> None:
        self._graphify_available = self._check_graphify()

    @staticmethod
    def _check_graphify() -> bool:
        """Check if Graphify is importable."""
        try:
            import graphify  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def graphify_available(self) -> bool:
        return self._graphify_available

    async def extract_workspace(
        self,
        root: Path,
        *,
        file_subset: list[str] | None = None,
    ) -> GraphifyExtractionBatch:
        """Extract workspace artifacts into a normalized batch.

        If Graphify is available, delegates to it. Otherwise uses Bond-native
        enhanced extractors (routes, tests, docs, config, migrations).

        Args:
            root: Workspace or repo root directory.
            file_subset: Optional list of relative paths to limit extraction.

        Returns:
            GraphifyExtractionBatch with extracted nodes and edges.
        """
        if self._graphify_available:
            return await self._extract_via_graphify(root, file_subset)
        return await self._extract_native(root, file_subset)

    async def _extract_via_graphify(
        self,
        root: Path,
        file_subset: list[str] | None,
    ) -> GraphifyExtractionBatch:
        """Delegate to Graphify's extraction pipeline.

        NOTE: This path is implemented as a concrete integration point.
        When Graphify becomes available as a dependency, this method will
        call graphify.extract.collect_files() and graphify.extract.extract()
        and map the results into a GraphifyExtractionBatch.
        """
        # Graphify integration point — not yet wired because graphify
        # is not available as an installed package in this environment.
        logger.info(
            "Graphify detected but extract path not yet wired; "
            "falling back to native extraction for %s",
            root,
        )
        return await self._extract_native(root, file_subset)

    async def _extract_native(
        self,
        root: Path,
        file_subset: list[str] | None,
    ) -> GraphifyExtractionBatch:
        """Bond-native Phase 2 enhanced extraction.

        Uses regex/heuristic extractors for:
        - routes (Express, FastAPI, Flask, Django, Next.js)
        - tests (pytest, jest, mocha, go test)
        - docs (markdown, rst, docstrings)
        - config (env files, yaml, toml, json configs)
        - migrations (SQL, alembic, knex, prisma)
        """
        from .phase2_extractors import extract_phase2_artifacts

        batch = extract_phase2_artifacts(root, file_subset=file_subset)
        batch.metadata["adapter"] = "bond-native"
        batch.metadata["graphify_available"] = self._graphify_available
        return batch

    def import_batch(
        self,
        workspace_id: str,
        repo_id: str | None,
        batch: GraphifyExtractionBatch,
        run_id: str,
    ) -> tuple[list[GraphNode], list[GraphEdge], list[Provenance], ImportSummary]:
        """Convert an extraction batch into Bond graph objects.

        Returns (nodes, edges, provenance, summary) ready for repository persistence.
        The caller is responsible for calling repo.upsert_nodes/edges and
        repo.record_provenance with the returned objects.
        """
        node_id_map: dict[str, str] = {}  # extraction id -> bond node id
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        provenance: list[Provenance] = []
        summary = ImportSummary()

        # Map nodes
        for en in batch.nodes:
            bond_id = str(ULID())
            node_id_map[en.id] = bond_id

            stable_key = en.attributes.get("stable_key", f"{en.node_type}:{en.id}")
            nodes.append(GraphNode(
                id=bond_id,
                workspace_id=workspace_id,
                repo_id=repo_id,
                node_type=en.node_type,
                stable_key=stable_key,
                display_name=en.label,
                path=en.source_file,
                language=en.attributes.get("language"),
                signature=en.attributes.get("signature"),
                metadata={
                    k: v for k, v in en.attributes.items()
                    if k not in ("stable_key", "language", "signature")
                },
            ))

            # Provenance for the node
            if en.source_file:
                loc = en.source_location or {}
                provenance.append(Provenance(
                    workspace_id=workspace_id,
                    provenance_type="phase2_extraction",
                    node_id=bond_id,
                    source_path=en.source_file,
                    source_line_start=loc.get("line_start"),
                    source_line_end=loc.get("line_end"),
                    excerpt=en.label,
                ))
            summary.nodes_imported += 1

        # Map edges
        node_ids_set = set(node_id_map.keys())
        for ee in batch.edges:
            if ee.source not in node_ids_set or ee.target not in node_ids_set:
                summary.dangling_edges_skipped += 1
                continue

            mode, confidence = _map_confidence(ee.confidence)
            edge_id = str(ULID())
            edges.append(GraphEdge(
                id=edge_id,
                workspace_id=workspace_id,
                repo_id=repo_id,
                source_node_id=node_id_map[ee.source],
                target_node_id=node_id_map[ee.target],
                edge_type=ee.relation,
                mode=mode,
                confidence=confidence,
                source_kind=ee.attributes.get("source_kind", "regex"),
                run_id=run_id,
            ))
            summary.edges_imported += 1

        summary.provenance_recorded = len(provenance)
        summary.warnings = list(batch.warnings)

        if summary.dangling_edges_skipped:
            summary.warnings.append(
                f"Skipped {summary.dangling_edges_skipped} edges with dangling endpoints"
            )

        return nodes, edges, provenance, summary
