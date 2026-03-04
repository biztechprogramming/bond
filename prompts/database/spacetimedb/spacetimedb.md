# SpacetimeDB

## When this applies
Working with SpacetimeDB modules, the Bond gateway, or any SpacetimeDB client code.

## Bond Architecture
- Module source: `spacetimedb/spacetimedb/src/index.ts`
- Publish: `cd spacetimedb && spacetime publish --server http://localhost:18787 bond-core-v2`
- Gateway is the ONLY writer to SpacetimeDB — workers never call SpacetimeDB directly
- `context_summaries` and `context_compression_log` stay in SQLite (not SpacetimeDB)

## CRITICAL: Bindings copy step (always required after spacetime publish)
```bash
cd ~/bond/spacetimedb/spacetimedb
spacetime generate bond-core-v2 --lang typescript --out-dir ./frontend/src/lib/spacetimedb
cp -r ./frontend/src/lib/spacetimedb/. ~/bond/frontend/src/lib/spacetimedb/
```
The generate out-dir and the real frontend are DIFFERENT paths. Missing the copy causes BSATN RangeErrors at runtime with no useful error message.

## HTTP API (use this, not WS SDK in Node)
- WS SDK is unreliable on Node 18 — reducer calls silently vanish with no error callback
- Use HTTP API: `POST /v1/database/{name}/call/{reducer}`
- Reducer args are positional JSON arrays, NOT named objects: `[arg1, arg2, arg3]`
- Auth header: `Authorization: Basic <hex-encoded-identity>:<hex-encoded-token>`

## Core Concepts (non-obvious)
- Each reducer call is a transaction — returning an error aborts ALL changes in that call
- Tables are private by default; public tables are read-only to clients (writes only via reducers)
- Identity-based auth: each client gets an `Identity`, no JWT/token scopes needed
- `ctx.timestamp` is server-injected; clients cannot forge timestamps
- No direct SQL writes — SQL is read-only; all mutations go through reducers

## SDK Package Change (v1.4+)
- `@clockworklabs/spacetimedb-sdk` is DEPRECATED
- Use `spacetimedb` package instead
- Connection builder pattern: `DbConnection.builder().withUri().withModuleName().build()`

## Never touch
- `connectToSpacetimeDB` default URI `ws://localhost:18788/stdb/` — do not change
- Pre-existing TS2742 errors in `src/spacetimedb/index.ts` — do not fix, they're harmless
