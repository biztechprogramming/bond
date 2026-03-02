# Bond vs memU — Memory System Comparison

Bond's memory system (`backend/app/features/memory/` + `foundations/knowledge/`) compared against [memU](https://github.com/NevaMind-AI/memU), a dedicated memory framework for 24/7 proactive AI agents.

## Architecture

| Aspect | Bond | memU |
|---|---|---|
| **Scope** | Memory is one feature of a full assistant | Memory is the entire product |
| **Language** | Python (async SQLAlchemy) | Python + Rust core (PyO3) |
| **Storage** | SQLite (aiosqlite) + sqlite-vec | PostgreSQL (pgvector) or in-memory; SQLite planned |
| **Embedding** | Voyage / Gemini / local (configurable) | OpenAI, Voyage, ONNX local, OpenRouter, LazyLLM, custom |
| **LLM dependency** | LiteLLM (any provider) | Multi-profile LLM config (SDK, HTTP, LazyLLM backends) |
| **Data model** | Flat memories table + entity graph | 3-layer hierarchy: Resource → Item → Category |

## Memory Model

| Aspect | Bond | memU |
|---|---|---|
| **Memory types** | `fact`, `solution`, `instruction`, `preference` (free-form) | `profile`, `event`, `knowledge`, `behavior`, `skill`, `tool` (typed) |
| **Hierarchy** | ❌ Flat — all memories in one table | ✅ 3-layer: Resources (raw data) → Items (extracted facts) → Categories (auto-organized topics) |
| **Auto-categorization** | ❌ Manual type assignment | ✅ LLM-driven automatic categorization with configurable categories |
| **Cross-references** | Via entity graph relationships | ✅ CategoryItem links + content hash dedup |
| **Deduplication** | ❌ None | ✅ Content hash-based dedup with reinforcement counting |
| **Reinforcement** | ❌ | ✅ Tracks reinforcement count + last reinforced timestamp |
| **Tool memory** | ❌ | ✅ Dedicated `tool` type with call history, success rate, time/token cost tracking |
| **Versioning** | ✅ Full version history (previous/new content, changed_by, reason) | ❌ No explicit versioning |
| **Soft delete** | ✅ | Not documented |
| **Importance scoring** | ✅ Explicit importance field (0–1) | ❌ Implicit via reinforcement count |

## Search & Retrieval

| Aspect | Bond | memU |
|---|---|---|
| **Full-text search** | ✅ FTS5 with BM25 | ❌ Not documented (vector-primary) |
| **Vector search** | ✅ sqlite-vec (cosine similarity) | ✅ pgvector or in-memory vector search |
| **Hybrid search** | ✅ FTS5 + vec0 merged via Reciprocal Rank Fusion (RRF) | ❌ Vector-only retrieval |
| **Recency boost** | ✅ Configurable half-life decay (30 days default) | Not documented |
| **Date filtering** | ✅ since/until parameters | Not documented |
| **Type filtering** | ✅ Filter by memory type or source type | ✅ Filter by category |
| **Context-aware retrieval** | ❌ Query-based only | ✅ Proactive context loading based on predicted intent |

## Proactive Intelligence

| Aspect | Bond | memU |
|---|---|---|
| **24/7 background agent** | ❌ (proactive module exists but empty) | ✅ Core feature — continuous monitoring + memorization |
| **Intent prediction** | ❌ | ✅ Anticipates user needs from interaction patterns |
| **Proactive suggestions** | ❌ | ✅ Pre-fetches context and prepares recommendations |
| **Background memorization** | ❌ | ✅ Automatically extracts and stores insights from conversations |
| **Autonomous task updates** | ❌ | ✅ Updates todolists and context autonomously |
| **Token cost reduction** | ❌ | ✅ Caches insights to avoid redundant LLM calls (~1/10 context size claimed) |

## Knowledge Structure

| Aspect | Bond | memU |
|---|---|---|
| **Entity graph** | ✅ Entities + relationships with LLM extraction | ❌ No explicit entity graph |
| **Entity types** | person, project, task, decision, meeting, document, event | N/A |
| **Relationship extraction** | ✅ LLM-based with context | N/A |
| **File-system metaphor** | ❌ | ✅ Memory organized like folders/files/symlinks |
| **Resource ingestion** | ❌ | ✅ Mount conversations, documents, images as queryable resources |
| **Blob/file storage** | ❌ | ✅ LocalFS blob store for resources |

## Workflow & Pipeline

| Aspect | Bond | memU |
|---|---|---|
| **Memory pipeline** | Direct save/search/update/delete | ✅ Multi-step workflow pipeline with interceptors |
| **Pipeline customization** | ❌ | ✅ Insert/remove/replace/configure steps; versioned revisions |
| **Workflow runners** | ❌ | ✅ Pluggable (local, Temporal, Burr) |
| **Interceptors** | ❌ | ✅ Before/after/on-error hooks for workflow steps and LLM calls |
| **LLM call wrapping** | ❌ | ✅ LLMClientWrapper with interceptors, metadata, profiling |

## Integration & Multi-tenancy

| Aspect | Bond | memU |
|---|---|---|
| **User scoping** | Single user (local-first) | ✅ User-scoped models with configurable scope fields |
| **Multi-provider LLM** | ✅ Via LiteLLM | ✅ Multi-profile config (different models for different operations) |
| **Cloud API** | ❌ Local only | ✅ Hosted service at memu.so with REST API |
| **SDK** | Python only (internal) | ✅ Python package (`pip install memu-py`) |
| **LangGraph integration** | ❌ | ✅ Built-in |
| **Vision support** | ❌ | ✅ Vision-capable models for image resources |

## What Bond Has That memU Doesn't

- **Hybrid search (FTS + vector + RRF)** — memU appears vector-only; Bond merges full-text BM25 with vector cosine via Reciprocal Rank Fusion
- **Entity graph** — LLM-extracted entities and relationships with typed connections
- **Memory versioning** — full audit trail of changes with previous/new content and change reason
- **Recency-weighted scoring** — configurable half-life decay for search result ranking
- **Soft delete** — memories can be tombstoned rather than permanently removed
- **Importance field** — explicit 0–1 importance scoring per memory

## What memU Has That Bond Doesn't

- **Proactive intelligence** — 24/7 background agent that monitors, memorizes, and predicts intent (Bond's proactive module is empty)
- **Hierarchical memory (Resource → Item → Category)** — structured 3-layer model vs Bond's flat table
- **Auto-categorization** — LLM-driven topic organization without manual tagging
- **Content deduplication** — hash-based dedup with reinforcement tracking
- **Tool memory** — tracks tool call history, success rates, and usage patterns
- **Workflow pipeline engine** — configurable multi-step pipelines with interceptors, versioned revisions, and pluggable runners
- **LLM interceptors** — wraps all LLM calls with before/after hooks for profiling and control
- **Resource ingestion** — mount documents, images, conversations as queryable memory sources
- **Multi-tenant scoping** — user-scoped data models for multi-user deployments
- **Vision support** — image resources processed via vision-capable models
- **Token cost optimization** — caches insights to reduce context size

## Highest-Impact Improvements for Bond

1. **Proactive memory agent** — Bond has the module stub (`features/proactive/`). Implementing background monitoring + intent prediction would be the single biggest upgrade. memU's architecture (main agent ↔ memory bot continuous sync loop) is a good reference.

2. **Hierarchical memory structure** — Moving from flat memories to Resource → Item → Category would enable auto-organization and better context assembly. Bond's entity graph could serve as a complementary layer.

3. **Content deduplication** — Simple content-hash dedup with reinforcement counting would prevent memory bloat and surface frequently-reinforced facts.

4. **Tool memory** — Tracking which tools succeed/fail and their cost would enable smarter tool selection over time.

5. **Workflow pipeline** — Bond's memory operations are direct function calls. A pipeline with interceptors would enable profiling, caching, and customization without modifying core logic.

## Summary

Bond's memory is a solid CRUD + search system with hybrid retrieval and entity extraction — good for reactive "save and search" workflows. memU is a purpose-built memory framework designed around proactive, always-on agents with hierarchical organization, deduplication, intent prediction, and workflow pipelines.

The fundamental difference: Bond's memory waits to be asked. memU's memory actively watches, learns, and anticipates. Bond's strongest advantage is its hybrid search (FTS + vector + RRF with recency decay) — something memU lacks. memU's strongest advantage is its proactive intelligence layer — exactly what Bond's empty `proactive` module is meant to become.
