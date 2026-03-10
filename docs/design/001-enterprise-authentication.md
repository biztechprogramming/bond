# Design Doc 001: Enterprise Authentication for Bond

**Status:** DRAFT — Awaiting Approval  
**Author:** Bond AI  
**Date:** 2025-01-XX  

---

## 1. Goal

Add enterprise-grade authentication to Bond so that:
- Users can **register, log in, and log out** securely
- Users can **edit their profile** (display name, email, avatar, timezone, preferences)
- All **agents and conversations are scoped per user** — users only see their own data
- **User sessions are tracked** with device info, IP, and expiry for security auditing
- The system supports future expansion (teams, RBAC, OAuth/SSO)

---

## 2. Current State

| Component | Technology | Auth Today |
|-----------|-----------|------------|
| Backend | Python / FastAPI / SQLAlchemy + SQLite (aiosqlite) | None |
| Gateway | TypeScript / Hono + WebSocket | None — ephemeral sessions |
| Frontend | Next.js (App Router) + SpacetimeDB client | None |
| Secrets | Fernet-encrypted vault (`core/vault.py`) | No user scoping |
| Database | SQLite with WAL mode, migrations in `migrations/` | No `user_id` columns |

**Key finding:** The `agents`, `conversations`, and `conversation_messages` tables have no `user_id` column. The gateway `Session` type is purely a WebSocket session with no identity.

---

## 3. ER Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER-BASED SCHEMA                              │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐       ┌──────────────────────────┐
│       users          │       │     user_sessions        │
├──────────────────────┤       ├──────────────────────────┤
│ id           TEXT PK │──┐    │ id            TEXT PK    │
│ email        TEXT UQ │  │    │ user_id       TEXT FK ───│──► users.id
│ username     TEXT UQ │  │    │ token_hash    TEXT UQ    │
│ display_name TEXT    │  │    │ device_info   TEXT       │
│ avatar_url   TEXT    │  │    │ ip_address    TEXT       │
│ timezone     TEXT    │  │    │ user_agent    TEXT       │
│ password_hash TEXT   │  │    │ expires_at    TIMESTAMP  │
│ is_active    INT     │  │    │ last_active_at TIMESTAMP │
│ is_admin     INT     │  │    │ created_at    TIMESTAMP  │
│ preferences  JSON    │  │    │ revoked_at    TIMESTAMP  │
│ created_at   TS      │  │    └──────────────────────────┘
│ updated_at   TS      │  │
└──────────────────────┘  │    ┌──────────────────────────┐
                          │    │     user_audit_log       │
                          │    ├──────────────────────────┤
                          ├───►│ id            TEXT PK    │
                          │    │ user_id       TEXT FK ───│──► users.id
                          │    │ action        TEXT       │
                          │    │ ip_address    TEXT       │
                          │    │ user_agent    TEXT       │
                          │    │ metadata      JSON       │
                          │    │ created_at    TIMESTAMP  │
                          │    └──────────────────────────┘
                          │
                          │    ┌──────────────────────────┐
                          │    │   agents (MODIFIED)      │
                          │    ├──────────────────────────┤
                          ├───►│ ...existing columns...   │
                          │    │ user_id  TEXT FK ─────────│──► users.id
                          │    └──────────────────────────┘
                          │
                          │    ┌──────────────────────────┐
                          │    │ conversations (MODIFIED) │
                          │    ├──────────────────────────┤
                          └───►│ ...existing columns...   │
                               │ user_id  TEXT FK ─────────│──► users.id
                               └──────────────────────────┘
```

### 3.1 Table Definitions

#### `users`
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | ULID |
| `email` | TEXT | UNIQUE, NOT NULL | Login identifier |
| `username` | TEXT | UNIQUE, NOT NULL | Display handle |
| `display_name` | TEXT | | Full name |
| `avatar_url` | TEXT | | Profile picture URL |
| `timezone` | TEXT | DEFAULT 'UTC' | User timezone (IANA) |
| `password_hash` | TEXT | NOT NULL | bcrypt hash |
| `is_active` | INTEGER | DEFAULT 1 | Soft-disable account |
| `is_admin` | INTEGER | DEFAULT 0 | Admin flag |
| `preferences` | JSON | DEFAULT '{}' | Theme, notifications, etc. |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Auto-updated via trigger |

#### `user_sessions`
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | ULID |
| `user_id` | TEXT | FK → users.id, NOT NULL | Session owner |
| `token_hash` | TEXT | UNIQUE, NOT NULL | SHA-256 of session token |
| `device_info` | TEXT | | Device/browser fingerprint |
| `ip_address` | TEXT | | Client IP at login |
| `user_agent` | TEXT | | Full UA string |
| `expires_at` | TIMESTAMP | NOT NULL | Session expiry (default 30 days) |
| `last_active_at` | TIMESTAMP | | Updated on each authenticated request |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |
| `revoked_at` | TIMESTAMP | | NULL = active, set on logout |

#### `user_audit_log`
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | TEXT | PK | ULID |
| `user_id` | TEXT | FK → users.id, NOT NULL | Who did it |
| `action` | TEXT | NOT NULL | `login`, `logout`, `password_change`, `profile_update`, `session_revoked` |
| `ip_address` | TEXT | | |
| `user_agent` | TEXT | | |
| `metadata` | JSON | | Action-specific details |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | |

---

## 4. Authentication Architecture

### 4.1 Auth Strategy: Session Tokens (not JWT)

**Decision:** Use **opaque session tokens** stored in `HttpOnly` cookies, with server-side session records in `user_sessions`.

**Why not JWT?**
- Bond is a monolithic local-first app, not a distributed microservices system
- Session tokens can be instantly revoked (logout = delete session row)
- No token refresh complexity
- HttpOnly cookies prevent XSS token theft
- Server-side session state enables rich session tracking (IP, device, last active)

**Token format:** 32-byte cryptographically random token, base64url-encoded (43 chars). Stored as SHA-256 hash in the database (the raw token is only in the cookie).

### 4.2 Password Hashing

- **Algorithm:** bcrypt via `passlib[bcrypt]` (or `bcrypt` directly)
- **Work factor:** 12 rounds (default)
- Future: support Argon2id upgrade path

### 4.3 Auth Flow

```
┌──────────┐     POST /api/v1/auth/register      ┌──────────┐
│          │ ──────────────────────────────────►   │          │
│          │     { email, username, password }     │          │
│          │                                      │          │
│          │     POST /api/v1/auth/login           │          │
│ Frontend │ ──────────────────────────────────►   │ Backend  │
│          │     { email, password }               │          │
│          │  ◄── Set-Cookie: bond_session=<token> │          │
│          │                                      │          │
│          │     POST /api/v1/auth/logout          │          │
│          │ ──────────────────────────────────►   │          │
│          │  ◄── Clear-Cookie                     │          │
│          │                                      │          │
│          │     GET /api/v1/auth/me               │          │
│          │ ──────────────────────────────────►   │          │
│          │  ◄── { user profile }                 │          │
└──────────┘                                      └──────────┘
```

### 4.4 Gateway WebSocket Auth

The gateway currently manages WebSocket sessions. With auth:

1. Frontend sends session cookie with the WebSocket upgrade request
2. Gateway validates the cookie against the backend (`GET /api/v1/auth/validate`)
3. Gateway associates the WebSocket session with a `userId`
4. All gateway operations (conversations, messages) are scoped to that user

```
┌──────────┐   WS upgrade + cookie   ┌─────────┐   validate   ┌─────────┐
│ Frontend │ ──────────────────────►  │ Gateway │ ────────────► │ Backend │
│          │                          │         │ ◄──── userId  │         │
│          │  ◄── WS connected        │         │               │         │
└──────────┘                          └─────────┘               └─────────┘
```

---

## 5. API Endpoints

### 5.1 Auth Routes (`/api/v1/auth/`)

| Method | Path | Description | Auth Required |
|--------|------|-------------|---------------|
| POST | `/auth/register` | Create account | No |
| POST | `/auth/login` | Authenticate, get session | No |
| POST | `/auth/logout` | Revoke current session | Yes |
| POST | `/auth/logout-all` | Revoke all sessions | Yes |
| GET | `/auth/me` | Get current user profile | Yes |
| PATCH | `/auth/me` | Update profile (name, email, avatar, timezone, preferences) | Yes |
| POST | `/auth/change-password` | Change password | Yes |
| GET | `/auth/sessions` | List active sessions | Yes |
| DELETE | `/auth/sessions/{id}` | Revoke specific session | Yes |
| GET | `/auth/validate` | Validate session (for gateway) | Internal |

### 5.2 Request/Response Schemas

```python
# Registration
class RegisterRequest(BaseModel):
    email: str          # validated as email
    username: str       # 3-30 chars, alphanumeric + underscores
    password: str       # min 8 chars
    display_name: str | None = None

class AuthResponse(BaseModel):
    user: UserProfile
    message: str

# Login
class LoginRequest(BaseModel):
    email: str
    password: str

# Profile Update
class ProfileUpdateRequest(BaseModel):
    display_name: str | None = None
    email: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None
    preferences: dict | None = None

# User Profile (returned by /me)
class UserProfile(BaseModel):
    id: str
    email: str
    username: str
    display_name: str | None
    avatar_url: str | None
    timezone: str
    is_admin: bool
    preferences: dict
    created_at: datetime

# Session Info (returned by /sessions)
class SessionInfo(BaseModel):
    id: str
    device_info: str | None
    ip_address: str | None
    last_active_at: datetime | None
    created_at: datetime
    is_current: bool        # True if this is the requesting session

# Password Change
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
```

---

## 6. Security Measures

### 6.1 Cookie Configuration
```python
response.set_cookie(
    key="bond_session",
    value=token,
    httponly=True,          # Prevents JS access (XSS protection)
    secure=False,           # False for localhost, True in production
    samesite="lax",         # CSRF protection
    max_age=30 * 24 * 3600, # 30 days
    path="/",
)
```

### 6.2 Rate Limiting
- Login: 5 attempts per minute per IP
- Registration: 3 per hour per IP
- Password change: 3 per hour per user

### 6.3 Session Security
- Sessions expire after 30 days of inactivity
- `last_active_at` updated on each authenticated request (debounced to every 5 minutes)
- Users can view and revoke individual sessions
- "Logout all" revokes every session except current (or including current)

### 6.4 Password Requirements
- Minimum 8 characters
- No maximum length (bcrypt truncates at 72 bytes — we'll validate this)
- No complexity rules (length is the best security measure)

### 6.5 Audit Trail
Every auth action is logged to `user_audit_log`:
- `login` — successful login (IP, device)
- `login_failed` — failed login attempt
- `logout` — explicit logout
- `session_revoked` — user revoked a session
- `password_change` — password was changed
- `profile_update` — profile fields changed (which fields in metadata)

---

## 7. Migration Plan for Existing Data

### 7.1 New Migration: `000008_auth.up.sql`

1. Create `users` table
2. Create `user_sessions` table  
3. Create `user_audit_log` table
4. Add `user_id` column to `agents` (nullable initially)
5. Add `user_id` column to `conversations` (nullable initially)
6. Create a **default user** (for existing data migration)
7. Backfill `user_id` on existing agents/conversations with the default user
8. Add NOT NULL constraint after backfill (via table rebuild in SQLite)

### 7.2 Default User Bootstrap

On first startup after migration, if no users exist:
- Create a default admin user from environment variables:
  - `BOND_ADMIN_EMAIL` (default: `admin@localhost`)
  - `BOND_ADMIN_USERNAME` (default: `admin`)
  - `BOND_ADMIN_PASSWORD` (required, or auto-generated and printed to console)
- Assign all existing agents/conversations to this user

---

## 8. Implementation Plan (Phased)

### Phase 1: Database & Core Auth (Backend)
**Complexity: L | ~2-3 hours**

- [ ] Create migration `000008_auth.up.sql` with all tables
- [ ] Add SQLAlchemy models: `User`, `UserSession`, `UserAuditLog`
- [ ] Create `backend/app/core/auth.py` — password hashing, token generation, session validation
- [ ] Create `backend/app/db/repositories/auth.py` — user CRUD, session CRUD
- [ ] Create `backend/app/api/v1/auth.py` — all auth endpoints
- [ ] Add `get_current_user` FastAPI dependency
- [ ] Wire auth middleware into `main.py`
- [ ] Default user bootstrap in `lifespan()`

### Phase 2: Per-User Data Scoping (Backend)
**Complexity: M | ~1-2 hours**

- [ ] Update agents repository to filter by `user_id`
- [ ] Update conversations repository to filter by `user_id`
- [ ] Update all existing API endpoints to use `current_user.id`
- [ ] Add `user_id` to agent/conversation creation flows

### Phase 3: Gateway Auth Integration
**Complexity: M | ~1-2 hours**

- [ ] Add session cookie validation on WebSocket upgrade
- [ ] Add `userId` to gateway `Session` type
- [ ] Scope conversation operations to authenticated user
- [ ] Add `/auth/validate` call from gateway to backend

### Phase 4: Frontend Auth UI
**Complexity: L | ~2-3 hours**

- [ ] Login page (`/login`)
- [ ] Registration page (`/register`)
- [ ] Profile page (`/profile`) with edit form
- [ ] Session management UI (view/revoke sessions)
- [ ] Auth context provider (React context with user state)
- [ ] Protected route wrapper (redirect to login if unauthenticated)
- [ ] Update WebSocket connection to include credentials
- [ ] Logout button in sidebar/header

### Phase 5: Polish & Hardening
**Complexity: S | ~1 hour**

- [ ] Rate limiting middleware
- [ ] Session cleanup job (expire old sessions)
- [ ] Comprehensive tests for auth flows
- [ ] CSRF protection review

---

## 9. File Changes Summary

### New Files
| File | Description |
|------|-------------|
| `migrations/000008_auth.up.sql` | Auth tables + agents/conversations user_id |
| `migrations/000008_auth.down.sql` | Rollback migration |
| `backend/app/core/auth.py` | Password hashing, token gen, session validation |
| `backend/app/db/models/user.py` | SQLAlchemy User, UserSession, UserAuditLog models |
| `backend/app/db/repositories/auth.py` | User & session CRUD |
| `backend/app/api/v1/auth.py` | Auth API endpoints |
| `frontend/src/app/login/page.tsx` | Login page |
| `frontend/src/app/register/page.tsx` | Registration page |
| `frontend/src/app/profile/page.tsx` | Profile & session management |
| `frontend/src/hooks/useAuth.ts` | Auth context & hooks |
| `frontend/src/components/auth/AuthGuard.tsx` | Protected route wrapper |

### Modified Files
| File | Change |
|------|--------|
| `backend/app/main.py` | Add auth router, bootstrap default user |
| `backend/app/api/deps.py` | Add `get_current_user` dependency |
| `backend/app/config.py` | Add auth-related settings |
| `gateway/src/sessions/types.ts` | Add `userId` to Session |
| `gateway/src/sessions/manager.ts` | Validate session on connect |
| `gateway/src/server.ts` | Auth middleware for WS upgrade |
| `frontend/src/app/layout.tsx` | Wrap with AuthProvider |
| `frontend/src/app/page.tsx` | Wrap with AuthGuard |
| `frontend/src/lib/ws.ts` | Send credentials with WS |

---

## 10. Open Questions

1. **First-user experience:** Should the first user to register automatically become admin, or require env var setup? (Proposed: env var for security)

2. **SpacetimeDB integration:** Some data is in SpacetimeDB. Should users/auth also live there, or stay in SQLite? (Proposed: SQLite — auth is backend-owned, SpacetimeDB is for real-time sync)

3. **OAuth/SSO:** Should we design the schema to support future Google/GitHub OAuth? (Proposed: yes — add `auth_provider` and `external_id` columns to `users` table in a future migration)

4. **Multi-tenant vs. single-instance:** Is Bond intended for a single user with multiple devices, or multiple distinct users? (Design supports both — the `is_admin` flag and per-user scoping handle either case)

---

## 11. Dependencies

### Python packages (new)
- `bcrypt` — password hashing
- `python-ulid` — ULID generation (if not already used)

### No new frontend packages needed
- Next.js built-in cookie handling
- Existing fetch/WS infrastructure

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking existing single-user workflows | High | Default user migration preserves all data |
| SQLite concurrent writes during auth | Medium | WAL mode already enabled; session updates are simple |
| Cookie not sent with WebSocket | Medium | Use `credentials: 'include'` on WS connection |
| Password in transit | Low | Localhost only; HTTPS recommended for remote |

---

**Awaiting approval to proceed with implementation.**
