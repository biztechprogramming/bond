# 103 — Gateway & Backend Auth Hardening

**Status:** Planned
**Created:** 2026-04-06
**Context:** The `token !== config.apiKey` checks in the gateway and backend were causing
spurious 401s across multiple callers. The auth middleware has been **disabled** until all
callers are fixed. This document tracks every place that needs to change before re-enabling.

---

## Problem

The gateway and backend both enforce a shared `BOND_API_KEY` via Bearer-token middleware.
In practice, several callers either don't send the key, send a stale key, or hit timing
issues during startup. The result is intermittent 401 errors that break:

- Frontend → Gateway REST calls (plans, MCP proxy, conversations)
- Gateway Broker → Backend proxy calls
- Worker (persistence_client) → Gateway tool-log calls
- Frontend → Gateway WebSocket connections

Rather than patch callers one at a time while the system is broken, the auth checks have
been commented out so development can continue. This doc defines what must be fixed before
re-enabling them.

---

## Disabled Code Locations

| # | File | What was disabled | Line ref |
|---|------|-------------------|----------|
| 1 | `gateway/src/server.ts` | HTTP middleware: `if (token !== config.apiKey)` | `// TODO(auth)` near top of `startGatewayServer` |
| 2 | `gateway/src/server.ts` | WebSocket auth: `if (token !== config.apiKey)` in `wss.on("connection")` | `// TODO(auth)` near bottom |
| 3 | `backend/app/main.py` | `check_api_key` middleware: `if token != BOND_API_KEY` | `# TODO(auth)` in middleware |

All three are marked with `TODO(auth)` and reference this design doc (103).

---

## Callers That Must Send a Valid Token

### 1. Frontend → Gateway (HTTP)

**File:** `frontend/src/lib/config.ts`
**Mechanism:** `getBondApiKey()` fetches key from `/api/bond-key` server route, which reads
`BOND_API_KEY` env var or `~/.bond/data/.gateway_key`. The key is cached in-memory.
`apiFetch()` and `authHeaders()` inject it as `Bearer <key>`.

**Issues to fix:**
- Some frontend `fetch()` calls bypass `apiFetch()` and use raw `fetch()` without auth headers.
  Audit all `fetch()` calls in `frontend/src/` that hit gateway/backend endpoints and convert
  them to `apiFetch()`.
- The `/api/bond-key` route reads the key at request time. If the key file doesn't exist yet
  (race during first startup), the frontend gets no key and caches `""` permanently. Add retry
  logic or invalidate the cache on empty response.

### 2. Frontend → Gateway (WebSocket)

**File:** `frontend/src/lib/ws.ts`
**Mechanism:** Connects to `ws://host:18789/ws?token=<apiKey>`. The key is fetched via
`getBondApiKey()` and set on the socket manager.

**Issues to fix:**
- Same cache-empty-key problem as HTTP. If the WS connects before the key is available,
  it sends `?token=` (empty) and gets rejected.
- On reconnect, the cached key might be stale if the gateway restarted with a new key.
  Add a re-fetch on WebSocket close code 4001.

### 3. Gateway → Backend (HTTP)

**File:** `gateway/src/backend/client.ts`
**Mechanism:** `BackendClient` has `setApiKey()` called at startup from `config.apiKey`.
All requests to the backend include `Authorization: Bearer <key>` via `authHeaders()`.

**File:** `gateway/src/server.ts` (inline fetch calls)
**Mechanism:** Several inline `fetch()` calls to `config.backendUrl` manually set
`Authorization: Bearer ${config.apiKey}` (e.g., `resolveWorkerUrl`, conversations router).

**Issues to fix:**
- The gateway and backend must resolve to the **same** key. Both read from `BOND_API_KEY`
  env var → `~/.bond/data/.gateway_key` fallback → auto-generate. If either auto-generates
  independently (e.g., the file doesn't exist and they race), they'll have different keys.
- **Fix:** Ensure `bond init` always writes the key file before either process starts.
  Remove auto-generation from `backend/app/main.py:_resolve_api_key()` — if the key isn't
  there, fail loudly instead of silently generating a different one.

### 4. Backend Worker → Gateway (HTTP)

**File:** `backend/app/agent/persistence_client.py`
**Mechanism:** Reads `BOND_API_KEY` or `BOND_AGENT_TOKEN` from env. Sends as Bearer token
to gateway endpoints like `/api/v1/tool-logs`.

**Issues to fix:**
- Workers running in containers receive `BOND_API_KEY` via `-e` flag in
  `backend/app/sandbox/adapters.py`. If the host's key rotates (gateway restart with
  auto-generation), running containers have the old key.
- **Fix:** Same as #3 — deterministic key from `bond init`, no auto-generation.

### 5. Gateway → SpacetimeDB

**File:** `gateway/src/conversations/router.ts`
**Mechanism:** Uses `callReducer()` with SpacetimeDB token (separate from API key).

**No auth issue** — this uses a different token. Listed for completeness.

### 6. Bond Host Daemon (sandbox)

**File:** `backend/app/sandbox/bond_host_daemon.py`
**Mechanism:** Has its own `auth_middleware` checking `Bearer {_config.auth_token}`.

**Issues to fix:**
- Uses a separate `auth_token` from config, not `BOND_API_KEY`. Verify this is intentional
  and document the relationship. If it should use the same key, unify.

---

## Key Resolution — Current State

All three processes (gateway, backend, frontend) resolve the API key the same way:

```
1. BOND_API_KEY env var
2. ~/.bond/data/.gateway_key file
3. Auto-generate a new key (gateway config + backend main.py)
```

**The bug:** Step 3 means two processes can independently generate different keys if the
file doesn't exist. The frontend never auto-generates — it just returns empty.

---

## Re-enablement Plan

### Phase 1: Deterministic Key (prerequisite)

1. `bond init` (cli.py) already generates the key and writes both `.env` and `.gateway_key`.
   Verify this always runs before gateway/backend start.
2. Remove auto-generation from `gateway/src/config/index.ts:resolveApiKey()` and
   `backend/app/main.py:_resolve_api_key()`. If the key isn't found, log an error and
   set it to a known sentinel value that will always fail auth (forcing the user to run
   `bond init`).
3. In Docker/container deployments, ensure `BOND_API_KEY` is passed as env var to all
   containers that need it.

### Phase 2: Fix All Callers

4. Audit every `fetch()` in `frontend/src/` — replace raw `fetch()` with `apiFetch()` for
   any call to gateway or backend URLs.
5. Add retry/cache-invalidation to `getBondApiKey()` so it doesn't permanently cache an
   empty key.
6. Add re-fetch on WebSocket 4001 close code in `frontend/src/lib/ws.ts`.
7. Verify `BackendClient.setApiKey()` is called with the resolved key (already done).

### Phase 3: Re-enable Auth

8. Uncomment the gateway HTTP middleware in `gateway/src/server.ts`.
9. Uncomment the gateway WebSocket auth in `gateway/src/server.ts`.
10. Uncomment the backend `check_api_key` middleware in `backend/app/main.py`.
11. Run full integration test: frontend → gateway → backend → worker → gateway round-trip.

### Phase 4: Harden

12. Add integration test that starts gateway + backend with a known key and verifies:
    - Requests without Bearer token get 401
    - Requests with wrong token get 401
    - Requests with correct token get 200
13. Add startup health check: gateway calls backend `/api/v1/health` with its key to
    verify they agree. Log a clear error if they don't.
14. Consider replacing the shared-secret approach with a proper auth mechanism (JWT, mTLS)
    for production deployments — but shared secret is fine for local-only use.

---

## Files Changed in This Disable

| File | Change |
|------|--------|
| `gateway/src/server.ts` | Commented out HTTP + WebSocket auth checks |
| `backend/app/main.py` | Commented out `check_api_key` middleware body |
| `docs/design/103-gateway-auth-hardening.md` | This document |
