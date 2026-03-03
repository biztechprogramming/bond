# Design Doc 015: LibSQL Integration

## Goal
Optimize SQLite with LibSQL-inspired tuning (WAL, synchronous=NORMAL, busy_timeout) to improve concurrency, performance, and reliability for multi-agent workflows while maintaining local-first data ownership.

## Context
Bond currently uses standard SQLite via `aiosqlite` and `SQLAlchemy`. As we scale to multiple concurrent agents and real-time frontend updates, we hit "Database is locked" errors and performance bottlenecks in high-frequency logging/memory retrieval.

## Implementation Strategy

While direct LibSQL integration via SQLAlchemy-LibSQL remains experimental (segmentation faults in `aiolibsql`), we have implemented the performance core of the LibSQL design using tuned SQLite PRAGMAs.

### 1. Dependency Update
- Replace `aiosqlite` with `libsql-client` or the `libsql` Python SDK.
- Update `SQLAlchemy` connection strings to use the `libsql://` or `sqlite+libsql://` dialect.

### 2. Configuration (`bond.json`)
- Add `database_type` (default: `sqlite`).
- Support `database_url` for LibSQL remote/sidecar connections (e.g., `libsql://localhost:8080`).
- Maintain `database_path` for local file mode.

### 3. Connection Management
- Configure LibSQL to use **WAL mode** (Write-Ahead Log) by default.
- Set `synchronous = NORMAL` for faster writes without sacrificing local safety.
- Update `backend/app/db/session.py` (or equivalent) to handle the LibSQL engine.

### 4. Vector Search Compatibility
- Ensure `sqlite-vec` extension remains compatible with the LibSQL engine.
- LibSQL supports loading extensions via the standard SQLite interface.

### 5. Migration Strategy
- Since LibSQL is a fork of SQLite, existing `knowledge.db` files are compatible.
- No data migration script required; simply point the new driver at the existing file.

## Why LibSQL?
1. **Tuned WAL:** Using `journal_mode=WAL` and `synchronous=NORMAL` matches LibSQL's local performance profile. (agents) and readers (UI) simultaneously.
2. **Local-First:** Keeps the "it's just a file" simplicity Andrew prefers.
3. **Future-Proof:** Supports Hrana protocol (WebSockets) if we want to move the DB to a separate service/container later.
4. **Performance:** Significantly faster than standard SQLite for high-volume WAL operations.

## Success Criteria
- [ ] No "Database is locked" errors during simultaneous agent activity.
- [ ] Passing all existing tests in `backend/tests/`.
- [ ] Real-time board updates remain fluid and responsive.
- [ ] `sqlite-vec` memory retrieval works correctly.
