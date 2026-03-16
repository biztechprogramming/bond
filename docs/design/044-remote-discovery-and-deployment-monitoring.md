# Design Doc 044: Remote Discovery & Deployment Monitoring

**Status:** Draft  
**Date:** 2026-03-16  
**Depends on:** 039 (Deployment Agents), 042 (Deployment Tab UI), 043 (Deployment UX & Resources)

---

## 1. The Problem

Bond can deploy scripts to remote servers and manage deployment pipelines, but there's a missing workflow before the first deployment: **understanding how an application is already deployed.** Most real-world deployments don't start from scratch — there's a running application on a server, with nginx configs, .env files, systemd services, DNS records, and connections to databases, caches, and other services. Before Bond can deploy to these environments, it needs to understand the current state.

Additionally, deployment agents today are reactive — they deploy when told to and run scheduled health checks. But there's no **continuous monitoring intelligence** where agents proactively watch logs, verify health checks, track error patterns, and file issues — while being smart enough not to duplicate existing open issues.

This document addresses five gaps:

1. **Remote discovery** — An agent SSHes to a remote server and maps out exactly how an application is deployed (nginx, .env, systemd, Docker, etc.)
2. **Cross-server topology** — The agent follows connections to discover databases, caches, and other services on other servers
3. **DNS/Route 53 discovery** — Mapping the entrypoint: which domain points where, through what CDN or load balancer
4. **Replication & improvement proposals** — From the discovery, generate deployment scripts that exactly replicate the setup, plus tiered improvement recommendations
5. **Continuous monitoring with intelligent issue management** — Cron-scheduled agents that watch logs, verify health, and file non-duplicate GitHub issues

All generated scripts must use the existing script types and infrastructure: bash deployment scripts with `meta:` headers registered in the script registry, executed via the broker, promoted through the pipeline (Doc 039 §5), or pipeline-as-code `.bond/deploy.yml` files (Doc 042 §14.2).

---

## 2. Remote Discovery

### 2.1 Discovery Flow

The user points Bond at a server and says "figure out how this app is deployed." The deployment agent SSHes to the server, runs a structured discovery, and produces a **deployment manifest** — a complete description of how the application runs.

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────────────┐
│  User        │     │  Deploy      │     │  Remote Server           │
│              │     │  Agent       │     │                          │
│ "Discover    │────►│              │     │  nginx, app, db,         │
│  app on      │     │  Calls       │────►│  .env, systemd, docker   │
│  prod-01"    │     │  broker      │     │                          │
│              │     │  /deploy     │◄────│  Discovery results       │
│              │◄────│  action:     │     │                          │
│              │     │  "discover"  │     └──────────────────────────┘
│  Receives    │     │              │
│  manifest +  │     │  Follows     │     ┌──────────────────────────┐
│  proposals   │     │  connections │────►│  Database Server         │
│              │     │              │◄────│  pg_version, roles, dbs  │
│              │     │              │     └──────────────────────────┘
│              │     │              │
│              │     │  Checks DNS  │     ┌──────────────────────────┐
│              │     │              │────►│  Route 53 / DNS          │
│              │     │              │◄────│  A/CNAME records, CDN    │
└──────────────┘     └──────────────┘     └──────────────────────────┘
```

### 2.2 What Gets Discovered

The discovery agent collects information in layers:

#### Layer 1: System Overview
- OS, kernel, architecture, hostname
- CPU, RAM, disk (same as existing probe in `resource-probe.ts`)
- Running services (systemd units, Docker containers, bare processes)
- Open ports and what's listening on them
- Installed runtimes (Node, Python, Go, Java, Ruby, etc.)
- Package manager state (apt, yum, brew)

#### Layer 2: Web Server / Reverse Proxy
- nginx: sites-enabled configs, upstream definitions, SSL certificates, proxy_pass targets
- Apache: virtual hosts, mod_proxy configs
- Caddy: Caddyfile contents
- Traefik: dynamic config, Docker labels
- HAProxy: frontend/backend definitions

#### Layer 3: Application
- Process details: command line, working directory, user, environment variables
- .env files: location and contents (with secret values masked in the manifest but noted as "present")
- Application framework detection (package.json → Node/Express/Next, requirements.txt → Python/Django/Flask, etc.)
- Git remote URL and current branch/commit (if deployed via git)
- Docker: Dockerfile, docker-compose.yml, running container config, volumes, networks
- systemd unit files: ExecStart, Environment, WorkingDirectory, restart policy

#### Layer 4: Data Stores & Services
- Databases: PostgreSQL, MySQL, MongoDB, Redis, SQLite — connection strings, version, size
- Message queues: RabbitMQ, Redis pub/sub, NATS
- Caches: Redis, Memcached
- Object storage: local paths, S3 bucket references
- Connection strings extracted from .env files and application config

#### Layer 5: DNS & Networking
- Route 53 hosted zones and record sets (if AWS credentials available)
- Cloudflare DNS records (if API token available)
- Generic DNS lookups (dig/nslookup) for the application's domain
- SSL certificate details (issuer, expiry, SAN)
- CDN detection (CloudFront, Cloudflare, Fastly headers)
- Load balancer detection

#### Layer 6: Cross-Server Topology
- From connection strings discovered in Layer 4, identify other servers
- SSH to those servers (if credentials are available) and run a scoped discovery
- Build a topology graph: App Server → Database Server → Backup Server

### 2.3 Discovery Scripts

Discovery is implemented as a set of bash scripts executed by the broker on the remote server via SSH. Each layer is a separate script, so the agent can run them incrementally and analyze results between steps.

```
~/.bond/deployments/discovery/
├── 01-system-overview.sh
├── 02-web-server.sh
├── 03-application.sh
├── 04-data-stores.sh
├── 05-dns-networking.sh
└── 06-topology.sh
```

These scripts output structured JSON. The agent receives the output via the broker and builds the deployment manifest.

**Example: `02-web-server.sh`**

```bash
#!/usr/bin/env bash
# Discovers web server / reverse proxy configuration
set -euo pipefail

result='{"web_servers":[]}'

# nginx
if command -v nginx &>/dev/null && systemctl is-active nginx &>/dev/null 2>&1; then
  NGINX_VERSION=$(nginx -v 2>&1 | grep -oP '[\d.]+')
  SITES_ENABLED=$(ls /etc/nginx/sites-enabled/ 2>/dev/null || echo "")
  CONFIGS=()
  
  for site in /etc/nginx/sites-enabled/*; do
    if [[ -f "$site" ]]; then
      CONTENT=$(cat "$site" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo '""')
      SERVER_NAMES=$(grep -oP 'server_name\s+\K[^;]+' "$site" 2>/dev/null | head -5 || echo "")
      PROXY_PASSES=$(grep -oP 'proxy_pass\s+\K[^;]+' "$site" 2>/dev/null || echo "")
      SSL=$(grep -l 'ssl_certificate' "$site" &>/dev/null && echo "true" || echo "false")
      CERT_PATH=$(grep -oP 'ssl_certificate\s+\K[^;]+' "$site" 2>/dev/null | head -1 || echo "")
      CERT_EXPIRY=""
      if [[ -n "$CERT_PATH" && -f "$CERT_PATH" ]]; then
        CERT_EXPIRY=$(openssl x509 -enddate -noout -in "$CERT_PATH" 2>/dev/null | cut -d= -f2 || echo "")
      fi
      
      CONFIGS+=("{\"file\":\"$(basename "$site")\",\"server_names\":\"$SERVER_NAMES\",\"proxy_pass\":\"$PROXY_PASSES\",\"ssl\":$SSL,\"cert_expiry\":\"$CERT_EXPIRY\",\"content\":$CONTENT}")
    fi
  done
  
  SITES_JSON=$(printf '%s\n' "${CONFIGS[@]}" | python3 -c "import sys,json; print(json.dumps([json.loads(l) for l in sys.stdin if l.strip()]))" 2>/dev/null || echo "[]")
  
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['web_servers'].append({
  'type': 'nginx',
  'version': '$NGINX_VERSION',
  'sites': $SITES_JSON
})
json.dump(r, sys.stdout)
")
fi

# Apache
if command -v apache2 &>/dev/null || command -v httpd &>/dev/null; then
  APACHE_VERSION=$(apache2 -v 2>/dev/null | head -1 || httpd -v 2>/dev/null | head -1 || echo "unknown")
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['web_servers'].append({'type': 'apache', 'version': '$APACHE_VERSION'})
json.dump(r, sys.stdout)
")
fi

# Caddy
if command -v caddy &>/dev/null; then
  CADDY_VERSION=$(caddy version 2>/dev/null || echo "unknown")
  CADDYFILE=""
  if [[ -f /etc/caddy/Caddyfile ]]; then
    CADDYFILE=$(cat /etc/caddy/Caddyfile | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")
  fi
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['web_servers'].append({'type': 'caddy', 'version': '$CADDY_VERSION', 'caddyfile': $CADDYFILE})
json.dump(r, sys.stdout)
")
fi

echo "$result"
```

### 2.4 Broker Integration

Discovery uses a new broker action on the existing `/deploy` endpoint:

```typescript
// New actions in deploy-handler.ts
case "discover":
  return runDiscovery(resource_id, env, body.discovery_layers);

case "discover-dns":
  return runDNSDiscovery(body.domain, body.dns_provider, env);

case "discover-topology":
  return runTopologyDiscovery(resource_id, env, body.connection_strings);
```

**`runDiscovery`** SSHes to the resource and runs discovery scripts layer by layer, returning structured JSON:

```typescript
async function runDiscovery(
  resourceId: string,
  env: string,
  layers?: string[],
): Promise<DeployResult> {
  const resource = await getResourceById(resourceId);
  if (!resource) return { status: "denied", action: "discover", reason: "Resource not found" };

  const conn = JSON.parse(resource.connection_json);
  if (conn.type !== "ssh") {
    return { status: "denied", action: "discover", reason: "Discovery requires SSH connection" };
  }

  const secrets = loadSecrets(env);
  const discoveryDir = path.join(DEPLOYMENTS_DIR, "discovery");

  // Default: run all layers
  const targetLayers = layers || [
    "01-system-overview",
    "02-web-server",
    "03-application",
    "04-data-stores",
    "05-dns-networking",
  ];

  const results: Record<string, any> = {};
  for (const layer of targetLayers) {
    const scriptPath = path.join(discoveryDir, `${layer}.sh`);
    if (!fs.existsSync(scriptPath)) continue;

    const result = await executeSshCommand(
      conn.host, conn.port || 22, conn.user,
      fs.readFileSync(scriptPath, "utf8"),
      { ...secrets, BOND_DEPLOY_ENV: env },
      conn.key_path,
      60, // 60s timeout per layer
    );

    if (result.exit_code === 0) {
      try {
        results[layer] = JSON.parse(result.stdout);
      } catch {
        results[layer] = { raw: result.stdout };
      }
    } else {
      results[layer] = { error: result.stderr, exit_code: result.exit_code };
    }
  }

  // Store discovery results as a manifest
  const manifest = {
    resource_id: resourceId,
    resource_name: resource.name,
    environment: env,
    discovered_at: new Date().toISOString(),
    layers: results,
  };

  const manifestPath = path.join(DEPLOYMENTS_DIR, "discovery", "manifests", `${resource.name}.json`);
  fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  return {
    status: "ok",
    action: "discover" as any,
    environment: env,
    info: manifest,
  };
}
```

### 2.5 Secret Masking

Discovery scripts handle secrets carefully:

1. `.env` file values are collected but **masked** in the manifest: `DATABASE_URL=postgresql://***` with a note `"has_value": true`
2. The full unmasked values are written to the environment's secrets YAML (`~/.bond/deployments/secrets/{env}.yaml`) by the broker — never exposed to the agent
3. SSL private keys are never collected — only certificate details (issuer, expiry, SANs)
4. SSH private keys on the remote server are noted as present but never copied

```json
{
  "env_files": [
    {
      "path": "/app/.env",
      "variables": {
        "DATABASE_URL": { "masked": "postgresql://user:***@db-server:5432/myapp", "has_value": true },
        "REDIS_URL": { "masked": "redis://***@cache-server:6379", "has_value": true },
        "NODE_ENV": { "value": "production", "secret": false },
        "PORT": { "value": "3000", "secret": false }
      }
    }
  ]
}
```

---

## 3. DNS & Route 53 Discovery

### 3.1 DNS Discovery Approach

The agent discovers the full DNS path from domain to server:

```
example.com → CloudFront (d123.cloudfront.net) → ALB (my-alb-123.us-east-1.elb.amazonaws.com) → EC2 (10.0.1.50)
```

### 3.2 Discovery Methods

**Generic DNS (no credentials needed):**
```bash
# A/AAAA records
dig +short example.com A
dig +short example.com AAAA

# CNAME chain
dig +short example.com CNAME

# MX, TXT, NS
dig example.com MX +short
dig example.com TXT +short
dig example.com NS +short

# SSL cert info via openssl
echo | openssl s_client -servername example.com -connect example.com:443 2>/dev/null | openssl x509 -noout -text
```

**Route 53 (if AWS credentials available in environment secrets):**
```bash
# List hosted zones
aws route53 list-hosted-zones --output json

# List records for a zone
aws route53 list-resource-record-sets --hosted-zone-id Z123456 --output json

# Find records matching our domain
aws route53 list-resource-record-sets --hosted-zone-id Z123456 \
  --query "ResourceRecordSets[?Name=='example.com.']"
```

**Cloudflare (if API token available):**
```bash
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=example.com" | jq '.result[0].id'

curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" | jq '.result'
```

### 3.3 DNS Discovery Output

```json
{
  "domain": "example.com",
  "dns_provider": "route53",
  "hosted_zone_id": "Z123456",
  "records": [
    { "name": "example.com", "type": "A", "alias": true, "target": "d123.cloudfront.net", "ttl": 300 },
    { "name": "api.example.com", "type": "CNAME", "target": "my-alb-123.us-east-1.elb.amazonaws.com", "ttl": 300 },
    { "name": "example.com", "type": "MX", "target": "10 mail.example.com", "ttl": 3600 }
  ],
  "resolution_chain": [
    { "step": 1, "name": "example.com", "type": "ALIAS/A", "target": "d123.cloudfront.net" },
    { "step": 2, "name": "d123.cloudfront.net", "type": "CNAME", "target": "server-abc.cloudfront.net" },
    { "step": 3, "name": "server-abc.cloudfront.net", "type": "A", "target": "52.84.123.45" }
  ],
  "ssl": {
    "issuer": "Amazon",
    "valid_from": "2025-12-01",
    "valid_to": "2026-12-01",
    "sans": ["example.com", "*.example.com"],
    "auto_renew": true
  },
  "cdn": {
    "provider": "cloudfront",
    "distribution_id": "E123456",
    "origin": "my-alb-123.us-east-1.elb.amazonaws.com"
  }
}
```

---

## 4. Cross-Server Topology Discovery

### 4.1 Following Connections

When the application discovery (Layer 4) finds connection strings to other servers, the agent can SSH to those servers for scoped discovery:

```
App Server (prod-01)
├── DATABASE_URL → db-server:5432 → discover PostgreSQL config
├── REDIS_URL → cache-server:6379 → discover Redis config  
└── BACKUP_HOST → backup-01 → discover backup config
```

### 4.2 Scoped Discovery

Cross-server discovery is **scoped** — it only discovers the service referenced by the connection string, not the entire server:

```typescript
interface TopologyDiscoveryRequest {
  source_resource_id: string;           // where we found the connection
  connection_string: string;            // the reference (host:port)
  service_type: "postgresql" | "mysql" | "redis" | "rabbitmq" | "ssh" | "unknown";
  target_host: string;
  target_port: number;
  // SSH credentials for the target (if available in env secrets)
  target_ssh_user?: string;
  target_ssh_key_secret?: string;
}
```

The broker checks if SSH credentials exist for the target host. If so, it runs a scoped discovery script. If not, it runs external probes only (port reachability, version detection via protocol).

### 4.3 Topology Graph

The discovery results form a directed graph stored as JSON:

```json
{
  "topology": {
    "nodes": [
      { "id": "prod-01", "type": "app-server", "host": "10.0.1.50", "resource_id": "res_01" },
      { "id": "db-server", "type": "postgresql", "host": "10.0.2.10", "version": "16.2" },
      { "id": "cache-server", "type": "redis", "host": "10.0.2.20", "version": "7.2" },
      { "id": "cdn", "type": "cloudfront", "distribution": "E123456" }
    ],
    "edges": [
      { "from": "cdn", "to": "prod-01", "protocol": "https", "port": 443 },
      { "from": "prod-01", "to": "db-server", "protocol": "postgresql", "port": 5432 },
      { "from": "prod-01", "to": "cache-server", "protocol": "redis", "port": 6379 }
    ]
  }
}
```

---

## 5. Deployment Manifest

### 5.1 Structure

The complete discovery output is a **deployment manifest** — a comprehensive snapshot of how the application is deployed:

```json
{
  "manifest_version": "1.0",
  "application": "myapp",
  "discovered_at": "2026-03-16T14:00:00Z",
  "discovered_by": "deploy-prod",

  "entrypoint": {
    "domain": "example.com",
    "dns_provider": "route53",
    "hosted_zone_id": "Z123456",
    "records": [...],
    "ssl": {...},
    "cdn": {...}
  },

  "servers": [
    {
      "name": "prod-01",
      "host": "10.0.1.50",
      "os": "Ubuntu 24.04 LTS",
      "role": "application",
      "system": {
        "cpu_cores": 4,
        "memory_gb": 8,
        "disk_gb": 80
      },
      "web_server": {
        "type": "nginx",
        "version": "1.24.0",
        "sites": [
          {
            "file": "myapp.conf",
            "server_names": "example.com www.example.com",
            "proxy_pass": "http://127.0.0.1:3000",
            "ssl": true,
            "cert_expiry": "2026-12-01"
          }
        ]
      },
      "application": {
        "name": "myapp",
        "runtime": "node",
        "version": "22.12.0",
        "process_manager": "pm2",
        "working_directory": "/app/myapp",
        "git_remote": "git@github.com:org/myapp.git",
        "git_branch": "main",
        "git_commit": "abc1234",
        "env_files": [...],
        "ports": [3000]
      },
      "services": {
        "systemd_units": ["nginx", "pm2-deploy"],
        "docker_containers": [],
        "cron_jobs": ["0 * * * * /app/myapp/scripts/cleanup.sh"]
      }
    },
    {
      "name": "db-server",
      "host": "10.0.2.10",
      "role": "database",
      "services": {
        "postgresql": {
          "version": "16.2",
          "databases": ["myapp_production"],
          "max_connections": 100,
          "data_directory": "/var/lib/postgresql/16/main",
          "config_highlights": {
            "shared_buffers": "2GB",
            "work_mem": "64MB",
            "wal_level": "replica"
          }
        }
      }
    }
  ],

  "topology": {...},

  "security_observations": [
    { "severity": "warning", "message": "SSH root login enabled on prod-01" },
    { "severity": "info", "message": "SSL cert expires in 9 months" },
    { "severity": "warning", "message": "PostgreSQL listens on 0.0.0.0 (should be private network only)" }
  ]
}
```

### 5.2 Storage

Manifests are stored on the host alongside other deployment artifacts:

```
~/.bond/deployments/discovery/
├── scripts/                    # Discovery scripts (layer 1-6)
│   ├── 01-system-overview.sh
│   ├── 02-web-server.sh
│   ├── 03-application.sh
│   ├── 04-data-stores.sh
│   ├── 05-dns-networking.sh
│   └── 06-topology.sh
└── manifests/                  # Discovery results per resource
    ├── prod-01.json
    ├── prod-01-2026-03-16.json # Timestamped snapshot for diff
    └── db-server.json
```

---

## 6. Replication & Improvement Proposals

### 6.1 From Manifest to Scripts

After discovery, the deployment agent analyzes the manifest and generates deployment scripts at multiple levels. All scripts use the existing infrastructure from Doc 039 §5 — bash scripts with `meta:` headers.

### 6.2 Level 0: Exact Replication

Generate scripts that replicate the existing deployment exactly as-is. This is the "if this server died, rebuild it" set.

The agent generates a set of scripts registered in the script registry:

```
Script: setup-{app}-infrastructure
  - Install OS packages (nginx, node, etc.)
  - Configure nginx (exact config from discovery)
  - Set up systemd services
  - Configure firewall rules

Script: deploy-{app}-application  
  - Clone repo at discovered commit
  - Install dependencies
  - Copy .env file (secrets injected by broker)
  - Start application via pm2/systemd

Script: setup-{app}-database
  - Install PostgreSQL at discovered version
  - Create database and roles
  - Apply current schema
  - Configure connection limits and tuning params

Script: setup-{app}-dns
  - Create/verify Route 53 records
  - Configure SSL certificate (Let's Encrypt / ACM)
```

Each script uses the existing template patterns from `script-templates.ts` and references environment secrets via `$DEPLOY_*` variables that the broker injects.

**Example generated script:**

```bash
#!/usr/bin/env bash
# meta:name: Setup infrastructure for myapp (exact replication)
# meta:version: 1
# meta:timeout: 900
# meta:depends_on: none
# Generated from discovery manifest: prod-01 (2026-03-16)
set -euo pipefail

DEPLOY_HOST="${RESOURCE_HOST:?RESOURCE_HOST is required}"
DEPLOY_USER="${RESOURCE_USER:?RESOURCE_USER is required}"

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "[DRY RUN] Would replicate infrastructure on $DEPLOY_HOST"
  echo "  - Install nginx 1.24, Node.js 22, PM2"
  echo "  - Configure nginx site: myapp.conf"
  echo "  - Create systemd unit: pm2-deploy"
  exit 0
fi

ssh "$DEPLOY_USER@$DEPLOY_HOST" bash -s <<'REMOTE'
set -euo pipefail

# System packages
sudo apt-get update
sudo apt-get install -y nginx

# Node.js 22
if ! node --version 2>/dev/null | grep -q "v22"; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

# PM2
sudo npm install -g pm2

# nginx config (exact replication of discovered config)
cat > /tmp/myapp.conf <<'NGINX_CONF'
server {
    listen 80;
    server_name example.com www.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name example.com www.example.com;
    
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX_CONF
sudo mv /tmp/myapp.conf /etc/nginx/sites-available/myapp
sudo ln -sf /etc/nginx/sites-available/myapp /etc/nginx/sites-enabled/myapp
sudo nginx -t
sudo systemctl reload nginx

echo "Infrastructure setup complete"
REMOTE
```

### 6.3 Level 1: Operational Improvements

Scripts that keep the same architecture but add observability and reliability:

| Improvement | What it adds |
|---|---|
| **Structured logging** | Add JSON logging to nginx, configure log rotation, set up centralized log shipping |
| **Health check endpoint** | Add `/health` endpoint that checks app + DB + Redis connectivity |
| **Graceful restarts** | Replace `kill + start` with PM2 reload / rolling restart |
| **Backup automation** | pg_dump cron job with retention policy, test restores |
| **SSL auto-renewal** | certbot cron or ACME integration |
| **Monitoring endpoints** | Expose `/metrics` for Prometheus, add nginx stub_status |
| **Error alerting** | Health check script that the deployment agent can run on schedule |

These are generated as additional scripts alongside the replication scripts:

```bash
#!/usr/bin/env bash
# meta:name: Add health monitoring for myapp
# meta:version: 1
# meta:timeout: 300
# meta:depends_on: setup-myapp-infrastructure
# Improvement Level 1: Operational
set -euo pipefail
# ... adds health check endpoint, log rotation, basic monitoring
```

### 6.4 Level 2: Architecture Improvements

Scripts that change how the application is deployed for better reliability:

| Improvement | What it changes |
|---|---|
| **Containerization** | Wrap the app in Docker with a Dockerfile, replace PM2 with Docker restart policies |
| **Docker Compose** | Multi-container setup: app + nginx + redis as a Compose stack |
| **Zero-downtime deploys** | Blue-green or rolling deployment strategy |
| **Secret management** | Replace .env files with Docker secrets or vault integration |
| **Database connection pooling** | Add PgBouncer between app and PostgreSQL |
| **Reverse proxy hardening** | Rate limiting, security headers, request size limits |

These generate both deployment scripts AND `.bond/deploy.yml` pipeline files:

```yaml
# .bond/deploy.yml — Level 2: Containerized deployment
pipeline: myapp-containerized

on:
  push:
    branches: [main]
  manual: true

steps:
  - name: build
    image: node:22
    run: |
      npm ci
      npm run build
      npm test

  - name: docker-build
    image: docker
    run: |
      docker build -t myapp:${COMMIT_SHA:-latest} .
      docker tag myapp:${COMMIT_SHA:-latest} myapp:previous || true
    needs: [build]

  - name: deploy
    image: bond-deploy-agent
    run: |
      docker compose -f docker-compose.prod.yml down
      docker compose -f docker-compose.prod.yml up -d
    needs: [docker-build]
    secrets: [DATABASE_URL, REDIS_URL]

  - name: health-check
    image: curlimages/curl
    run: |
      sleep 5
      curl -f http://localhost:3000/health || exit 1
    needs: [deploy]

environments:
  - name: dev
    auto_promote: true
  - name: staging
    auto_promote: false
  - name: prod
    auto_promote: false
    approval:
      required: 1
```

### 6.5 Level 3: Platform Evolution

Recommendations that require infrastructure changes:

| Improvement | What it proposes |
|---|---|
| **Kubernetes migration** | K3s on the same server, or managed K8s (EKS/GKE) for multi-server |
| **Serverless components** | Move static assets to S3+CloudFront, API to Lambda/Cloud Functions |
| **Managed database** | Migrate from self-hosted PostgreSQL to RDS/Cloud SQL |
| **Infrastructure as Code** | Generate Terraform/Pulumi configs for the entire stack |
| **Multi-region** | Active-passive or active-active across regions |

Level 3 proposals are generated as **documentation + scripts**, not auto-applied. The agent writes a detailed proposal with:
- Current cost estimate
- Proposed cost estimate
- Migration steps
- Risk assessment
- Rollback plan

### 6.6 Proposal Storage & Presentation

Proposals are stored alongside the discovery manifest:

```
~/.bond/deployments/discovery/
└── proposals/
    └── myapp/
        ├── proposal-summary.md
        ├── level-0-replication/
        │   ├── setup-myapp-infrastructure.sh
        │   ├── deploy-myapp-application.sh
        │   ├── setup-myapp-database.sh
        │   └── setup-myapp-dns.sh
        ├── level-1-operational/
        │   ├── add-health-monitoring.sh
        │   ├── add-backup-automation.sh
        │   └── add-ssl-autorenew.sh
        ├── level-2-architecture/
        │   ├── containerize-app.sh
        │   ├── docker-compose.prod.yml
        │   └── deploy.yml
        └── level-3-platform/
            └── proposal.md
```

The agent presents proposals in conversation and the user can:
- **Accept & register** — script gets registered in the script registry and promoted to dev
- **Modify** — agent edits the script based on feedback, re-registers
- **Reject** — proposal archived, not registered

---

## 7. Continuous Monitoring

### 7.1 Monitoring Agent Cron Schedule

Each deployment agent runs on a cron schedule (in addition to responding to deployment events). The schedule is configurable per environment via the environment settings:

```rust
// Addition to deployment_environments table
pub struct DeploymentEnvironment {
    // ... existing fields ...
    
    // Monitoring schedule (cron expression, default: "*/5 * * * *" = every 5 min)
    pub monitoring_cron: String,
    
    // What to monitor
    pub monitor_health_checks: bool,    // default: true
    pub monitor_logs: bool,             // default: true
    pub monitor_error_rate: bool,       // default: true
    pub monitor_resource_usage: bool,   // default: true
    pub monitor_drift: bool,            // default: true
    
    // Issue management
    pub auto_file_issues: bool,         // default: true
    pub issue_repo: String,             // "org/repo" for GitHub issues
    pub issue_labels: String,           // JSON array of default labels
    pub issue_dedup_window_hours: u32,  // don't file duplicate within N hours (default: 24)
}
```

### 7.2 Monitoring Loop

The monitoring loop runs via Bond's heartbeat system. Each deploy agent's heartbeat prompt includes monitoring instructions:

```
HEARTBEAT CHECK — deploy-{env}

Check the following and report any issues:
1. Run health-check action. Report any failing checks.
2. Review recent logs for errors (via log-check action).
3. Check resource usage (CPU, RAM, disk) for anomalies.
4. Check for drift from the last deployment baseline.
5. Verify all expected services are running.

If everything is healthy, respond: HEARTBEAT_OK
If there are issues, report them and file bug tickets for any NEW issues.
IMPORTANT: Before filing a ticket, search for existing open issues to avoid duplicates.
```

### 7.3 Log Monitoring

A new broker action enables the agent to check application logs:

```typescript
case "log-check":
  return runLogCheck(resource_id, env, body.log_sources, body.since_minutes);
```

**`runLogCheck`** SSHes to the resource and retrieves recent log entries:

```bash
#!/usr/bin/env bash
# Log collection script — executed by broker on the remote host
set -euo pipefail

SINCE="${SINCE_MINUTES:-5}"
result='{"log_sources":[]}'

# journalctl for systemd services
for service in ${MONITORED_SERVICES:-}; do
  LOGS=$(journalctl -u "$service" --since "$SINCE minutes ago" --no-pager -o json 2>/dev/null | tail -100 || echo "")
  ERRORS=$(echo "$LOGS" | grep -c '"PRIORITY":"3"' 2>/dev/null || echo "0")
  WARNINGS=$(echo "$LOGS" | grep -c '"PRIORITY":"4"' 2>/dev/null || echo "0")
  
  # Extract unique error messages
  ERROR_MSGS=$(echo "$LOGS" | python3 -c "
import sys, json
errors = []
for line in sys.stdin:
    try:
        entry = json.loads(line)
        if entry.get('PRIORITY') in ('3', '4'):
            msg = entry.get('MESSAGE', '')
            if msg and msg not in errors:
                errors.append(msg)
    except: pass
print(json.dumps(errors[:20]))
" 2>/dev/null || echo "[]")
  
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'journalctl',
    'service': '$service',
    'error_count': $ERRORS,
    'warning_count': $WARNINGS,
    'error_messages': $ERROR_MSGS
})
json.dump(r, sys.stdout)
")
done

# Docker container logs
for container in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  LOGS=$(docker logs --since "${SINCE}m" "$container" 2>&1 | tail -100 || echo "")
  ERRORS=$(echo "$LOGS" | grep -ciE "(error|exception|fatal|panic)" || echo "0")
  
  ERROR_LINES=$(echo "$LOGS" | grep -iE "(error|exception|fatal|panic)" | head -10 | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")
  
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'docker',
    'container': '$container',
    'error_count': $ERRORS,
    'error_lines': $ERROR_LINES
})
json.dump(r, sys.stdout)
")
done

# Nginx error log
if [[ -f /var/log/nginx/error.log ]]; then
  SINCE_TIME=$(date -d "$SINCE minutes ago" '+%Y/%m/%d %H:%M' 2>/dev/null || date -v-${SINCE}M '+%Y/%m/%d %H:%M' 2>/dev/null)
  ERRORS=$(tail -200 /var/log/nginx/error.log | grep -c "error" 2>/dev/null || echo "0")
  ERROR_LINES=$(tail -200 /var/log/nginx/error.log | grep "error" | tail -10 | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")
  
  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'nginx-error',
    'error_count': $ERRORS,
    'error_lines': $ERROR_LINES
})
json.dump(r, sys.stdout)
")
fi

echo "$result"
```

### 7.4 Resource Usage Monitoring

```typescript
case "resource-usage":
  return getResourceUsage(resource_id, env);
```

Collects CPU load, memory usage, disk usage, and open file descriptors. Returns structured data the agent can compare against thresholds and historical baselines.

---

## 8. Intelligent Issue Management

### 8.1 The Deduplication Problem

A naive monitoring agent would file a new GitHub issue every time a health check fails. If nginx goes down at 2am and stays down, you'd have 12 duplicate issues by 3am (one per 5-minute check). This is worse than no monitoring.

### 8.2 Issue Deduplication Strategy

Before filing an issue, the agent:

1. **Searches existing open issues** using the GitHub API
2. **Matches by fingerprint** — a hash of the error signature
3. **Updates existing issues** instead of creating new ones
4. **Respects the dedup window** — won't file for the same fingerprint within N hours (configurable)

### 8.3 Error Fingerprinting

Each error gets a fingerprint based on:
- Environment name
- Error category (health-check-failure, log-error, drift-detected, resource-threshold, service-down)
- Affected service/component
- Error message pattern (normalized — stripped of timestamps, IDs, and variable data)

```typescript
interface ErrorFingerprint {
  environment: string;
  category: string;
  component: string;        // "nginx", "app", "postgresql", "redis"
  message_pattern: string;  // normalized error message
  hash: string;             // SHA-256 of the above
}

function computeFingerprint(
  env: string,
  category: string,
  component: string,
  rawMessage: string,
): ErrorFingerprint {
  // Normalize: remove timestamps, UUIDs, IPs, ports, numbers that change
  const normalized = rawMessage
    .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\dZ]*/g, "<timestamp>")
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, "<uuid>")
    .replace(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g, "<ip>")
    .replace(/:\d{4,5}/g, ":<port>")
    .replace(/pid \d+/g, "pid <pid>")
    .replace(/\b\d{5,}\b/g, "<id>")
    .trim();

  const hash = crypto.createHash("sha256")
    .update(`${env}:${category}:${component}:${normalized}`)
    .digest("hex")
    .slice(0, 16);

  return { environment: env, category, component, message_pattern: normalized, hash };
}
```

### 8.4 Issue Search Before Filing

The agent uses `gh issue list` (via the broker's `/exec` endpoint, which deploy agents are allowed to use for `gh issue create*`) to search for existing open issues. We extend the policy to also allow `gh issue list*` and `gh issue search*`:

```yaml
# Updated policy for deployment agents
rules:
  - commands: ["gh issue create*", "gh issue list*", "gh issue search*", "gh issue comment*"]
    decision: allow

  - commands: ["*"]
    decision: deny
```

**Search flow:**

```typescript
// Agent's deduplication logic (runs in the agent's LLM context)
async function shouldFileIssue(fingerprint: ErrorFingerprint): Promise<{
  file: boolean;
  existing_issue?: number;
  action: "create" | "comment" | "skip";
}> {
  // 1. Search for open issues with matching fingerprint label
  const searchResult = await broker.exec(
    `gh issue list --repo ${issueRepo} --state open --label "fingerprint:${fingerprint.hash}" --json number,title,createdAt --limit 5`
  );
  
  const existingIssues = JSON.parse(searchResult.stdout || "[]");
  
  if (existingIssues.length > 0) {
    const latest = existingIssues[0];
    const hoursSinceCreated = (Date.now() - new Date(latest.createdAt).getTime()) / 3600000;
    
    if (hoursSinceCreated < dedupWindowHours) {
      // Comment on existing issue with latest occurrence
      return { file: false, existing_issue: latest.number, action: "comment" };
    }
  }
  
  // 2. Also search by title similarity (fallback for issues without fingerprint label)
  const titleSearch = await broker.exec(
    `gh issue list --repo ${issueRepo} --state open --search "${fingerprint.component} ${fingerprint.category}" --json number,title --limit 10`
  );
  
  // 3. Agent uses LLM reasoning to determine if any existing issue matches
  // (This happens naturally — the agent sees the search results and decides)
  
  return { file: true, action: "create" };
}
```

### 8.5 Issue Template with Fingerprint

When the agent creates an issue, it includes the fingerprint as a label for future dedup:

```markdown
## 🔍 Monitoring Alert: {title}

**Environment:** {environment}
**Category:** {category}
**Component:** {component}
**Severity:** {severity}
**Detected:** {timestamp}
**Fingerprint:** `{fingerprint_hash}`

### Current Status
{description of what was detected}

### Error Details
```
{error_output}
```

### Historical Context
- First detected: {first_occurrence}
- Occurrences in last 24h: {count}
- Last successful health check: {last_healthy}
- Last deployment: {last_deploy_receipt_id}

### Resource Status at Time of Detection
- CPU: {cpu_load}
- RAM: {memory_pct}%
- Disk: {disk_pct}%
- Uptime: {uptime}

### Agent Analysis
{agent's diagnosis based on logs, code, and deployment history}

### Suggested Actions
{agent's recommended fix}

---
*Filed by deploy-{environment} agent — Monitoring cycle #{cycle_number}*
*Fingerprint: `{fingerprint_hash}` — Duplicate issues with this fingerprint will be added as comments.*
```

Labels: `deployment`, `monitoring`, `env:{environment}`, `severity:{severity}`, `fingerprint:{hash}`, `component:{component}`

### 8.6 Issue Lifecycle

```
1. Error detected → compute fingerprint
2. Search open issues → match by fingerprint label
3a. Match found, within dedup window → add comment to existing issue
3b. Match found, outside dedup window → create new issue (link to previous)
3c. No match → create new issue
4. When health recovers → agent comments on open issue: "✅ Resolved — health check passing as of {time}"
5. Agent does NOT auto-close issues — humans close them after verifying the fix
```

### 8.7 Monitoring as Improvement Suggestions

Beyond error detection, monitoring agents track patterns and periodically suggest improvements:

- "Over the past week, deploy-prod has seen 3 connection pool exhaustion errors during peak hours. Recommend increasing `max_connections` from 100 to 200, or adding PgBouncer."
- "nginx error logs show 15 upstream timeout errors in the past 24h. The proxy_read_timeout is set to 30s but the `/api/reports` endpoint averages 45s. Recommend increasing timeout for that location block."
- "Disk usage on prod-01 has grown from 44% to 67% in 2 weeks. At this rate, it will hit 90% in ~3 weeks. Recommend setting up log rotation or expanding the volume."

These suggestions are filed as GitHub issues with label `improvement` rather than `monitoring`, so they don't trigger the same alerting urgency.

---

## 9. Monitoring Schedule Configuration

### 9.1 Environment-Level Configuration

Monitoring settings are configured per environment through the existing environment management API:

```
PUT /api/v1/deployments/environments/prod
{
  "monitoring_cron": "*/5 * * * *",
  "monitor_health_checks": true,
  "monitor_logs": true,
  "monitor_error_rate": true,
  "monitor_resource_usage": true,
  "monitor_drift": true,
  "auto_file_issues": true,
  "issue_repo": "org/myapp",
  "issue_labels": ["deployment", "automated"],
  "issue_dedup_window_hours": 24
}
```

### 9.2 Heartbeat Integration

The monitoring cron schedule integrates with Bond's existing heartbeat system. The Gateway's heartbeat scheduler triggers the deploy agent at the configured interval:

```typescript
// In health-scheduler.ts — extended to include monitoring
async function runMonitoringCycle(env: string, config: GatewayConfig): Promise<void> {
  const envConfig = await getEnvConfig(config, env);
  if (!envConfig) return;

  // 1. Health checks (existing)
  if (envConfig.monitor_health_checks) {
    const health = await executeHealthCheck(env);
    if (health.status === "unhealthy") {
      // Notify deploy agent to investigate and potentially file issue
      await notifyAgent(`deploy-${env}`, {
        type: "monitoring_alert",
        category: "health-check-failure",
        data: health,
      });
    }
  }

  // 2. Log check (new)
  if (envConfig.monitor_logs) {
    const resources = await getResources(config, env);
    for (const resource of resources) {
      // Agent handles log analysis — we just trigger it
      await notifyAgent(`deploy-${env}`, {
        type: "monitoring_check",
        category: "log-review",
        resource_id: resource.id,
      });
    }
  }

  // 3. Drift detection (existing, via drift-detector.ts)
  if (envConfig.monitor_drift) {
    const health = await executeHealthCheck(env);
    const drift = compareDrift(env, health.results);
    if (drift.has_drift) {
      await notifyAgent(`deploy-${env}`, {
        type: "monitoring_alert",
        category: "drift-detected",
        data: drift,
      });
    }
  }
}
```

### 9.3 Monitoring UI

The Deployment tab gains a monitoring section per environment:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Monitoring — prod                                          [Configure]│
│                                                                         │
│  Schedule: Every 5 minutes                    Last run: 2m ago         │
│  Status: ● All checks passing                                          │
│                                                                         │
│  ┌─── Recent Alerts ──────────────────────────────────────────────────┐│
│  │  ✅ 10:25 — Health check passed (5/5 checks)                       ││
│  │  ✅ 10:20 — Health check passed (5/5 checks)                       ││
│  │  ⚠️ 10:15 — Log warning: 3 upstream timeouts (commented on #47)   ││
│  │  ✅ 10:10 — Health check passed (5/5 checks)                       ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                         │
│  ┌─── Open Issues ────────────────────────────────────────────────────┐│
│  │  #47  ⚠️ Upstream timeouts on /api/reports endpoint   (3 days)     ││
│  │  #52  💡 Suggestion: Increase PgBouncer pool size      (1 day)     ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                         │
│  Monitored resources: web-prod-01, db-prod-01                          │
│  Checks: ☑ Health  ☑ Logs  ☑ Resources  ☑ Drift                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Agent-Driven Discovery Workflow

### 10.1 Conversational Discovery

The user doesn't need to know which discovery scripts to run. They talk to the deployment agent:

**User:** "I have an app running on prod.example.com. Can you figure out how it's deployed?"

**Agent:**
1. Checks if a resource exists for that host, or asks to create one
2. Runs Layer 1 (system overview) via broker → analyzes results
3. Runs Layer 2 (web server) → finds nginx → reports findings
4. Runs Layer 3 (application) → finds Node.js app with PM2 → reports
5. Runs Layer 4 (data stores) → finds PostgreSQL connection → asks: "I found a database at db-server:5432. Should I discover that server too?"
6. If yes, runs Layer 6 (topology) → SSHes to db-server → discovers PostgreSQL config
7. Runs Layer 5 (DNS) → maps domain → reports full resolution chain
8. Compiles manifest → generates proposals at all levels
9. Presents summary with options: "I've mapped your full deployment. Here's what I found..."

### 10.2 Agent System Prompt Addition

Add to the deployment agent's system prompt (from Doc 043 §5.2):

```
## Discovery

When asked to discover or analyze how an application is deployed:
1. Use deploy_action with action "discover" to probe the target resource
2. Analyze results layer by layer — report findings as you go
3. Follow connections to discover related servers (databases, caches)
4. Map DNS/CDN entrypoints with action "discover-dns"
5. Compile a full deployment manifest
6. Generate deployment scripts at multiple improvement levels:
   - Level 0: Exact replication of current setup
   - Level 1: Operational improvements (monitoring, backups, logging)
   - Level 2: Architecture improvements (containers, CI/CD, zero-downtime)
   - Level 3: Platform evolution proposals (K8s, serverless, IaC)
7. Present findings and let the user choose which scripts to register

When generating scripts, always use:
- Bash scripts with meta: headers (for the script registry)
- .bond/deploy.yml pipeline files (for multi-step deployments)
- Environment variables for secrets ($DEPLOY_*, $RESOURCE_*)
- The existing script templates as patterns (SSH Deploy, Docker Build, etc.)

## Monitoring

Between deployments, you are responsible for monitoring your environment:
- Run health checks at the configured interval
- Review logs for errors and anomalies
- Track resource usage trends
- Detect drift from the deployment baseline
- File GitHub issues for problems — BUT ALWAYS SEARCH FIRST:
  1. Search open issues with the fingerprint label
  2. If a matching issue exists, comment on it instead of creating a new one
  3. Only create new issues for genuinely new problems
  4. When issues resolve, comment on the issue with recovery details
  5. Never auto-close issues — humans verify and close

Suggest improvements when you notice patterns:
- Recurring errors → suggest fixes
- Growing resource usage → suggest scaling
- Frequent manual interventions → suggest automation
```

---

## 11. Broker Actions Summary

### 11.1 New Actions for `/broker/deploy`

| Action | Purpose | Input | Output |
|---|---|---|---|
| `discover` | Run discovery layers on a resource | `resource_id`, `discovery_layers[]` | Full discovery manifest |
| `discover-dns` | Map DNS/CDN for a domain | `domain`, `dns_provider` | DNS records, resolution chain, SSL info |
| `discover-topology` | Follow connections to other servers | `resource_id`, `connection_strings[]` | Topology graph |
| `log-check` | Retrieve recent log entries | `resource_id`, `since_minutes`, `log_sources[]` | Log entries with error counts |
| `resource-usage` | Get current CPU/RAM/disk/network | `resource_id` | Usage metrics |
| `generate-replication-scripts` | Create Level 0 scripts from manifest | `manifest_path` | Registered script IDs |
| `search-issues` | Search GitHub for existing issues | `query`, `labels[]`, `state` | Matching issues |

### 11.2 Extended Agent Exec Policy

```yaml
# Updated ~/.bond/policies/agents/deploy-{env}.yaml
rules:
  # GitHub issue management (monitoring)
  - commands: ["gh issue create*", "gh issue list*", "gh issue search*", "gh issue comment*", "gh issue view*"]
    decision: allow

  # Everything else goes through /broker/deploy actions
  - commands: ["*"]
    decision: deny
```

---

## 12. Security Considerations

### 12.1 Discovery Security

- Discovery scripts run via the broker on the Bond host, SSHing to target servers. The agent never gets SSH credentials.
- Discovered secrets (.env values, connection strings) are masked in the manifest and stored only in the broker's secrets directory.
- The agent sees masked values like `postgresql://user:***@host:5432/db` — enough to understand the architecture but not enough to connect directly.
- SSH to cross-server targets requires credentials in the environment's secrets YAML. If credentials don't exist, the agent reports that it can't reach the target and asks the user to provide access.

### 12.2 Monitoring Security

- Log contents are returned to the agent via the broker. Logs may contain sensitive data (user IPs, request payloads). The agent should not include raw log data in GitHub issues — only sanitized error messages and patterns.
- Issue filing goes through the broker's exec endpoint with the whitelisted `gh issue` commands. The agent cannot run arbitrary commands.

### 12.3 Issue Dedup Security

- Fingerprint labels on GitHub issues are not a security mechanism — they're a convenience for dedup search. An attacker who can modify issue labels could cause duplicate filing, but that's a nuisance, not a security breach.
- The agent searches with `gh issue list --label "fingerprint:..."` which is a read-only operation.

---

## 13. File Structure

```
gateway/src/
├── broker/
│   └── deploy-handler.ts         # MODIFIED — new actions: discover, discover-dns,
│                                 #   discover-topology, log-check, resource-usage,
│                                 #   generate-replication-scripts, search-issues
├── deployments/
│   ├── ... (existing files)
│   ├── discovery.ts              # NEW — discovery orchestration
│   ├── discovery-scripts.ts      # NEW — manages discovery script execution over SSH
│   ├── manifest.ts               # NEW — manifest parsing, storage, comparison
│   ├── proposal-generator.ts     # NEW — generate scripts from manifests
│   ├── monitoring.ts             # NEW — monitoring cycle orchestration
│   ├── issue-dedup.ts            # NEW — fingerprinting, dedup search, issue lifecycle
│   ├── log-collector.ts          # NEW — remote log collection and parsing
│   └── __tests__/
│       ├── discovery.test.ts
│       ├── manifest.test.ts
│       ├── proposal-generator.test.ts
│       ├── issue-dedup.test.ts
│       └── log-collector.test.ts

~/.bond/deployments/
├── ... (existing directories)
├── discovery/
│   ├── scripts/                  # Discovery layer scripts
│   │   ├── 01-system-overview.sh
│   │   ├── 02-web-server.sh
│   │   ├── 03-application.sh
│   │   ├── 04-data-stores.sh
│   │   ├── 05-dns-networking.sh
│   │   └── 06-topology.sh
│   ├── manifests/                # Per-resource discovery results
│   │   └── {resource-name}.json
│   └── proposals/                # Generated improvement proposals
│       └── {app-name}/
│           ├── proposal-summary.md
│           ├── level-0-replication/
│           ├── level-1-operational/
│           ├── level-2-architecture/
│           └── level-3-platform/

frontend/src/app/settings/deployment/
├── ... (existing files)
├── MonitoringSection.tsx         # NEW — monitoring status per environment
├── MonitoringConfig.tsx          # NEW — configure monitoring schedule & checks
├── DiscoveryView.tsx             # NEW — discovery manifest viewer
├── ProposalViewer.tsx            # NEW — browse and accept/reject proposals
├── TopologyGraph.tsx             # NEW — visual topology diagram
└── IssueTracker.tsx              # NEW — open monitoring issues list

prompts/
└── deployment/
    └── deployment.md             # MODIFIED — add discovery + monitoring instructions
```

---

## 14. SpacetimeDB Additions

```rust
// Discovery manifests — stored for history/comparison
#[spacetimedb::table(name = discovery_manifests, public)]
pub struct DiscoveryManifest {
    #[primary_key]
    pub id: String,                      // ulid
    pub resource_id: String,
    pub environment: String,
    pub discovered_at: u64,
    pub manifest_json: String,           // full manifest JSON
    pub topology_json: String,           // topology graph
}

// Monitoring alerts — tracks what's been detected and filed
#[spacetimedb::table(name = monitoring_alerts, public)]
pub struct MonitoringAlert {
    #[primary_key]
    pub id: String,                      // ulid
    pub environment: String,
    pub category: String,                // health-check-failure, log-error, drift, etc.
    pub component: String,               // nginx, app, postgresql
    pub fingerprint_hash: String,        // for dedup
    pub severity: String,                // critical, high, medium, low
    pub message: String,
    pub detected_at: u64,
    pub issue_number: u32,               // GitHub issue number (0 = not filed)
    pub issue_action: String,            // "created", "commented", "skipped"
    pub resolved_at: u64,                // 0 = unresolved
}

// Monitoring configuration per environment (extends deployment_environments)
// Stored as additional fields on the existing table or as a settings key-value store
```

---

## 15. Build Order

### Phase 1: Discovery Foundation (~3 days)

1. Discovery scripts (01-system-overview through 05-dns-networking)
2. `discovery.ts` — orchestration: run layers via SSH, collect results
3. `discovery-scripts.ts` — script execution via broker SSH
4. `manifest.ts` — parse, store, compare manifests
5. New broker actions: `discover`, `discover-dns`
6. Agent system prompt updates (discovery instructions)

### Phase 2: Topology & Proposals (~2.5 days)

7. `06-topology.sh` — cross-server discovery script
8. Broker action: `discover-topology` — follow connections
9. `proposal-generator.ts` — generate Level 0-2 scripts from manifest
10. Level 0 scripts: exact replication from discovered config
11. Level 1 scripts: operational improvements
12. Level 2 scripts: architecture improvements (including `.bond/deploy.yml`)
13. Level 3: proposal documentation generation

### Phase 3: Monitoring Infrastructure (~3 days)

14. `monitoring.ts` — monitoring cycle orchestration (integrates with heartbeat)
15. `log-collector.ts` — remote log collection via SSH
16. Broker actions: `log-check`, `resource-usage`
17. `issue-dedup.ts` — fingerprinting, search, lifecycle
18. Extended broker exec policy for `gh issue list/search/comment`
19. Environment monitoring config (cron, checks, issue repo)
20. Monitoring heartbeat prompt integration

### Phase 4: Frontend (~2.5 days)

21. `DiscoveryView.tsx` — manifest viewer with expandable layers
22. `TopologyGraph.tsx` — visual server topology (SVG/canvas)
23. `ProposalViewer.tsx` — browse proposals, accept/reject/register scripts
24. `MonitoringSection.tsx` — per-environment monitoring status
25. `MonitoringConfig.tsx` — configure schedule, checks, issue settings
26. `IssueTracker.tsx` — list of open monitoring issues

### Phase 5: Polish & Integration (~2 days)

27. SpacetimeDB tables for manifests and alerts
28. Manifest diffing (compare current discovery to previous)
29. Monitoring auto-recovery detection (comment on resolved issues)
30. Improvement suggestion generation from monitoring patterns
31. Discovery re-run scheduling (periodic re-discovery to detect manual changes)

**Total estimate: ~13 days**

---

## 16. Open Questions

1. **Discovery credential management.** When the agent discovers connections to other servers (db-server:5432), how does it get SSH credentials for those servers? Options: (a) user provides them upfront for all servers, (b) agent asks the user when it needs to SSH somewhere new, (c) agent uses the same SSH key with the assumption it works across the infrastructure.

2. **Discovery depth limits.** Should there be a maximum discovery depth (how many hops the agent follows)? Unbounded topology discovery could traverse an entire infrastructure. A default depth of 2 (app server → directly connected services) seems reasonable.

3. **Read-only discovery mode.** Should discovery scripts be guaranteed read-only? Currently they collect info but some commands (like checking PostgreSQL table sizes) require database read access. Strict read-only mode would skip anything requiring DB credentials; permissive mode would use them for richer discovery.

4. **Proposal approval flow.** Should accepting a Level 2 proposal (e.g., containerization) go through the normal script promotion pipeline, or should there be a separate "infrastructure change" approval workflow? Infrastructure changes are higher risk than application deployments.

5. **Monitoring frequency per check type.** Should all monitoring checks run on the same cron schedule, or should different check types have different frequencies? (e.g., health checks every 1 min, log review every 5 min, drift detection every 30 min)

6. **Issue assignment.** Should monitoring issues be auto-assigned to someone? The `deployment_environments` table has an approvers list — should the first approver be auto-assigned to monitoring issues?

7. **Multi-app servers.** What happens when discovery finds multiple applications on one server? Should each get its own manifest and script set, or should they share infrastructure scripts?

8. **Cost of monitoring.** Each monitoring cycle involves SSH connections, log collection, and potentially LLM inference (for the agent to analyze results). For prod environments with 1-minute health checks, this adds up. Should there be a "lightweight check" (just health endpoints) vs "deep check" (logs + resources + drift) distinction?

9. **Discovery of containerized apps.** When the app is already running in Docker, discovery needs to look inside containers (docker exec) as well as on the host. Should discovery scripts automatically `docker exec` into running containers to inspect the application layer?

10. **Compatibility with existing `resource-probe.ts`.** The existing probe system (Doc 043 §7) overlaps with discovery Layer 1. Should discovery replace probing, or should probing remain lightweight and discovery be the deep version? Current recommendation: probing stays as the quick check; discovery is the comprehensive analysis.
