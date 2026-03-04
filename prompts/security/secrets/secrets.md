# Secrets Management

## When this applies
Handling API keys, credentials, or sensitive configuration.

## Patterns / Gotchas
- `.env` files: NEVER commit to git — add to `.gitignore` BEFORE first commit; git history is permanent
- Environment variables in Docker: `docker run -e VAR=val` is visible in `docker inspect` — use `--env-file` or Docker secrets instead
- Bond loads from both `.env.local` and `.env` — `.env.local` takes precedence; secrets go in `.env.local` only
- Kubernetes secrets are base64-encoded, NOT encrypted — anyone with RBAC access can decode them. Use external secret stores for real security
- API key rotation: always support TWO active keys simultaneously — rotate by adding new key, updating consumers, then revoking old key
- Git history: if a secret was EVER committed, consider it compromised — rotate immediately, then use `git filter-branch` or BFG repo cleaner
- Client-side secrets: `NEXT_PUBLIC_` prefix in Next.js exposes to browser bundle — NEVER put API keys with write access in `NEXT_PUBLIC_` vars
- Secret scanning: GitHub, GitLab have built-in scanners — enable them. Also consider pre-commit hooks (`detect-secrets`, `gitleaks`)
- Process environment: child processes inherit parent's env vars — be careful spawning subprocesses with sensitive env
- Logging: secrets in URLs (query params) get logged by web servers, proxies, and browsers — use headers or POST body instead
