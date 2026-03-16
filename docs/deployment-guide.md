# Bond Deployment System — Operations Guide

## Overview

Bond's deployment system lets AI agents deploy scripts to environments (dev → qa → staging → uat → prod) through a secure broker. Agents never see scripts, secrets, or promotion state — they send a script ID and get back results.

**Three tiers of complexity:**

| Tier | For | How |
|------|-----|-----|
| **1 — Quick Deploy** | Solo devs, side projects | Connect repo, click Deploy. No YAML, no pipeline. |
| **2 — Pipeline-as-Code** | Teams, CI/CD | `.bond/deploy.yml` in your repo defines multi-step pipelines. |
| **3 — Full Pipeline** | Regulated environments | Multi-approver workflows, deployment windows, drift detection. |

---

## What's Working Today

### ✅ Fully Implemented

| Feature | Where | How to use |
|---------|-------|------------|
| **Environment management** | Gateway API | `GET/POST/PUT/DELETE /api/v1/deployments/environments` |
| **5 default environments** | Auto-seeded on startup | dev, qa, staging, uat, prod |
| **Script registry** | Gateway API + host filesystem | `POST /api/v1/deployments/scripts` to register |
| **SHA-256 verification** | Broker, on every execution | Automatic — broker verifies hash before running any script |
| **Promotion API** | Gateway API | `POST /api/v1/deployments/promote` (user-session-auth only) |
| **Multi-approver workflows** | Gateway API | Configure `required_approvals` per environment |
| **Broker /deploy endpoint** | Gateway broker | Agents call this with script_id + action |
| **All deployment actions** | Broker | deploy, rollback, dry-run, validate, pre/post hooks, health-check, info, receipt, status, lock-status |
| **Deployment receipts** | Host filesystem | Every deploy generates an immutable JSON receipt |
| **Deployment locks** | Host filesystem | One deploy per environment at a time, stale lock auto-release |
| **Deployment windows** | SpacetimeDB | Per-environment day/time restrictions |
| **Environment secrets** | Host filesystem | YAML files injected as env vars during script execution |
| **Bug ticket tool** | Agent tool | `file_bug_ticket` creates GitHub issues via broker |
| **Deploy action tool** | Agent tool | `deploy_action` sends actions to broker /deploy |
| **Pipeline view API** | Gateway API | `GET /api/v1/deployments/pipeline` |
| **Agent pause/resume/abort** | Gateway API | `POST /api/v1/deployments/agents/:id/pause\|resume\|abort` |
| **Deployment tab UI** | Frontend | Setup wizard, agent cards, pipeline section |
| **Quick Deploy form UI** | Frontend | Connect repo, configure, deploy |
| **Pipeline YAML editor UI** | Frontend | In-browser editor with validation |

### ❌ Not Yet Implemented

| Feature | Design Doc | Notes |
|---------|-----------|-------|
| **Pipeline YAML parser** (gateway) | Doc 042 §14.8 | `pipeline-parser.ts` — parse `.bond/deploy.yml` |
| **Pipeline runner/orchestrator** | Doc 042 §14.9 | `pipeline-runner.ts` — step execution orchestration |
| **Step executor** | Doc 042 §14.9 | Container-per-step execution (run steps in arbitrary images) |
| **Trigger handler** | Doc 042 §14.3 | Webhook → pipeline trigger |
| **Build strategy detector** (gateway) | Doc 042 §14.10 | Auto-detect Dockerfile/package.json/etc. |
| **Quick deploy generator** (gateway) | Doc 042 §14.10 | Auto-generate pipeline YAML from form |
| **Matrix expander** | Doc 042 §14.7 | Expand matrix configurations |
| **Service manager** | Doc 042 §14.7 | Start/stop sidecar containers for steps |
| **Preview deployments** | Doc 042 §14.8 | Ephemeral environments for PRs |
| **Drift detection** | Doc 039 §10.4 | Compare environment state between deployments |
| **Secret encryption at rest** | Doc 039 §8.4 | Currently plaintext YAML |
| **Context passing between agents** | Doc 039 §12.2 | Receipt chaining across environments |
| **Deployment log streaming** | Doc 039 §21 Phase 4 | Real-time logs in UI |

---

## How to Use It

### 1. Register a Deployment Script

Scripts live on the host at `~/.bond/deployments/scripts/registry/`. Register them via the API:

```bash
# Encode your script as base64
SCRIPT=$(base64 -w0 my-deploy-script.sh)

# Get a session token (for auth)
TOKEN=$(curl -s -X POST http://localhost:18789/api/v1/deployments/session \
  -H "Content-Type: application/json" \
  -d '{"user_id":"andrew","role":"owner"}' | jq -r .token)

# Register the script
curl -X POST http://localhost:18789/api/v1/deployments/scripts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"script_id\": \"001-my-deployment\",
    \"version\": \"v1\",
    \"name\": \"My Deployment Script\",
    \"timeout\": 300,
    \"dry_run\": true,
    \"files\": {
      \"deploy.sh\": \"$SCRIPT\"
    }
  }"
```

The script is immutable once registered. New changes = new version (`v2`, `v3`, etc.).

### 2. Write a Deployment Script

Scripts are environment-agnostic bash. The broker injects environment-specific config:

```bash
#!/usr/bin/env bash
# meta:name: Deploy web application
# meta:version: 1
# meta:timeout: 300
# meta:dry_run: true
# meta:rollback: rollback.sh

set -euo pipefail

ENV="${BOND_DEPLOY_ENV:?Environment not set}"
echo "Deploying to environment: $ENV"

# These come from ~/.bond/deployments/secrets/{env}.yaml
# Injected by the broker — your script just uses them
SERVER="${DEPLOY_SERVER:?Server not configured}"
DEPLOY_KEY="${DEPLOY_SSH_KEY:?SSH key not configured}"

# Dry-run support
if [[ "${1:-}" == "--dry-run" ]]; then
    echo "[DRY RUN] Would deploy to $SERVER"
    echo "[DRY RUN] Would restart services"
    exit 0
fi

# The actual deployment
ssh -i "$DEPLOY_KEY" deploy@"$SERVER" "cd /app && git pull && systemctl restart app"

echo "Deployment complete"
```

**Available environment variables (injected by broker):**

| Variable | Source | Description |
|----------|--------|-------------|
| `BOND_DEPLOY_ENV` | Broker | Environment name (dev, qa, prod, etc.) |
| `SCRIPT_DIR` | Broker | Path to the script's registry directory on the host |
| `DEPLOY_RECEIPT_ID` | Broker | Unique receipt ID for this deployment |
| Any key from secrets YAML | `~/.bond/deployments/secrets/{env}.yaml` | Environment-specific secrets |

### 3. Configure Environment Secrets

Create a YAML file per environment on the host:

```yaml
# ~/.bond/deployments/secrets/dev.yaml
DEPLOY_SERVER: "dev.example.com"
DEPLOY_SSH_KEY: "/home/andrew/.ssh/deploy_dev"
DATABASE_URL: "postgresql://dev-user:pass@dev-db:5432/app"
API_KEY: "dev-key-xxx"
```

```yaml
# ~/.bond/deployments/secrets/prod.yaml
DEPLOY_SERVER: "prod.example.com"
DEPLOY_SSH_KEY: "/home/andrew/.ssh/deploy_prod"
DATABASE_URL: "postgresql://prod-user:pass@prod-db:5432/app"
API_KEY: "prod-key-xxx"
```

**Security:** These files are host-only, never mounted into agent containers. The broker reads them and injects values as environment variables during script execution. Agents never see the raw values.

### 4. Promote a Script

Promotion is user-only (agents cannot self-promote):

```bash
# Promote to a single environment
curl -X POST http://localhost:18789/api/v1/deployments/promote \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "script_id": "001-my-deployment",
    "version": "v1",
    "target_environments": ["dev"]
  }'

# Promote to all environments (stops at ones needing approval)
curl -X POST http://localhost:18789/api/v1/deployments/promote \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "script_id": "001-my-deployment",
    "version": "v1",
    "target_environments": ["dev", "qa", "staging", "uat", "prod"]
  }'
```

Once promoted, the deployment agent for that environment can execute the script.

### 5. Talk to a Deployment Agent

Chat with the deploy agent via webchat. The agent has two tools:

**`deploy_action`** — Send deployment actions to the broker:
> "Deploy script 001-my-deployment to your environment"
> "Run a dry-run of 001-my-deployment"
> "Check the health of your environment"
> "Show me the latest deployment receipt from dev"

**`file_bug_ticket`** — File GitHub issues on failure:
> The agent does this automatically when deployments fail.

### 6. Configure Pre/Post Hooks

Create hook scripts per environment:

```bash
# ~/.bond/deployments/hooks/qa/pre_deploy.sh
#!/bin/bash
echo "Snapshotting QA database..."
pg_dump "$DATABASE_URL" > /tmp/qa-snapshot-$(date +%s).sql

# ~/.bond/deployments/hooks/qa/post_deploy.sh
#!/bin/bash
echo "Running integration tests..."
cd /app && npm run test:integration
```

### 7. Configure Health Checks

```bash
# ~/.bond/deployments/health/qa/check.sh
#!/bin/bash
curl -f http://qa.example.com/health || exit 1
echo '{"status":"healthy","checks":[{"name":"api","status":"pass"}]}'
```

### 8. View the Pipeline

In the UI: **Settings → Deployment** tab shows:
- Agent cards for each environment with health status
- Pipeline visualization showing script promotion status across environments
- Promote buttons to advance scripts

Via API:
```bash
# Full pipeline view
curl http://localhost:18789/api/v1/deployments/pipeline

# All promotions
curl http://localhost:18789/api/v1/deployments/promotions

# Receipts for an environment
curl http://localhost:18789/api/v1/deployments/receipts/qa

# Scripts in the registry
curl http://localhost:18789/api/v1/deployments/scripts
```

### 9. Manage Environments

```bash
# List environments
curl http://localhost:18789/api/v1/deployments/environments

# Add a new environment
curl -X POST http://localhost:18789/api/v1/deployments/environments \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "name": "canary",
    "display_name": "Canary",
    "order": 6,
    "max_script_timeout": 600,
    "required_approvals": 2,
    "deployment_window": {
      "days": ["tue","wed","thu"],
      "start": "09:00",
      "end": "16:00",
      "timezone": "America/New_York"
    }
  }'

# Add an approver to an environment
curl -X POST http://localhost:18789/api/v1/deployments/environments/prod/approvers \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"user_id": "sarah"}'
```

### 10. Emergency Controls

```bash
# Pause an agent (stops new deployments, health checks continue)
curl -X POST http://localhost:18789/api/v1/deployments/agents/deploy-prod/pause \
  -H "Authorization: Bearer $TOKEN"

# Resume
curl -X POST http://localhost:18789/api/v1/deployments/agents/deploy-prod/resume \
  -H "Authorization: Bearer $TOKEN"

# Abort a running deployment (kills script, triggers rollback)
curl -X POST http://localhost:18789/api/v1/deployments/agents/deploy-prod/abort \
  -H "Authorization: Bearer $TOKEN"
```

---

## How Agents Connect to Servers

Deployment agents **don't connect to servers directly.** The broker executes scripts on the Bond host, and those scripts handle the actual connections. Here's how to configure it:

### Option A: SSH-based deployment (most common)

1. Set up SSH keys on the Bond host:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/deploy_prod -N ""
   ssh-copy-id -i ~/.ssh/deploy_prod.pub deploy@your-server.com
   ```

2. Add the key path to environment secrets:
   ```yaml
   # ~/.bond/deployments/secrets/prod.yaml
   DEPLOY_SERVER: "your-server.com"
   DEPLOY_USER: "deploy"
   DEPLOY_SSH_KEY: "/home/andrew/.ssh/deploy_prod"
   ```

3. Use in your deployment script:
   ```bash
   ssh -i "$DEPLOY_SSH_KEY" "$DEPLOY_USER@$DEPLOY_SERVER" "cd /app && ./deploy.sh"
   ```

### Option B: Docker/container deployment

```yaml
# ~/.bond/deployments/secrets/prod.yaml
DOCKER_HOST: "ssh://deploy@your-server.com"
REGISTRY_URL: "ghcr.io/yourorg"
REGISTRY_TOKEN: "ghp_xxx"
```

```bash
# In your deploy.sh
docker login "$REGISTRY_URL" -u token -p "$REGISTRY_TOKEN"
DOCKER_HOST="$DOCKER_HOST" docker compose up -d
```

### Option C: Cloud provider CLI

```yaml
# ~/.bond/deployments/secrets/prod.yaml
AWS_ACCESS_KEY_ID: "AKIA..."
AWS_SECRET_ACCESS_KEY: "..."
AWS_REGION: "us-east-1"
ECS_CLUSTER: "prod-cluster"
ECS_SERVICE: "app-service"
```

```bash
# In your deploy.sh
aws ecs update-service --cluster "$ECS_CLUSTER" --service "$ECS_SERVICE" --force-new-deployment
```

### Key point

The deployment scripts run **on the Bond host**, not inside agent containers. The broker injects secrets as environment variables. The agent never sees the secrets — it only gets stdout/stderr back from the broker.

---

## Directory Structure

```
~/.bond/deployments/                 # HOST-ONLY — never mounted into containers
├── scripts/
│   └── registry/                    # Immutable script snapshots
│       └── {script-id}/
│           └── v{n}/
│               ├── deploy.sh        # Main deployment script
│               ├── rollback.sh      # Optional rollback
│               ├── manifest.json    # Metadata + SHA-256
│               └── .sha256          # Hash of all files
├── hooks/
│   └── {env}/
│       ├── pre_deploy.sh           # Runs before every deploy
│       └── post_deploy.sh          # Runs after every deploy
├── health/
│   └── {env}/
│       └── check.sh               # Periodic + post-deploy health check
├── secrets/
│   └── {env}.yaml                  # Environment secrets (plaintext for now)
├── receipts/
│   └── {env}/
│       └── {receipt-id}.json       # Immutable deployment records
├── locks/
│   └── {env}.lock                  # Active deployment locks
└── logs/
    └── {env}/
        └── deploy-{date}.log       # Deployment logs
```

---

## Deployment Flow (What Happens When You Deploy)

```
1. You promote script "001-migrate" to QA     (via UI or API)
2. deploy-qa agent calls broker:               POST /broker/deploy {action:"info"}
3. Agent calls broker:                         POST /broker/deploy {action:"validate"}
   → Broker checks: syntax, hash, window, dependencies
4. Agent calls broker:                         POST /broker/deploy {action:"pre-hook"}
   → Broker runs hooks/qa/pre_deploy.sh on host
5. Agent calls broker:                         POST /broker/deploy {action:"dry-run"}
   → Broker runs script with --dry-run flag
6. Agent calls broker:                         POST /broker/deploy {action:"deploy"}
   → Broker loads secrets/qa.yaml
   → Broker runs script on host with secrets injected
   → Returns stdout/stderr to agent
7. Agent calls broker:                         POST /broker/deploy {action:"post-hook"}
8. Agent calls broker:                         POST /broker/deploy {action:"health-check"}
9. Broker writes receipt to receipts/qa/
10. Agent reports result to you

On failure: agent runs rollback, files a GitHub issue with diagnosis
```

---

## Design Docs

- **039 — Deployment Agents:** Full architecture, security model, broker design → `docs/design/039-deployment-agents.md`
- **042 — Deployment Tab UI:** Frontend design, three-tier complexity model → `docs/design/042-deployment-tab-ui.md`
