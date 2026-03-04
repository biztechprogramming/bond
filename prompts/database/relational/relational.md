# Relational Databases

## When this applies
Working with SQL databases (SQLite, PostgreSQL, libSQL).

## Patterns / Gotchas
- Always use parameterized queries — never string-interpolate user input into SQL
- RETURNING clause behavior differs: SQLite added it in 3.35, PostgreSQL has had it forever
- NULL handling: `WHERE col != 'x'` does NOT match NULL rows — use `WHERE col IS DISTINCT FROM 'x'` (PG) or explicit `OR col IS NULL`
- Transaction isolation defaults differ: SQLite = DEFERRED, PostgreSQL = READ COMMITTED
- Integer overflow: SQLite silently converts overflowed integers to floats; PostgreSQL throws
- UPSERT syntax differs: SQLite uses `ON CONFLICT`, PostgreSQL uses `ON CONFLICT` too but with different index predicate support
