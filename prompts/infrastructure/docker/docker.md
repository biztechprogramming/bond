# Docker

## When this applies
Working with Docker containers, images, or Bond's container-based agent architecture.

## Bond-Specific
- Agent containers use `host.docker.internal` to reach the gateway — this resolves to the host machine's IP
- On Linux, `host.docker.internal` requires `--add-host=host.docker.internal:host-gateway` in run command or `extra_hosts` in compose
- Default agent mounts: `~/bond` → `/workspace/bond`, `~/.claude` → `/.claude`
- Per-agent data: `data/agents/<id>/` on host → `/data` in container

## Patterns / Gotchas
- `host.docker.internal` does NOT work by default on Linux Docker — only Mac/Windows Docker Desktop adds it automatically
- `COPY` vs `ADD`: `ADD` auto-extracts tarballs and supports URLs — use `COPY` unless you specifically need extraction
- `.dockerignore`: missing it copies `.git`, `node_modules`, `.env` into build context — slows builds and leaks secrets
- `RUN` layer caching: changing ANY instruction invalidates ALL subsequent layers — put `apt-get install` before `COPY . .`
- `apt-get install -y --no-install-recommends`: without `--no-install-recommends`, installs 2-3x more packages
- `USER nonroot`: run as non-root in production — but `npm install` may fail as non-root if it needs to write to `/root`
- Volume mounts: `-v /host:/container` creates bind mount — container sees host file changes in real-time (Bond uses this for workspace)
- Named volumes: persist across container restarts but NOT across `docker compose down -v` — the `-v` flag is destructive
- `ENTRYPOINT` vs `CMD`: `ENTRYPOINT` is the executable, `CMD` is default args — `docker run image arg` overrides CMD but NOT ENTRYPOINT
- BuildKit: `DOCKER_BUILDKIT=1` enables parallel build stages, better caching, secrets mounting — use it always
- `docker exec -it container bash`: `-i` is interactive, `-t` allocates TTY — missing either makes the shell unusable
