# PostgreSQL Best Practices

## Schema Design
- **Identity Columns**: Use `GENERATED ALWAYS AS IDENTITY` instead of the legacy `SERIAL` type.
- **Data Types**:
    - Use `TEXT` instead of `VARCHAR(N)` unless a specific length constraint is required (performance is identical).
    - Use `TIMESTAMPTZ` for all timestamps to handle timezones correctly.
    - Use `JSONB` for semi-structured data (better performance and indexing than `JSON`).
    - Use `NUMERIC` for currency; never use `FLOAT` or `REAL` for exact values.

## Performance & Optimization
- **Connection Pooling**: Use a pooler like PgBouncer. PostgreSQL connections are expensive (~10MB per connection).
- **Indexes**:
    - Use **Partial Indexes** for frequently queried subsets: `CREATE INDEX ... WHERE status = 'active'`.
    - Use **Covering Indexes** (`INCLUDE`) to allow Index-Only scans.
    - Use **GIN** indexes for `JSONB` or Full-Text Search.
- **CTEs**: Since PG12, CTEs are inlined by default. Use them for readability without fear of performance penalties unless explicitly using `MATERIALIZED`.

## Concurrency & Locking
- **Locking**: Be aware that `ALTER TABLE` and `TRUNCATE` take Access Exclusive locks.
- **Skip Locked**: Use `SELECT ... FOR UPDATE SKIP LOCKED` for high-performance job queues.
- **Advisory Locks**: Use `pg_advisory_xact_lock` for application-level distributed locks tied to transaction lifecycle.

## Operational
- **Explain Analyze**: Use it to see actual execution times and row counts.
- **Vacuum**: Understand that `VACUUM ANALYZE` is necessary for reclaiming space and updating statistics, but `VACUUM FULL` locks the table.
- **Maintenance**: Monitor `pg_stat_statements` to find the most expensive queries in the system.
