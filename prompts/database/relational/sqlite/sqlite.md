# SQLite Best Practices

## Configuration (PRAGMAs)
Always set these at the start of every connection for optimal performance and reliability:
- `PRAGMA journal_mode = WAL;` — Enables Write-Ahead Logging for better concurrency (multiple readers, one writer).
- `PRAGMA synchronous = NORMAL;` — Faster than FULL, safe in WAL mode.
- `PRAGMA busy_timeout = 5000;` — Prevents "database is locked" errors by waiting for the lock to clear.
- `PRAGMA foreign_keys = ON;` — Must be enabled per connection to enforce constraints.

## Schema & Types
- **Strict Mode**: Use `CREATE TABLE ... STRICT` (SQLite 3.37+) to enforce data types.
- **Dynamic Typing**: Remember that SQLite is dynamically typed unless STRICT is used. A "STRING" column can hold an "INT".
- **Dates**: Store dates as ISO8601 strings, integers (Unix time), or reals (Julian days). Use `strftime` for manipulation.
- **JSON**: Use the built-in JSON functions (`json_extract`, `->`, `->>`) for semi-structured data.

## Performance
- **Write Contention**: SQLite only allows one writer at a time. Keep transactions short.
- **Batching**: Always wrap multiple inserts/updates in a single `BEGIN TRANSACTION` / `COMMIT` block. Single inserts are extremely slow.
- **Indexes**: Use `EXPLAIN QUERY PLAN` to ensure indexes are being used. SQLite's query planner is simpler than Postgres.
- **Vacuuming**: Run `VACUUM` occasionally to defragment the database file, but be aware it rebuilds the entire file.

## Limitations
- **ALTER TABLE**: Limited support for altering columns. Often requires recreating the table.
- **Concurrency**: Not suitable for high-concurrency write environments. Best for edge, local, or low-write applications.
- **Type System**: No native Boolean or DateTime types; use integers (0/1) and strings/integers respectively.
