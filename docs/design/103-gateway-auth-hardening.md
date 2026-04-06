# 103 — Gateway & Backend Auth Hardening

**Status:** Planned
**Created:** 2026-04-06
**Context:** The API key authentication middleware from Design Doc 101 caused widespread 401
errors because not all callers were updated to send the key. All auth-related code has been
**fully reverted** (not just disabled). This document tracks what was reverted and what must
be done before re-implementing.

---

## Problem

The gateway and backend both attempted to enforce a shared `BOND_API_KEY` via Bearer-token
middleware. In practice, several callers either didn't send the key, sent a stale key, or hit
timing issues during startup. The result was intermittent 401 errors that broke:

- Frontend → Gateway REST calls (plans, MCP proxy, conversations)
- Gateway Broker → Backend proxy calls
- Worker (persistence_client) → Gateway tool-log calls
- Frontend → Gateway WebSocket connections

Rather than patch callers one at a time while the system was broken, all auth code has been
fully reverted so development can continue. This doc defines what was removed and what must
be fixed before re-implementing.

---

## Reverted Auth Features (from Design Doc 101)

The following API key authentication features were implemented as part of Design Doc 101
(Pre-Release Security Hardening) but have been fully reverted because they caused widespread
401 errors across callers that weren't updated to send the key. These need to be
re-implemented properly with all callers fixed simultaneously.

### 1. Gateway HTTP API Key Middleware
- **What:** Express middleware on all routes (except `/health` and `/webhooks`) that validates
  `Authorization: Bearer <BOND_API_KEY>` on every request.
- **File:** `gateway/src/server.ts`
- **Why reverted:** Frontend, backend proxy calls, and internal service calls weren't sending the key.

### 2. Gateway WebSocket Authentication
- **What:** Validate `?token=<BOND_API_KEY>` query parameter on WebSocket upgrade requests.
  Reject connections with `4001 Unauthorized` if token doesn't match.
- **File:** `gateway/src/server.ts`
- **Why reverted:** Frontend WebSocket client wasn't passing the token.

### 3. Backend FastAPI API Key Middleware
- **What:** HTTP middleware checking `Authorization: Bearer <BOND_API_KEY>` on all routes
  except `/api/v1/health`, `/docs`, `/openapi.json`, and `OPTIONS` preflight requests.
- **File:** `backend/app/main.py`
- **Why reverted:** Internal callers (gateway→backend proxy, worker→backend) weren't sending the key.

### 4. BOND_API_KEY Resolution & Generation
- **What:** Auto-generate a 32-byte hex API key on first run, store in `.env` and
  `~/.bond/data/.gateway_key`. Resolution chain: env var → file → auto-generate.
- **Files:** `backend/app/main.py` (`_resolve_api_key()`), `backend/app/cli.py` (setup wizard)
- **Why reverted:** Key generation worked but callers weren't consistently reading/sending it.

### 5. Caller Auth Header Injection
- **What:** Add `Authorization: Bearer <BOND_API_KEY>` header to internal service calls:
  - `oauth.py` → gateway provider-api-keys endpoint
  - `persistence_client.py` → gateway persistence API
  - `sandbox/adapters.py` → pass BOND_API_KEY env var into worker containers
- **Files:** `backend/app/core/oauth.py`, `backend/app/agent/persistence_client.py`,
  `backend/app/sandbox/adapters.py`
- **Why reverted:** Not all callers were covered; some had timing issues during startup.

### 6. Setup Wizard Security Credentials
- **What:** Generate BOND_API_KEY and BOND_VAULT_KEY during `make setup`, display credentials
  with security warnings, require "I understand" acknowledgment.
- **File:** `backend/app/cli.py`
- **Why reverted:** Removed along with the auth system it supported.

### 7. Makefile & First-Run Integration
- **What:** `show-credentials` make target, `.env` sourcing in frontend target,
  `BOND_API_KEY` check in `first-run.sh`.
- **Files:** `Makefile`, `scripts/first-run.sh`
- **Why reverted:** Supporting infrastructure for the auth system.

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

**Issues to fix:**
- The gateway and backend must resolve to the **same** key. Both read from `BOND_API_KEY`
  env var → `~/.bond/data/.gateway_key` fallback → auto-generate. If either auto-generates
  independently (e.g., the file doesn't exist and they race), they'll have different keys.
- **Fix:** Ensure `bond init` always writes the key file before either process starts.
  Remove auto-generation — if the key isn't there, fail loudly instead of silently generating
  a different one.

### 4. Backend Worker → Gateway (HTTP)

**File:** `backend/app/agent/persistence_client.py`
**Mechanism:** Reads `BOND_AGENT_TOKEN` from env. Sends as Bearer token
to gateway endpoints like `/api/v1/tool-logs`.

**Issues to fix:**
- Workers running in containers need `BOND_API_KEY` passed via `-e` flag in
  `backend/app/sandbox/adapters.py`. If the host's key rotates (gateway restart with
  auto-generation), running containers have the old key.
- **Fix:** Deterministic key from `bond init`, no auto-generation.

### 5. Gateway → SpacetimeDB

**File:** `gateway/src/conversations/router.ts`
**Mechanism:** Uses `callReducer()` with SpacetimeDB token (separate from API key).

**No auth issue** — this uses a different token. Listed for completeness.

---

## Re-implementation Requirements

Before re-enabling API key auth, ALL of the following must be true:

1. **Every HTTP caller** (frontend, gateway→backend proxy, worker→gateway, oauth resolver)
   must send `Authorization: Bearer <BOND_API_KEY>`.
2. **WebSocket clients** must pass `?token=<BOND_API_KEY>` on connection.
3. **Container workers** must receive `BOND_API_KEY` as an env var.
4. **The setup wizard** must generate and persist the key before any service starts.
5. **Integration tests** must verify auth works end-to-end with the key.
6. **All changes must ship in a single atomic deployment** — no partial rollouts.

---

## Re-implementation Plan

### Phase 1: Deterministic Key (prerequisite)

1. `bond init` (cli.py) generates the key and writes both `.env` and `.gateway_key`.
   Verify this always runs before gateway/backend start.
2. Do not auto-generate keys in gateway or backend. If the key isn't found, fail loudly
   instead of silently generating a different one.
3. In Docker/container deployments, ensure `BOND_API_KEY` is passed as env var to all
   containers that need it.

### Phase 2: Fix All Callers

4. Audit every `fetch()` in `frontend/src/` — replace raw `fetch()` with `apiFetch()` for
   any call to gateway or backend URLs.
5. Add retry/cache-invalidation to `getBondApiKey()` so it doesn't permanently cache an
   empty key.
6. Add re-fetch on WebSocket 4001 close code in `frontend/src/lib/ws.ts`.
7. Add `Authorization: Bearer <BOND_API_KEY>` to all internal service calls:
   - `oauth.py` → gateway provider-api-keys endpoint
   - `persistence_client.py` → gateway persistence API
   - `sandbox/adapters.py` → pass BOND_API_KEY env var into worker containers

### Phase 3: Re-enable Auth

8. Add gateway HTTP middleware in `gateway/src/server.ts`.
9. Add gateway WebSocket auth in `gateway/src/server.ts`.
10. Add backend `check_api_key` middleware in `backend/app/main.py`.
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
