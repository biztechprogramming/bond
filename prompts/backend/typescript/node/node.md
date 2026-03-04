# Node.js

## When this applies
Working with Node.js runtime in Bond's gateway or tools.

## Bond-Specific
- Bond gateway runs Node 22 — use Node 22 APIs freely
- Gateway: TypeScript WebSocket server on port 18789

## Patterns / Gotchas
- Node 22 native: `fetch()`, `WebSocket`, `structuredClone()`, `crypto.randomUUID()` — no polyfills needed
- `node --watch` (22+) replaces `nodemon` — built-in file watching with automatic restart
- `fs/promises` is the standard — never use `fs.readFileSync` in async contexts, it blocks the event loop
- `node:` protocol prefix: `import { readFile } from 'node:fs/promises'` — disambiguates from npm packages, mandatory in some bundlers
- `process.env` values are ALWAYS strings — `process.env.PORT` is `"3000"` not `3000`; `parseInt` is required
- `EventEmitter` memory leak warning at 11 listeners — increase with `emitter.setMaxListeners(n)` or investigate actual leak
- `child_process.exec` buffers entire output in memory — use `spawn` with streams for large output
- `AbortController` for cancellable operations: `fetch(url, { signal: controller.signal })` — works with `setTimeout`, `fetch`, streams
- `import.meta.url` replaces `__dirname` in ESM — convert with `fileURLToPath(import.meta.url)` and `dirname()`
- Top-level `await` works in ESM modules — but NOT in CommonJS. Bond uses ESM for gateway
- `process.on('unhandledRejection')` — unhandled promise rejections terminate the process in Node 15+ (not just a warning)
- `crypto.subtle` (Web Crypto API): available in Node 20+ — use instead of `crypto.createHash` for web-compatible code
