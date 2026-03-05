# PostgreSQL

## Performance & Scaling
- **Connection Pooling**: Mandatory for production. Use PgBouncer or application-level pools (e.g., Hikari, TypeORM pool).
- **Indexing Strategy**:
  - Use **Partial Indexes** for selective queries: `CREATE INDEX ... WHERE status = 'active'`.
  - Use **GIN/GIST** for full-text search and JSONB.
  - Use **Covering Indexes** (`INCLUDE` clause) to allow Index-Only scans.
- **Vacuuming**: Monitor bloat. Autovacuum is usually sufficient but may need tuning for high-write tables.

## Schema Design
- **Identity Columns**: Use `GENERATED ALWAYS AS IDENTITY` instead of `serial`.
- **Data Types**:
  - Use `text` instead of `varchar(n)` unless a hard limit is required.
  - Use `timestamptz` for all timestamps to handle time zones correctly.
  - Use `jsonb` for semi-structured data; avoid plain `json`.
- **Constraints**: Use `CHECK` constraints for domain-level validation (e.g., `price > 0`).

## Querying & DML
- **Locking**: `TRUNCATE` and `ALTER TABLE` take heavy locks. Use `DELETE` or concurrent index creation where possible.
- **CTEs**: Since PG12, CTEs are inlined. Use them for readability without fear of performance barriers.
- **Upserts**: Use `INSERT ... ON CONFLICT (key) DO UPDATE SET ...` for atomic upserts.
- **Explain Analyze**: Use it to debug, but remember it executes the query. Use `EXPLAIN (ANALYZE, BUFFERS)` for detailed I/O stats.

## Pitfalls
- **Transaction Snapshots**: `NOW()` is stable within a transaction. Use `clock_timestamp()` if you need real-time progress.
- **Enum Limitations**: You can add values to Enums but not remove them easily. Consider a lookup table for high-churn categories.
- **Count(*)**: Slow on large tables. Use `reltuples` from `pg_class` for approximations if exact counts aren't needed.
