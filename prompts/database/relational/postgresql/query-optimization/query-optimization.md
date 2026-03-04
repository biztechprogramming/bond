# Query Optimization

## When this applies
Debugging slow PostgreSQL queries or optimizing database performance.

## Patterns / Gotchas
- `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` gives the most useful output — BUFFERS shows cache hit ratio
- Look at actual rows vs estimated rows — >10x mismatch means stale statistics, run `ANALYZE table_name`
- Sequential scan on large table is not always bad — PG chooses seq scan when >5-10% of rows match (cheaper than random I/O)
- `Index Cond` vs `Filter` in explain output: Filter means rows were fetched then discarded (wasteful), Index Cond means index did the filtering
- `Nested Loop` is fine for small outer sets; `Hash Join` for medium; `Merge Join` for pre-sorted large sets
- JIT compilation (`jit: true` in explain) has startup cost — disable with `SET jit = off` for OLTP queries under 100ms
- `work_mem` is per-operation, not per-query — a query with 5 sorts uses 5x work_mem; don't set globally too high
- Bitmap heap scan with `Recheck Cond` means work_mem was too small for the bitmap — increase work_mem or reduce result set
- `LIMIT` doesn't always short-circuit — with ORDER BY and no matching index, PG sorts everything first
- Correlated subqueries in SELECT list execute once per row — rewrite as JOIN or lateral join
- `OR` conditions often prevent index usage — rewrite as `UNION ALL` of two indexed queries
- Window functions force a sort — add index on partition/order columns to avoid in-memory sort
