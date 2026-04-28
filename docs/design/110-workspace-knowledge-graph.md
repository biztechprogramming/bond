# 110 — Workspace Knowledge Graph

**Status:** Draft (Revised)
**Author:** Sage
**Date:** 2026-04-13
**Revised:** 2026-04-13 — Storage layer changed from SQLite to SpacetimeDB per project standards (Design Doc 018)

## Depends on

- 001 — Knowledge Store & Persistent Memory
- 002 — Entity Graph
- 012 — Context Distillation Pipeline
- 026 — Haystack Pipeline Patterns
- 034 — Conversation Continuation
- 056 — Aider RepoMap Integration
- 075 — Automatic Context Indexing
- 098 — File Reading Tools Redesign

## Overview

Bond already has several strong context systems:

- persistent memory and knowledge store
- entity graph for user-world relationships
- context distillation/compression
- continuation/checkpointing
- repo-map style structural awareness
- automatic indexing of large tool outputs

What Bond does not yet have is a first-class, durable, queryable graph of the active workspace itself.

The **Workspace Knowledge Graph (WKG)** is that missing layer.

It creates and maintains a persistent structural and semantic graph for one or more mounted workspaces or repositories so the agent can answer questions like:

- what code paths connect this route to this table?
- what files are impacted by changing this interface?
- where is the real implementation of this concept?
- what tests cover this component?
- what docs, plans, and decisions relate to this module?
- what changed since the graph was last indexed?

The WKG is not a replacement for file reads or repo maps. It is a higher-value substrate that:

- narrows search
- improves provenance
- supports path, impact, and rationale queries
- enables graph-aware context retrieval
- persists across turns and sessions

## Problem Statement

Today Bond can discover files, search content, build repo maps, persist memories, and maintain an entity graph for user-world concepts. But for active coding work it still lacks a durable graph of workspace structure and relationships.

That gap creates recurring problems:

- the agent re-discovers the same architecture repeatedly
- impact analysis is shallow and often file-name based
- cross-file provenance is weak
- continuation relies too heavily on transcript summaries instead of structural anchors
- related docs, tests, configs, and code paths are not linked in a persistent substrate

The result is higher token usage, slower grounding, and more repeated file reads than necessary.

## Goals

- create a durable, queryable graph for mounted workspaces and repositories
- support deterministic structural extraction first, with optional semantic enrichment later
- improve file selection, impact analysis, continuation, and provenance
- integrate with Bond’s existing knowledge-store and repository patterns
- remain local-first and incremental

## Non-Goals

- replacing direct file reads
- replacing repo-map generation
- replacing the existing entity graph for user-world concepts
- requiring a graph UI in Phase 1
- making an external graph engine mandatory for basic usefulness

## Core Model

The WKG models technical artifacts and their relationships across one or more mounted workspaces.

### Node types

Initial node types should include:

- `workspace`
- `repository`
- `directory`
- `file`
- `symbol`
- `route`
- `config_key`
- `migration`
- `test`
- `document`
- `plan_item`
- `checkpoint`
- `service`
- `table`
- `column`

### Edge types

Initial edge types should include:

- `contains`
- `defines`
- `imports`
- `exports`
- `calls`
- `references`
- `inherits`
- `implements`
- `tests`
- `routes_to`
- `handles`
- `persists_to`
- `reads_from`
- `writes_to`
- `configured_by`
- `documents`
- `mentions`
- `related_to`
- `generated_from`
- `changed_by`
- `covered_by`
- `depends_on`
- `blocks`
- `belongs_to_repo`
- `belongs_to_workspace`

### Edge quality

Every edge includes:

- `relationship_type`
- `direction`
- `mode`: `extracted | inferred | ambiguous`
- `confidence`: `0.0..1.0`
- `source_kind`: `ast | regex | llm | tool_output | manual | imported`
- `provenance_refs`: file path, line span, source record IDs
- `last_confirmed_at`

### Graph layers

The WKG should be layered.

#### Layer A — Structural graph

Deterministic extraction from code, config, and docs:

- files
- symbols
- imports
- calls
- routes
- migrations
- tests

#### Layer B — Semantic or inferred graph

LLM- or heuristic-derived relationships:

- concept similarity
- likely ownership
- likely rationale links
- ambiguous references

#### Layer C — Operational graph

Links from Bond runtime artifacts:

- plan items
- tool output indexes
- conversation checkpoints
- changed files
- deployment services

This layered model keeps deterministic structure separate from softer inference.

## Storage Model

Bond already has a knowledge-store direction in Doc 001 and an entity-graph direction in Doc 002. The WKG should reuse those ideas but not overload the existing user-world entities schema.

### What Bond already gives us

Reviewing the current Bond implementation suggests the WKG should be designed as a composition of existing patterns rather than a brand-new subsystem:

- `backend/app/foundations/entity_graph/repository.py` already provides the right repository shape for graph traversal primitives such as `get_neighborhood`, `find_path`, `search`, and `resolve`
- `backend/app/agent/context_store.py` demonstrates a local SQLite pattern for durable indexing, but WKG should follow the project's SpacetimeDB direction instead
- `backend/app/agent/workspace_map.py` already discovers workspace and repo boundaries and gives us a natural source for `workspace` and `repository` nodes
- `backend/app/agent/repomap/tags.py` and `backend/app/agent/repomap/ranking.py` already extract symbol definitions/references and rank files by structural importance, which is a strong Phase 1 seed for deterministic graph extraction
- Bond’s SpacetimeDB module (`spacetimedb/spacetimedb/src/index.ts`) already defines 40+ tables covering agents, conversations, work plans, providers, deployments, and database integration — the WKG tables follow the same patterns

That review changes the recommendation: WKG should be a dedicated set of SpacetimeDB tables, following the same table/reducer patterns used by the rest of the Bond module, so it can be implemented incrementally with low architectural risk and full real-time capability.

### Recommendation

Create separate WKG tables rather than stuffing workspace artifacts into `entities` and `relationships`.

Reason:

- workspace graph has different scale and churn
- node types are more technical and numerous
- provenance and extraction metadata are richer
- indexing cadence is different
- query patterns are different
- Bond’s current `entities` table is intentionally scoped to user-world concepts (`person`, `project`, `task`, `decision`, etc.), and widening it to files/routes/tests/symbols would blur semantics and complicate existing entity resolution

### Recommended persistence approach

Use SpacetimeDB as the canonical store for WKG data from Phase 1.

Specifically:

- store WKG nodes, edges, provenance, runs, and file state as SpacetimeDB tables in the Bond module (`spacetimedb/spacetimedb/src/index.ts`)
- use SpacetimeDB indexes for efficient lookups (workspace-scoped, source/target node, edge/node provenance)
- expose mutations through reducers following Bond's existing SpacetimeDB patterns (upsert, soft-delete, batch import)
- frontend and gateway get real-time subscriptions to graph state for free
- keep embeddings as a future extension (side table or external sidecar)

Why SpacetimeDB is the right fit:

- Bond's migration direction (Design Doc 018) is toward SpacetimeDB as the unified state store
- all other major subsystems (agents, conversations, work plans, providers, deployments) already live in SpacetimeDB
- adding WKG to SQLite would create a divergent storage path that contradicts the dual-write migration strategy
- SpacetimeDB's real-time subscriptions give the UI graph explorer (Phase 3) reactivity without additional plumbing
- the Gateway already mediates SpacetimeDB access, so backend graph queries use the same infrastructure as everything else

### Proposed schema

All WKG tables are defined as SpacetimeDB tables in `spacetimedb/spacetimedb/src/index.ts`.

#### `workspace_graph_nodes`

SpacetimeDB table with BTree index on `workspaceId`.

Fields: `id` (PK), `workspaceId`, `repoId`, `nodeType`, `stableKey`, `displayName`, `path`, `language`, `signature`, `contentHash`, `isDeleted`, `metadata` (JSON string), `embeddingModel`, `processedAt`, `createdAt`, `updatedAt`.

#### `workspace_graph_edges`

SpacetimeDB table with BTree indexes on `workspaceId`, `sourceNodeId`, `targetNodeId`.

Fields: `id` (PK), `workspaceId`, `repoId`, `sourceNodeId`, `targetNodeId`, `edgeType`, `mode` (extracted/inferred/ambiguous), `confidence`, `sourceKind`, `runId`, `isDeleted`, `metadata` (JSON string), `createdAt`, `updatedAt`, `lastConfirmedAt`.

#### `workspace_graph_provenance`

SpacetimeDB table with BTree indexes on `edgeId`, `nodeId`.

Fields: `id` (PK), `workspaceId`, `edgeId`, `nodeId`, `provenanceType`, `sourcePath`, `sourceLineStart`, `sourceLineEnd`, `sourceRef`, `excerpt`, `createdAt`.

#### `workspace_graph_runs`

SpacetimeDB table with BTree index on `workspaceId`.

Fields: `id` (PK), `workspaceId`, `repoId`, `runType` (full/incremental/on_demand), `status` (pending/running/success/failed/partial), `trigger`, `filesScanned`, `nodesWritten`, `edgesWritten`, `startedAt`, `completedAt`, `error`.

#### `workspace_graph_file_state`

SpacetimeDB table with BTree index on `workspaceId`.

Fields: `id` (PK), `workspaceId`, `repoId`, `path`, `contentHash`, `language`, `mtimeNs`, `sizeBytes`, `lastIndexedAt`, `lastRunId`, `status` (indexed/skipped/error/deleted), `lastError`, `metadata` (JSON string).

#### Search capabilities

Full-text search is handled at the application layer (Gateway/backend) by querying SpacetimeDB tables via subscriptions and filtering on `displayName`, `stableKey`, `path`, `signature`, and provenance `excerpt` fields. For heavy search workloads, a future phase may add an external search index (e.g., tantivy sidecar) fed by SpacetimeDB subscriptions.

Optional later:

- `workspace_graph_embeddings` (side table or external vector store)
- `workspace_graph_communities` (Graphify-derived clustering)
- `workspace_graph_materialized_paths` (precomputed traversals)

### Stable key guidance

Stable keys should be workspace-relative and semantic where possible, not absolute-path based.

Examples:

- `repo:bond`
- `file:bond/backend/app/agent/pre_gather.py`
- `symbol:bond/backend/app/agent/pre_gather.py::workspace_plan_phase`
- `test:bond/backend/tests/agent/test_pre_gather.py::test_workspace_plan_phase`

Avoid absolute filesystem paths in stable keys because mounts and host paths can differ across machines and sessions.

## Extraction and Indexing Architecture

### Indexing triggers

The WKG indexer runs on:

- workspace startup or mount
- explicit user request
- background file-change detection
- after large file or tool-output ingestion
- continuation resume for stale workspaces
- post-edit refresh after successful code changes

### Extractor pipeline

Following Doc 026, extraction should be componentized.

```text
WorkspaceDiscovery
  -> RepoBoundaryResolver
  -> FileEnumerator
  -> ChangeDetector
  -> LanguageClassifier
  -> StructuralExtractors
  -> ConfigExtractors
  -> DocExtractors
  -> TestExtractors
  -> EdgeNormalizer
  -> ProvenanceWriter
  -> GraphWriter
  -> Embedding/InferenceStage (optional)
```

### Recommended Phase 1 implementation in Bond

A practical first implementation should reuse current Bond modules directly, while defining a clean insertion point for Graphify.

1. `WorkspaceDiscovery`
   - build on `backend/app/agent/workspace_map.py`
   - use `discover_repos()` and `build_workspace_overview()` logic to establish workspace and repo boundaries
   - create `workspace` and `repository` nodes first so all later imports have stable parents

2. `FileEnumerator` + `ChangeDetector`
   - persist file hashes and indexing status in `workspace_graph_file_state`
   - only re-extract files whose hash, mtime, size, or deletion state changed
   - when Graphify is enabled, feed the changed file set into `graphify/graphify/extract.py:collect_files()` / `extract()` rather than reprocessing the whole repo

3. `StructuralExtractors`
   - Phase 1 minimum: reuse `backend/app/agent/repomap/tags.py` as the initial symbol extractor for supported languages
   - convert `Tag(kind='def'|'ref')` into `symbol` nodes plus `defines` / `references` edges
   - Phase 1.5 / 2: add a `GraphifyAdapter` that imports Graphify extraction results for broader multi-language support and richer relation types such as imports, calls, rationale links, and optional hyperedges
   - reuse `backend/app/agent/repomap/ranking.py` later as a ranking layer over the graph rather than recomputing structural importance from scratch

4. `GraphWriter`
   - follow the repository style already used by `EntityRepository` rather than scattering SQL across the agent loop
   - expose methods like `upsert_nodes`, `upsert_edges`, `get_neighbors`, `find_path`, and `search`
   - add a dedicated import path for external extraction batches so Graphify-originated metadata and provenance are preserved instead of flattened away

5. `Search surface`
   - query SpacetimeDB tables via Gateway client using index lookups and subscription-based filtering
   - keep node search cheap and deterministic before adding embeddings or LLM inference
   - expose both exact/stable-key lookup and text search over labels, paths, signatures, and provenance summaries

6. `Optional Graphify-derived artifacts`
   - if Graphify analysis is run, store references to derived artifacts such as `graph.json`, `GRAPH_REPORT.md`, and community assignments as secondary outputs
   - do not require those artifacts for core WKG query correctness

This gives Bond a credible Phase 1 without forcing immediate dependency on Graphify, while still making Graphify a concrete Phase 1.5/2 accelerant instead of a vague future possibility.

### Relationship to Graphify

Graphify is now available in the workspace, and it materially changes what Bond can reuse versus what Bond still needs to own.

What Graphify already provides well:

- a staged extraction pipeline: `collect_files() -> extract() -> build() -> cluster() -> analyze() -> report() -> export()` (`graphify/ARCHITECTURE.md`)
- deterministic multi-language structural extraction using tree-sitter in `graphify/graphify/extract.py`
- a normalized extraction shape of `nodes[]` and `edges[]` with per-edge confidence labels `EXTRACTED | INFERRED | AMBIGUOUS`
- graph assembly in `graphify/graphify/build.py`, including directed-edge preservation and support for optional `hyperedges`
- corpus ingestion for non-code artifacts in `graphify/graphify/ingest.py`, which can turn URLs and fetched content into graphable local files
- incremental reuse through Graphify’s cache and ignore-file model (`graphify-out/cache/`, `.graphifyignore`)

That said, Graphify should still be treated as an extraction and analysis engine, not Bond’s canonical runtime store.

Recommended integration boundary:

- Bond owns canonical persistence, query APIs, provenance records, and agent-facing tools
- Graphify is used as an extractor/adapter that emits normalized nodes and edges into Bond’s `workspace_graph_*` tables
- Bond keeps working when Graphify is unavailable, by falling back to native repo-map/workspace-map extraction for Phase 1 coverage
- Graphify-specific outputs such as `community`, `GRAPH_REPORT.md`, `graph.json`, and `graph.html` are optional derived artifacts, not required for core agent workflows

Specific design implications from the Graphify codebase:

1. **Do not make NetworkX graphs the primary persisted representation.**
   Graphify builds in-memory NetworkX graphs and exports files under `graphify-out/`. That is excellent for batch analysis and visualization, but Bond still needs durable relational storage for cross-turn queryability, incremental updates, and integration with existing repositories and tools.

2. **Adopt Graphify’s confidence taxonomy directly.**
   Bond’s draft uses `mode: extracted | inferred | ambiguous`. That maps cleanly to Graphify’s `EXTRACTED | INFERRED | AMBIGUOUS`. The doc should explicitly standardize this mapping so Graphify edges can be imported without lossy translation.

3. **Expect partial graphs and dangling references.**
   `graphify/graphify/build.py` intentionally skips edges whose endpoints are not present in the node set. Bond should preserve provenance for these dropped relationships during import, or at minimum count and log them, because they can indicate stdlib/external dependencies that matter for explanation.

4. **Use Graphify as Phase 2+ breadth, not as the only Phase 1 path.**
   Graphify’s extractor supports many languages and non-code artifacts, but Bond should still ship a minimal Phase 1 using existing Bond modules. That avoids making WKG dependent on a large external extraction stack before core storage and query semantics are proven.

5. **Add an explicit import adapter layer.**
   The WKG pipeline should include a `GraphifyAdapter` stage that:
   - runs `collect_files()` / `extract()` for selected repos or file sets
   - maps Graphify node dicts into Bond `workspace_graph_nodes`
   - maps Graphify edge dicts into Bond `workspace_graph_edges`
   - records Graphify source metadata (`source_file`, `source_location`, extraction run, confidence label) into provenance tables or JSON metadata
   - optionally stores Graphify communities/hyperedges in dedicated side tables later

6. **Reuse Graphify for docs/media ingestion where Bond is currently thin.**
   `graphify/graphify/ingest.py` is especially relevant for future WKG support of fetched docs, PDFs, images, and YouTube/audio-derived artifacts. The design should call this out as a concrete route for non-code workspace knowledge ingestion rather than treating all semantic extraction as hypothetical.

7. **Keep clustering and topology analysis optional.**
   Graphify’s Leiden clustering and analysis/report stages are valuable, but they should be Phase 3 derived capabilities. Core WKG queries—neighbors, pathfinding, impact analysis, provenance—must not depend on clustering having run.

Recommended Bond import contract from Graphify:

```python
class GraphifyAdapter:
    async def extract_workspace(self, root: Path) -> GraphifyExtractionBatch: ...
    async def import_batch(
        self,
        workspace_id: str,
        repo_id: str | None,
        batch: GraphifyExtractionBatch,
        run_id: str,
    ) -> ImportSummary: ...
```

Where `GraphifyExtractionBatch` should preserve at least:

- `nodes[]` with `id`, `label`, `source_file`, `source_location`, and arbitrary attributes
- `edges[]` with `source`, `target`, `relation`, `confidence`, and arbitrary attributes
- optional `hyperedges[]`
- extraction warnings such as dropped/dangling-edge counts
- import-level metadata such as extractor version, root path, and cache hit information

## Incremental indexing behavior

The WKG should be incrementally maintained rather than rebuilt on every turn.

### File-state ledger

Track, per workspace path:

- content hash
- mtime and size
- last successful run
- last failed run
- deletion status
- extraction warnings

### Reconciliation rules

On incremental runs:

- mark previously known nodes and edges for changed files as stale for the current run
- upsert freshly extracted nodes and edges with the new `run_id`
- soft-delete nodes and edges that disappeared because the source file was removed or the symbol no longer exists
- preserve provenance history even when the current node or edge is soft-deleted

### Staleness semantics

Queries should be able to:

- exclude soft-deleted nodes and edges by default
- optionally include stale/deleted artifacts for debugging or historical explanation
- report last indexed time and last successful run so the agent can tell the user when the graph may be outdated

## Query and Retrieval

The WKG should support both graph traversal and search.

### Core query patterns

- get node by path or stable key
- get neighbors
- find path
- impact analysis
- find definitions or references
- find related tests
- find related docs or plans
- explain relationship provenance
- search graph nodes by keyword or hybrid similarity

### Example repository interface

```python
class WorkspaceGraphRepository:
    async def upsert_nodes(self, nodes: list[GraphNode]) -> None: ...
    async def upsert_edges(self, edges: list[GraphEdge]) -> None: ...
    async def record_run(self, run: GraphRun) -> str: ...
    async def get_node(self, workspace_id: str, stable_key: str) -> GraphNode | None: ...
    async def get_neighbors(
        self,
        workspace_id: str,
        node_id: str,
        edge_types: list[str] | None = None,
        depth: int = 1,
        min_confidence: float = 0.0,
    ) -> GraphSubgraph: ...
    async def find_path(
        self,
        workspace_id: str,
        source_node_id: str,
        target_node_id: str,
        max_depth: int = 4,
        edge_types: list[str] | None = None,
    ) -> GraphPath | None: ...
    async def impact_analysis(
        self,
        workspace_id: str,
        seed_node_ids: list[str],
        max_depth: int = 3,
    ) -> ImpactReport: ...
    async def explain_edge(self, edge_id: str) -> EdgeExplanation: ...
```

## Agent Tooling

The WKG should be exposed as first-class tools.

### New tools

#### `workspace_graph_query`

Structured graph lookup:

Inputs:

- workspace or repo scope
- query type: `neighbors | path | impact | related | search | explain`
- seed node or search text
- filters for edge type, depth, confidence, freshness

Returns:

- compact answer
- ranked nodes or paths
- provenance snippets
- freshness metadata

#### `workspace_graph_refresh`

Trigger indexing:

Inputs:

- workspace or repo scope
- mode: `incremental | full`
- optional file subset

Returns:

- run status
- files scanned
- nodes/edges updated
- warnings or failures

### How tools should be used by the agent

Use WKG tools before broad search when the task is structural, such as:

- impact analysis
- tracing routes to handlers or tables
- finding related tests or docs
- resuming interrupted code work

Do not use WKG first for:

- greetings
- tiny single-file edits
- purely conversational turns
- direct requests with explicit file paths already provided

### Prompt budget behavior

Graph context should usually be summarized into:

- candidate files
- relationship paths
- impacted tests or docs
- short provenance lines

Do not dump raw subgraphs into the prompt unless specifically asked.

## Continuation Integration

Doc 034’s continuation model becomes stronger with WKG support.

### Resume context

On continuation:

- resolve active plan item
- fetch changed files from prior work
- fetch neighboring code artifacts
- fetch related tests, docs, routes, and config
- include only a compact structural summary

### Checkpoint enrichment

Checkpoints may store:

- anchor node IDs
- impacted file node IDs
- unresolved dependency nodes
- relevant test nodes

This allows continuation to resume from structure, not only transcript summary.

## UI and Observability

### UI opportunities

- graph neighborhood explorer
- path visualization
- impact analysis panel
- “why these files?” provenance drawer
- stale index or indexing progress status

### Observability

Track:

- indexing duration
- files indexed per run
- cache hit rate
- graph query latency
- query usefulness
- prompt token savings
- reduction in repeated file reads
- continuation success rate

## Security and Privacy

- WKG remains local-first by default
- graph extraction must respect workspace boundaries
- secrets or config values should be redacted or typed, not stored raw
- provenance excerpts should avoid exposing secret material
- external adapters must be opt-in

## Failure Modes and Graceful Degradation

If indexing fails:

- Bond still uses `project_search` and `file_read`

If graph is stale:

- tools should report staleness
- the agent may fall back to direct file inspection

If inferred edges are low confidence:

- mark them clearly
- do not over-prioritize them in prompt assembly

## Rollout Plan

### Phase 1

- canonical storage in SpacetimeDB tables (workspace_graph_nodes, edges, provenance, runs, file_state)
- reducers for upsert, soft-delete, batch import, run lifecycle, and workspace purge
- deterministic workspace, repository, file, and symbol graph
- incremental hashing and file-state ledger
- native Bond extractor path using workspace-map and repo-map tags
- basic graph tools and repository APIs (via Gateway SpacetimeDB client)
- no UI required

### Phase 2

- Graphify adapter for broader multi-language extraction
- routes, tests, docs, config, and migration extraction
- Graphify-backed docs/media ingestion where useful
- context pipeline integration
- continuation integration
- frontend subscriptions to graph tables for live views

### Phase 3

- inferred semantic edges
- impact analysis and path explainability
- optional clustering/community side tables
- UI graph explorer with real-time SpacetimeDB subscriptions
- optional external search index for heavy FTS workloads

## Open Questions

- Should graph embeddings be stored with nodes or in a side table?
- How much of repo-map generation should become a WKG view?
- ~~Should extracted graph data sync to the frontend via SpacetimeDB projections?~~ **Resolved:** Yes — WKG tables live in SpacetimeDB natively, frontend subscribes directly.
- How should multi-repo workspaces model cross-repo edges?
- What is the minimal graph schema that still supports impact analysis well?
- How should Bond represent dangling external references imported from Graphify: dropped with counters, stored as unresolved nodes, or preserved only in provenance?
