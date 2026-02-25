# Design: Knowledge Store & Persistent Memory

**Status:** DRAFT v2 — awaiting approval  
**Author:** Developer Agent  
**Date:** 2026-02-25  
**Refs:** [02-foundations](../architecture/02-foundations.html), [05-data-architecture](../architecture/05-data-architecture.html), [03-agent-runtime](../architecture/03-agent-runtime.html)

---

## 1. Overview

This document covers two tightly coupled deliverables:

1. **Knowledge Store (F1)** — The unified storage substrate: SQLite + sqlite-vec + FTS5, repository pattern, migrations, hybrid search, and embedding pipeline.
2. **Persistent Memory (Phase 2)** — Save, recall, update, and invalidate facts, solutions, and instructions across sessions. Built on top of the Knowledge Store.

These are built together because memory is the first consumer of the knowledge store and validates its design end-to-end.

### Design Principles

- **Enterprise-grade data integrity** — CHECK constraints, NOT NULL enforcement, JSON validation, foreign key enforcement, immutable audit trails.
- **Graceful degradation** — System works without sqlite-vec (FTS-only search), without embedding providers (memories still saved and keyword-searchable).
- **Provenance & lineage** — Every memory has a traceable origin. Every change is versioned. Every search is observable.
- **Local-first, privacy-aware** — PII sensitivity classification, encryption-at-rest for secrets, all data stays on disk under user control.

---

## 2. ER Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          KNOWLEDGE STORE SCHEMA                             │
│                                                                             │
│  PK = Primary Key    FK = Foreign Key    NN = NOT NULL    UQ = Unique       │
│  TS = TIMESTAMP      CHK = CHECK constraint                                 │
└─────────────────────────────────────────────────────────────────────────────┘


  ┌─────────────────────────┐             ┌─────────────────────────┐
  │     content_chunks       │             │        memories          │
  ├─────────────────────────┤             ├─────────────────────────┤
  │ id           TEXT PK     │             │ id           TEXT PK     │
  │ source_type  TEXT NN     │             │ type         TEXT NN CHK │
  │ source_id    TEXT        │             │  ∊{fact,solution,       │
  │ text         TEXT NN     │             │    instruction,         │
  │ summary      TEXT        │             │    preference}          │
  │ chunk_index  INT NN DF 0 │             │ content      TEXT NN    │
  │ parent_id    TEXT FK ────┼──self       │ summary      TEXT       │
  │ sensitivity  TEXT NN CHK │             │ source_type  TEXT       │
  │  ∊{normal,personal,     │             │ source_id    TEXT       │
  │    secret}               │             │ sensitivity  TEXT NN CHK│
  │ metadata     JSON CHK    │             │  ∊{normal,personal,    │
  │  json_valid(metadata)    │             │    secret}              │
  │ embedding_model TEXT     │             │ metadata     JSON CHK   │
  │ processed_at TS          │             │  json_valid(metadata)   │
  │ created_at   TS NN       │             │ embedding_model TEXT    │
  │ updated_at   TS NN       │             │ importance   REAL NN CHK│
  └────────┬────────────────┘             │  BETWEEN 0.0 AND 1.0   │
           │                               │ access_count INT NN DF 0│
           │ vec0 virtual table            │ last_accessed_at TS     │
           ▼                               │ processed_at TS         │
  ┌─────────────────────────┐             │ deleted_at   TS         │
  │  content_chunks_vec      │             │ created_at   TS NN      │
  │  (sqlite-vec)            │             │ updated_at   TS NN      │
  ├─────────────────────────┤             └────────┬────────────────┘
  │ id        TEXT PK        │                      │
  │ embedding FLOAT[N]*    │                      │ vec0 virtual table
  └─────────────────────────┘                      ▼
                                           ┌─────────────────────────┐
  ┌─────────────────────────┐             │    memories_vec          │
  │  content_chunks_fts      │             │    (sqlite-vec)          │
  │  (FTS5)                  │             ├─────────────────────────┤
  ├─────────────────────────┤             │ id        TEXT PK        │
  │ id      TEXT UNINDEXED   │             │ embedding FLOAT[N]*    │
  │ text    TEXT             │             └─────────────────────────┘
  │ summary TEXT             │
  └─────────────────────────┘             ┌─────────────────────────┐
                                           │    memories_fts          │
       * Dimension N is user-              │    (FTS5)                │
         configurable per model            ├─────────────────────────┤
                                           │ id      TEXT UNINDEXED   │
                                           │ content TEXT             │
  ┌─────────────────────────┐             │ summary TEXT             │
  │   session_summaries      │             └─────────────────────────┘
  ├─────────────────────────┤
  │ id           TEXT PK     │             ┌─────────────────────────┐
  │ session_key  TEXT NN UQ  │             │   session_summaries_vec  │
  │ summary      TEXT NN     │             │   (sqlite-vec)           │
  │ key_decisions JSON CHK   │             ├─────────────────────────┤
  │ message_count INT NN DF 0│             │ id        TEXT PK        │
  │ embedding_model TEXT     │             │ embedding FLOAT[N]*    │
  │ processed_at TS          │             └─────────────────────────┘
  │ created_at   TS NN       │
  │ updated_at   TS NN       │             ┌─────────────────────────┐
  └─────────────────────────┘             │   session_summaries_fts  │
                                           │   (FTS5)                 │
                                           ├─────────────────────────┤
  ┌─────────────────────────┐             │ id      TEXT UNINDEXED   │
  │    memory_versions       │             │ summary TEXT             │
  │    (append-only)         │             │ key_decisions TEXT       │
  ├─────────────────────────┤             └─────────────────────────┘
  │ id           TEXT PK     │
  │ memory_id    TEXT FK ────┼────────────▶ memories.id
  │ version      INT NN      │
  │ previous_content TEXT    │
  │ new_content  TEXT NN     │
  │ previous_type TEXT       │
  │ new_type     TEXT NN     │
  │ changed_by   TEXT NN     │
  │  (agent/user/system)     │
  │ change_reason TEXT       │
  │ created_at   TS NN       │
  └─────────────────────────┘


  ┌─────────────────────────┐             ┌─────────────────────────┐
  │      entities            │             │   entities_vec           │
  ├─────────────────────────┤             │   (sqlite-vec)           │
  │ id           TEXT PK     │             ├─────────────────────────┤
  │ type         TEXT NN CHK │             │ id        TEXT PK        │
  │  ∊{person,project,task, │             │ embedding FLOAT[N]*    │
  │    decision,meeting,     │             └─────────────────────────┘
  │    document,event}       │
  │ name         TEXT NN     │
  │ metadata     JSON CHK    │
  │ embedding_model TEXT     │
  │ processed_at TS          │
  │ created_at   TS NN       │
  │ updated_at   TS NN       │
  └──────┬──────────────────┘
         │
         │ referenced by
         ▼
  ┌─────────────────────────┐
  │    relationships         │
  ├─────────────────────────┤
  │ id           TEXT PK     │
  │ source_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
  │ target_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
  │ type         TEXT NN     │
  │ weight       REAL NN CHK │
  │  BETWEEN 0.0 AND 1.0    │
  │ context      TEXT        │
  │ created_at   TS NN       │
  │ updated_at   TS NN       │
  ├─────────────────────────┤
  │ UQ(source_id,target_id, │
  │    type)                 │
  └─────────────────────────┘

  ┌─────────────────────────┐
  │   entity_mentions        │
  ├─────────────────────────┤
  │ id           TEXT PK     │
  │ entity_id    TEXT NN FK ─┼──▶ entities.id (CASCADE)
  │ source_type  TEXT NN     │
  │ source_id    TEXT NN     │
  │ created_at   TS NN       │
  └─────────────────────────┘


  ┌─────────────────────────┐             ┌─────────────────────────┐
  │      audit_log           │             │   embedding_configs      │
  ├─────────────────────────┤             ├─────────────────────────┤
  │ id           TEXT PK     │             │ model_name   TEXT PK     │
  │ timestamp    TS NN       │             │ family       TEXT NN     │
  │ command      TEXT NN     │             │ provider     TEXT NN     │
  │ actor        TEXT        │             │ max_dimension INT NN     │
  │ capability   TEXT        │             │ supported_dims JSON NN   │
  │ context      JSON CHK    │             │ supports_local BOOL NN   │
  │ result       TEXT        │             │ supports_api   BOOL NN   │
  │ duration_ms  INT         │             │ is_default   BOOL NN DF 0│
  │ created_at   TS NN       │             │ created_at   TS NN       │
  └─────────────────────────┘             └─────────────────────────┘
                                           ┌─────────────────────────┐
                                           │      settings (exists)   │
                                           ├─────────────────────────┤
                                           │ key        TEXT PK       │
                                           │ value      TEXT NN       │
                                           │ created_at TS NN         │
                                           │ updated_at TS NN         │
                                           └─────────────────────────┘
```

### Relationship Summary

```
content_chunks   1──vec──1   content_chunks_vec          (embedding storage)
content_chunks   1──fts──1   content_chunks_fts          (full-text index)
content_chunks   *──self──1  content_chunks.parent_id    (multi-chunk docs)
memories         1──vec──1   memories_vec                (embedding storage)
memories         1──fts──1   memories_fts                (full-text index)
memories         1──*        memory_versions             (immutable change log)
session_summaries 1──vec──1  session_summaries_vec       (embedding storage)
session_summaries 1──fts──1  session_summaries_fts       (full-text index)
entities         1──vec──1   entities_vec                (embedding storage)
entities         1──*        entity_mentions             (where entity appears)
entities         *──rel──*   entities                    (via relationships)
embedding_configs            (standalone reference table)
```

---

## 3. Design Decisions

### 3.1 IDs: ULIDs

All primary keys use [ULID](https://github.com/ulid/spec) — sortable, unique, no coordination needed. Stored as TEXT (26 chars). Time-sortable without a separate index, globally unique without auto-increment, portable across SQLite and PostgreSQL.

### 3.2 Separate Tables for content_chunks vs memories

These tables share structural similarity but serve different purposes:

- **`content_chunks`** — ephemeral indexed content from external sources (conversations, files, emails). May be pruned, re-chunked, or re-indexed. High volume.
- **`memories`** — curated knowledge the agent explicitly decided to persist. Has lifecycle management (importance, access tracking, soft delete, versioning). Low volume, high value.

The alternative (single unified table with a `kind` discriminator) was considered and rejected because:
- Memory-specific columns (importance, access_count, deleted_at, versioning) don't apply to content chunks.
- Query patterns differ: memories are filtered by type, scored by importance; content chunks are filtered by source.
- Separate tables allow different retention policies without complex WHERE clauses.

Shared search logic is extracted into a `SearchableMixin` base class to avoid duplicating vec/FTS/RRF code.

### 3.3 Embeddings: sqlite-vec with vec0 virtual tables

Each embeddable table gets a companion `_vec` virtual table using sqlite-vec's `vec0` module.

**Dimension management:** The `embedding_configs` table stores `(model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api)`. Vec0 virtual tables are **not** created in migrations — they are created at application startup by `ensure_vec_tables()` using the user's configured `embedding.output_dimension`. This allows the dimension to be user-configurable without migration changes. The `embedding_model` column on source tables tracks which model generated each embedding.

#### Supported Model Families

| Family | Models | Shared Space | Local | API | Supported Dims |
|--------|--------|-------------|-------|-----|----------------|
| **Voyage 4** | voyage-4-nano, voyage-4-lite, voyage-4, voyage-4-large | ✅ All compatible | nano | lite/standard/large | 256, 512, **1024**, 2048 |
| **Qwen3** | Qwen3-Embedding-0.6B, 4B, 8B | ❌ Each incompatible | All | — | 256, 512, **1024**, up to max |
| **Gemini** | gemini-embedding-001 | — | ❌ | API only | 768 (fixed) |

**One active model at a time.** Users select a single model; all embeddings use that model. The `embedding_configs` table is pre-seeded with all supported models so the UI can display them.

#### Smart Default Selection

1. If a Voyage AI API key is configured → default to `voyage-4-large` (best quality, shared space)
2. If no API key → default to `voyage-4-nano` (local, free, same embedding space as API models)
3. User can override in Settings UI at any time

#### Model Switching Rules

- **Within Voyage 4 family, same dimension** → seamless switch, no re-embedding needed (shared embedding space)
- **Same model, different dimension** → requires re-embedding (vec0 tables must be recreated at new dimension)
- **Between families** (e.g., Voyage → Qwen3) or **between Qwen3 sizes** → requires re-embedding:
  1. UI shows warning: "Switching model families requires re-embedding all data. During re-embedding, search uses keywords only."
  2. User confirms
  3. System drops and recreates all `_vec` virtual tables with new dimension
  4. Sets `processed_at = NULL` on all source rows
  5. Background worker re-embeds everything in batches
  6. During re-indexing, search falls back to FTS-only

#### Execution Mode

Each model has a preferred execution mode:
- **Local** — runs on-device via `sentence-transformers`. Free, private, requires GPU for good performance.
- **API** — calls provider's HTTP API. Requires API key, costs per token, faster for large batches.

For Voyage 4 models that support both (e.g., nano can run locally OR via API), the user chooses in settings.

#### Embedding Settings (stored in `settings` table)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `embedding.model` | string | (smart default) | Active model name |
| `embedding.execution_mode` | string | `auto` | `local`, `api`, or `auto` (prefer local if available) |
| `embedding.output_dimension` | int | 1024 | MRL truncation dimension (models that support it) |
| `embedding.api_key.voyage` | string | null | Voyage AI API key (encrypted at rest) |
| `embedding.api_key.gemini` | string | null | Google API key (encrypted at rest) |

These settings are managed through the Settings UI and stored in the `settings` table.

### 3.4 FTS: FTS5 virtual tables

Content tables with searchable text get a companion `_fts` FTS5 table. Kept in sync via triggers on INSERT/DELETE of the source table. FTS5 does not support UPDATE on content — triggers use DELETE + INSERT for updates.

### 3.5 Hybrid Search: Reciprocal Rank Fusion

Search queries run against sqlite-vec (semantic) and FTS5 (keyword), then merge using Reciprocal Rank Fusion (RRF):

```
score(doc) = Σ  1 / (k + rank_in_source)
```

Where `k = 60` (standard RRF constant). Final score is optionally boosted by recency:

```
recency_boost = 1.0 / (1.0 + days_since_created * decay_rate)
final_score = rrf_score * (1.0 + recency_weight * recency_boost)
```

Search runs across memories, content_chunks, and session_summaries in a single transaction to ensure consistency.

**Pre-filtering for query speed:** sqlite-vec performs brute-force scans (O(n) over all vectors in a table). At high volumes (e.g., 500K email chunks over years of use), unfiltered scans become slow (~20ms+ at 1024 dims). To keep queries fast regardless of data volume:

1. **Source-type scoping.** Callers can restrict search to specific `source_type` values (e.g., only `'email'`, only `'conversation'`). The FTS5 query is filtered first, producing a candidate set. Vector search then runs only against the candidate IDs, not the full vec0 table.

2. **Time-window filtering.** Callers can pass `since` / `until` timestamps. The candidate set is narrowed via indexed `created_at` columns before vector comparison.

3. **Two-phase search.** For large tables:
   - Phase 1: FTS5 + metadata filters → candidate ID set (fast, uses indexes)
   - Phase 2: Vector similarity scored only against candidates (via `WHERE id IN (...)` on vec0)
   - Phase 3: RRF merge of both result sets

This keeps vector scan proportional to the candidate set, not the total table size. For most personal assistant queries (recent context, specific source type), the candidate set stays under 10K even with millions of total vectors.

#### ANN Indexing (HNSW via usearch)

sqlite-vec uses brute-force scans — O(n) over all vectors. Pre-filtering keeps this manageable for most queries, but at high data volumes (500K+ vectors per table), even filtered scans can slow down. To handle this, the system supports optional HNSW (Hierarchical Navigable Small World) indexing via [usearch](https://github.com/unum-cloud/usearch) as a sidecar index.

**Architecture:**

```
                    ┌─────────────────────┐
  query embedding ──▶   VectorSearcher     │  (abstraction layer)
                    ├─────────────────────┤
                    │ strategy: auto       │
                    │  - brute_force       │  sqlite-vec vec0 (always available)
                    │  - hnsw              │  usearch index file (optional)
                    └──────┬──────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
     sqlite-vec (vec0)          usearch (.idx file)
     exact cosine sim           approximate NN (HNSW)
     O(n) scan                  O(log n) lookup
     always consistent          rebuilt async
```

**How it works:**

1. **Index files live alongside the SQLite database.** Each vec0 table gets an optional `.idx` sidecar file (e.g., `memories_vec.idx`). These are standard usearch HNSW index files.

2. **Index building is async.** When vector count in a table exceeds a configurable threshold (default: 50K), the system starts building an HNSW index in the background. During build, queries continue using brute-force. When the index is ready, queries switch to HNSW automatically.

3. **Index maintenance.** New vectors are added to both vec0 (immediately) and the HNSW index (batched, every 30s). The HNSW index is periodically compacted/rebuilt during idle time to maintain quality.

4. **Query routing:**
   - If HNSW index exists and is fresh → use HNSW (fast, approximate)
   - If HNSW index is stale (>1000 un-indexed vectors) → use brute-force on vec0
   - If no HNSW index → always brute-force on vec0
   - Pre-filters (source_type, time window) are applied as post-filters on HNSW results, with over-fetch to compensate (fetch 3×limit, filter, return top limit)

5. **Settings:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `search.hnsw_enabled` | bool | true | Enable HNSW index building when thresholds are met |
| `search.hnsw_threshold` | int | 50000 | Vector count per table before HNSW index is built |
| `search.hnsw_ef_construction` | int | 128 | HNSW build quality (higher = better recall, slower build) |
| `search.hnsw_ef_search` | int | 64 | HNSW search quality (higher = better recall, slower query) |
| `search.hnsw_m` | int | 16 | HNSW connectivity (higher = better recall, more memory) |

**Performance at scale:**

| Vectors | Brute-force (1024d) | HNSW (1024d) |
|---------|-------------------|-------------|
| 10K | <0.5ms | <0.1ms |
| 100K | ~5ms | <0.1ms |
| 500K | ~25ms | <0.2ms |
| 1M | ~50ms | <0.3ms |

**Why usearch over alternatives:**
- Pure Python bindings, no heavy C++ build deps (unlike FAISS)
- 10x faster than FAISS for index building
- Supports user-defined metrics, int8/binary quantization
- 3K SLOC — auditable, maintainable
- Apache 2.0 license
- Proven at scale (used by ClickHouse, DuckDB)

**Unprocessed items:** Items with `processed_at = NULL` (no embedding yet) are included in FTS results but excluded from vector results. They appear in merged results with `vector_score = None`, ensuring newly saved memories are immediately searchable via keywords.

### 3.6 SearchResult Contract

```python
@dataclass
class SearchResult:
    id: str
    content: str
    summary: str | None
    score: float              # final RRF score (with boosts)
    vector_score: float | None  # cosine similarity 0.0–1.0 (None if unprocessed)
    fts_score: float | None   # BM25 rank score (None if no keyword match)
    recency_boost: float      # 0.0–1.0
    source_table: str         # 'memories' | 'content_chunks' | 'session_summaries'
    source_type: str | None   # e.g. 'conversation', 'fact', 'file'
    metadata: dict
    sensitivity: str          # 'normal' | 'personal' | 'secret'
```

### 3.7 Repository Pattern with Protocols

Each domain gets a repository class implementing a formal protocol:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class SearchableRepository(Protocol):
    """Base protocol for repositories that support hybrid search."""
    async def search(
        self,
        query: str,
        embedding: list[float] | None,
        *,
        limit: int = 10,
        min_score: float = 0.0,
        sensitivity_max: str = "personal",
        source_types: list[str] | None = None,  # filter by source_type
        since: datetime | None = None,           # time-window lower bound
        until: datetime | None = None,           # time-window upper bound
    ) -> list[SearchResult]: ...

class MemoryRepository(SearchableRepository):
    async def save(self, memory: SaveMemoryInput) -> Memory
    async def get(self, id: str) -> Memory | None
    async def update(self, id: str, content: str, changed_by: str, reason: str) -> Memory
    async def soft_delete(self, id: str, changed_by: str, reason: str) -> bool
    async def search(...) -> list[SearchResult]
    async def update_access(self, id: str) -> None
    async def get_versions(self, memory_id: str) -> list[MemoryVersion]

class ContentRepository(SearchableRepository):
    async def save_chunk(self, chunk: SaveChunkInput) -> ContentChunk
    async def search(...) -> list[SearchResult]
    async def get_unprocessed(self, batch_size: int = 32) -> list[ContentChunk]
    async def mark_processed(self, ids: list[str], model: str) -> None

class SessionSummaryRepository(SearchableRepository):
    async def save(self, summary: SaveSummaryInput) -> SessionSummary
    async def get_by_session(self, session_key: str) -> SessionSummary | None
    async def search(...) -> list[SearchResult]
```

Repositories accept an `AsyncSession` and are injected via the mediator pipeline.

### 3.8 Embedding Pipeline

**Dual strategy:**

| Content Type | Embedding Strategy | Rationale |
|---|---|---|
| Memories | **Synchronous** on save | Small, high-value, must be immediately searchable by vector |
| Content chunks | **Async background worker** | High volume, batch-efficient, slight delay acceptable |
| Session summaries | **Async background worker** | Generated periodically, not urgent |

Background worker:
```
Loop every 5 seconds:
  Pick up rows WHERE processed_at IS NULL (batch of 32)
  Generate embeddings (local or API)
  Insert into _vec table
  Set processed_at + embedding_model
  On failure: log error, skip item, retry next cycle
```

**Retry & resilience:**
- Failed embeddings are retried up to 3 times with exponential backoff.
- If the embedding provider is unreachable, worker logs a warning every 60s and continues retrying.
- Items that fail 3 times get `metadata.embedding_error` set and are skipped until manually cleared.

### 3.9 Memory Types

| Type | Description | Example |
|------|-------------|---------|
| `fact` | Objective information about the user or world | "User's timezone is EST" |
| `solution` | Working approach to a problem | "To fix X, do Y then Z" |
| `instruction` | User-stated preference or directive | "Always use TypeScript for new projects" |
| `preference` | Inferred behavioral preference | "User prefers concise answers" |

### 3.10 Memory Lifecycle

```
  CREATE ──▶ version 1 logged in memory_versions
    │
  RETRIEVE ──▶ access_count++, last_accessed_at updated
    │
  UPDATE ──▶ new version appended to memory_versions
    │         previous content preserved
    │         changed_by + reason recorded
    │
  SOFT DELETE ──▶ deleted_at set, version logged
    │              excluded from search by default
    │              data preserved for audit
    │
  (never hard-deleted except by explicit admin action)
```

### 3.11 Memory Deduplication

On save, the system checks for near-duplicate memories (cosine similarity > 0.92 against existing memories of the same type). If a near-duplicate is found:
- The save is **not blocked**.
- The agent receives a warning: `"Similar memory exists (id=X, similarity=0.95): <content>. Saved as new memory Y. Consider updating X instead."`
- This keeps the system non-blocking while surfacing potential conflicts.

### 3.12 Sensitivity Classification

| Level | Description | Storage | Search Visibility |
|-------|-------------|---------|-------------------|
| `normal` | General knowledge | Plaintext | Always visible |
| `personal` | PII, personal details | Plaintext | Excluded from logs, filtered in low-trust contexts |
| `secret` | API keys, passwords, credentials | Encrypted at rest via vault key | Only returned when explicitly requested |

`secret`-level content is encrypted using the existing vault's Fernet key before storage. The `text`/`content` column stores the ciphertext. Decryption happens in the repository layer on read.

### 3.13 Concurrency Model

Bond is single-user, local-first. The concurrency model is:

- **Single writer, multiple readers** — SQLite WAL mode.
- **Writes** use `BEGIN IMMEDIATE` transactions to fail-fast on contention rather than wait.
- **Search** runs within a single `DEFERRED` transaction (default) to ensure consistency between the vec query, FTS query, and metadata lookup.
- **Background embedding worker** uses its own connection with `BEGIN IMMEDIATE` for writes.
- If write contention occurs (SQLITE_BUSY), retry up to 3 times with 50ms backoff.

---

## 4. Migrations (golang-migrate)

Migrations use [golang-migrate](https://github.com/golang-migrate/migrate) running in a Docker container. This is the canonical migration tool per the architecture docs — it supports both SQLite and PostgreSQL, ensuring the same migration files work regardless of backend.

### Migration Runner

**Docker Compose service:**

```yaml
# Added to docker-compose.yml and docker-compose.dev.yml
migrate:
  image: migrate/migrate:v4.17.0
  volumes:
    - ./migrations:/migrations
    - bond-data:/home/bond/.bond
  entrypoint: >
    migrate
    -path=/migrations
    -database="sqlite3:///home/bond/.bond/data/knowledge.db"
  profiles:
    - tools
```

**Makefile targets:**

```makefile
# Run migrations up
migrate-up:
	docker compose run --rm migrate up

# Run migrations down one step
migrate-down:
	docker compose run --rm migrate down 1

# Create a new migration
migrate-create:
	docker compose run --rm migrate create -ext sql -dir /migrations -seq $(name)

# Show current migration version
migrate-version:
	docker compose run --rm migrate version

# Force a specific version (for fixing dirty state)
migrate-force:
	docker compose run --rm migrate force $(version)
```

**CLI integration** (for non-Docker local dev):

```python
# bond migrate up / bond migrate down / bond migrate version
# Falls back to running golang-migrate binary if installed,
# or pulls the Docker image automatically.
```

**Startup behavior:** The backend checks migration version on startup. If behind, it prints a warning and refuses to start. Migrations are never auto-applied in production — always explicit.

### Migration 000002: Knowledge Store + Memory

**Up:**

```sql
-- ============================================================
-- Migration 000002: Knowledge Store + Persistent Memory
-- ============================================================

-- Enforce foreign keys (idempotent, must be set per-connection)
PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------
-- Embedding configuration reference table
-- -----------------------------------------------------------
CREATE TABLE embedding_configs (
    model_name TEXT PRIMARY KEY,
    family TEXT NOT NULL,              -- 'voyage4', 'qwen3', 'gemini'
    provider TEXT NOT NULL,            -- 'voyage', 'huggingface', 'google'
    max_dimension INTEGER NOT NULL,
    supported_dimensions TEXT NOT NULL, -- JSON array, e.g. '[256,512,1024,2048]'
    supports_local INTEGER NOT NULL DEFAULT 0,
    supports_api INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Seed all supported embedding models
-- Voyage 4 family (shared embedding space — all interchangeable)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('voyage-4-nano',  'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 1, 1, 1),
    ('voyage-4-lite',  'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0),
    ('voyage-4',       'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0),
    ('voyage-4-large', 'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0);

-- Qwen3 family (each model has its own embedding space, MRL supports any dim up to max)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('Qwen3-Embedding-0.6B', 'qwen3', 'huggingface', 1024, '[256,512,1024]',      1, 0, 0),
    ('Qwen3-Embedding-4B',   'qwen3', 'huggingface', 2560, '[256,512,1024,2560]',  1, 0, 0),
    ('Qwen3-Embedding-8B',   'qwen3', 'huggingface', 4096, '[256,512,1024,4096]',  1, 0, 0);

-- Gemini family (fixed dimension)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('gemini-embedding-001', 'gemini', 'google', 768, '[768]', 0, 1, 0);

-- -----------------------------------------------------------
-- content_chunks: indexed content from any source
-- -----------------------------------------------------------
CREATE TABLE content_chunks (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,                -- 'conversation', 'file', 'email', 'web'
    source_id TEXT,                            -- FK to source (session_key, email_id, etc.)
    text TEXT NOT NULL,
    summary TEXT,
    chunk_index INTEGER NOT NULL DEFAULT 0,   -- position within multi-chunk document
    parent_id TEXT REFERENCES content_chunks(id) ON DELETE SET NULL,
    sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK(sensitivity IN ('normal', 'personal', 'secret')),
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_cc_source ON content_chunks(source_type, source_id);
CREATE INDEX idx_cc_parent ON content_chunks(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_cc_unprocessed ON content_chunks(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_cc_sensitivity ON content_chunks(sensitivity);

CREATE TRIGGER content_chunks_updated_at
    AFTER UPDATE ON content_chunks FOR EACH ROW
BEGIN
    UPDATE content_chunks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- NOTE: vec0 virtual tables (content_chunks_vec, memories_vec, session_summaries_vec,
-- entities_vec) are NOT created in migrations. They are created at application startup
-- by ensure_vec_tables() using the user's configured embedding.output_dimension.
-- Example: CREATE VIRTUAL TABLE content_chunks_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[N])
-- where N = embedding.output_dimension from settings (default 1024).

-- FTS5 index for content_chunks
CREATE VIRTUAL TABLE content_chunks_fts USING fts5(
    id UNINDEXED,
    text,
    summary
);

-- FTS sync triggers (FTS5 updates require DELETE + INSERT)
CREATE TRIGGER cc_fts_insert AFTER INSERT ON content_chunks BEGIN
    INSERT INTO content_chunks_fts(id, text, summary)
    VALUES (NEW.id, NEW.text, NEW.summary);
END;

CREATE TRIGGER cc_fts_update AFTER UPDATE OF text, summary ON content_chunks BEGIN
    DELETE FROM content_chunks_fts WHERE id = OLD.id;
    INSERT INTO content_chunks_fts(id, text, summary)
    VALUES (NEW.id, NEW.text, NEW.summary);
END;

CREATE TRIGGER cc_fts_delete AFTER DELETE ON content_chunks BEGIN
    DELETE FROM content_chunks_fts WHERE id = OLD.id;
END;

-- -----------------------------------------------------------
-- memories: persistent facts, solutions, instructions
-- -----------------------------------------------------------
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('fact', 'solution', 'instruction', 'preference')),
    content TEXT NOT NULL,
    summary TEXT,
    source_type TEXT,                          -- 'conversation', 'user_explicit', 'extraction'
    source_id TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK(sensitivity IN ('normal', 'personal', 'secret')),
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    importance REAL NOT NULL DEFAULT 0.5
        CHECK(importance BETWEEN 0.0 AND 1.0),
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMP,
    processed_at TIMESTAMP,
    deleted_at TIMESTAMP,                      -- soft delete
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_mem_type ON memories(type);
CREATE INDEX idx_mem_unprocessed ON memories(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_mem_active ON memories(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_mem_sensitivity ON memories(sensitivity);
CREATE INDEX idx_mem_importance ON memories(importance DESC);

CREATE TRIGGER memories_updated_at
    AFTER UPDATE ON memories FOR EACH ROW
BEGIN
    UPDATE memories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for memories: created at runtime by ensure_vec_tables()

-- FTS5 index for memories
CREATE VIRTUAL TABLE memories_fts USING fts5(
    id UNINDEXED,
    content,
    summary
);

-- FTS sync triggers
CREATE TRIGGER mem_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER mem_fts_update AFTER UPDATE OF content, summary ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER mem_fts_delete AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
END;

-- -----------------------------------------------------------
-- memory_versions: immutable change log (append-only)
-- -----------------------------------------------------------
CREATE TABLE memory_versions (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    previous_content TEXT,            -- NULL for version 1 (creation)
    new_content TEXT NOT NULL,
    previous_type TEXT,               -- NULL for version 1
    new_type TEXT NOT NULL,
    changed_by TEXT NOT NULL,         -- 'agent', 'user', 'system'
    change_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_mv_memory ON memory_versions(memory_id, version);

-- -----------------------------------------------------------
-- session_summaries: compressed conversation history
-- -----------------------------------------------------------
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    key_decisions JSON DEFAULT '[]' CHECK(json_valid(key_decisions)),
    message_count INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_ss_key ON session_summaries(session_key);
CREATE INDEX idx_ss_unprocessed ON session_summaries(processed_at) WHERE processed_at IS NULL;

CREATE TRIGGER session_summaries_updated_at
    AFTER UPDATE ON session_summaries FOR EACH ROW
BEGIN
    UPDATE session_summaries SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for session_summaries: created at runtime by ensure_vec_tables()

-- FTS5 index for session_summaries
CREATE VIRTUAL TABLE session_summaries_fts USING fts5(
    id UNINDEXED,
    summary,
    key_decisions
);

CREATE TRIGGER ss_fts_insert AFTER INSERT ON session_summaries BEGIN
    INSERT INTO session_summaries_fts(id, summary, key_decisions)
    VALUES (NEW.id, NEW.summary, NEW.key_decisions);
END;

CREATE TRIGGER ss_fts_update AFTER UPDATE OF summary, key_decisions ON session_summaries BEGIN
    DELETE FROM session_summaries_fts WHERE id = OLD.id;
    INSERT INTO session_summaries_fts(id, summary, key_decisions)
    VALUES (NEW.id, NEW.summary, NEW.key_decisions);
END;

CREATE TRIGGER ss_fts_delete AFTER DELETE ON session_summaries BEGIN
    DELETE FROM session_summaries_fts WHERE id = OLD.id;
END;
```

**Down:**

```sql
-- Drop in reverse order of creation
DROP TRIGGER IF EXISTS ss_fts_delete;
DROP TRIGGER IF EXISTS ss_fts_update;
DROP TRIGGER IF EXISTS ss_fts_insert;
DROP TRIGGER IF EXISTS session_summaries_updated_at;
DROP TABLE IF EXISTS session_summaries_fts;
DROP TABLE IF EXISTS session_summaries_vec;
DROP TABLE IF EXISTS session_summaries;

DROP TABLE IF EXISTS memory_versions;

DROP TRIGGER IF EXISTS mem_fts_delete;
DROP TRIGGER IF EXISTS mem_fts_update;
DROP TRIGGER IF EXISTS mem_fts_insert;
DROP TRIGGER IF EXISTS memories_updated_at;
DROP TABLE IF EXISTS memories_fts;
DROP TABLE IF EXISTS memories_vec;
DROP TABLE IF EXISTS memories;

DROP TRIGGER IF EXISTS cc_fts_delete;
DROP TRIGGER IF EXISTS cc_fts_update;
DROP TRIGGER IF EXISTS cc_fts_insert;
DROP TRIGGER IF EXISTS content_chunks_updated_at;
DROP TABLE IF EXISTS content_chunks_fts;
DROP TABLE IF EXISTS content_chunks_vec;
DROP TABLE IF EXISTS content_chunks;

DROP TABLE IF EXISTS embedding_configs;
```

### Migration 000003: Entity Graph

**Up:**

```sql
-- ============================================================
-- Migration 000003: Entity Graph
-- ============================================================

PRAGMA foreign_keys = ON;

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN (
        'person', 'project', 'task', 'decision', 'meeting', 'document', 'event'
    )),
    name TEXT NOT NULL,
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_ent_type ON entities(type);
CREATE INDEX idx_ent_name ON entities(name);
CREATE INDEX idx_ent_unprocessed ON entities(processed_at) WHERE processed_at IS NULL;

CREATE TRIGGER entities_updated_at
    AFTER UPDATE ON entities FOR EACH ROW
BEGIN
    UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for entities: created at runtime by ensure_vec_tables()

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
        CHECK(weight BETWEEN 0.0 AND 1.0),
    context TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_rel_source ON relationships(source_id);
CREATE INDEX idx_rel_target ON relationships(target_id);
CREATE INDEX idx_rel_type ON relationships(type);
CREATE UNIQUE INDEX idx_rel_unique ON relationships(source_id, target_id, type);

CREATE TRIGGER relationships_updated_at
    AFTER UPDATE ON relationships FOR EACH ROW
BEGIN
    UPDATE relationships SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE entity_mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_em_entity ON entity_mentions(entity_id);
CREATE INDEX idx_em_source ON entity_mentions(source_type, source_id);
```

**Down:**

```sql
DROP TABLE IF EXISTS entity_mentions;
DROP TRIGGER IF EXISTS relationships_updated_at;
DROP TABLE IF EXISTS relationships;
DROP TABLE IF EXISTS entities_vec;
DROP TRIGGER IF EXISTS entities_updated_at;
DROP TABLE IF EXISTS entities;
```

### Migration 000004: Audit Log

**Up:**

```sql
-- ============================================================
-- Migration 000004: Audit Log
-- ============================================================

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    command TEXT NOT NULL,
    actor TEXT,
    capability TEXT,
    context JSON DEFAULT '{}' CHECK(json_valid(context)),
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_al_timestamp ON audit_log(timestamp);
CREATE INDEX idx_al_command ON audit_log(command);
CREATE INDEX idx_al_actor ON audit_log(actor) WHERE actor IS NOT NULL;
```

**Down:**

```sql
DROP TABLE IF EXISTS audit_log;
```

### Migration Notes

- **Virtual tables (vec0, FTS5) cannot be ALTERed.** Future schema changes require DROP + CREATE. Down migrations for these are destructive — vector data must be re-embedded from source. This is acceptable because source data is in the main tables.
- **PRAGMA foreign_keys = ON** is per-connection, not persistent. It's set in each migration for safety, and also in the application's DB session setup.
- **Migrations are never auto-applied.** The backend checks the current version on startup and refuses to start if migrations are pending. Explicit `make migrate-up` or `bond migrate up` required.
- **Testing:** Migrations are tested in CI via: `migrate up` → verify schema → `migrate down` → verify clean → `migrate up` again.

---

## 5. Agent Tools

Three tools added to the agent's tool set:

### `memory_save`
```json
{
  "name": "memory_save",
  "description": "Save a fact, solution, instruction, or preference for future recall. Check for similar memories first to avoid duplicates.",
  "parameters": {
    "type": "object",
    "required": ["type", "content"],
    "properties": {
      "type": {
        "type": "string",
        "enum": ["fact", "solution", "instruction", "preference"],
        "description": "Category of memory"
      },
      "content": {
        "type": "string",
        "description": "The memory content to save"
      },
      "importance": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "default": 0.5,
        "description": "How important this memory is (0.0-1.0)"
      },
      "sensitivity": {
        "type": "string",
        "enum": ["normal", "personal", "secret"],
        "default": "normal",
        "description": "Privacy classification"
      }
    }
  }
}
```

### `memory_update`
```json
{
  "name": "memory_update",
  "description": "Update an existing memory with corrected or refined content. Preserves change history.",
  "parameters": {
    "type": "object",
    "required": ["memory_id", "content", "reason"],
    "properties": {
      "memory_id": {
        "type": "string",
        "description": "ID of the memory to update"
      },
      "content": {
        "type": "string",
        "description": "Updated memory content"
      },
      "reason": {
        "type": "string",
        "description": "Why this memory is being updated"
      }
    }
  }
}
```

### `search_memory`
```json
{
  "name": "search_memory",
  "description": "Search memories and knowledge for relevant context. Returns scored results from memories, content, and session summaries.",
  "parameters": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "string",
        "description": "What to search for"
      },
      "types": {
        "type": "array",
        "items": { "type": "string", "enum": ["fact", "solution", "instruction", "preference"] },
        "description": "Filter memory results by type (optional)"
      },
      "sources": {
        "type": "array",
        "items": { "type": "string", "enum": ["memories", "content", "sessions"] },
        "default": ["memories", "content", "sessions"],
        "description": "Which knowledge stores to search"
      },
      "limit": {
        "type": "integer",
        "default": 10,
        "minimum": 1,
        "maximum": 50
      }
    }
  }
}
```

---

## 6. Graceful Degradation

| Component Missing | Behavior | Search Impact |
|---|---|---|
| **sqlite-vec extension** | Logged as WARNING on startup. Vec tables not created. `processed_at` columns unused. | FTS-only search. No semantic matching. |
| **Embedding provider** (sentence-transformers not installed, no API key) | Logged as WARNING. Background worker disabled. Memories saved without embedding. | FTS-only search. Items accumulate in unprocessed queue. |
| **FTS5** (highly unlikely — built into SQLite) | Fatal error on migration. | System cannot start. |

Extension availability is checked once at startup and cached:

```python
class KnowledgeStoreCapabilities:
    has_vec: bool         # sqlite-vec loaded successfully
    has_embeddings: bool  # at least one embedding provider available
    has_usearch: bool     # usearch library available for HNSW indexing
    vec_dimension: int    # from user's embedding.output_dimension setting
    hnsw_tables: set[str] # tables with active HNSW indexes
```

Repository methods check capabilities before attempting vec operations.

---

## 7. Module Structure

```
backend/app/
├── foundations/
│   ├── knowledge/
│   │   ├── __init__.py
│   │   ├── capabilities.py        # KnowledgeStoreCapabilities (startup checks)
│   │   ├── repository.py          # ContentRepository
│   │   ├── search.py              # HybridSearch (RRF merger), SearchResult
│   │   ├── searchable.py          # SearchableMixin (shared vec/FTS/RRF logic)
│   │   ├── vector.py              # VectorSearcher abstraction (brute-force + HNSW routing)
│   │   ├── vec_brute.py           # sqlite-vec brute-force operations (with graceful fallback)
│   │   ├── vec_hnsw.py            # usearch HNSW sidecar index (build, query, maintain)
│   │   └── fts.py                 # FTS5 operations
│   └── embeddings/
│       ├── __init__.py
│       ├── engine.py              # EmbeddingEngine (provider abstraction + smart default)
│       ├── local.py               # Local provider (sentence-transformers, voyage-4-nano, Qwen3)
│       ├── voyage.py              # Voyage AI API provider (voyage-4-lite/standard/large)
│       └── gemini.py              # Google Gemini API provider
├── features/
│   └── memory/
│       ├── __init__.py
│       ├── repository.py          # MemoryRepository (extends SearchableMixin)
│       ├── commands.py            # SaveMemory, UpdateMemory, SearchMemory, DeleteMemory
│       └── handlers.py            # Command handlers
├── agent/
│   └── tools/
│       ├── __init__.py
│       ├── memory_save.py         # memory_save tool
│       ├── memory_update.py       # memory_update tool
│       └── search_memory.py       # search_memory tool
└── workers/
    └── embedding_worker.py        # Background embedding processor (with retry)
```

---

## 8. Observability

### Structured Logging

All repository operations emit structured log events:

```python
logger.info("memory.saved", extra={
    "memory_id": id, "type": type, "sensitivity": sensitivity,
    "content_length": len(content), "source_type": source_type
})
logger.info("search.completed", extra={
    "query_length": len(query), "sources": sources,
    "result_count": len(results), "duration_ms": duration,
    "had_vector": had_vector, "had_fts": had_fts
})
```

### System Metrics (via audit_log)

The audit_log table doubles as a metrics source. Key queries:

```sql
-- Memory count by type
SELECT type, COUNT(*) FROM memories WHERE deleted_at IS NULL GROUP BY type;

-- Embedding queue depth
SELECT COUNT(*) FROM memories WHERE processed_at IS NULL AND deleted_at IS NULL;
SELECT COUNT(*) FROM content_chunks WHERE processed_at IS NULL;

-- Search latency (from audit_log)
SELECT AVG(duration_ms), P95(duration_ms) FROM audit_log
WHERE command = 'SearchMemory' AND timestamp > datetime('now', '-1 hour');
```

A future `bond status` CLI command will surface these.

---

## 9. Capacity Planning

| Table | Expected Volume (1 year) | Performance Notes |
|---|---|---|
| `memories` | 1K–10K rows | sqlite-vec performs well up to ~100K vectors at 1024 dims |
| `content_chunks` | 10K–100K rows | Needs archival strategy beyond 100K |
| `session_summaries` | 1K–5K rows | One per conversation session |
| `entities` | 1K–10K rows | Grows with memory/email extraction |
| `relationships` | 5K–50K rows | Relational index handles this fine |
| `audit_log` | 100K+ rows | Append-only; archive after 90 days |
| `memory_versions` | 2K–20K rows | ~2 versions per memory average |

### Archival Strategy

| Data | Retention | Archival |
|---|---|---|
| `memories` | Never auto-pruned | Soft-deleted items reviewed quarterly |
| `content_chunks` | Active | Chunks older than 90 days with 0 search hits archived to `knowledge_archive.db` |
| `session_summaries` | Active | Summaries older than 1 year archived |
| `audit_log` | 90 days active | Older rows moved to `audit_archive.db` |
| `memory_versions` | Forever | Append-only, never pruned |

### FTS5 Maintenance

`OPTIMIZE` should be run periodically:

```sql
-- Run weekly via bond maintenance command
INSERT INTO content_chunks_fts(content_chunks_fts) VALUES('optimize');
INSERT INTO memories_fts(memories_fts) VALUES('optimize');
INSERT INTO session_summaries_fts(session_summaries_fts) VALUES('optimize');
```

---

## 10. Test Matrix

### Unit Tests

| Module | Tests | Coverage |
|---|---|---|
| `MemoryRepository` | save, get, update, soft_delete, search, update_access, get_versions | All methods + edge cases |
| `ContentRepository` | save_chunk, search, get_unprocessed, mark_processed | All methods |
| `SessionSummaryRepository` | save, get_by_session, search | All methods |
| `HybridSearch` | RRF merge, recency boost, empty results, single-source results | Score calculation correctness |
| `SearchableMixin` | vec search, FTS search, combined, graceful degradation (no vec) | Both paths |
| `EmbeddingEngine` | local provider, Voyage API, Gemini API, smart default, fallback when unavailable | Provider abstraction |
| `KnowledgeStoreCapabilities` | detect vec, detect no vec, detect embeddings | Startup checks |

### Integration Tests

| Scenario | What It Tests |
|---|---|
| Save memory → embed (sync) → search by semantics | Full memory pipeline end-to-end |
| Save memory → search by keyword (before embedding) | FTS fallback for unprocessed items |
| Save → update → get_versions | Version history integrity |
| Save → soft_delete → search (not found) → search(include_deleted=True) (found) | Soft delete behavior |
| Save secret memory → search → verify encrypted at rest | Sensitivity handling |
| Near-duplicate save → warning returned | Dedup detection |
| Search across memories + content + sessions | Cross-source RRF merge |
| Disable sqlite-vec → save → search (FTS only) | Graceful degradation |

### Migration Tests (CI)

```bash
# Run in CI against empty database
migrate -path=./migrations -database="sqlite3:///tmp/test.db" up
migrate -path=./migrations -database="sqlite3:///tmp/test.db" down
migrate -path=./migrations -database="sqlite3:///tmp/test.db" up
# Verify schema matches expected
```

### Performance Benchmarks

| Benchmark | Target | Measured At |
|---|---|---|
| Memory save (with sync embedding) | < 500ms | 1K existing memories |
| Hybrid search (vec + FTS + merge) | < 200ms | 10K content chunks, 1K memories |
| Hybrid search (FTS only, no vec) | < 100ms | 10K content chunks |
| Embedding batch (32 items, local) | < 5s | voyage-4-nano on GPU |

---

## 11. Dependencies

| Package | Purpose | Required | Size |
|---------|---------|----------|------|
| `sqlite-vec` | Vector similarity search | Optional (graceful degradation) | ~2MB |
| `python-ulid` | ULID generation | Required | tiny |
| `sentence-transformers` | Local embeddings | Optional (`[embeddings]` extra) | ~100MB + model |

```toml
# pyproject.toml additions
dependencies = [
    # ... existing ...
    "sqlite-vec>=0.1.0",
    "python-ulid>=2.0.0",
]

[project.optional-dependencies]
embeddings = [
    "sentence-transformers>=3.0.0",
]
```

---

## 12. What's NOT in Scope

- Email tables (Phase 3)
- Task tables (Phase 3)
- Capability policies table (built with Capability Gate)
- Behavior model tables (later Phase 2)
- Background workers for session summarization (separate design)
- Blob store (built when email attachments need it)
- Entity extraction logic (tables created now, logic deferred)

---

## 13. Implementation Order

1. **Docker + Makefile** — Add golang-migrate service, migration targets
2. **Migrations** — 000002, 000003, 000004 with up + down files
3. **Capabilities check** — Detect sqlite-vec, embedding providers at startup
4. **Embeddings engine** — Provider abstraction, local (voyage-4-nano/Qwen3), Voyage API, Gemini API, smart defaults
5. **SearchableMixin** — Shared vec + FTS + RRF logic with graceful degradation
6. **Content repository** — CRUD + search
7. **Memory repository** — CRUD + versioning + soft delete + dedup check + search
8. **Session summary repository** — CRUD + search
9. **Mediator commands** — SaveMemory, UpdateMemory, DeleteMemory, SearchMemory
10. **Agent tools** — memory_save, memory_update, search_memory
11. **RAG integration** — Auto-search before each agent turn
12. **Embedding worker** — Background processor with retry
13. **Tests** — Full test matrix (unit + integration + migration + benchmarks)

---

## Questions for Review

1. **Entity graph logic now or later?** Tables are created in migration 000003. Should we implement the entity repository + extraction pipeline now, or defer to a separate design doc?

2. **Archival implementation:** The archival strategy describes moving old data to separate `.db` files. Should this be a `bond archive` CLI command, a scheduled worker, or both?

3. **`bond reindex` scope:** Should reindexing be per-table or all-at-once? Per-table is more flexible but more CLI surface area.
