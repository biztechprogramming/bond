# C3: Local Memory Store — Enterprise Grade Tasks

Story: Design Doc 008, C3 — Local memory store in agent DB (search + save, both DBs)

## Context

The agent worker (`backend/app/worker.py`) runs inside a container with its own aiosqlite database. Native tool handlers in `backend/app/agent/tools/native.py` provide memory_save and search_memory. These currently work at a prototype level but need to match the enterprise-grade standards of the host-side memory system.

**Key reference files:**
- Host-side memory repo: `backend/app/features/memory/repository.py`
- Host-side memory models: `backend/app/features/memory/models.py`
- Host-side search: `backend/app/foundations/knowledge/search.py`
- Host-side migration: `migrations/000002_knowledge_store.up.sql`
- Current native handlers: `backend/app/agent/tools/native.py`
- Current worker: `backend/app/worker.py`
- Current tests: `backend/tests/test_worker.py`
- Native registry: `backend/app/agent/tools/native_registry.py`

---

## Task 1: Align Agent DB Schema

**File:** `backend/app/worker.py` (`_AGENT_DB_SCHEMA`)

Update the `memories` table to include all columns from the host migration:
- `summary TEXT`
- `source_type TEXT`
- `source_id TEXT`
- `sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK(sensitivity IN ('normal','personal','secret'))`
- `metadata JSON DEFAULT '{}' CHECK(json_valid(metadata))`
- `importance REAL NOT NULL DEFAULT 0.5 CHECK(importance BETWEEN 0.0 AND 1.0)`
- `access_count INTEGER NOT NULL DEFAULT 0`
- `last_accessed_at TIMESTAMP`
- `embedding_model TEXT`
- `processed_at TIMESTAMP`
- `deleted_at TIMESTAMP`

Add `memory_versions` table (matching host `migrations/000002`):
- id, memory_id, version, previous_content, new_content, previous_type, new_type, changed_by, change_reason, created_at
- Index on (memory_id, version)

Add `entities` table for agent-local working context (per design doc §9.2):
- id TEXT PRIMARY KEY
- name TEXT NOT NULL
- entity_type TEXT NOT NULL
- attributes TEXT (JSON)
- created_at TEXT NOT NULL

Keep `content_chunks` and `content_chunks_fts` as-is.

**Acceptance criteria:**
- Schema matches host parity (minus vec0 which is runtime)
- All columns have proper constraints and defaults
- Existing tests still pass after schema change

---

## Task 2: Fix FTS with Sync Triggers

**File:** `backend/app/worker.py` (`_AGENT_DB_SCHEMA`)

The current FTS setup uses `content='memories', content_rowid='rowid'` (content-table binding) but then does manual INSERT in the handler. This is contradictory and will break on UPDATE/DELETE.

Fix by adding proper sync triggers matching the host pattern:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    id UNINDEXED,
    content,
    summary
);

CREATE TRIGGER IF NOT EXISTS mem_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_update AFTER UPDATE OF content, summary ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS mem_fts_delete AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
END;
```

Remove the manual FTS INSERT from `handle_memory_save` in `native.py` — triggers handle it now.

**Acceptance criteria:**
- FTS auto-syncs on INSERT, UPDATE, DELETE
- FTS indexes both content AND summary
- No manual FTS manipulation in handler code
- Test: save → search finds it. Update → search finds new content, not old. Delete → search doesn't find it.

---

## Task 3: Transaction Safety

**File:** `backend/app/agent/tools/native.py`

Wrap memory operations in proper transaction handling:

```python
try:
    # ... inserts ...
    await agent_db.commit()
except Exception as e:
    await agent_db.rollback()
    logger.warning("memory_save failed: %s", e, exc_info=True)
    return {"error": str(e)}
```

Apply to: `handle_memory_save`, `handle_memory_update` (new), `handle_memory_delete` (new).

**Acceptance criteria:**
- All write operations rollback on failure
- No partial state possible
- Errors are logged with stack traces

---

## Task 4: Implement `handle_memory_update`

**File:** `backend/app/agent/tools/native.py`

New handler matching host-side `MemoryRepository.update()`:

1. Fetch current memory by ID (must exist, must not be deleted)
2. Get current max version number
3. UPDATE memories SET content, updated_at
4. INSERT into memory_versions (previous_content, new_content, changed_by, change_reason)
5. Return `{"status": "updated", "memory_id": ..., "version": ...}`

If memory not found or deleted, return `{"error": "Memory not found: {id}"}`.

**Acceptance criteria:**
- Updates content and creates version record
- Rejects updates to nonexistent or soft-deleted memories
- FTS auto-updates via trigger (Task 2)
- Proper transaction rollback on failure

---

## Task 5: Implement `handle_memory_delete`

**File:** `backend/app/agent/tools/native.py`

Soft delete matching host-side `MemoryRepository.soft_delete()`:

1. SET deleted_at = now WHERE id = :id AND deleted_at IS NULL
2. If rowcount == 0, return `{"error": "Memory not found or already deleted"}`
3. Record deletion in memory_versions (new_content = "[deleted]")
4. Return `{"status": "deleted", "memory_id": ...}`

**Acceptance criteria:**
- Soft delete only (never hard delete)
- Creates version record for audit trail
- Deleted memories excluded from search (Task 7)
- Idempotent — deleting twice returns error, not crash

---

## Task 6: Input Validation

**File:** `backend/app/agent/tools/native.py`

Add validation to all memory handlers:

- `memory_save`: require `content` (non-empty string). Validate `memory_type` against allowed types. Validate `sensitivity` if provided. Validate `importance` range 0.0–1.0 if provided.
- `memory_update`: require `memory_id` (non-empty) and `content` (non-empty).
- `memory_delete`: require `memory_id` (non-empty).
- `search_memory`: require `query` (non-empty). Validate `limit` is positive int. Validate `memory_types` is list of strings if provided.

Return clear error messages: `{"error": "content is required and must be non-empty"}`.

**Acceptance criteria:**
- All required fields validated before DB access
- Clear, actionable error messages
- Bad input never reaches the database

---

## Task 7: Enhance `search_memory`

**File:** `backend/app/agent/tools/native.py`

Upgrade to match host-side search quality:

1. **Search summary + content** — FTS query should match both fields (handled by Task 2 FTS change)
2. **Type filtering** — accept `memory_types: list[str]` param, add `AND m.type IN (...)` clause
3. **Time filtering** — accept `since` and `until` params
4. **Exclude soft-deleted** — add `AND m.deleted_at IS NULL` to JOIN
5. **Recency boost** — port `_recency_boost()` from `search.py` (exponential decay, 30-day half-life, max 0.01 boost)
6. **Access tracking** — after returning results, update `access_count` and `last_accessed_at` for returned memory IDs

Apply same enhancements to shared.db query (but skip access tracking on shared — it's read-only).

**Acceptance criteria:**
- Filters work correctly (type, time range)
- Deleted memories never appear in results
- Recency boost applied
- Access count incremented on search hits

---

## Task 8: Update `handle_memory_save` for Schema Parity

**File:** `backend/app/agent/tools/native.py`

Update to use all new columns:

1. Accept and store: `summary`, `importance`, `sensitivity`, `metadata`, `source_type`, `source_id`
2. Create version 1 record in `memory_versions`
3. Remove manual FTS INSERT (triggers handle it — Task 2)
4. Keep promotion logic but include `summary` in `_promote` dict

**Acceptance criteria:**
- All columns populated with proper defaults
- Version 1 always created
- No manual FTS manipulation
- Backward compatible — old callers without new fields still work (defaults apply)

---

## Task 9: Register New Tools

**File:** `backend/app/agent/tools/native_registry.py`

Register:
- `memory_update` → `handle_memory_update`
- `memory_delete` → `handle_memory_delete`

**File:** `backend/app/agent/tools/definitions.py` (or wherever TOOL_MAP lives)

Add tool definitions for `memory_update` and `memory_delete` so the LLM knows how to call them:
- `memory_update`: params `memory_id` (required), `content` (required), `reason` (optional)
- `memory_delete`: params `memory_id` (required), `reason` (optional)

**Acceptance criteria:**
- New tools appear in registry
- Tool definitions have proper parameter schemas
- Worker can route calls to new handlers

---

## Task 10: Observability

**File:** `backend/app/agent/tools/native.py`

Improve logging:
- Log memory_save with memory_id, type, content length, promoted flag
- Log memory_update with memory_id, new version number
- Log memory_delete with memory_id
- Log search_memory with query, result count, sources (local vs shared), elapsed time
- All warnings include `exc_info=True` for stack traces

**Acceptance criteria:**
- Every memory operation produces a structured log line
- Failures include full stack trace
- Search logs include timing for performance monitoring

---

## Task 11: Tests — Enterprise Coverage

**File:** `backend/tests/test_worker.py` (or new `backend/tests/test_native_memory.py`)

Match host-side test coverage (`test_memory_repository.py`). Required tests:

**Schema:**
- test_agent_db_schema_creates_all_tables (memories, memory_versions, entities, content_chunks, FTS tables)

**Save:**
- test_memory_save_all_fields (summary, importance, sensitivity, metadata, source)
- test_memory_save_defaults (missing optional fields get proper defaults)
- test_memory_save_creates_version_1
- test_memory_save_validation_empty_content
- test_memory_save_validation_bad_type
- test_memory_save_validation_bad_importance
- test_memory_save_promoted_types (preference, fact, instruction, entity, person)
- test_memory_save_non_promoted_types (general, solution)

**Update:**
- test_memory_update_changes_content
- test_memory_update_creates_version
- test_memory_update_nonexistent_returns_error
- test_memory_update_deleted_returns_error
- test_memory_update_multiple_versions_track_history
- test_memory_update_validation

**Delete:**
- test_memory_delete_soft_deletes
- test_memory_delete_creates_version
- test_memory_delete_nonexistent_returns_error
- test_memory_delete_already_deleted_returns_error
- test_memory_delete_excluded_from_search

**Search:**
- test_search_finds_by_content
- test_search_finds_by_summary
- test_search_type_filter
- test_search_time_filter
- test_search_excludes_deleted
- test_search_recency_boost
- test_search_updates_access_count
- test_search_local_and_shared (with attached shared.db)
- test_search_no_db_returns_error
- test_search_empty_query_validation

**FTS Integrity:**
- test_fts_syncs_on_insert
- test_fts_syncs_on_update
- test_fts_syncs_on_delete

**Transaction Safety:**
- test_rollback_on_save_failure
- test_rollback_on_update_failure

**Registry:**
- test_registry_includes_memory_update_and_delete

**Acceptance criteria:**
- All tests pass
- Coverage matches host-side test patterns
- No tests skip or xfail
- Tests run in isolation (tmp directories, no shared state)

---

## Definition of Done

- [ ] All 11 tasks implemented
- [ ] All existing 231 tests still pass
- [ ] All new tests pass (target: 30+ new tests)
- [ ] `npm run build` / typecheck passes (if applicable)
- [ ] No TODOs or incomplete implementations
- [ ] Code follows existing conventions in the project
- [ ] Committed with clear message: `feat: C3 — enterprise-grade local memory store`
