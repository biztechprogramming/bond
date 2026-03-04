# Authentication

## When this applies
Implementing or modifying authentication flows.

## Patterns / Gotchas
- Password hashing: use bcrypt or argon2, NEVER SHA-256/MD5 — those are fast hashes, not password hashes
- bcrypt has a 72-byte input limit — longer passwords are silently truncated. Pre-hash with SHA-256 if accepting long passwords
- Session vs token auth: sessions require server-side storage but are revocable; tokens (JWT) are stateless but non-revocable until expiry
- OAuth2 authorization code flow: PKCE is mandatory for public clients (SPAs, mobile) — implicit flow is deprecated
- `state` parameter in OAuth2: prevents CSRF on the callback — MUST be cryptographically random and validated on return
- Multi-factor: TOTP (Google Authenticator) codes are valid for 30 seconds before AND after — the RFC recommends accepting ±1 step for clock drift
- Cookie auth: `SameSite=Lax` is the browser default now — `SameSite=None` requires `Secure` flag (HTTPS only)
- `HttpOnly` cookies: inaccessible to JavaScript — use for session tokens. Non-HttpOnly only for CSRF tokens read by JS
- Refresh token rotation: issue new refresh token on each use, invalidate the old one — detects token theft (both parties try to use the same old token)
