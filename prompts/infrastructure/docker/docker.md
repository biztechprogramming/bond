## Docker

Principles for containerized development and deployment:

- **Small images** — Use multi-stage builds. Start from slim/alpine base images. Don't install dev dependencies in production images.
- **One process per container** — Each container runs one service. Use compose or orchestration for multi-service setups.
- **Layer caching** — Order Dockerfile instructions from least-changed to most-changed. Copy dependency files before source code.
- **No secrets in images** — Use runtime environment variables, mounted secrets, or vault. Never bake credentials into layers.
- **Health checks** — Every service container should have a HEALTHCHECK instruction or equivalent probe.
