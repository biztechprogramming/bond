# Design Doc 059: OAuth Credential Unification via pi-ai

**Status:** In Progress  
**Created:** 2026-03-22  
**Branch:** fix/open-sandbox-get-executor  

## Problem

Bond was originally designed to use `@mariozechner/pi-ai` for OAuth token
management (see `docs/architecture/09-tech-stack.html`, commit `d606ca3`).
This was never implemented — litellm was used instead, which has partial OAuth
support but doesn't handle token refresh, and Claude CLI uses its own internal
OAuth flow via `~/.claude/.credentials.json`.

The result: **three competing credential mechanisms** that don't share tokens:

| Mechanism | Where | Works? |
|-----------|-------|--------|
| litellm `api_key` (injected from DB) | Worker → Anthropic API | ❌ OAuth tokens rejected as `api_key`¹ |
| Claude CLI OAuth (`~/.claude/.credentials.json`) | Coding agent inside worker | ✅ When mounted to correct `$HOME` |
| `ANTHROPIC_API_KEY` env var | Container env | ❌ OAuth tokens (`sk-ant-oat01-`) rejected |

¹ litellm v1.82.5 detects `sk-ant-oat` and sets Bearer auth + OAuth headers,
but Anthropic's API returns 400 — likely needs `claude-code-20250219` beta header
and `x-app: cli` that pi-ai includes but litellm doesn't.

### Claude Max Users Are Blocked

Users with Claude Max subscriptions have **no regular API key** (`sk-ant-api03-`).
They authenticate via OAuth, which produces `sk-ant-oat01-` tokens. These tokens:

- ✅ Work as Bearer tokens with correct headers (pi-ai proves this)
- ✅ Work inside Claude CLI via `.credentials.json`  
- ❌ Don't work as `ANTHROPIC_API_KEY` env var
- ❌ Don't work via litellm's partial OAuth support (missing required headers)
- ❌ Expire and rotate — stale tokens stored in DB become useless

### Test Results (2026-03-22)

| Scenario | Result |
|----------|--------|
| OAuth token as `ANTHROPIC_API_KEY` env var | ❌ "Invalid API key" |
| OAuth `.credentials.json` mounted to correct `$HOME` | ✅ Works |
| OAuth `.credentials.json` mounted to wrong `$HOME` | ❌ "Not logged in" |
| Inside running worker as `bond-agent` with mounted creds | ✅ Works |
| Raw curl with OAuth token + pi-ai-style headers | ❌ 400 Bad Request² |
| No credentials at all | ❌ "Not logged in" |

² Even with `claude-code-20250219` beta header — token may have been stale.
pi-ai handles refresh automatically; we don't.

## Solution: Integrate pi-ai

Restore the original architecture: use `@mariozechner/pi-ai` in the **gateway**
for OAuth token management. The gateway is TypeScript, pi-ai is TypeScript — 
natural fit.

### What pi-ai Provides

1. **OAuth login flow** — `loginAnthropic()` with PKCE
2. **Automatic token refresh** — `refreshAnthropicToken()` using refresh token
3. **Correct API headers** — Bearer auth, `oauth-2025-04-20` beta, `claude-code-20250219`, `x-app: cli`
4. **Multi-provider support** — Same flow for OpenAI Codex, Google, GitHub Copilot
5. **Token-as-API-key formatting** — `getApiKey(credentials)` returns access token

### Architecture Change

```
BEFORE (broken):
  UI saves OAuth token → DB (provider_api_keys) → worker reads stale token
  → litellm rejects it OR Anthropic rejects it

AFTER (working):
  1. User authenticates via pi-ai OAuth flow (one-time)
  2. Gateway stores refresh_token + access_token + expiry in DB
  3. On each worker request, gateway provides fresh access token
  4. Worker passes token to litellm with correct extra_headers
  5. For coding agents, fresh token is written to .credentials.json in container
```

### Implementation Plan

#### Phase 1: Gateway — pi-ai integration (this PR)

1. **Add `@mariozechner/pi-ai` to `gateway/package.json`**

2. **New module: `gateway/src/oauth/anthropic.ts`**
   - Wraps pi-ai's `loginAnthropic()` and `refreshAnthropicToken()`
   - Stores credentials in SpacetimeDB (`provider_api_keys` table) with
     structured JSON: `{ type: "oauth", access, refresh, expires, email }`
   - Auto-refresh on gateway startup and before serving to workers

3. **New API endpoint: `POST /api/v1/oauth/anthropic/login`**
   - Initiates OAuth flow, returns auth URL
   - `POST /api/v1/oauth/anthropic/callback` — completes flow with code

4. **Modify: `GET /api/v1/provider-api-keys/:providerId`**
   - For OAuth providers: check expiry, auto-refresh if needed, return fresh token
   - Add `x-auth-mode: oauth` response header so worker knows to set extra headers

#### Phase 2: Worker — OAuth-aware API calls

5. **Modify: `backend/app/agent/api_key_resolver.py`**
   - Detect OAuth tokens (`sk-ant-oat`) in resolved keys
   - Set `extra_headers` for litellm: `anthropic-beta`, `user-agent`, `x-app`
   - Pass through to `litellm.acompletion(extra_headers=...)` 

6. **Modify: `backend/app/agent/loop.py`**
   - Pass `extra_headers` from resolver to litellm calls

#### Phase 3: Coding Agent — fresh credentials

7. **Modify: `backend/app/sandbox/manager.py`**
   - Before spawning worker container, fetch fresh OAuth token from gateway
   - Write `.credentials.json` with fresh token (not stale mounted file)
   - Keep `_append_credential_mounts` as fallback

8. **Modify: `backend/app/agent/tools/coding_agent.py`**  
   - Keep ANTHROPIC_API_KEY stripping (correct behavior for Claude CLI)
   - Add pre-flight check: verify `.credentials.json` exists and isn't expired

#### Phase 4: Settings UI

9. **Modify: `frontend/src/app/settings/page.tsx`**
   - Add "Login with Claude" button for Anthropic (OAuth flow)
   - Show token status (valid/expired/refresh needed)
   - Keep manual API key input as alternative for users with regular keys

## Files Affected

### Gateway (TypeScript)
- `gateway/package.json` — add `@mariozechner/pi-ai`
- `gateway/src/oauth/anthropic.ts` — new: OAuth flow wrapper
- `gateway/src/persistence/router.ts` — modify: auto-refresh on read
- `gateway/src/server.ts` — add OAuth routes

### Backend (Python)  
- `backend/app/agent/api_key_resolver.py` — detect OAuth, set headers
- `backend/app/agent/loop.py` — pass extra_headers to litellm
- `backend/app/sandbox/manager.py` — fresh token injection
- `backend/app/agent/tools/coding_agent.py` — pre-flight credential check

### Frontend (React)
- `frontend/src/app/settings/page.tsx` — OAuth login button

### Tests
- `tests/test_credential_matrix.py` — update with OAuth-via-litellm tests

## References

- Original architecture: `docs/architecture/09-tech-stack.html` (commit d606ca3)
- pi-ai OAuth: `node_modules/@mariozechner/pi-ai/dist/utils/oauth/anthropic.js`
- pi-ai API adapter: `node_modules/@mariozechner/pi-ai/dist/providers/anthropic.js`
- litellm OAuth detection: `litellm/llms/anthropic/common_utils.py`
- OpenClaw auth profiles: `openclaw/src/agents/auth-profiles/oauth.ts`
