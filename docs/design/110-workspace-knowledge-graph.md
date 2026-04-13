# 099 — Workspace Knowledge Graph

**Status:** Draft  
**Author:** Sage  
**Date:** 2026-04-13

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

Today Bond’s code understanding is distributed across several mechanisms:

- file search and file reads at turn time
- repo-map style summaries
- indexed tool outputs
- memory and conversation summaries
- prompt fragment routing
- ad hoc LLM reasoning over raw files

This creates several problems.

### 1. Repeated orientation cost

The agent repeatedly re-discovers the same structure across turns:

- where a symbol is defined
- which module depends on which
- which route maps to which service
- what tests are relevant

### 2. Weak structural provenance

Bond can often produce the right answer, but it lacks a durable structure tying:

- symbol to file
- file to imports
- function to calls
- route to handler
- model to table
- doc to code artifact
- plan item to changed files

### 3. Poor support for impact analysis

Impact analysis currently requires repeated:

- `file_search`
- `file_read`
- grep-like exploration
- LLM synthesis

This is expensive and inconsistent.

### 4. Context assembly is still too prompt-centric

Docs 012 and 026 correctly identify the need for componentized retrieval and assembly, but the current context flow remains heavily procedural. Structural workspace knowledge is not yet a first-class retrieval lane.

### 5. Existing graph concepts are scoped to user knowledge, not code knowledge

Doc 002 defines an entity graph for people, projects, tasks, and decisions in the user’s world. That is useful, but different from a code or workspace graph. The WKG must be purpose-built for engineering artifacts.

## Goals

### Primary goals

- maintain a persistent graph of workspace artifacts and relationships
- support incremental indexing and partial refresh
- expose graph queries to agents as first-class tools
- feed graph-derived context into prompt assembly selectively
- provide provenance and confidence for graph edges
- improve continuation, planning, and code navigation

### Secondary goals

- link code artifacts to docs, tasks, decisions, and memories
- support multi-repo workspaces
- enable UI visualization of graph neighborhoods and paths
- allow optional external graph backends or adapters

### Non-goals

- replacing direct file reads as source of truth
- forcing graph retrieval on every turn
- building a full general-purpose graph database first
- perfect semantic understanding of every language in v1
- indexing all conversation content into the workspace graph

## Design Principles

### 1. Graph is additive, not mandatory

Bond must continue to work if graph indexing is disabled, stale, or unavailable.

### 2. Extracted and inferred relationships must be distinguished

Every node and edge must carry:

- extraction mode: `extracted`, `inferred`, `ambiguous`
- confidence score
- provenance

### 3. Incremental by default

The graph should update based on changed files or hashes, not full re-indexes on every turn.

### 4. Structural retrieval before raw expansion

For architecture and impact questions, the graph should narrow candidates before the agent reads full files.

### 5. Typed components over monoliths

WKG retrieval should plug into Bond’s context pipeline as a typed component, not as ad hoc logic inside `context_builder.py`.

### 6. Local-first and inspectable

The user should be able to inspect:

- what was indexed
- what relationships exist
- where an edge came from
- why a result was returned

## Conceptual Model

The Workspace Knowledge Graph models durable workspace artifacts.

### Node types

Initial node types:

- `workspace`
- `repository`
- `directory`
- `file`
- `language`
- `module`
- `symbol`
- `class`
- `function`
- `method`
- `interface`
- `type`
- `route`
- `api_endpoint`
- `database_table`
- `database_column`
- `migration`
- `test`
- `doc`
- `plan`
- `plan_item`
- `service`
- `container`
- `config_key`
- `prompt_fragment`
- `tool_output_index`
- `concept`

Not every language or repo will use every type.

### Edge types

Initial edge types:

- `contains`
- `defines`
- `declares`
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

### Recommendation

Create separate WKG tables rather than stuffing workspace artifacts into `entities` and `relationships`.

Reason:

- workspace graph has different scale and churn
- node types are more technical and numerous
- provenance and extraction metadata are richer
- indexing cadence is different
- query patterns are different

### Proposed schema

#### `workspace_graph_nodes`

- `id TEXT PRIMARY KEY`
- `workspace_id TEXT NOT NULL`
- `repo_id TEXT`
- `node_type TEXT NOT NULL`
- `stable_key TEXT NOT NULL`
- `display_name TEXT NOT NULL`
- `path TEXT`
- `language TEXT`
- `metadata JSON`
- `content_hash TEXT`
- `embedding_model TEXT`
- `processed_at TIMESTAMP`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

Constraints:

- unique `(workspace_id, stable_key)`

Examples of `stable_key`:

- `file:/workspace/bond/backend/app/agent/context_builder.py`
- `symbol:python:backend.app.agent.context_builder:build_agent_context`
- `route:GET:/api/v1/conversations/{id}/turn`

#### `workspace_graph_edges`

- `id TEXT PRIMARY KEY`
- `workspace_id TEXT NOT NULL`
- `source_node_id TEXT NOT NULL`
- `target_node_id TEXT NOT NULL`
- `edge_type TEXT NOT NULL`
- `mode TEXT NOT NULL CHECK(mode IN ('extracted','inferred','ambiguous'))`
- `confidence REAL NOT NULL`
- `source_kind TEXT NOT NULL`
- `metadata JSON`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

Constraints:

- unique `(workspace_id, source_node_id, target_node_id, edge_type, source_kind)`

#### `workspace_graph_provenance`

- `id TEXT PRIMARY KEY`
- `workspace_id TEXT NOT NULL`
- `edge_id TEXT`
- `node_id TEXT`
- `provenance_type TEXT NOT NULL`
- `source_path TEXT`
- `source_line_start INT`
- `source_line_end INT`
- `source_ref TEXT`
- `excerpt TEXT`
- `created_at TIMESTAMP NOT NULL`

#### `workspace_graph_runs`

- `id TEXT PRIMARY KEY`
- `workspace_id TEXT NOT NULL`
- `repo_id TEXT`
- `run_type TEXT NOT NULL CHECK(run_type IN ('full','incremental','on_demand'))`
- `status TEXT NOT NULL CHECK(status IN ('pending','running','success','failed','partial'))`
- `trigger TEXT NOT NULL CHECK(trigger IN ('workspace_mount','file_change','git_change','manual','tool_intercept','continuation','startup'))`
- `files_scanned INT NOT NULL DEFAULT 0`
- `nodes_written INT NOT NULL DEFAULT 0`
- `edges_written INT NOT NULL DEFAULT 0`
- `started_at TIMESTAMP NOT NULL`
- `completed_at TIMESTAMP`
- `error TEXT`

#### `workspace_graph_file_state`

- `id TEXT PRIMARY KEY`
- `workspace_id TEXT NOT NULL`
- `repo_id TEXT`
- `path TEXT NOT NULL`
- `content_hash TEXT NOT NULL`
- `language TEXT`
- `last_indexed_at TIMESTAMP`
- `last_run_id TEXT`
- `status TEXT NOT NULL CHECK(status IN ('indexed','skipped','error','deleted'))`
- `metadata JSON`

Unique:

- `(workspace_id, path)`

Optional later:

- `workspace_graph_embeddings`
- `workspace_graph_communities`
- `workspace_graph_materialized_paths`

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

### Extractor types

#### Deterministic extractors

- tree-sitter or AST-based symbol extraction
- import or export extraction
- route extraction from framework conventions
- migration or schema extraction
- test-to-source heuristics
- config or service extraction

#### Heuristic extractors

- naming-based test coverage links
- route-to-handler mapping where framework metadata is partial
- doc-to-code links via path and symbol references

#### LLM-assisted extractors

Used sparingly for:

- concept clustering
- rationale extraction from docs
- ambiguous relationship disambiguation
- semantic doc or code linking

### Relationship to repo map

Doc 056’s repo map becomes an input or sibling artifact, not the whole solution.

Repo map:

- compact, token-budgeted prompt artifact

WKG:

- persistent, queryable storage and retrieval substrate

The repo map renderer can later become a view over the WKG.

## Query Model

The WKG should support a small set of high-value queries first.

### Core query operations

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

General graph lookup or search.

Parameters:

- `query`
- `workspace_id?`
- `node_types?`
- `edge_types?`
- `limit?`

Returns:

- matching nodes
- summary
- top provenance

#### `workspace_graph_neighbors`

Parameters:

- `node`
- `depth`
- `edge_types?`
- `min_confidence?`

Returns:

- neighborhood summary
- nodes
- edges

#### `workspace_graph_path`

Parameters:

- `source`
- `target`
- `max_depth?`
- `edge_types?`

Returns:

- best path
- alternate paths
- provenance

#### `workspace_graph_impact`

Parameters:

- `seed_nodes`
- `max_depth?`
- `include_tests?`
- `include_docs?`

Returns:

- impacted files, symbols, tests, and docs
- confidence buckets

#### `workspace_graph_explain`

Parameters:

- `node_or_edge`
- `include_provenance?`

Returns:

- explanation
- evidence

### Existing tools that should integrate

- `file_read`
- `project_search`
- `ctx_search`

Examples:

- `project_search` may use graph hints to rerank files
- `file_read` may attach graph metadata in summaries
- `ctx_search` indexed tool outputs may emit graph nodes or edges for durable artifacts

## Context Assembly Integration

This is where the WKG materially improves Bond.

### New retrieval lane

Add **Structural Context Retrieval** as a distinct lane in prompt assembly.

Current conceptual lanes:

- system or prompt fragments
- conversation history
- memory
- plan or continuation
- tool results
- MCP context

Add:

- workspace graph context

### New component in context pipeline

Per Doc 026, add a component like:

- `WorkspaceGraphRetriever`

Inputs:

- user request
- active plan
- current workspace
- recent changed files
- continuation intent

Outputs:

- top relevant nodes
- graph path summaries
- impacted files, tests, and docs
- provenance snippets

### Retrieval heuristics

Prefer WKG retrieval when the user asks:

- architecture questions
- “where is X?”
- “what depends on Y?”
- “what will this change affect?”
- “how does A connect to B?”
- “find the right file or module”
- “continue working on this feature” in a large repo

Skip or minimize WKG retrieval for:

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

- deterministic file, symbol, and import graph
- incremental file hashing
- basic graph tools
- no UI required

### Phase 2

- routes, tests, docs, and config extraction
- context pipeline integration
- continuation integration

### Phase 3

- inferred semantic edges
- impact analysis and path explainability
- UI graph explorer
- optional external adapters

## Open Questions

- Should WKG live in SQLite or libsql tables, SpacetimeDB, or hybrid storage?
- Should graph embeddings be stored with nodes or in a side table?
- How much of repo-map generation should become a WKG view?
- Should extracted graph data sync to the frontend via SpacetimeDB projections?
- How should multi-repo workspaces model cross-repo edges?
- What is the minimal graph schema that still supports impact analysis well?
