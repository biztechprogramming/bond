# SpacetimeDB TypeScript SDK

## When this applies
Using SpacetimeDB from TypeScript/JavaScript clients (frontend or Node).

## Patterns / Gotchas
- Package renamed: use `spacetimedb` not `@clockworklabs/spacetimedb-sdk` (deprecated since v1.4)
- Connection uses builder pattern: `DbConnection.builder().withUri(uri).withModuleName(name).withToken(token).onConnect(cb).build()`
- Token stored in localStorage for persistent identity across page refreshes
- React hook: `useTable<DbConnection, TableType>('table_name')` returns `{ rows }` — NOT a raw array
- Filtered subscriptions: `useTable('user', where(eq('online', true)))` — predicates use helper functions, not SQL strings
- Reducer calls are fire-and-forget on the connection object: `conn.reducers.sendMessage(text)` — no await, no return value
- Type generation: `spacetime generate --lang typescript --out-dir ./bindings` creates types for tables, reducers, custom types
- BSATN RangeError at runtime usually means stale bindings — regenerate and copy to frontend
- WS SDK on Node 18 silently drops reducer calls — use HTTP API for server-side calls instead
- Callbacks: `onInsert`, `onUpdate`, `onDelete` on table subscriptions — not on the connection object
- No partial updates — entire row is replaced on update; diff detection is client-side
- Identity is a byte array, not a string — use provided serialization helpers for display
- Subscription errors are silent by default — register `onError` callback explicitly
