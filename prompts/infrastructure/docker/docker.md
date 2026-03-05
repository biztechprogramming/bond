# Docker Best Practices

Guidelines for creating efficient, secure, and maintainable Docker containers.

## Image Construction
- **Use Minimal Base Images**: Prefer `alpine` or `slim` variants to reduce attack surface and image size.
- **Multi-Stage Builds**: Use multi-stage builds to keep the final production image clean of build tools and intermediate artifacts.
- **Layer Optimization**:
    - Order instructions from least to most frequently changed (e.g., `COPY package.json` before `COPY .`).
    - Combine `RUN` commands (e.g., `apt-get update && apt-get install ... && rm -rf /var/lib/apt/lists/*`) to reduce layer count and size.
- **`.dockerignore`**: Always include a `.dockerignore` file to prevent sensitive files (`.env`, `.git`) and heavy directories (`node_modules`) from entering the build context.

## Security
- **Run as Non-Root**: Use `USER` to switch to a non-privileged user. Never run processes as `root` inside the container.
- **Scan for Vulnerabilities**: Regularly use tools like `trivy` or `docker scan` to identify vulnerabilities in base images and dependencies.
- **Read-Only Filesystem**: Whenever possible, run containers with a read-only root filesystem (`--read-only`).
- **No Secrets in Images**: Never use `ENV` or `ARG` for sensitive data that ends up in the image layers. Use build-time secrets or runtime environment variables.

## Runtime & Connectivity
- **Health Checks**: Define `HEALTHCHECK` in the Dockerfile to allow the orchestrator to monitor container status.
- **Signal Handling**: Ensure the application correctly handles `SIGTERM` for graceful shutdowns. Use `exec` form for `ENTRYPOINT` (`["node", "app.js"]`) instead of shell form.
- **Resource Limits**: Always define CPU and Memory limits to prevent a single container from exhausting host resources.
- **Host Connectivity**:
    - Use `host.docker.internal` to reach services on the host machine.
    - **Linux Note**: Requires `--add-host=host.docker.internal:host-gateway` or `extra_hosts` in Compose.

## Bond-Specific Patterns
- **Workspace Mounts**: Bond typically mounts `~/bond` to `/workspace/bond`. Ensure permissions are handled if the container user ID differs from the host.
- **Persistence**: Use named volumes for persistent data, but remember they are not automatically backed up.
