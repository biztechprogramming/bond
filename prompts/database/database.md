# Database

## Core Principles
- **Schema First**: Design schemas with strict constraints (NOT NULL, UNIQUE, CHECK) to ensure data integrity at the storage layer.
- **Normalization**: Aim for 3NF by default; denormalize only for proven performance bottlenecks with a clear synchronization strategy.
- **Atomic Operations**: Use transactions for all multi-step mutations. Keep transactions short to minimize lock contention.
- **Least Privilege**: Application users should only have the minimum permissions required (e.g., SELECT/INSERT/UPDATE, no DROP/TRUNCATE).

## Bond-Specific Architecture
- **Knowledge DB**: Primary SQLite DB at `~/.bond/data/knowledge.db`.
- **Agent DBs**: Per-agent SQLite DBs at `data/agents/<agent_id>/agent.db` for containerized runtime data.
- **SpacetimeDB**: Used for real-time state and global synchronization.
- **Concurrency**: SQLite is configured with WAL mode and `busy_timeout` to handle concurrent access.

## Best Practices
- **Indexing**: Index columns used in WHERE, JOIN, and ORDER BY. Avoid over-indexing as it slows down writes.
- **Query Optimization**: Use `EXPLAIN` to verify query plans. Avoid `SELECT *`; fetch only the columns you need.
- **Migrations**: Always use a migration tool for schema changes. Never perform manual schema edits in production.
- **Backups**: Treat database files as critical assets. Automate backups before major schema changes.

## Common Pitfalls
- **N+1 Queries**: Use JOINs or batching instead of executing queries inside loops.
- **Silent Failures**: Always check return codes/exceptions from database drivers.
- **Connection Leaks**: Use connection pools or ensure every connection is explicitly closed/returned to the pool.
- **Stale Data**: Be aware of caching layers; ensure cache invalidation happens alongside DB updates.
