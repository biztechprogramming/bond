# Docker Compose Best Practices

Guidelines for defining and managing multi-container applications with Docker Compose.

## Configuration & Structure
- **Declarative Services**: Every dependency (DB, Cache, Queue) should be defined as a service in the `docker-compose.yml`.
- **Environment Variables**: Use an `.env` file for local defaults, but allow overrides via system environment variables. Use `env_file` for grouping related variables.
- **Profiles**: Use `profiles` to group services that aren't always needed (e.g., `debug`, `tools`, `testing`).

## Reliability
- **Dependency Management**: Use `depends_on` with `condition: service_healthy` to ensure services start in the correct order and are actually ready.
- **Healthchecks**: Always define `healthcheck` for stateful services (databases, APIs) so dependent services can wait for readiness.
- **Restart Policies**: Use `restart: unless-stopped` for production-like services and `no` or `on-failure` for one-off tasks.

## Networking & Volumes
- **Named Networks**: Use custom named networks instead of the default network for better isolation and DNS resolution.
- **Network Aliases**: Use service names as hostnames for internal communication (e.g., `http://api:8080`).
- **Volume Persistence**:
    - Use named volumes for database data to ensure persistence across container restarts.
    - Use bind mounts (`./src:/app/src`) only for development to enable hot-reloading.
- **Cleanup**: Be careful with `docker compose down -v` as it deletes all named volumes defined in the file (Permanent Data Loss).

## Performance & Optimization
- **Build Caching**: Use `cache_from` in CI environments to speed up builds by pulling previous image layers.
- **Resource Constraints**: Define `deploy.resources.limits` to prevent service interference, even in local development.
- **Port Mapping**: Only expose ports to the host (`ports`) that need to be accessed externally. Use `expose` for internal-only communication.

## Bond-Specific Patterns
- **Host Access**: Ensure `extra_hosts: ["host.docker.internal:host-gateway"]` is present for Linux environments needing to reach the Bond gateway.
- **Consistent Naming**: Use `container_name` sparingly to avoid conflicts when running multiple instances of the same project.
