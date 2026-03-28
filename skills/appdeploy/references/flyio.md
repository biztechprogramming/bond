# Fly.io Deployment

Best for: Containerized apps, edge deployment, multi-region, long-running servers.

## Prerequisites

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
```

## Deploy

### First time

```bash
fly launch
```

This creates a `fly.toml` config, provisions the app, and deploys. It auto-detects Dockerfiles, or generates one via buildpacks.

### Subsequent deploys

```bash
fly deploy
```

### Dockerfile-based (preferred)

Fly.io works best with a Dockerfile. If your project has one, `fly launch` uses it automatically. If not, Fly generates one using buildpacks.

## Environment Variables

```bash
# Set secrets (encrypted, not visible in dashboard)
fly secrets set KEY=value

# Set multiple
fly secrets set KEY1=value1 KEY2=value2

# List secrets
fly secrets list
```

## Custom Domain

```bash
fly certs add example.com
```

Then add a CNAME or A record pointing to your app. Fly provides free SSL via Let's Encrypt.

## Scaling

```bash
# Scale to multiple instances
fly scale count 2

# Change VM size
fly scale vm shared-cpu-1x

# Available sizes: shared-cpu-1x, shared-cpu-2x, shared-cpu-4x,
# performance-1x, performance-2x, performance-4x, performance-8x
```

## Volumes (Persistent Storage)

```bash
# Create a volume
fly volumes create mydata --size 1 --region ord

# Mount in fly.toml:
# [mounts]
#   source = "mydata"
#   destination = "/data"
```

## Multi-Region Deployment

```bash
# Add regions
fly regions add lax ams

# Scale instances across regions
fly scale count 3
```

Fly automatically distributes instances across your selected regions.

## Health Checks

In `fly.toml`:

```toml
[[services.http_checks]]
  interval = "10s"
  timeout = "2s"
  grace_period = "5s"
  method = "GET"
  path = "/health"
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails | Check Dockerfile — `fly deploy` builds remotely. Use `fly logs` for details |
| App won't start | Ensure your app listens on `0.0.0.0:8080` (Fly's default internal port) |
| Health check failing | Add a `/health` endpoint and configure checks in `fly.toml` |
| Volume data lost | Volumes are region-specific — ensure your app and volume are in the same region |
| Out of memory | Scale up VM size with `fly scale vm` |
| Deploy timeout | Increase `kill_timeout` in `fly.toml` or optimize startup time |
