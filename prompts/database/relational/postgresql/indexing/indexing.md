# Indexing

## When this applies
Creating, evaluating, or debugging PostgreSQL indexes.

## Patterns / Gotchas
- `CREATE INDEX CONCURRENTLY` does NOT block writes but takes 2-3x longer and cannot run inside a transaction
- Multicolumn index order matters: `(a, b)` index supports `WHERE a = ?` and `WHERE a = ? AND b = ?` but NOT `WHERE b = ?` alone
- Covering indexes: `CREATE INDEX idx ON t(a) INCLUDE (b, c)` avoids heap lookup for index-only scans
- GIN indexes for jsonb: `CREATE INDEX idx ON t USING gin(data jsonb_path_ops)` — `jsonb_path_ops` is 2-3x smaller than default operator class but only supports `@>` operator
- Partial indexes dramatically reduce index size: `WHERE deleted_at IS NULL` excludes soft-deleted rows
- Expression indexes: `CREATE INDEX idx ON users(lower(email))` — query MUST use `lower(email)` to hit the index
- Unused indexes waste write performance — every INSERT/UPDATE/DELETE maintains all indexes
- `pg_stat_user_indexes.idx_scan = 0` for extended period means the index is unused — safe to drop
- B-tree indexes are useless for `IS NULL` checks unless you create a partial index `WHERE col IS NULL`
- Hash indexes: since PG10 they're crash-safe and work for equality checks only — smaller than B-tree for high-cardinality columns
- BRIN indexes: ideal for naturally ordered data (timestamps, serial IDs) — 1000x smaller than B-tree but only useful for range scans
- Reindexing: `REINDEX INDEX CONCURRENTLY idx_name` rebuilds without locking (PG12+)
- Index bloat: dead tuples cause index bloat even after VACUUM — `REINDEX` or `pg_repack` fixes this
