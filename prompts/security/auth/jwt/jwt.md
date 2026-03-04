# JWT

## When this applies
Working with JSON Web Tokens for authentication or API authorization.

## Patterns / Gotchas
- `alg: "none"` attack: some libraries accept unsigned tokens if `alg` is `"none"` — ALWAYS validate algorithm against an allowlist, never trust the header
- RS256 vs HS256 confusion attack: attacker sends RS256 token but sets `alg: HS256` — library uses the PUBLIC key as HMAC secret and validates successfully. Always enforce expected algorithm
- Token expiry (`exp`): short-lived (5-15 min) for access tokens — use refresh tokens for longer sessions. 1-hour access tokens are too long for most cases
- `iat` (issued at) and `nbf` (not before): validate these to prevent token reuse attacks — clock skew tolerance should be <60 seconds
- Payload is base64-encoded, NOT encrypted — anyone can decode it. Never put secrets, passwords, or sensitive PII in JWT payload
- Token revocation: JWTs cannot be revoked before expiry — use a token blocklist (Redis) for forced logout, or keep expiry very short
- Refresh tokens: store in `HttpOnly` cookie, NOT localStorage — localStorage is vulnerable to XSS; cookies with `HttpOnly` + `Secure` + `SameSite=Strict` are safer
- Key rotation: use `kid` (key ID) header to support multiple signing keys — allows rotation without invalidating all existing tokens
- Nested JWTs (JWS inside JWE): almost never needed — if you think you need encryption, consider using opaque tokens with server-side lookup instead
- Size: JWTs go in every request header — keep payload small. Headers have size limits (~8KB in most servers); bloated JWTs can cause silent 431 errors
- `sub` claim: should be user ID, not email — emails change, IDs don't
