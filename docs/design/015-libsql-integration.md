# Design Doc 015: LibSQL Integration

## Goal
Optimize local SQLite-based storage with LibSQL-inspired tuning (WAL, synchronous=NORMAL, busy_timeout) for vector, embedding, and other explicitly local data paths while keeping Bond operational state in SpaceTimeDB.

## Context
Bond operational state lives in **SpaceTimeDB**, not SQLite. However, Bond may still use local SQLite-based storage for explicitly local concerns such as vector / embedding data and certain agent-local stores. Those paths can still benefit from SQLite/LibSQL-style tuning to reduce lock contention and improve local performance.

## Implementation Strategy

While direct LibSQL integration via SQLAlchemy-LibSQL remains experimental (segmentation faults in `aiolibsql`), we can still apply the performance core of the LibSQL design using tuned SQLite PRAGMAs for local SQLite-backed storage.

### 1. Dependency Update
- Evaluate replacing `aiosqlite` with `libsql-client` or the `libsql` Python SDK only for explicitly local SQLite-backed storage.
- Update local SQLite connection code if a LibSQL dialect is adopted for those local/vector paths.

### 2. Configuration (`bond.json`)
- Avoid presenting SQLite as Bond's primary datastore.
- If configurable, use storage-specific names for local SQLite paths rather than generic `database_path` semantics.
- Keep any local SQLite configuration clearly scoped to vector, embedding, or agent-local storage.

### 3. Connection Management
- Configure local SQLite storage to use **WAL mode** where appropriate.
- Set `synchronous = NORMAL` for faster writes without sacrificing local safety.
- Keep this tuning limited to local SQLite-backed components rather than Bond operational state.

### 4. Vector Search Compatibility
- Ensure `sqlite-vec` extension remains compatible with any tuned SQLite or LibSQL-backed local storage.
- Preserve compatibility for vector / embedding retrieval paths.

### 5. Migration Strategy
- Do not treat existing `knowledge.db` usage as Bond operational source of truth.
- Any retained `knowledge.db` or similar SQLite files should be considered local/specialized storage only.
- No architecture decision in this doc should imply moving Bond core state away from SpaceTimeDB.

## Why LibSQL-style tuning?
1. **Reduced local lock contention:** WAL and related tuning can improve local SQLite concurrency.
2. **Useful for local specialized storage:** Especially relevant for vector or embedding stores.
3. **Future flexibility:** May help if local SQLite-backed components evolve independently.
4. **Performance:** Can improve throughput for local SQLite workloads.

## Success Criteria
- [ ] No avoidable local SQLite lock contention in vector / embedding paths.
- [ ] Passing all relevant tests for local SQLite-backed components.
- [ ] SpaceTimeDB remains the source of truth for Bond operational state.
- [ ] `sqlite-vec` retrieval works correctly where local vector storage is enabled.
