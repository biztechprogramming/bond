# SQLite

## When this applies
Working with Bond's SQLite databases (knowledge.db, agent.db).

## Bond-Specific Configuration
- WAL mode enabled: `PRAGMA journal_mode=WAL` — required for concurrent reads during writes
- `PRAGMA synchronous=NORMAL` — safe with WAL, faster than FULL
- `PRAGMA busy_timeout=5000` — prevents immediate SQLITE_BUSY on write contention
- `PRAGMA foreign_keys=ON` — SQLite disables foreign keys by default (per-connection setting!)

## Patterns / Gotchas
- Foreign keys are OFF by default — must enable per connection, not per database
- WAL mode persists on the database file; journal_mode only needs setting once, but verify on connect
- Only ONE writer at a time even with WAL — concurrent writes queue behind busy_timeout
- `aiosqlite` wraps synchronous sqlite3 in a thread — it's not truly async I/O
- `aiosqlite` connection must set PRAGMAs immediately after opening (before any queries)
- vec0 extension for embeddings: `CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[1536])` — dimensions are fixed at creation
- vec0 queries: `SELECT * FROM vec_items WHERE embedding MATCH ? ORDER BY distance LIMIT 10` — MATCH operator, not cosine similarity function
- SQLite JSON: `json_extract(col, '$.key')` works but `->` operator requires SQLite 3.38+
- Date functions return strings, not date objects — always use ISO8601 format for consistency
- VACUUM rebuilds the entire database file — do NOT run on production with active connections
- Maximum database size: 281 TB theoretically, but performance degrades badly past ~100GB
- ALTER TABLE cannot drop columns in SQLite < 3.35 — must recreate table
