# PostgreSQL

## When this applies
Working with PostgreSQL databases.

## Patterns / Gotchas
- Connection pooling is mandatory in production — each PG connection uses ~10MB RSS; use PgBouncer or connection pool library
- `serial` / `bigserial` is legacy — use `GENERATED ALWAYS AS IDENTITY` instead (SQL standard, prevents accidental manual inserts)
- `TRUNCATE` takes an ACCESS EXCLUSIVE lock on the table — use `DELETE FROM` in concurrent environments
- `EXPLAIN ANALYZE` actually executes the query — do NOT use on destructive DML in production
- CTEs (WITH clauses) were optimization barriers before PG12 — since PG12 the planner can inline them
- `jsonb` operators: `->>` returns text, `->` returns jsonb — mixing these up causes silent type mismatches
- `COALESCE(nullable_col, 'default')` evaluates ALL arguments — don't put expensive subqueries in fallback position
- Partial indexes: `CREATE INDEX idx ON orders(status) WHERE status = 'pending'` — dramatically faster for selective queries
- `pg_stat_statements` requires shared_preload_libraries restart — can't enable at runtime
- Advisory locks (`pg_advisory_lock`) are session-level by default — use `pg_advisory_xact_lock` for transaction-scoped
- `NOW()` returns the same value within a transaction — use `clock_timestamp()` for actual current time in loops
- Enum types cannot have values removed — only added with `ALTER TYPE ... ADD VALUE`
- `text` and `varchar` have identical performance in PostgreSQL — no reason to use varchar(n) unless you need a length constraint
