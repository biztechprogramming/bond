# Docker Compose

## When this applies
Working with Docker Compose for multi-service development.

## Patterns / Gotchas
- `depends_on` only waits for container START, not readiness — use `depends_on.condition: service_healthy` with healthcheck for actual readiness
- `extra_hosts: ["host.docker.internal:host-gateway"]` — required on Linux for containers to reach host services (Bond agents need this)
- Network aliases: services are reachable by service name within the compose network — `http://gateway:18789` from another container
- `build.context` vs `build.dockerfile`: context is the directory sent to daemon, dockerfile is the path relative to context
- Volume mount gotcha: `-v ./local:/container` in compose v2 uses relative paths — v1 required absolute paths
- Environment precedence: `environment:` in compose > `.env` file > `env_file:` directive — confusing when values conflict
- `restart: unless-stopped` vs `restart: always`: `unless-stopped` respects manual `docker stop`, `always` restarts even after manual stop
- `docker compose up -d --build`: `--build` forces image rebuild — without it, stale images are reused even if Dockerfile changed
- Port mapping: `"18789:18789"` (quotes required when port starts with `0`) — `ports` exposes to host, `expose` only within compose network
- Compose profiles: `profiles: ["debug"]` — service only starts with `docker compose --profile debug up`; useful for optional services
- `docker compose down` removes containers and default network — `down -v` also removes named volumes (DATA LOSS)
- Secrets in compose: `secrets:` top-level key with file reference — mounts at `/run/secrets/` in container, more secure than environment variables
