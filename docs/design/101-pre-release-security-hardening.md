# Design Doc 101: Pre-Release Security Hardening

**Status:** DRAFT — Awaiting Approval  
**Author:** Bond AI  
**Date:** 2026-07-XX  
**Depends on:** 001 (Enterprise Authentication), 035 (Secure Agent Execution), 036 (Permission Broker)  

---

## 1. Goal

Establish the minimum security posture required before Bond can be distributed to other users — particularly those running Bond on a shared Tailscale network. This document catalogs every security gap, prioritizes them into tiers (must-have, should-have, nice-to-have), and provides implementation guidance for each.

### Threat Model

Bond is a **single-user, self-hosted AI assistant** that will be shared with technically-capable users who run it on their own machines or within a Tailscale tailnet. The primary threats are:

1. **Lateral access on a tailnet** — Other devices on the same Tailscale network can reach Bond's ports. A curious or compromised peer gets full access to conversations, API keys, and Docker.
2. **Browser-based attacks (CSRF/CORS)** — Any website a user visits can make requests to Bond's localhost ports due to `Access-Control-Allow-Origin: *`.
3. **Credential theft** — API keys (Anthropic, OpenAI, GitHub tokens) are served unauthenticated over HTTP.
4. **Agent impersonation / escalation** — Broker token issuance has no caller verification; a compromised agent can mint tokens or impersonate other agents.
5. **Data exfiltration via Docker socket** — The mounted Docker socket allows arbitrary container creation.

### Non-Goals (for this release)

- Multi-user / team RBAC (covered by Design Doc 001)
- OAuth/SSO federation
- End-to-end encryption of stored data
- Public internet exposure (Tailscale Funnel hardening)

---

## 2. Current State — Vulnerability Inventory

### 2.1 No Authentication on Any Service

| Port  | Service           | Binding   | Auth  | Risk |
|-------|-------------------|-----------|-------|------|
| 18788 | Frontend (Next.js) | localhost | ❌ None | UI fully accessible |
| 18789 | Gateway (Express/WS) | `0.0.0.0` | ❌ None | All APIs + WebSocket open |
| 18790 | Backend (FastAPI)  | `0.0.0.0` | ❌ None | All APIs open |

**Impact:** Anyone who can reach these ports has full control of Bond — read/write conversations, steal API keys, execute agent turns, manage deployments.

### 2.2 CORS Allows All Origins

**Gateway** (`server.ts:122-128`):
```typescript
res.header("Access-Control-Allow-Origin", "*");
res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
```

**Backend** (`main.py:72-77`):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Impact:** Any website the user visits can silently make authenticated requests to Bond. Combined with `allow_credentials=True`, this is a textbook CSRF vector. Note: browsers actually reject `credentials: true` with `origin: *`, but the intent is clearly wrong and some request types (simple GET/POST) still succeed without preflight.

### 2.3 SpacetimeDB Token Served Unauthenticated

**Gateway** (`server.ts:134-140`):
```typescript
app.get("/api/v1/spacetimedb/token", (_req, res) => {
    const token = config.spacetimedbToken;
    res.json({ token });
});
```

**Impact:** This token grants full read/write access to all SpacetimeDB tables — agents, conversations, messages, settings, provider API keys, work plans, MCP servers. Anyone on the network can grab it.

### 2.4 Broker Token Issuance Has No Caller Verification

**Gateway** (`broker/router.ts`): The `/api/v1/broker/token/issue` endpoint creates HMAC-signed agent tokens. While the broker's *action endpoints* validate these tokens (good), the *issuance endpoint itself* has no authentication — any caller can mint a token for any agent ID.

**Impact:** An attacker can create tokens to impersonate any agent, then use those tokens to execute commands through the broker.

### 2.5 Provider API Keys Served in Cleartext

**Gateway** (`persistence/router.ts:132-196`): `GET /provider-api-keys/:providerId` returns the raw API key (or OAuth access token) with no authentication. The field is named `encryptedValue` but the value returned is the *decrypted* plaintext.

**Impact:** Direct theft of LLM provider API keys (Anthropic, OpenAI, etc.) by any network peer.

### 2.6 All Backend Routes Are Unprotected

Every FastAPI router is mounted without any dependency or middleware requiring authentication:

- `/api/v1/settings/*` — Read/write all configuration, including LLM API keys
- `/api/v1/agents/*` — Create, modify, delete agents
- `/api/v1/conversations/*` — Read all conversation history
- `/api/v1/memory/*` — Read/write Bond's memory
- `/api/v1/hosts/*` — Manage remote container hosts (SSH keys, daemon install)
- `/api/v1/mcp/*` — Manage MCP servers, proxy tool calls
- `/api/v1/deployments/*` — Trigger deployments
- `/api/v1/prompts/*` — Modify system prompts
- `/api/v1/llm/complete` — Direct LLM completion endpoint (burns API credits)

### 2.7 WebSocket Has No Authentication

**Gateway** (`server.ts:393-396`):
```typescript
wss.on("connection", (socket, req) => {
    webchat.handleConnection(socket);
});
```

No token, cookie, or any form of identity is checked on WebSocket upgrade. Any client that connects gets a full interactive session.

### 2.8 Docker Socket Mounted in Compose

The `docker-compose.yml` mounts `/var/run/docker.sock` into the container. Combined with unauthenticated API access, this allows arbitrary container creation — effectively root access on the host.

### 2.9 Global Broadcast Endpoint Is Unauthenticated

**Gateway** (`server.ts:370-373`):
```typescript
app.post("/api/v1/broadcast", (req, res) => {
    webchat.broadcast(req.body);
    res.status(200).json({ status: "broadcasted" });
});
```

**Impact:** Anyone can push arbitrary messages to all connected WebSocket clients — potential for phishing, confusion, or UI manipulation.

### 2.10 Error Handler Leaks Internal Details

**Backend** (`main.py:81-95`): The global exception handler returns full error type names, stack traces, and internal paths to the caller. Useful for debugging, dangerous in production.

### 2.11 SQL Injection Surface in Persistence Router

**Gateway** (`persistence/router.ts:106-107`): Settings and API key lookups use string interpolation into SQL queries with only single-quote escaping. While SpacetimeDB's SQL interface may provide some protection, this is a fragile pattern.

```typescript
const escapedKey = key.replace(/'/g, "''");
const rows = await sqlQuery(spacetimedbUrl, spacetimedbModuleName,
    `SELECT key, value, key_type FROM settings WHERE key = '${escapedKey}'`, token);
```

---

## 3. Prioritized Security Work Items

### Tier 1 — MUST HAVE (Blocks Release)

These items prevent trivial, unauthenticated access to all of Bond's data and capabilities.

#### 3.1 Bearer Token Authentication Gate

**What:** A single shared secret (`BOND_AUTH_TOKEN`) that must be presented as a `Bearer` token on all HTTP and WebSocket requests.

**Where it applies:**
- **Backend (FastAPI):** Global middleware or dependency that checks `Authorization: Bearer <token>` on every route except `GET /api/v1/health`.
- **Gateway (Express):** Middleware on all routes except `GET /health` and `/webhooks/*` (which use their own HMAC signature verification).
- **Gateway (WebSocket):** Check token in the `Sec-WebSocket-Protocol` header or as a query parameter during the upgrade handshake. Reject connections without a valid token.
- **Frontend:** Store the token in a `httpOnly` cookie or pass it via environment variable at build time. The frontend proxies all API calls, so it can inject the header.

**Token lifecycle:**
1. On first run, if `BOND_AUTH_TOKEN` is not set, Bond auto-generates a 32-byte random token and writes it to `~/.bond/data/.auth_token` (mode `0600`).
2. The token is displayed once in the startup log: `[bond] Auth token: bond_xxxx... (set BOND_AUTH_TOKEN to override)`.
3. Users can set `BOND_AUTH_TOKEN` in `.env` to use a custom token.
4. The frontend reads the token from a local file or environment variable — it never prompts the user for a password.

**Implementation notes:**
- Use constant-time comparison (`hmac.compare_digest` in Python, `crypto.timingSafeEqual` in Node).
- Return `401 Unauthorized` with a generic message — never reveal whether the token format is wrong vs. the value is wrong.
- The health endpoint remains unauthenticated for monitoring/load balancer probes.

#### 3.2 Lock Down CORS

**What:** Replace `Access-Control-Allow-Origin: *` with the actual frontend origin.

**Backend (`main.py`):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{settings.frontend_port}"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Gateway (`server.ts`):**
```typescript
const allowedOrigin = config.frontendOrigin; // already resolved from bond.json
res.header("Access-Control-Allow-Origin", allowedOrigin);
```

**Additional origins:** If the user accesses Bond via a Tailscale hostname (e.g., `http://my-machine:18788`), they should be able to configure additional allowed origins via `BOND_CORS_ORIGINS` (comma-separated) or `bond.json`.

#### 3.3 Gate the SpacetimeDB Token Endpoint

**What:** The `GET /api/v1/spacetimedb/token` endpoint must require the `BOND_AUTH_TOKEN` (covered by 3.1's middleware). No additional work beyond the global auth gate.

**Verify:** After 3.1 is implemented, confirm this endpoint returns `401` without the bearer token.

#### 3.4 Gate Broker Token Issuance

**What:** The `POST /api/v1/broker/token/issue` endpoint must require the `BOND_AUTH_TOKEN`. Currently, the broker's action endpoints correctly validate *agent tokens* (HMAC-signed, scoped to agent+session), but the issuance endpoint itself is open.

**Implementation:** The global auth middleware from 3.1 covers this. The broker's internal `authMiddleware` (which validates agent tokens) remains unchanged — it protects agent-scoped actions. The outer auth gate protects the issuance endpoint.

#### 3.5 Protect Provider API Key Endpoints

**What:** Ensure `GET /provider-api-keys/:providerId` is behind the auth gate. Additionally, the response should **never** return the raw key to the frontend — return a masked version (`sk-ant-...xxxx`) for display, and only serve the full key to internal backend/worker callers.

**Implementation:**
- Add a `X-Internal-Caller` header check or a separate internal-only route for worker API key resolution.
- The frontend-facing endpoint returns `{ providerId, configured: true, keyPreview: "sk-...7f2a" }`.
- The worker-facing endpoint (called by `api_key_resolver.py`) returns the full key but is only reachable from `127.0.0.1` or via the agent's broker token.

---

### Tier 2 — SHOULD HAVE (Ship Shortly After Release)

These items significantly improve security but aren't exploitable without first bypassing Tier 1 controls.

#### 3.6 Bind Services to localhost by Default

**What:** Change the default bind address for the gateway and backend from `0.0.0.0` to `127.0.0.1`.

**Gateway** (`config/index.ts:65`):
```typescript
// Change from: const host = process.env.BOND_GATEWAY_HOST || gw.host || "0.0.0.0";
const host = process.env.BOND_GATEWAY_HOST || gw.host || "127.0.0.1";
```

**Backend** (`Dockerfile`, line 42):
```dockerfile
# Change from: uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 18790
uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 18790
```

**Tailscale users:** When using Tailscale, users explicitly set `BOND_GATEWAY_HOST=0.0.0.0` (or their Tailscale IP) in `.env`. This is an opt-in to network exposure, not a default.

**Docker users:** The `docker-compose.yml` port mappings (`18789:18789`) already handle external access — the container-internal bind doesn't need to be `0.0.0.0` unless other containers need direct access. Document the correct configuration for Docker networking.

#### 3.7 Authenticate WebSocket Upgrade

**What:** Require the auth token during the WebSocket handshake.

**Implementation options (pick one):**
1. **Query parameter:** `ws://host:18789/ws?token=<BOND_AUTH_TOKEN>` — simple, but token appears in server logs and browser history.
2. **Subprotocol header:** Client sends `Sec-WebSocket-Protocol: bond, <token>`. Server validates and responds with `Sec-WebSocket-Protocol: bond`. Cleaner, but slightly more complex client-side.
3. **First-message auth:** Accept the connection, require the first message to be `{"type": "auth", "token": "..."}`, close the socket if not received within 5 seconds. Most flexible.

**Recommendation:** Option 3 (first-message auth) — it's the most compatible with proxies and doesn't leak the token in URLs.

#### 3.8 Sanitize Error Responses

**What:** In production mode, the global exception handler should return generic error messages without stack traces, internal paths, or exception type names.

```python
if os.environ.get("BOND_ENV") == "production":
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Internal server error", "path": str(request.url.path)},
    )
```

**Development mode** (default) continues to return full details for debugging.

#### 3.9 Parameterize SpacetimeDB Queries

**What:** Replace string-interpolated SQL in `persistence/router.ts` with parameterized queries or a query builder. If SpacetimeDB's HTTP SQL interface doesn't support parameterized queries, add robust input validation (allowlist of characters for setting keys, UUID validation for IDs).

**Affected endpoints:**
- `GET /settings/:key`
- `GET /provider-api-keys/:providerId`
- Any other `sqlQuery()` call with user-supplied input

#### 3.10 Rate Limiting on Auth Endpoints

**What:** Add rate limiting to prevent brute-force attacks on the auth token.

**Implementation:**
- Limit failed auth attempts to 10 per minute per IP.
- After 10 failures, return `429 Too Many Requests` with a `Retry-After` header.
- Use an in-memory store (Map with TTL) — no need for Redis at this scale.
- Apply to both HTTP and WebSocket auth.

#### 3.11 Secure the Broadcast Endpoint

**What:** The `POST /api/v1/broadcast` endpoint is already covered by the global auth gate (3.1), but additionally restrict it to internal callers only (localhost or agent broker tokens). No external client should be able to push arbitrary messages to all WebSocket connections.

---

### Tier 3 — NICE TO HAVE (Post-Release Improvements)

These items improve defense-in-depth and operational security.

#### 3.12 Tailscale Identity Integration

**What:** For users running Bond behind Tailscale Serve or a Tailscale-aware reverse proxy, extract the `Tailscale-User-Login` and `Tailscale-User-Name` headers to identify callers without passwords.

**Implementation:**
- Add an optional `BOND_AUTH_MODE=tailscale` setting.
- When enabled, trust `Tailscale-User-*` headers (only if the request comes from the Tailscale interface — verify source IP is in the `100.x.x.x` CGNAT range).
- Log the Tailscale identity on every request for audit purposes.
- This is a stepping stone toward multi-user support (Design Doc 001).

#### 3.13 Audit Logging for Security Events

**What:** Log all authentication attempts (success and failure), token issuance, API key access, and configuration changes to a structured audit log.

**Implementation:**
- Append to `~/.bond/data/audit.jsonl` (one JSON object per line).
- Fields: `timestamp`, `event_type`, `source_ip`, `user_agent`, `path`, `result`, `details`.
- Rotate at 10MB, keep 5 files.
- This complements Design Doc 085 (Audit Trails) but focuses specifically on security events.

#### 3.14 Docker Socket Access Controls

**What:** Reduce the blast radius of the mounted Docker socket.

**Options:**
1. **Socket proxy:** Use a Docker socket proxy (e.g., `tecnativa/docker-socket-proxy`) that whitelists only the API calls Bond needs (container create, start, stop, logs, exec). Deny volume mounts, privileged mode, and host networking.
2. **Rootless Docker:** Document and recommend rootless Docker for Bond deployments.
3. **Remove socket mount from default compose:** Only mount the socket in an explicit `docker-compose.agent.yml` overlay that users opt into.

#### 3.15 TLS for Non-Tailscale Deployments

**What:** For users who expose Bond on a LAN without Tailscale (which provides its own encryption), provide optional TLS termination.

**Implementation:**
- Support `BOND_TLS_CERT` and `BOND_TLS_KEY` environment variables.
- When set, the gateway serves HTTPS and WSS.
- Alternatively, document using a reverse proxy (Caddy, nginx) for TLS termination.

#### 3.16 Content Security Policy Headers

**What:** Add CSP headers to the frontend to prevent XSS via inline scripts.

```
Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self' ws://localhost:18789;
```

---

## 4. Implementation Order

```
Phase 1 (Blocks Release) — ~3-5 days
├── 3.1  Bearer token auth gate (backend + gateway + frontend)
├── 3.2  Lock down CORS
├── 3.3  Verify SpacetimeDB token is gated (free after 3.1)
├── 3.4  Verify broker issuance is gated (free after 3.1)
└── 3.5  Mask API keys in frontend-facing responses

Phase 2 (Fast Follow) — ~2-3 days
├── 3.6  Bind to localhost by default
├── 3.7  WebSocket auth (first-message pattern)
├── 3.8  Sanitize error responses
├── 3.9  Parameterize SpacetimeDB queries
├── 3.10 Rate limiting
└── 3.11 Restrict broadcast endpoint

Phase 3 (Post-Release) — ~3-5 days
├── 3.12 Tailscale identity integration
├── 3.13 Security audit logging
├── 3.14 Docker socket access controls
├── 3.15 Optional TLS
└── 3.16 CSP headers
```

---

## 5. Files Affected

### Phase 1
| File | Change |
|------|--------|
| `backend/app/main.py` | Add auth middleware, fix CORS origins |
| `backend/app/api/v1/health.py` | Exempt from auth |
| `backend/app/config.py` | Add `auth_token` setting |
| `gateway/src/server.ts` | Add auth middleware, fix CORS, gate WS |
| `gateway/src/config/index.ts` | Add `authToken` to config, change default bind |
| `gateway/src/persistence/router.ts` | Mask API key responses |
| `frontend/src/**` | Inject auth token in API calls |
| `frontend/.env.local` | Add `BOND_AUTH_TOKEN` |
| `.env.example` | Document `BOND_AUTH_TOKEN` |
| `Dockerfile` | Update bind addresses |
| `docker-compose.yml` | Add `BOND_AUTH_TOKEN` env var |

### Phase 2
| File | Change |
|------|--------|
| `gateway/src/server.ts` | First-message WS auth, rate limiting, broadcast restriction |
| `gateway/src/persistence/router.ts` | Parameterize SQL queries |
| `backend/app/main.py` | Conditional error detail based on `BOND_ENV` |

---

## 6. Migration / Backward Compatibility

### Existing Users (upgrading)
- On first start after upgrade, if `BOND_AUTH_TOKEN` is not set, Bond generates one and prints it to stdout. The user must note this token to access the UI.
- The frontend will show a "connection refused" state if the token is missing or wrong, with a clear message pointing to the startup log.
- All existing data (conversations, settings, API keys) remains unchanged — this is purely an access control layer.

### New Users (fresh install)
- The setup flow (`bond setup` or first Docker start) generates the token automatically.
- The README documents how to find and use the token.

### Breaking Changes
- **API clients** that call Bond's REST API directly (scripts, integrations) must add the `Authorization: Bearer <token>` header. Document this prominently in the changelog.
- **WebSocket clients** must send an auth message within 5 seconds of connecting (Phase 2).

---

## 7. Testing Strategy

### Unit Tests
- Auth middleware rejects requests without token → `401`
- Auth middleware rejects requests with wrong token → `401`
- Auth middleware accepts requests with correct token → passes through
- Health endpoint works without token
- CORS rejects requests from non-allowed origins
- API key masking returns preview, not full key
- Rate limiter blocks after threshold

### Integration Tests
- Full flow: frontend → gateway → backend with auth token
- WebSocket connection with first-message auth
- Agent worker → broker token issuance → broker action (internal auth chain)
- Webhook delivery (uses HMAC signature, not bearer token)

### Manual Testing
- Fresh install: verify token is auto-generated and printed
- Access UI without token: verify clear error message
- Access from another Tailscale device: verify auth is required
- Visit a malicious page while Bond is running: verify CORS blocks the request

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Users lose their auth token | Medium | Token is persisted in `~/.bond/data/.auth_token`; can be re-read at any time. Add `bond token show` CLI command. |
| Auth breaks agent worker ↔ gateway communication | High | Agent workers use broker tokens (HMAC-signed), not the user auth token. Internal service-to-service calls on localhost are exempt from the user auth gate. |
| CORS too restrictive for Tailscale hostnames | Medium | Support `BOND_CORS_ORIGINS` env var for additional allowed origins. |
| Rate limiting locks out legitimate user | Low | 10 failures/min is generous; rate limit resets automatically. No permanent lockout. |
| Token in `.env` file committed to git | Medium | `.env` is already in `.gitignore`. Add pre-commit hook documentation. |

---

## 9. Relationship to Existing Design Docs

| Doc | Relationship |
|-----|-------------|
| **001 — Enterprise Authentication** | This doc is a *subset* of 001. We implement the minimum viable auth (shared token) now; 001's full user/session/RBAC system comes later. Nothing in this doc conflicts with 001. |
| **035 — Secure Agent Execution** | Complementary. 035 focuses on agent-to-host security boundaries; this doc focuses on user-to-Bond access control. |
| **036 — Permission Broker** | The broker already has its own HMAC token system for agent actions. This doc adds the *outer* auth layer that gates who can issue those tokens. |
| **085 — Audit Trails** | This doc's §3.13 is a lightweight precursor to the full audit system in 085. |

---

**Awaiting approval to proceed with Phase 1 implementation.**
