## Summary

Implements **Option 3 (Graceful Degradation)** from the [token decoupling design doc](docs/token_decoupling_design.md), allowing the agent to function without `SPACETIMEDB_TOKEN` by falling back to local SQLite storage.

## Problem

When `SPACETIMEDB_TOKEN` is not set, tool calls like `work_plan` and `search_memory` fail with HTTP 500 errors, making the agent unable to use planning and memory features.

## Solution

### Architecture
- **`StorageBackend` abstract interface** (`src/backends/base.py`) — defines the contract for all storage backends (store, retrieve, list_keys, delete, is_available)
- **`SQLiteBackend`** (`src/backends/sqlite_backend.py`) — local fallback using SQLite with WAL mode, stores data in `~/.bond/fallback.db`
- **`SpacetimeDBBackend`** (`src/backends/spacetimedb_backend.py`) — primary backend wrapping SpacetimeDB HTTP API
- **`get_client()` factory** (`src/spacetime_client.py`) — returns the appropriate backend:
  - `SPACETIMEDB_TOKEN` set + SpacetimeDB reachable → `SpacetimeDBBackend`
  - Otherwise → `SQLiteBackend` with a logged warning

### Design Doc Updates
- Added decision rationale, architecture diagram, risks table, testing strategy, and rollback plan
- Selected Option 3 with clear justification

## Testing

24 unit tests covering:
- SQLite CRUD operations, upserts, namespace isolation
- Backend selection logic (token present/absent, SpacetimeDB reachable/unreachable)
- Singleton caching and reset behavior
- End-to-end fallback workflow
- Interface compliance

All tests pass ✅

## Files Changed

| File | Description |
|------|-------------|
| `docs/token_decoupling_design.md` | Updated with architecture, rationale, risks, testing strategy |
| `src/backends/base.py` | Abstract `StorageBackend` interface |
| `src/backends/sqlite_backend.py` | SQLite fallback implementation |
| `src/backends/spacetimedb_backend.py` | SpacetimeDB primary implementation |
| `src/spacetime_client.py` | Factory function with automatic backend selection |
| `tests/test_spacetime_client.py` | 24 unit tests |
