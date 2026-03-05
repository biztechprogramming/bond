# SQLite

## Bond-Specific Configuration
- **WAL Mode**: `PRAGMA journal_mode=WAL` is required for concurrent reads during writes.
- **Synchronous**: `PRAGMA synchronous=NORMAL` provides a good balance of safety and speed in WAL mode.
- **Busy Timeout**: `PRAGMA busy_timeout=5000` prevents `SQLITE_BUSY` errors during write contention.
- **Foreign Keys**: `PRAGMA foreign_keys=ON` must be enabled **per connection**.

## Performance Optimization
- **Prepared Statements**: Always use them to prevent SQL injection and improve performance via plan reuse.
- **Batching**: Wrap multiple inserts/updates in a single `BEGIN...COMMIT` block. SQLite is slow with many small transactions.
- **Indexes**: SQLite supports partial indexes and indexes on expressions. Use them to optimize specific query patterns.
- **Vacuuming**: `VACUUM` rebuilds the DB file. Use `PRAGMA auto_vacuum` or run manually during maintenance windows.

## Vector Search (vec0)
- **Fixed Dimensions**: Embedding dimensions must be set at table creation: `vec0(embedding float[1536])`.
- **Querying**: Use the `MATCH` operator for vector similarity searches.
- **Performance**: Keep vector tables separate from metadata tables if they grow large.

## SQLite Quirks & Limitations
- **Typing**: SQLite uses manifest typing (mostly). Be explicit with types but know that any value can technically go in any column.
- **Alter Table**: Limited support in older versions. Modern SQLite (3.35+) supports dropping columns, but complex changes still require table recreation.
- **Concurrency**: Only one writer at a time. If you have high write volume, SQLite may not be the right choice.
- **JSON**: Use `json_extract` or the `->` / `->>` operators (3.38+).

## Implementation Details
- **aiosqlite**: Be aware it runs in a thread pool. Pragma settings must be applied immediately after connection.
- **Paths**: Always use absolute paths for DB files in Bond to avoid issues with different working directories in agents.
