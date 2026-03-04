# Database Migrations

## When this applies
Creating, modifying, or running database migrations.

## Bond-Specific Rules
- ALWAYS run migrations using: `bun run migrate:up`
- NEVER modify migration scripts that have already been run
- If a migration has been run and has issues: create a NEW migration to fix it
- Uses Docker container `migrate/migrate` (golang-migrate) — do NOT change this approach
- Migration infrastructure (`scripts/migrate.js`, `scripts/migrate.sh`) is SACRED — never modify

## Patterns / Gotchas
- Column renames: NEVER rename a column directly — add new column, backfill, update code, drop old column (3-step migration)
- Adding NOT NULL column to existing table: add as nullable first, backfill, then add constraint in separate migration
- Index creation on large tables: `CREATE INDEX CONCURRENTLY` (PG) avoids locking but can't be in a transaction block
- Backfills on large tables: batch in chunks of 1000-10000 rows with explicit commits — never update entire table in one transaction
- Foreign key addition: on large tables, use `NOT VALID` then `VALIDATE CONSTRAINT` separately to avoid full table lock
- SQLite migrations: no transactional DDL for some operations — `ALTER TABLE` can't be rolled back
- Down migrations: always write them, test them, but never run them in production unless recovering from a failed up
- Timestamp vs sequential numbering: Bond uses sequential — never create out-of-order migration numbers
- Data migrations (backfills) should be separate from schema migrations — don't mix DDL and DML in one file
