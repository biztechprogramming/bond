# Security

## When this applies
Any security-sensitive code, authentication, authorization, or secrets handling.

## Patterns / Gotchas
- Input validation at the boundary, sanitization at the output — validate structure on input, escape/encode when rendering
- Never log sensitive data: passwords, tokens, API keys, PII — redact in log formatters, not call sites
- Timing attacks: use constant-time comparison for secrets — `hmac.compare_digest()` (Python) or `crypto.timingSafeEqual()` (Node)
- CORS: `Access-Control-Allow-Origin: *` combined with `credentials: true` is rejected by browsers — must specify exact origin
- Content-Security-Policy: missing CSP allows XSS via inline scripts — set `script-src 'self'` at minimum
- Rate limiting: implement per-endpoint, not just globally — auth endpoints need stricter limits than read endpoints
