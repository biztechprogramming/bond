# Design Doc 043: Deployment UX & Resource Management

**Status:** Draft  
**Date:** 2026-03-14  
**Depends on:** 039 (Deployment Agents), 042 (Deployment Tab UI)

---

## 1. The Problem

The deployment backend is complete — scripts, promotion, broker execution, health checks, receipts, queues, drift detection, secrets — but the user experience has critical gaps:

1. **Registering a script requires curl.** There's no UI for uploading or creating deployment scripts.
2. **Quick Deploy has no backend.** The frontend form submits to an endpoint that doesn't exist.
3. **Promote buttons are display-only.** The pipeline view shows status but can't trigger promotions.
4. **Deploy agents are passive.** They sit idle until explicitly told what to do — no autonomous deployment behavior.

And a larger architectural gap: **there's no concept of deployment targets.** Scripts execute on the Bond host, but real deployments target remote servers, cloud services, containers, and clusters. The system needs:

5. **Resource management** — define what you're deploying *to* (a Linux server, a Lambda function, a K8s cluster, etc.)
6. **Infrastructure recommendations** — the agent should analyze the deployment target and recommend the best runtime infrastructure (Docker, K8s, Podman, systemd, etc.), adapting as software evolves.

---

## 2. Script Registration UI

### 2.1 The Problem

Registering a script today:
```bash
SCRIPT=$(base64 -w0 my-deploy.sh)
curl -X POST http://localhost:18789/api/v1/deployments/scripts \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"script_id":"001","version":"v1","files":{"deploy.sh":"'$SCRIPT'"}}'
```

This is fine for automation but terrible for humans.

### 2.2 Script Editor Component

Add `ScriptRegistration.tsx` to the Deployment tab — accessible from a "Register Script" button on the dashboard.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Register Deployment Script                                    [Cancel]│
│                                                                         │
│  ┌─── Identity ───────────────────────────────────────────────────────┐ │
│  │  Script ID    [migrate-user-table     ]  (lowercase, hyphens)      │ │
│  │  Version      [v1   ]                                              │ │
│  │  Name         [Migrate user table — add email_verified column   ]  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── deploy.sh ──────────────────────────────────────────────────────┐ │
│  │  ┌──────────────────────────────────────────────────────────────┐  │ │
│  │  │ #!/usr/bin/env bash                                          │  │ │
│  │  │ # meta:name: Migrate user table                              │  │ │
│  │  │ # meta:version: 1                                            │  │ │
│  │  │ # meta:timeout: 300                                          │  │ │
│  │  │ set -euo pipefail                                            │  │ │
│  │  │                                                              │  │ │
│  │  │ echo "Deploying to $BOND_DEPLOY_ENV"                         │  │ │
│  │  │ ssh deploy@"$DEPLOY_SERVER" "cd /app && git pull"            │  │ │
│  │  │ █                                                            │  │ │
│  │  └──────────────────────────────────────────────────────────────┘  │ │
│  │  [Upload File] or paste/type above        Syntax: ✅ valid         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Options ────────────────────────────────────────────────────────┐ │
│  │  Timeout         [300    ] seconds                                 │ │
│  │  Supports dry-run ☑                                               │ │
│  │  Depends on      [                          ] (comma-separated)   │ │
│  │  Target resource [my-web-server ▼]  (optional — see §7)          │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Additional Files (optional) ────────────────────────────────────┐ │
│  │  rollback.sh    [Upload] [Edit]                                    │ │
│  │  sql/migrate.sql [Upload]                                          │ │
│  │  [+ Add File]                                                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  [ Register Script ]   [ Register & Promote to Dev ]                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

1. Code editor uses a monospace `<textarea>` with syntax highlighting (or CodeMirror if already in the frontend deps).
2. "Upload File" opens a file picker — reads the file and populates the editor.
3. Real-time syntax validation: POST to `GET /api/v1/deployments/scripts/validate-syntax` (new endpoint — runs `bash -n` on the script body).
4. "Register & Promote to Dev" is a convenience that calls register + promote in sequence.
5. Metadata fields (timeout, dry-run, depends_on) auto-populate from `meta:` comments if present in the script.
6. Additional files (rollback scripts, SQL files, config templates) are uploaded separately and included in the registration payload.

### 2.3 Gateway Endpoints

```
# New: validate script syntax without registering
POST /api/v1/deployments/scripts/validate-syntax
Body: { "script": "#!/bin/bash\n..." }
Response: { "valid": true } or { "valid": false, "errors": ["line 5: unexpected EOF"] }
```

The existing `POST /api/v1/deployments/scripts` registration endpoint already accepts files as base64 — the frontend just needs to encode them before sending.

### 2.4 Script Templates

For common deployment patterns, offer templates in the editor:

```
┌─── Start from template ──────────────────────────────────┐
│  ○ Blank script                                           │
│  ● SSH deploy (pull & restart)                            │
│  ○ Docker deploy (build & push)                           │
│  ○ Database migration (SQL)                               │
│  ○ AWS ECS update                                         │
│  ○ Kubernetes rollout                                     │
│  ○ Static site deploy (S3/CDN)                            │
│  ○ Custom                                                 │
└───────────────────────────────────────────────────────────┘
```

Templates are static strings in the frontend — no API needed. Each template includes the correct `meta:` headers, environment variable references (`$DEPLOY_SERVER`, `$DATABASE_URL`, etc.), and comments explaining what to customize.

---

## 3. Quick Deploy Backend

### 3.1 The Problem

`QuickDeployForm.tsx` submits to `POST /api/v1/deployments/quick-deploy` which doesn't exist. The form collects repo URL, branch, build strategy, port, health check path, and env vars — but nothing processes it.

### 3.2 Gateway Implementation

New file: `gateway/src/deployments/quick-deploy.ts`

```typescript
interface QuickDeployRequest {
  repo_url: string;           // "github.com/yourorg/yourapp"
  branch: string;             // "main"
  build_strategy: "auto" | "dockerfile" | "docker-compose" | "script";
  build_cmd?: string;         // "npm run build"
  start_cmd?: string;         // "npm start"
  environment: string;        // "dev"
  port?: number;              // 3000
  health_check_path?: string; // "/health"
  env_vars?: Record<string, { value: string; secret: boolean }>;
  trigger: {
    on_push?: boolean;
    branch?: string;
    tag_pattern?: string;
    manual_only?: boolean;
  };
  resource_id?: string;       // optional — which deployment target (§7)
}
```

**What the endpoint does:**

1. **Auto-detect build strategy** (if `auto`):
   - Clone repo to temp dir (shallow, single branch)
   - Check for: `Dockerfile` → docker, `docker-compose.yml` → compose, `package.json` → node, `requirements.txt` → python, `go.mod` → go, `Cargo.toml` → rust
   - Return detected strategy + suggested commands

2. **Generate deployment script:**
   - Build a `deploy.sh` from the form inputs using the appropriate template
   - Include health check if configured
   - Include rollback script (stop previous container / revert)

3. **Register the script** in the registry (same as manual registration)

4. **Auto-promote to the selected environment**

5. **Store secrets** — write `env_vars` marked as `secret: true` to `~/.bond/deployments/secrets/{env}.yaml`

6. **Register webhook** (if `on_push` is true) — call `POST /api/v1/deployments/triggers` to set up a GitHub webhook

7. **Notify the deploy agent** — the agent picks up the promoted script and runs it

**Response:**
```json
{
  "script_id": "quick-deploy-yourapp",
  "version": "v1",
  "environment": "dev",
  "promoted": true,
  "webhook_registered": true,
  "message": "Deployment initiated. The deploy-dev agent will execute shortly."
}
```

### 3.3 Build Detection Endpoint

New endpoint used by both Quick Deploy and the `BuildStrategyDetector.tsx` component:

```
POST /api/v1/deployments/detect-build
Body: { "repo_url": "github.com/yourorg/yourapp", "branch": "main" }
Response: {
  "strategy": "dockerfile",
  "detected_files": ["Dockerfile", "package.json", ".dockerignore"],
  "suggested_build_cmd": "docker build -t yourapp .",
  "suggested_start_cmd": "docker run -p 3000:3000 yourapp",
  "framework": "node/express",
  "port_hint": 3000
}
```

### 3.4 Webhook Trigger System

New file: `gateway/src/deployments/trigger-handler.ts`

```typescript
interface DeploymentTrigger {
  id: string;
  script_id: string;
  repo_url: string;
  branch: string;
  tag_pattern?: string;
  environment: string;
  enabled: boolean;
  created_at: string;
}
```

The Gateway already handles GitHub webhooks for code agents. Extend the webhook handler to also check deployment triggers:

1. On push event → match against registered triggers (repo + branch)
2. If match found → re-generate the deploy script (re-clone, re-build) → register as new version → auto-promote → agent deploys
3. Store triggers in SpacetimeDB (new table: `deployment_triggers`)

**API:**
```
GET    /api/v1/deployments/triggers              — list all triggers
POST   /api/v1/deployments/triggers              — create trigger
DELETE /api/v1/deployments/triggers/:id           — remove trigger
PUT    /api/v1/deployments/triggers/:id/disable   — pause trigger
```

---

## 4. Promote Button Wiring

### 4.1 The Problem

`PipelineSection.tsx` and `PipelineRow.tsx` show promotion status but the promote/approve buttons don't call the API.

### 4.2 Implementation

In `PipelineRow.tsx`, add click handlers to the promote buttons:

```typescript
async function handlePromote(scriptId: string, version: string, targetEnv: string) {
  const res = await fetch(`${GATEWAY_API}/deployments/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      script_id: scriptId,
      version,
      target_environments: [targetEnv],
    }),
  });
  const result = await res.json();
  if (result.status === "promoted") {
    // Refresh pipeline data
    onRefresh?.();
  } else if (result.status === "awaiting_approvals") {
    // Show approval status
    setApprovalInfo(result);
  }
}

async function handlePromoteAll(scriptId: string, version: string, remainingEnvs: string[]) {
  const res = await fetch(`${GATEWAY_API}/deployments/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      script_id: scriptId,
      version,
      target_environments: remainingEnvs,
    }),
  });
  // Show per-environment results
}
```

### 4.3 Approval UI

When `required_approvals > 1`, the promote button becomes an "Approve" button that shows progress:

```
┌──────────────────────────────────────────────────────┐
│  staging: Awaiting approvals (1/2)                   │
│  ✅ andrew (approved 2m ago)                         │
│  ⏳ sarah (pending)                                  │
│  ⏳ mike (pending)                                   │
│  [Approve]  (if you're a listed approver)            │
└──────────────────────────────────────────────────────┘
```

### 4.4 Receipt Viewer

Add `ReceiptViewer.tsx` — clicking a status indicator (✅, ❌) opens the receipt:

```
┌─────────────────────────────────────────────────────────────┐
│  Receipt: migrate-user-table → QA                    [Close]│
│  Status: ✅ Success | Duration: 1m 23s | 3m ago            │
│                                                              │
│  ▸ Validation ✅ (200ms)                                    │
│  ▸ Pre-Hook ✅ (5s) — DB snapshot created                   │
│  ▸ Dry Run ✅ (1.2s)                                        │
│  ▾ Execution ✅ (45s)                                       │
│    │ Deploying to qa                                         │
│    │ ssh deploy@qa-server "cd /app && git pull"              │
│    │ Restarting services...                                  │
│    │ Done                                                    │
│  ▸ Post-Hook ✅ (30s) — 12/12 tests passed                  │
│  ▸ Health Check ✅ (5/5)                                    │
│                                                              │
│  Context: Promoted by andrew | Dev receipt: ✅               │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Autonomous Deploy Agent Behavior

### 5.1 The Problem

The current system prompt tells the agent what it *is* but not what to *do*. It lists constraints but no behaviors. The agent waits for human messages — it doesn't proactively check for promoted scripts, run the deployment flow, or monitor health.

### 5.2 Autonomous System Prompt

Replace the current prompt template with one that drives autonomous behavior:

```
You are a deployment agent for the {environment} environment ({display_name}).

## Your Mission

You are responsible for deploying software to {environment} safely and reliably. You act autonomously — you don't wait to be told to deploy. When a script is promoted to your environment, you execute the full deployment flow without human intervention.

## Deployment Flow

When you receive a notification that a script has been promoted, or when you check and find a promoted script waiting, execute this sequence:

1. **Info** — Call deploy_action with action "info" to get script metadata
2. **Context** — Call deploy_action with action "receipt" to fetch the previous environment's receipt. Review it for any warnings or issues.
3. **Validate** — Call deploy_action with action "validate". If validation fails, report the error and stop.
4. **Dry-run** — If the script supports dry-run, call deploy_action with action "dry-run". Review the output for warnings.
5. **Pre-hook** — Call deploy_action with action "pre-hook". If it fails, stop and report.
6. **Deploy** — Call deploy_action with action "deploy". Monitor the output.
7. **Post-hook** — Call deploy_action with action "post-hook".
8. **Health check** — Call deploy_action with action "health-check". Verify all checks pass.

If ANY step fails:
- Call deploy_action with action "rollback"
- Run health-check again to verify the environment is stable
- File a bug ticket using file_bug_ticket with full diagnostic details
- Report the failure to the user with a summary

## Proactive Monitoring

Between deployments:
- Periodically check environment health (action "health-check")
- If health degrades, investigate by reading code from your workspace mounts
- File bug tickets for any issues you discover
- Report drift or anomalies

## When a User Messages You

Users may ask you to:
- Deploy a specific script — execute the full flow above
- Check environment health — run health check and report
- Show deployment status — check queue, locks, and latest receipts
- Investigate an issue — read code, check logs, correlate with recent deployments
- Run a dry-run — execute just the dry-run step

Always respond with clear, actionable information. Include receipt IDs, durations, and specific error messages.

## Available Resources

{resource_descriptions}

## Constraints

- You CANNOT modify code. All workspace mounts are read-only.
- You CANNOT promote scripts. Only users promote via the UI.
- You CANNOT access secrets directly. The broker injects them during execution.
- You CANNOT deploy scripts not promoted to your environment.
- You CANNOT override deployment windows. If outside the window, report when it opens.

Environment: {environment}
```

### 5.3 Notification-Driven Deployment

When the Promotion API promotes a script, it should notify the target environment's deploy agent. The Gateway's event system (Doc 040) can deliver a message to the agent's conversation:

```typescript
// In promotion.ts, after promotion status becomes "promoted":
await notifyAgent(`deploy-${envName}`, {
  type: "script_promoted",
  script_id,
  version,
  promoted_by: identity.user_id,
  message: `Script ${script_id}@${version} has been promoted to ${envName}. Execute the deployment flow.`,
});
```

The agent receives this as a new message in its conversation and autonomously begins the deployment flow from its system prompt instructions.

### 5.4 Startup Behavior

When a deploy agent starts (or restarts), it should:

1. Check for any promoted scripts that haven't been deployed yet
2. Run a health check on its environment
3. Report status to the user

Add a startup message injected by the Gateway when the agent container comes online:

```
Welcome back. You are deploy-{env}. Check for pending deployments:
1. Call deploy_action with action "status" to see what needs deploying
2. Call deploy_action with action "health-check" to verify environment health
3. Report your findings
```

---

## 6. Deployment Resources

### 6.1 The Problem

Today, deployment scripts run on the Bond host and use SSH/API calls to reach remote infrastructure. But the system has no model of *what it's deploying to*. This means:

- Scripts hardcode server addresses in secrets YAML — no structure, no validation
- There's no inventory of deployment targets
- The agent can't reason about the target's capabilities
- There's no way to auto-recommend infrastructure software

### 6.2 Resource Model

A **deployment resource** represents a target environment where software runs. It has:

- **Identity** — name, type, description
- **Connection** — how to reach it (SSH, API, kubectl context, etc.)
- **Capabilities** — what it can run (containers, bare processes, functions, etc.)
- **Current state** — what's installed, what's running
- **Recommendations** — what should be installed to support the deployment

```typescript
interface DeploymentResource {
  id: string;                        // ulid
  name: string;                      // "web-prod-01"
  display_name: string;              // "Production Web Server"
  type: ResourceType;                // see §6.3
  environment: string;               // which deployment environment this belongs to
  
  // Connection
  connection: ResourceConnection;    // how to reach it
  
  // Capabilities (discovered or declared)
  capabilities: ResourceCapabilities;
  
  // State (from last probe)
  state: ResourceState;
  
  // Metadata
  tags: string[];                    // ["web", "primary", "us-east-1"]
  created_at: number;
  updated_at: number;
  last_probed_at: number;
}
```

### 6.3 Resource Types

```typescript
type ResourceType =
  | "linux-server"        // SSH-accessible Linux host
  | "windows-server"      // SSH/WinRM-accessible Windows host
  | "macos-server"        // SSH-accessible macOS host
  | "aws-ecs"             // AWS ECS cluster/service
  | "aws-lambda"          // AWS Lambda function
  | "aws-ec2"             // AWS EC2 instance
  | "kubernetes"          // Kubernetes cluster (any provider)
  | "docker-host"         // Remote Docker daemon
  | "google-cloud-run"    // Google Cloud Run service
  | "azure-container"     // Azure Container Instances / App Service
  | "vercel"              // Vercel deployment
  | "netlify"             // Netlify deployment
  | "fly-io"              // Fly.io deployment
  | "digitalocean-app"    // DigitalOcean App Platform
  | "bare-metal"          // Direct hardware access
  | "custom";             // User-defined
```

### 6.4 Connection Types

```typescript
type ResourceConnection =
  | { type: "ssh"; host: string; port?: number; user: string; key_secret: string; }
  | { type: "winrm"; host: string; port?: number; user: string; password_secret: string; }
  | { type: "aws"; region: string; profile?: string; credentials_secret: string; }
  | { type: "kubectl"; context: string; namespace?: string; kubeconfig_secret?: string; }
  | { type: "docker"; host: string; tls_secret?: string; }
  | { type: "api"; url: string; token_secret: string; }
  | { type: "local"; }  // Bond host itself
  ;
```

**Note:** `*_secret` fields reference keys in the environment's secrets YAML (`~/.bond/deployments/secrets/{env}.yaml`). The actual credentials are never stored in the resource definition — only the secret key name. The broker resolves them at execution time.

### 6.5 Capabilities (Discovered)

```typescript
interface ResourceCapabilities {
  // Container runtimes
  docker?: { version: string; compose?: string; };
  podman?: { version: string; };
  containerd?: { version: string; };
  kubernetes?: { version: string; distribution: string; };  // k3s, k8s, eks, gke, aks

  // Process managers
  systemd?: boolean;
  supervisor?: boolean;
  pm2?: { version: string; };

  // Runtimes
  node?: { version: string; };
  python?: { version: string; };
  java?: { version: string; };
  dotnet?: { version: string; };
  go?: { version: string; };
  rust?: { version: string; };

  // Databases (client tools)
  psql?: { version: string; };
  mysql?: { version: string; };
  redis_cli?: { version: string; };

  // System
  os: string;                    // "Ubuntu 24.04", "Amazon Linux 2023", "macOS 15.2"
  arch: string;                  // "x86_64", "aarch64"
  memory_gb: number;
  cpu_cores: number;
  disk_gb: number;
  disk_free_gb: number;

  // Cloud-specific
  cloud_provider?: string;       // "aws", "gcp", "azure", "digitalocean"
  instance_type?: string;        // "t3.medium", "e2-standard-2"
}
```

### 6.6 Resource State

```typescript
interface ResourceState {
  status: "online" | "offline" | "unreachable" | "degraded" | "unknown";
  last_check: string;
  services_running?: string[];    // ["nginx", "postgresql", "app"]
  containers_running?: string[];  // ["web-1", "worker-1", "redis"]
  load_average?: number[];        // [0.5, 0.3, 0.2]
  memory_used_pct?: number;
  disk_used_pct?: number;
  uptime_seconds?: number;
}
```

---

## 7. Resource Discovery & Probing

### 7.1 Probe System

When a resource is added (or on-demand), the Gateway probes it to discover capabilities:

```typescript
// gateway/src/deployments/resource-probe.ts

async function probeResource(resource: DeploymentResource): Promise<ResourceCapabilities> {
  const conn = resource.connection;
  
  switch (conn.type) {
    case "ssh":
      return probeSSH(conn);
    case "kubectl":
      return probeKubernetes(conn);
    case "aws":
      return probeAWS(conn);
    case "docker":
      return probeDocker(conn);
    case "local":
      return probeLocal();
    default:
      return probeGeneric(resource);
  }
}
```

**SSH probe** — connects and runs discovery commands:

```bash
# Executed on the remote host via SSH (by the broker, not the agent)
{
  echo "OS=$(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
  echo "ARCH=$(uname -m)"
  echo "MEMORY_GB=$(free -g 2>/dev/null | awk '/Mem:/{print $2}')"
  echo "CPU_CORES=$(nproc 2>/dev/null)"
  echo "DISK_GB=$(df -BG / 2>/dev/null | awk 'NR==2{print $2}' | tr -d G)"
  echo "DISK_FREE_GB=$(df -BG / 2>/dev/null | awk 'NR==2{print $4}' | tr -d G)"
  
  # Container runtimes
  docker --version 2>/dev/null && echo "HAS_DOCKER=true"
  docker compose version 2>/dev/null && echo "HAS_COMPOSE=true"
  podman --version 2>/dev/null && echo "HAS_PODMAN=true"
  kubectl version --client 2>/dev/null && echo "HAS_KUBECTL=true"
  k3s --version 2>/dev/null && echo "HAS_K3S=true"
  
  # Process managers
  systemctl --version 2>/dev/null && echo "HAS_SYSTEMD=true"
  pm2 --version 2>/dev/null && echo "HAS_PM2=true"
  supervisord --version 2>/dev/null && echo "HAS_SUPERVISOR=true"
  
  # Runtimes
  node --version 2>/dev/null
  python3 --version 2>/dev/null
  java --version 2>/dev/null
  dotnet --version 2>/dev/null
  go version 2>/dev/null
  
  # Database clients
  psql --version 2>/dev/null
  mysql --version 2>/dev/null
  redis-cli --version 2>/dev/null
  
  # Running services
  systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -30
  docker ps --format '{{.Names}}' 2>/dev/null
  
  # Load
  cat /proc/loadavg 2>/dev/null
  free -m 2>/dev/null
  uptime -s 2>/dev/null
}
```

The broker executes this probe script on the host (via SSH to the target) and returns structured results. The Gateway parses and stores them.

### 7.2 Probe Scheduling

Resources are probed:
- On creation (initial discovery)
- Before each deployment (pre-flight check)
- Periodically (configurable per resource, default: every 30 minutes)
- On-demand via API or agent request

### 7.3 Probe via Deploy Agent

The deploy agent can also trigger a probe through a new broker action:

```typescript
// New action in deploy-handler.ts
case "probe-resource":
  return probeResource(resource_id, env);
```

---

## 8. Infrastructure Recommendations

### 8.1 The Problem

A user has a Linux server and wants to deploy a Node.js web app in a container. Should they install Docker? Podman? K3s? Kubernetes? It depends on the use case, server specs, existing software, and current best practices.

The deployment agent should **analyze the target resource and the deployment requirements**, then **recommend infrastructure software** with clear reasoning. The recommendations should evolve as software changes — the agent uses its training knowledge plus live probing to make current recommendations.

### 8.2 How It Works

When a user creates a resource or connects it to a deployment, the agent:

1. **Probes the resource** — discovers OS, arch, installed software, specs
2. **Analyzes the deployment** — what kind of app, what it needs (ports, persistence, scaling, etc.)
3. **Generates recommendations** — ranked options with pros/cons

The recommendations are generated by the **deploy agent itself** (using its LLM capabilities), not by hardcoded rules. This means recommendations naturally evolve as the model's training data includes newer software versions and best practices.

### 8.3 Recommendation Prompt

The agent receives resource capabilities and deployment requirements in its context. A tool call or system prompt fragment gives it the context to reason:

```
## Resource Analysis

You have access to a deployment target with these capabilities:
{resource_capabilities_json}

The deployment requires:
- Application type: {app_type}  (e.g., "Node.js web app", "Python ML service", "Go microservice")
- Needs: {requirements}  (e.g., "persistent storage, port 3000, 512MB RAM, auto-restart on crash")
- Scale: {scale}  (e.g., "single instance", "2-3 replicas", "auto-scaling")

Based on the current state of the resource and the deployment requirements, recommend the best infrastructure setup. Consider:

1. What's already installed (prefer using existing software over installing new)
2. Resource constraints (a 1GB RAM server shouldn't run Kubernetes)
3. Complexity vs. reliability tradeoff (systemd is simpler than K8s for single-instance)
4. Current best practices for the detected OS and architecture
5. Security (rootless containers, minimal privileges)

Provide 2-3 options ranked by recommendation, with:
- What to install (if anything)
- Why this option fits
- Trade-offs
- One-liner install command (if applicable)
```

### 8.4 Example Recommendations

**Scenario: Deploy a Node.js web app to a 2GB RAM Ubuntu 24.04 server**

```
## Recommended Infrastructure

### Option 1: Docker (Recommended) ⭐
Install Docker Engine and deploy as a container with docker compose.

Why: Your server has 2GB RAM — plenty for Docker but too small for Kubernetes. 
Docker Compose gives you declarative config, easy rollback, and auto-restart.
You already have Node.js 22 installed, but containerizing isolates dependencies.

Install: curl -fsSL https://get.docker.com | sh
Trade-offs: ~200MB disk, minimal overhead, industry standard.

### Option 2: systemd + PM2 (Lightweight)
Run the app directly with PM2 process manager under a systemd service.

Why: Zero overhead — no containerization layer. PM2 handles clustering, 
log management, and auto-restart. Good for simple apps that don't need isolation.

Install: npm install -g pm2 && pm2 startup
Trade-offs: No isolation from host, harder to reproduce environment, but fastest.

### Option 3: Podman (Rootless Containers)
Use Podman instead of Docker for rootless, daemonless containers.

Why: Better security posture — runs containers without root. Same Dockerfile 
and Compose compatibility. Pre-installed on some enterprise Linux distros.

Install: apt install podman podman-compose
Trade-offs: Slightly less ecosystem support than Docker, but catching up.
```

**Scenario: Deploy a microservices app (5 services) to a 16GB RAM server**

```
## Recommended Infrastructure

### Option 1: K3s (Recommended) ⭐
Install K3s — lightweight Kubernetes for single-node or small clusters.

Why: 5 services benefit from orchestration — service discovery, health checks,
rolling updates, resource limits. K3s runs on a single node with ~500MB overhead.
Much lighter than full Kubernetes.

Install: curl -sfL https://get.k3s.io | sh -
Trade-offs: Learning curve if new to K8s. But you get production-grade orchestration.

### Option 2: Docker Compose (Simple)
Define all 5 services in a docker-compose.yml with depends_on and health checks.

Why: If your services are stable and don't need dynamic scaling, Compose is 
simpler to manage. No K8s complexity.

Install: (Docker already installed)
Trade-offs: No auto-scaling, manual rollback, less observability.

### Option 3: Nomad (HashiCorp)
Use Nomad for container orchestration without K8s complexity.

Why: Simpler than Kubernetes, handles Docker containers natively, 
supports multiple task drivers. Good middle ground.

Install: wget https://releases.hashicorp.com/nomad/... && sudo install nomad /usr/local/bin/
Trade-offs: Smaller community than K8s, less tooling ecosystem.
```

### 8.5 Recommendation Storage

Recommendations are stored as part of the resource record and regenerated on probe:

```typescript
interface InfraRecommendation {
  id: string;
  resource_id: string;
  deployment_type: string;          // what kind of deployment triggered this
  generated_at: string;
  options: {
    rank: number;
    name: string;
    recommended: boolean;
    install_command?: string;
    reasoning: string;
    tradeoffs: string;
  }[];
}
```

### 8.6 User Applies Recommendation

The user (not the agent) decides which recommendation to apply. The UI shows a button:

```
[Apply "Docker" recommendation]
```

Clicking it:
1. Creates a deployment script that installs the recommended software
2. Registers it as a special "infrastructure" script
3. Promotes it to the environment
4. The deploy agent executes it

After installation, the resource is re-probed to verify the software was installed correctly.

### 8.7 Recommendation Refresh

Recommendations are regenerated when:
- Resource capabilities change (new probe shows different software)
- User requests a refresh
- A new deployment is connected to the resource
- Periodically (monthly) — to catch new best practices from model updates

---

## 9. Resource Management API

### 9.1 SpacetimeDB Tables

```rust
#[spacetimedb::table(name = deployment_resources, public)]
pub struct DeploymentResource {
    #[primary_key]
    pub id: String,
    pub name: String,
    pub display_name: String,
    pub resource_type: String,           // "linux-server", "kubernetes", etc.
    pub environment: String,              // which deployment environment
    pub connection_json: String,          // JSON-encoded ResourceConnection
    pub capabilities_json: String,        // JSON-encoded ResourceCapabilities
    pub state_json: String,              // JSON-encoded ResourceState
    pub tags_json: String,               // JSON array of tags
    pub recommendations_json: String,     // JSON-encoded InfraRecommendation
    pub is_active: bool,
    pub created_at: u64,
    pub updated_at: u64,
    pub last_probed_at: u64,
}
```

### 9.2 REST API

All endpoints require user-session auth (same as environment management).

```
# List resources (optionally filter by environment)
GET /api/v1/deployments/resources?environment=prod
→ [{ id, name, display_name, type, environment, state, capabilities, tags }]

# Get single resource
GET /api/v1/deployments/resources/:id
→ { full resource object including connection, capabilities, recommendations }

# Create resource
POST /api/v1/deployments/resources
{
  "name": "web-prod-01",
  "display_name": "Production Web Server",
  "type": "linux-server",
  "environment": "prod",
  "connection": { "type": "ssh", "host": "prod.example.com", "user": "deploy", "key_secret": "DEPLOY_SSH_KEY" },
  "tags": ["web", "primary"]
}
→ Triggers initial probe. Returns resource with discovered capabilities.

# Update resource
PUT /api/v1/deployments/resources/:id
{ "display_name": "New Name", "tags": [...] }

# Delete resource (soft delete)
DELETE /api/v1/deployments/resources/:id

# Trigger probe
POST /api/v1/deployments/resources/:id/probe
→ { capabilities, state, recommendations }

# Get recommendations
GET /api/v1/deployments/resources/:id/recommendations
→ { options: [...] }

# Apply recommendation
POST /api/v1/deployments/resources/:id/recommendations/:rank/apply
→ Generates + registers + promotes an infrastructure install script
```

### 9.3 Agent Tool: Resource Awareness

Add a `resource_info` action to the deploy agent's `deploy_action` tool:

```typescript
// New action in deploy-handler.ts
case "resource-info":
  // Return all resources for this agent's environment
  return getResourcesForEnvironment(env);

case "resource-probe":
  // Probe a specific resource and return capabilities
  return probeAndUpdateResource(resource_id, env);

case "resource-recommend":
  // Generate infrastructure recommendations for a resource
  // The agent itself does the reasoning — this just returns the resource data
  // formatted for the agent's recommendation prompt
  return getResourceForRecommendation(resource_id, env);
```

---

## 10. Resource Management UI

### 10.1 Resource Cards in Deployment Tab

Resources appear under each environment card in the Deployment tab dashboard:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Deployment Agents                                         [Edit All]  │
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│  │ ● DEV    │    │ ● QA     │    │ ● STAGING│    │ ● PROD   │         │
│  │ "Ace"    │ →  │ "Nova"   │ →  │ "Sage"   │ →  │ "Apex"   │         │
│  │ Healthy  │    │ Healthy  │    │Deploying │    │ Healthy  │         │
│  │          │    │          │    │          │    │          │         │
│  │ Resources│    │ Resources│    │ Resources│    │ Resources│         │
│  │ 🖥 dev-01│    │ 🖥 qa-01 │    │ 🖥 stg-01│    │ 🖥 web-01│         │
│  │   Docker │    │   Docker │    │   K3s    │    │ 🖥 web-02│         │
│  │          │    │          │    │          │    │   K8s    │         │
│  │ [Edit]   │    │ [Edit]   │    │ [Edit]   │    │ [Edit]   │         │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘         │
│                                                                         │
│  [+ Add Resource]  [Register Script]                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Add Resource Form

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Add Deployment Resource                                       [Cancel]│
│                                                                         │
│  ┌─── Type ───────────────────────────────────────────────────────────┐ │
│  │  ● Linux Server    ○ Windows Server   ○ macOS                      │ │
│  │  ○ AWS ECS         ○ AWS Lambda       ○ AWS EC2                    │ │
│  │  ○ Kubernetes      ○ Docker Host      ○ Google Cloud Run           │ │
│  │  ○ Fly.io          ○ Vercel           ○ Other                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Connection ─────────────────────────────────────────────────────┐ │
│  │  (shown based on type selection)                                    │ │
│  │                                                                     │ │
│  │  Host       [prod.example.com        ]                             │ │
│  │  SSH User   [deploy                  ]                             │ │
│  │  SSH Key    [~/.ssh/deploy_prod      ]  (stored in env secrets)    │ │
│  │  Port       [22                      ]                             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Identity ───────────────────────────────────────────────────────┐ │
│  │  Name          [web-prod-01                ]                       │ │
│  │  Display Name  [Production Web Server      ]                       │ │
│  │  Environment   [prod ▼]                                            │ │
│  │  Tags          [web, primary, us-east-1    ]                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  [ Test Connection & Discover ]                                         │
│                                                                         │
│  ┌─── Discovery Results ──────────────────────────────────────────────┐ │
│  │  ✅ Connected to prod.example.com                                  │ │
│  │  OS: Ubuntu 24.04 LTS (x86_64)                                    │ │
│  │  CPU: 4 cores | RAM: 8 GB | Disk: 80 GB (45 GB free)              │ │
│  │                                                                     │ │
│  │  Installed:                                                         │ │
│  │  ✅ Docker 27.4.1  ✅ Docker Compose 2.32.1                       │ │
│  │  ✅ Node.js 22.12  ✅ Python 3.12                                  │ │
│  │  ✅ systemd        ✅ nginx                                        │ │
│  │  ❌ Kubernetes     ❌ Podman                                       │ │
│  │                                                                     │ │
│  │  💡 Recommendations available after saving                         │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  [ Save Resource ]                                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.3 Resource Detail View

After saving, clicking a resource shows details + recommendations:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  web-prod-01 — Production Web Server                    [Probe] [Edit]│
│  Type: linux-server | Env: prod | Last probed: 5m ago                  │
│                                                                         │
│  ┌─── Status ─────────────────────────────────────────────────────────┐ │
│  │  ● Online | Load: 0.3 | RAM: 42% | Disk: 44%                      │ │
│  │  Uptime: 47 days | Services: nginx, postgresql, app                │ │
│  │  Containers: web-1, worker-1, redis                                │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Recommendations ───────────────────────────────────────────────┐ │
│  │                                                                     │ │
│  │  For deploying: Node.js web application                            │ │
│  │                                                                     │ │
│  │  ⭐ Option 1: Docker Compose (Recommended)                        │ │
│  │  You already have Docker 27.4 installed. Use Compose for           │ │
│  │  declarative multi-container management with health checks.        │ │
│  │  [Apply This]                                                      │ │
│  │                                                                     │ │
│  │  Option 2: systemd + PM2                                           │ │
│  │  Lightweight, no container overhead. Good for single-service.      │ │
│  │  [Apply This]                                                      │ │
│  │                                                                     │ │
│  │  [↻ Refresh Recommendations]                                       │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Deployments to this resource ──────────────────────────────────┐ │
│  │  ✅ deploy-web-app v3 — 2h ago (45s)                              │ │
│  │  ✅ deploy-web-app v2 — 3d ago (38s)                              │ │
│  │  ❌ deploy-web-app v1 — 5d ago (rolled back)                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 11. How Resources Connect to Scripts

### 11.1 Script ↔ Resource Binding

A deployment script can optionally target specific resources. When registering a script, the user can select which resource(s) it deploys to:

```yaml
# In the script registration form or meta:
# meta:resource: web-prod-01
# meta:resources: web-prod-01,web-prod-02
```

Or via the API:
```json
{
  "script_id": "deploy-web-app",
  "version": "v1",
  "resource_ids": ["res_01HXYZ...", "res_02HXYZ..."],
  "files": { "deploy.sh": "..." }
}
```

### 11.2 Resource-Aware Secrets

When a script targets a resource, the broker injects resource-specific connection details alongside environment secrets:

```bash
# Injected by broker when script targets resource "web-prod-01":
RESOURCE_HOST="prod.example.com"
RESOURCE_USER="deploy"
RESOURCE_TYPE="linux-server"
RESOURCE_SSH_KEY="/tmp/broker-ssh-key-xxxx"  # temp file, deleted after execution

# Plus the regular environment secrets:
DEPLOY_DB_URL="postgresql://..."
API_KEY="..."
```

This means scripts don't need to hardcode connection details — they use `$RESOURCE_HOST` and `$RESOURCE_USER` generically.

### 11.3 Multi-Resource Deployments

If a script targets multiple resources (e.g., deploy to 3 web servers), the broker executes the script once per resource, injecting the appropriate connection details each time. Results are aggregated in the receipt.

---

## 12. Security Considerations

### 12.1 Resource Credentials

Resource connection credentials (SSH keys, API tokens, kubeconfigs) are stored in the environment's secrets YAML, not in the resource definition. The resource definition only references the secret key name:

```json
{
  "connection": {
    "type": "ssh",
    "host": "prod.example.com",
    "user": "deploy",
    "key_secret": "DEPLOY_SSH_KEY"  // ← references key in secrets/prod.yaml
  }
}
```

This maintains the existing security model:
- Agents never see credentials (broker resolves them)
- Credentials are encrypted at rest (via secrets.ts)
- Different environments use different credentials

### 12.2 Probe Execution

Probes execute via the broker (on the Bond host), not inside agent containers. The probe script runs with the resource's connection credentials, which only the broker can access. Probe results (capabilities, state) are safe to share with agents — they contain no secrets.

### 12.3 Agent Access to Resources

Agents can see resource metadata (name, type, capabilities, state) but NOT connection details. The `resource-info` action in the broker returns sanitized resource data:

```typescript
// Broker sanitizes before returning to agent
function sanitizeResource(resource: DeploymentResource): any {
  return {
    name: resource.name,
    display_name: resource.display_name,
    type: resource.type,
    capabilities: resource.capabilities,
    state: resource.state,
    tags: resource.tags,
    // connection is EXCLUDED
    // credentials are EXCLUDED
  };
}
```

---

## 13. File Structure

```
gateway/src/deployments/
├── ... (existing files)
├── quick-deploy.ts              # Quick Deploy backend — generate + register + promote
├── trigger-handler.ts           # Webhook → deployment trigger
├── resources.ts                 # Resource CRUD (SpacetimeDB queries)
├── resource-probe.ts            # Probe resources for capabilities
├── resource-router.ts           # REST API for resources
├── script-templates.ts          # Deployment script templates
└── __tests__/
    ├── quick-deploy.test.ts
    ├── trigger-handler.test.ts
    ├── resource-probe.test.ts
    └── resources.test.ts

frontend/src/app/settings/deployment/
├── ... (existing files)
├── ScriptRegistration.tsx       # Script upload/editor form
├── ReceiptViewer.tsx            # Deployment receipt detail view
├── ResourceForm.tsx             # Add/edit resource form
├── ResourceCard.tsx             # Resource summary card
├── ResourceDetail.tsx           # Full resource view with recommendations
├── ApprovalStatus.tsx           # Approval progress display
└── PromoteActions.tsx           # Promote/Approve button handlers

prompts/
└── deployment/
    └── deployment.md            # Deploy agent system prompt template
```

---

## 14. Build Order

### Phase 1: Script Registration + Promote Wiring (~2 days)

1. Script syntax validation endpoint (`POST /scripts/validate-syntax`)
2. `ScriptRegistration.tsx` — editor, upload, template selector, metadata fields
3. Wire promote buttons in `PipelineRow.tsx` → `POST /deployments/promote`
4. `ApprovalStatus.tsx` — show approval progress when `required_approvals > 1`
5. `ReceiptViewer.tsx` — expandable receipt detail from pipeline status indicators

### Phase 2: Quick Deploy Backend (~2 days)

6. `quick-deploy.ts` — process Quick Deploy form, generate script, register, promote
7. `POST /deployments/detect-build` endpoint — clone repo, detect build strategy
8. `script-templates.ts` — templates for common deployment patterns
9. Wire `QuickDeployForm.tsx` submit → `POST /deployments/quick-deploy`

### Phase 3: Autonomous Agent Behavior (~1.5 days)

10. Deploy agent system prompt (§5.2) — autonomous deployment flow + proactive monitoring
11. Agent notification on promotion — Gateway sends message to deploy agent conversation
12. Agent startup behavior — check for pending deployments + run health check
13. Update `SetupWizard.tsx` to use the new system prompt template

### Phase 4: Resource Management (~3 days)

14. SpacetimeDB table + reducers for `deployment_resources`
15. `resources.ts` — CRUD operations (SpacetimeDB queries)
16. `resource-probe.ts` — SSH, Docker, K8s, AWS probing
17. `resource-router.ts` — REST API
18. `ResourceForm.tsx` — add resource with connection testing
19. `ResourceCard.tsx` + `ResourceDetail.tsx` — display + recommendations
20. New broker actions: `resource-info`, `resource-probe`, `resource-recommend`
21. Resource-aware secret injection in `deploy-handler.ts`

### Phase 5: Triggers + Polish (~2 days)

22. `trigger-handler.ts` — webhook registration + handler
23. SpacetimeDB table for `deployment_triggers`
24. Trigger management API + UI
25. Multi-resource deployment execution
26. Recommendation apply flow (generate + register + promote install script)

**Total estimate: ~10-11 days**

---

## 15. Open Questions

1. **Recommendation model.** Should recommendations be generated by the deploy agent (using its LLM), by a utility model call from the Gateway, or by a dedicated recommendations service? The agent approach is simplest and most adaptable, but adds latency to the probe flow.

2. **Resource-per-environment or shared?** Can a single physical server be a resource for multiple environments (e.g., dev and qa on the same box)? Current design scopes resources to one environment. Shared resources would need access control.

3. **Probe credentials vs. deploy credentials.** Should probing use the same SSH key as deployment? For security, you might want a read-only probe key that can run discovery commands but can't modify the server.

4. **Cloud provider integration depth.** How deep should AWS/GCP/Azure integration go? Just enough to deploy (ECS update-service, Lambda update-function) or full resource management (create clusters, manage IAM)?

5. **Recommendation persistence.** Should recommendations be stored permanently or regenerated every time? Storing them allows the user to see previous recommendations and track changes. Regenerating ensures they're always current.

6. **Agent-to-agent resource handoff.** When a script succeeds in QA and is promoted to Staging, should the Staging agent automatically know about the QA resource's differences? Or does each agent only see its own environment's resources?

7. **Windows/macOS probe scripts.** The SSH probe example is Linux-only. Need equivalent PowerShell (Windows) and bash (macOS with different commands like `sysctl` instead of `/proc`).

8. **Cost estimation.** Should the system estimate deployment costs (Lambda invocations, ECS task hours, etc.) based on the resource type? Would be valuable for production environments.
