# Zero-Downtime Migrations

## When this applies
Running migrations against databases with active traffic.

## Patterns / Gotchas
- Never drop a column that's still referenced by running code — deploy code change first, then migrate
- Column rename is a 3-phase deploy: (1) add new column + dual-write, (2) backfill + switch reads, (3) drop old column
- Table renames: create new table, dual-write, backfill, switch reads, drop old — same as column rename but harder
- Adding a default value to existing column: PostgreSQL 11+ stores default in catalog (fast), older versions rewrite entire table
- `ALTER TABLE ... ADD COLUMN` with DEFAULT is instant in PG11+ but rewrites table in PG10 and all SQLite versions
- Enum value additions: `ALTER TYPE ... ADD VALUE` in PG cannot run in a transaction — must be its own migration step
- Large table backfills: use `UPDATE ... WHERE id BETWEEN ? AND ?` in batches with `pg_sleep(0.01)` between batches to reduce replication lag
- Advisory locks in migrations: use `pg_advisory_lock(hash)` to prevent concurrent migration runs
- Test rollback: every migration should be tested up AND down in CI before deploying
- Ghost tables pattern: create new table with desired schema, copy data in background, swap names — for massive schema changes
