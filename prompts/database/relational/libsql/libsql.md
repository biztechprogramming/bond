# libSQL

## When this applies
Working with libSQL (Turso) databases or considering SQLite alternatives.

## Patterns / Gotchas
- libSQL is a SQLite fork — most SQLite docs apply, but with key differences below
- Embedded mode: runs in-process like SQLite, no server needed — `import { createClient } from '@libsql/client'` with `url: 'file:local.db'`
- Turso sync: embedded replica syncs to Turso edge — `syncUrl` for remote, `url: 'file:local.db'` for local replica
- Sync is eventually consistent — local reads may be stale by seconds; call `client.sync()` to force
- `ALTER TABLE` supports more operations than upstream SQLite (column rename, drop)
- Native vector search via `vector` type — no extension needed unlike SQLite's vec0
- HTTP API uses JSON-over-HTTP, not the SQLite wire protocol — different from standard SQLite drivers
- Turso's free tier has row-read limits, not storage limits — a query scanning 1M rows costs 1M row reads even if it returns 1 row
- `@libsql/client` npm package works in Node, Deno, Bun, and edge runtimes (Cloudflare Workers)
- Batch transactions: `client.batch([stmt1, stmt2], 'write')` — must specify mode, default is not transactional
- Embedded replicas: writes go to remote primary, reads are local — write latency is network-bound
