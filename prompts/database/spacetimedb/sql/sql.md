# SpacetimeDB SQL

## When this applies
Writing SQL queries against SpacetimeDB (subscriptions or ad-hoc queries).

## Patterns / Gotchas
- SQL is READ-ONLY — all writes go through reducers, never INSERT/UPDATE/DELETE
- `COUNT(*)` requires an alias: `SELECT COUNT(*) AS count FROM table` — bare `COUNT(*)` fails
- `LIMIT` does NOT imply ordering — it just caps row count from arbitrary order
- No `ORDER BY` combined with `LIMIT` — get all rows and sort client-side
- No arithmetic in WHERE clauses for subscriptions: `WHERE price * qty > 100` is rejected
- Subscription queries can only return rows from a SINGLE table with ALL columns — no column projections
- Subscription joins limited to exactly TWO tables; ad-hoc queries support unlimited joins
- Join columns in subscriptions MUST have explicit indexes on both sides (compiler-enforced)
- No DELETE or UPDATE with joins — DML is single-table only
- Cannot construct product or sum types in SQL — only return rows containing them
- PostgreSQL reserved keywords must be quoted as identifiers (inherits PG keyword list)
- SET and SHOW statements are EXPERIMENTAL — no forward-compatibility guarantee
- The only aggregation function is `COUNT(*)` — no SUM, AVG, MIN, MAX
- System variables are experimental and may change between versions
