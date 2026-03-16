# Design Doc 045: Deployment UX — Guided Workflows & Infrastructure Visibility

**Status:** Draft  
**Date:** 2026-03-16  
**Depends on:** 039 (Deployment Agents), 042 (Deployment Tab UI), 043 (Deployment UX & Resources), 044 (Remote Discovery & Deployment Monitoring)

---

## 1. The Problem

Bond now has a powerful deployment backend: per-environment agents, broker-mediated SSH execution, discovery scripts that map out running applications, monitoring cycles with intelligent issue deduplication, and proposal generation that turns discovered state into deployable scripts. The individual capabilities are there.

But the UI doesn't connect them. A user looking at the Deployment tab sees an agent card grid, a pipeline section, and some buttons. The new discovery and monitoring features from Doc 044 live in components (`DiscoveryMonitoringPanel`, `DiscoveryView`, `MonitoringSection`, `IssueTracker`) that aren't wired into the main view and require the user to already know what they're doing.

The current experience:

```
1. Create agents (SetupWizard)
2. Add a resource (ResourceForm — separate view)
3. Probe it (ResourceDetail — separate view)
4. Trigger discovery (??? — no clear entry point)
5. View manifest (DiscoveryView — disconnected)
6. View proposals (ProposalViewer — separate click, no context)
7. Accept script → manually register
8. Promote → approve → deploy across environments
```

That's **8+ steps across disconnected views** to go from "I have a server" to "Bond is managing it." The backend can do this in a single orchestrated flow — the UI should too.

### The Competitive Gap

| Tool | "Add server to deployment" flow | Steps |
|---|---|---|
| **Coolify** | Add server → paste SSH key → auto-discover → one-click deploy from Git | 3 |
| **Kamal** | `kamal init` → auto-detects app → generates deploy.yml → `kamal deploy` | 2 |
| **Portainer** | Connect environment → auto-discovers all containers → manage in-place | 2 |
| **Backstage** | Software catalog auto-populates → pick template → fill 3 fields → done | 3 |
| **Netdata** | Install agent → auto-discovers everything → monitoring works immediately | 1 |
| **Railway** | Connect repo → auto-detect language/framework → deploy | 2 |
| **Bond (today)** | 8+ manual steps across disconnected views | 8+ |

Bond's backend is more capable than most of these tools. The problem is purely UX: **features exist but aren't composed into workflows.**

---

## 2. Design Principles

### 2.1 Workflows, Not Feature Menus

Every major action should be a guided flow that chains the right backend calls together. The user picks an intent ("add a server", "deploy my app", "set up monitoring") and the UI orchestrates the multi-step process.

### 2.2 Progressive Disclosure

Show the minimum at each step. Don't present 8 checkboxes when smart defaults cover 7 of them. Power users can expand "Advanced" sections; new users see only what matters.

### 2.3 Discovery Is Setup

Don't treat discovery as a feature to invoke — treat it as the onboarding path. When a user adds a server, discovery runs automatically. The results drive what happens next (which scripts to generate, which monitoring to enable, which alerts to configure).

### 2.4 Zero-Config Monitoring

Monitoring should activate with smart defaults the moment a resource is added. The user should never have to think about cron expressions or check intervals unless they want to tune them.

### 2.5 Context Follows the User

When the user is looking at a server, they should see its health, its last deploy, its alerts, and its logs — all in one place. No "go to the monitoring tab, then filter by this environment, then find this resource."

---

## 3. Open Source Inspiration

### 3.1 Coolify — Server Onboarding

Coolify's "Add Server" flow is the gold standard for self-hosted deployment UX:
- Single form: hostname, SSH key, port
- One click → validates connection → installs Docker if needed → discovers running containers
- Server dashboard immediately shows all resources with health status
- Deploy from Git: pick repo → detect Dockerfile/buildpack → deploy

**What we adopt:** The guided connection flow with immediate auto-discovery. The server-centric dashboard as the primary view.

**Where we exceed:** Coolify only discovers Docker containers. Bond discovers nginx configs, .env files, systemd services, database connections, DNS records, and cross-server topology.

### 3.2 Backstage — Software Catalog + Scaffolder

Backstage treats the software catalog as the center of everything:
- Every service, database, and infrastructure component is an entity in the catalog
- Entities have owners, lifecycle stages, and dependencies
- The scaffolder generates new services from templates (Cookiecutter/Yeoman-style)
- TechDocs provides inline documentation per entity

**What we adopt:** The entity model (our resources + manifests). The scaffolder concept (our proposal generator turning discovery into scripts). The relationship graph (our topology).

**Where we exceed:** Backstage requires manual catalog-info.yaml files or external processors. Bond auto-discovers everything via SSH.

### 3.3 Portainer — Environment Dashboard

Portainer's environment view is the reference for at-a-glance infrastructure status:
- Environments listed as cards with resource counts and health indicators
- Click into an environment → see all containers, volumes, networks, images
- Real-time stats (CPU, RAM, network) per container
- Stack management: compose files deployed and managed in-place

**What we adopt:** The environment-as-primary-navigation pattern. Live resource stats on the dashboard. The stack/compose management UX.

### 3.4 Grafana — Dashboards + Log Viewer

Grafana's log exploration (Loki) and dashboard composition:
- Log viewer with live tail, search, label filtering, time range scrubbing
- Dashboard panels composable from queries
- Alerting rules with notification channels
- Annotation markers for deployments on time-series graphs

**What we adopt:** The log viewer UX (live tail with search). Deployment annotations on timelines. Alert rule configuration patterns.

### 3.5 Netdata — Zero-Config Monitoring

Netdata's approach to monitoring is "install and forget":
- Agent auto-detects hundreds of applications (databases, web servers, message queues)
- Pre-configured alert rules for every detected application
- No dashboards to build — everything is auto-generated
- Anomaly detection built-in

**What we adopt:** Auto-detection driving auto-configuration. Smart default alerts per discovered service type. Zero manual setup for basic monitoring.

### 3.6 Weave Scope — Infrastructure Topology

Weave Scope provides real-time infrastructure visualization:
- Auto-discovered topology graph of hosts, containers, processes
- Click a node → see connections, resource usage, metadata
- Time travel: replay the topology at any past point
- Grouping by host, container, Kubernetes namespace

**What we adopt:** The topology-as-home-screen concept. Grouping by environment. Click-to-drill-down on nodes.

### 3.7 Argo CD — Deployment Pipeline Visualization

Argo CD's application sync status:
- Per-app status cards: synced, out-of-sync, degraded, healthy
- Sync history with diff viewer
- Resource tree showing Kubernetes objects and their status
- Rollback to any previous sync

**What we adopt:** The sync/deploy status model. History with diff. One-click rollback from the timeline.

---

## 4. New Components

### Tier 1: Critical — Unlock Core Value

These three components transform Bond from "tool collection" to "guided platform."

---

### 4.1 `OnboardServerWizard.tsx` — "Add & Discover Server"

**The single most important component.** Replaces the disconnected ResourceForm → probe → discovery → proposal flow with one guided wizard.

#### User Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Step 1: Connect                                                 │
│                                                                  │
│  Hostname or IP:  [ 10.0.1.50              ]                    │
│  SSH User:        [ deploy                  ]                    │
│  SSH Port:        [ 22                      ]                    │
│  Authentication:  (•) SSH Key File  ( ) Paste Key  ( ) Password  │
│  Key Path:        [ ~/.ssh/id_ed25519       ]                    │
│                                                                  │
│  Display Name:    [ prod-web-01             ] (optional)         │
│                                                                  │
│             [ Test Connection ]  ← validates SSH before proceed  │
│                                    shows: ✓ Connected (Ubuntu    │
│                                    22.04, 4 CPU, 8GB RAM)       │
│                                                                  │
│                                          [ Next → ]              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Step 2: Discovery  (auto-starts, no user action needed)         │
│                                                                  │
│  Discovering prod-web-01...                                      │
│                                                                  │
│  ✓ System Overview    ████████████████████ 100%  3.2s            │
│  ✓ Web Server         ████████████████████ 100%  2.1s            │
│  ◐ Applications       █████████████░░░░░░░  65%  ...             │
│  · Data Stores        ░░░░░░░░░░░░░░░░░░░░   0%                 │
│  · DNS & Networking   ░░░░░░░░░░░░░░░░░░░░   0%                 │
│                                                                  │
│  Found so far:                                                   │
│    🌐 nginx 1.24 — 3 sites configured                           │
│    📦 Node.js 20.11 — 2 apps running (Express, Next.js)         │
│    🐘 PostgreSQL 15 — 4 databases                                │
│    🔴 Redis 7.2 — 256MB used                                    │
│                                                                  │
│  ⚠ 2 security observations found                                │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Step 3: Review                                                  │
│                                                                  │
│  ┌─ System ─────────────────────────────────────────────────┐   │
│  │  Ubuntu 22.04 · 4 CPU · 8GB RAM · 120GB disk (42% used) │   │
│  │  Uptime: 47 days · Kernel: 5.15.0-91                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Applications ───────────────────────────────────────────┐   │
│  │  📦 my-api (Node.js 20, Express)                        │   │
│  │     Port: 3000 · PM2 managed · 14 env vars (3 secrets)  │   │
│  │     Git: github.com/org/my-api @ main (abc1234)         │   │
│  │                                                          │   │
│  │  📦 my-frontend (Node.js 20, Next.js)                   │   │
│  │     Port: 3001 · systemd · 8 env vars (1 secret)        │   │
│  │     Git: github.com/org/my-frontend @ main (def5678)    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Web Server ─────────────────────────────────────────────┐   │
│  │  🌐 nginx 1.24.0                                        │   │
│  │     api.example.com → localhost:3000 (SSL, Let's Encrypt)│   │
│  │     app.example.com → localhost:3001 (SSL, Let's Encrypt)│   │
│  │     ⚠ Cert expires in 12 days                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Data Stores ────────────────────────────────────────────┐   │
│  │  🐘 PostgreSQL 15.4  · 4 databases · WAL: replica       │   │
│  │  🔴 Redis 7.2.3      · 256MB / 512MB                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Security ── ⚠ 2 observations ──────────────────────────┐   │
│  │  ⚠ SSH root login is enabled                             │   │
│  │  ⚠ SSL certificate expires in 12 days                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│                                          [ Next → ]              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Step 4: Environment & Monitoring                                │
│                                                                  │
│  Assign to environment:  [ Production ▾ ]                        │
│                                                                  │
│  ┌─ Monitoring (auto-configured) ──────────────────────────┐    │
│  │  Based on what we found, we'll monitor:                 │    │
│  │                                                          │    │
│  │  ✓ Health checks          every 60s (production default) │    │
│  │  ✓ Log monitoring         nginx, pm2, journalctl        │    │
│  │  ✓ Resource usage         CPU, RAM, disk alerts          │    │
│  │  ✓ SSL certificate expiry alert at 14 days              │    │
│  │  ✓ Drift detection        after each deployment          │    │
│  │                                                          │    │
│  │  [ ] Auto-file GitHub issues                             │    │
│  │      Repo: [ org/infrastructure  ]                       │    │
│  │                                                          │    │
│  │  [ Advanced settings ▸ ]                                 │    │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│                                          [ Next → ]              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Step 5: Generate Scripts                                        │
│                                                                  │
│  We can generate deployment scripts from what we discovered.     │
│                                                                  │
│  ☑ Replication Scripts (Level 0)                                 │
│    Exactly replicate the current setup on a fresh server.        │
│    Scripts: setup-system.sh, deploy-my-api.sh,                   │
│             deploy-my-frontend.sh, setup-nginx.sh,               │
│             setup-postgres.sh, setup-redis.sh                    │
│                                                                  │
│  ☐ Operational Improvements (Level 1)          [ Preview ▸ ]    │
│    Add health checks, log rotation, automatic restarts,          │
│    backup scripts, SSL auto-renewal.                             │
│                                                                  │
│  ☐ Architecture Proposal (Level 2)             [ Preview ▸ ]    │
│    Docker Compose migration, CI/CD pipeline, staging             │
│    environment parity.                                           │
│                                                                  │
│  ┌─ Preview: deploy-my-api.sh ─────────────────────────────┐   │
│  │  #!/usr/bin/env bash                                     │   │
│  │  # meta: name=deploy-my-api                              │   │
│  │  # meta: version=v1                                      │   │
│  │  # meta: description=Deploy my-api Node.js application   │   │
│  │  # meta: timeout=120                                     │   │
│  │  # meta: dry_run=true                                    │   │
│  │  set -euo pipefail                                       │   │
│  │  ...                                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  [ Generate & Register Scripts ]  [ Skip — I'll write my own ]   │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  Step 6: Done! ✓                                                 │
│                                                                  │
│  prod-web-01 is now managed by Bond.                             │
│                                                                  │
│  ✓ Resource registered in Production                             │
│  ✓ Discovery manifest saved                                     │
│  ✓ Monitoring active (health, logs, SSL, drift)                  │
│  ✓ 6 deployment scripts registered                               │
│                                                                  │
│  What's next?                                                    │
│  • [ View Environment Dashboard ]                                │
│  • [ Promote scripts to QA ]                                     │
│  • [ Add another server ]                                        │
│  • [ View topology map ]                                         │
│                                                                  │
│  Tip: The deploy-prod agent will now monitor this server         │
│  continuously. If anything breaks, it'll file a GitHub issue.    │
└──────────────────────────────────────────────────────────────────┘
```

#### API Calls Per Step

| Step | Backend Calls |
|---|---|
| 1. Connect | `POST /deployments/resources` (create resource) → `POST /deployments/resources/:id/probe` (test connection) |
| 2. Discovery | `POST /broker/deploy` action:"discover" (runs all 6 layers via agent broker) |
| 3. Review | Display only — manifest already in memory from step 2 |
| 4. Environment | `PUT /deployments/resources/:id` (assign environment) → `PUT /deployments/monitoring/:env` (configure monitoring) |
| 5. Scripts | `POST /broker/deploy` action:"generate-replication-scripts" → `POST /deployments/scripts` (register each) |
| 6. Done | Display only — links to other views |

#### Component Interface

```typescript
interface OnboardServerWizardProps {
  environments: Array<{ name: string; display_name: string }>;
  onComplete: (result: OnboardingResult) => void;
  onCancel: () => void;
}

interface OnboardingResult {
  resource_id: string;
  environment: string;
  manifest_name: string;
  scripts_registered: string[];
  monitoring_enabled: boolean;
}

type WizardStep = "connect" | "discovery" | "review" | "environment" | "scripts" | "done";
```

#### Implementation Notes

- **Step 2 should use SSE or polling** for real-time progress. The backend discovery runs 5-6 layers sequentially (each 2-10s). The wizard should show per-layer progress as each completes rather than a single loading spinner.
- **Step 3 uses collapsible sections** — not all users need to see every detail. Applications and security observations should be expanded by default; system details collapsed.
- **Step 4 auto-suggests environment** based on hostname patterns: `prod-*` → Production, `staging-*` → Staging, etc. User can override.
- **Step 4 monitoring defaults** are driven by environment: production gets 60s intervals + auto-issue-filing, dev gets 5min intervals + no issues.
- **Step 5 script preview** shows the full generated script in a read-only code editor (dark theme, monospace). Users can copy but not edit inline — editing happens after registration via the existing ScriptRegistration view.
- **Step 6 "What's next" links** navigate to other views described in this doc.

---

### 4.2 `EnvironmentDashboard.tsx` — Per-Environment Overview

**Replaces the agent card grid as the primary deployment view.** Instead of "here are your agents," the view becomes "here are your environments and their health."

#### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  Production                                        ● Healthy        │
│  Last deploy: setup-nginx v2 — 2h ago (success)    Agent: active    │
├──────────────┬───────────────────────────────┬───────────────────────┤
│  Servers     │  Recent Deployments           │  Alerts              │
│              │                               │                      │
│  ● prod-01   │  ✓ setup-nginx v2    2h ago   │  ⚠ SSL expires in   │
│    4CPU 8GB  │  ✓ deploy-api v4     1d ago   │    12 days           │
│    CPU: 23%  │  ✗ deploy-api v3     1d ago   │                      │
│    RAM: 61%  │    ↩ rolled back              │  0 critical          │
│    Disk: 42% │  ✓ setup-redis v1    3d ago   │  1 warning           │
│              │                               │  0 info              │
│  ● prod-02   │                               │                      │
│    4CPU 16GB │  [ View all receipts ]        │  [ View all → ]      │
│    CPU: 45%  │                               │                      │
│    RAM: 78%  │                               │                      │
│    Disk: 31% │                               │                      │
│              │                               │                      │
│  [ + Add     │                               │                      │
│    Server ]  │                               │                      │
├──────────────┴───────────────────────────────┴───────────────────────┤
│  Quick Actions                                                       │
│  [ Deploy Script ] [ Run Discovery ] [ View Logs ] [ Check Health ] │
│  [ View Topology ] [ Agent Settings ] [ Monitoring Config ]          │
└──────────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Environments are the primary navigation** — not agents. The top of the Deployment tab shows environment tabs (Dev | QA | Staging | UAT | Prod) that each load an EnvironmentDashboard. This replaces the agent card grid as the default view.

2. **Three-column layout**: Servers (left), Deployments (center), Alerts (right). This puts the most actionable information (deployments and alerts) front and center while keeping server health visible at a glance.

3. **Live resource stats** on server cards. The dashboard polls `resource-usage` for each server every 30s, showing CPU/RAM/disk gauges. Color thresholds: green < 70%, yellow 70-90%, red > 90%.

4. **Quick Actions row** at the bottom provides direct access to all major workflows without navigating away. Each action opens the appropriate wizard or view.

5. **"+ Add Server" button** in the servers column launches `OnboardServerWizard`.

#### Component Interface

```typescript
interface EnvironmentDashboardProps {
  environment: { name: string; display_name: string };
  agents: Agent[];
  onNavigate: (view: string, params?: Record<string, any>) => void;
}

interface ServerStatus {
  resource_id: string;
  name: string;
  display_name: string;
  status: "online" | "degraded" | "offline" | "unknown";
  cpu_percent: number;
  ram_percent: number;
  disk_percent: number;
  last_probe: string;
}
```

#### Data Sources

| Panel | Endpoint | Refresh |
|---|---|---|
| Servers | `GET /deployments/resources?environment={env}` + `POST /broker/deploy` action:"resource-usage" per resource | 30s |
| Deployments | `GET /deployments/receipts?environment={env}&limit=10` | 60s |
| Alerts | `GET /deployments/monitoring/{env}/alerts?limit=10` | 30s |
| Agent status | `GET /deployments/agents/{agent_id}/status` | 30s |
| Health | `GET /deployments/health/{env}` | 30s |

---

### 4.3 `ScriptFromDiscoveryWizard.tsx` — "Generate Scripts from What's Running"

**The unique value prop of Bond's discovery system** — turns discovered infrastructure state into registered, deployable scripts without writing bash.

#### User Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Generate Deployment Scripts                                     │
│                                                                  │
│  Source: prod-web-01 discovery manifest (discovered 10 min ago)  │
│                                                                  │
│  Select components to include:                                   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ☑ 📦 my-api (Node.js Express)                              ││
│  │   Restart: [ PM2 reload ▾ ]  Health: [ HTTP :3000/health ] ││
│  │   Backup before deploy: ☑    Drain connections first: ☑    ││
│  │                                                              ││
│  │ ☑ 📦 my-frontend (Next.js)                                 ││
│  │   Restart: [ systemd restart ▾ ]  Health: [ HTTP :3001/ ]  ││
│  │   Backup before deploy: ☐    Drain connections first: ☐    ││
│  │                                                              ││
│  │ ☑ 🌐 nginx (reverse proxy)                                 ││
│  │   Config test before reload: ☑                              ││
│  │   Zero-downtime reload: ☑                                   ││
│  │                                                              ││
│  │ ☐ 🐘 PostgreSQL (data store — skip by default)             ││
│  │   ⓘ Database scripts are risky — enable only if you want   ││
│  │     automated schema migrations or config changes.          ││
│  │                                                              ││
│  │ ☐ 🔴 Redis (cache — skip by default)                       ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  Script level:                                                   │
│  (•) Level 0 — Exact replication (safest)                        │
│  ( ) Level 1 — With operational improvements                     │
│  ( ) Level 2 — Architecture modernization                        │
│                                                                  │
│                                    [ Preview ] [ Generate All ]  │
└──────────────────────────────────────────────────────────────────┘
```

#### Preview Mode

```
┌──────────────────────────────────────────────────────────────────┐
│  Preview: deploy-my-api.sh                          [ ✕ Close ] │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ #!/usr/bin/env bash                                         ││
│  │ # meta: name=deploy-my-api                                  ││
│  │ # meta: version=v1                                          ││
│  │ # meta: description=Deploy my-api Node.js application       ││
│  │ # meta: timeout=120                                         ││
│  │ # meta: dry_run=true                                        ││
│  │ # meta: depends_on=setup-system                             ││
│  │ # meta: rollback=rollback-my-api.sh                         ││
│  │ set -euo pipefail                                           ││
│  │                                                              ││
│  │ APP_DIR="/opt/my-api"                                       ││
│  │ APP_USER="deploy"                                           ││
│  │ APP_PORT="3000"                                             ││
│  │ HEALTH_URL="http://localhost:${APP_PORT}/health"            ││
│  │                                                              ││
│  │ echo "[deploy] Starting deployment of my-api..."            ││
│  │                                                              ││
│  │ # Backup current state                                      ││
│  │ if [[ -d "$APP_DIR" ]]; then                                ││
│  │   cp -r "$APP_DIR" "${APP_DIR}.backup.$(date +%s)"         ││
│  │ fi                                                          ││
│  │ ...                                                         ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  This script will:                                               │
│  • Back up the current /opt/my-api directory                     │
│  • Pull latest code from github.com/org/my-api (main branch)    │
│  • Install dependencies (npm ci --production)                    │
│  • Reload via PM2 (zero-downtime)                                │
│  • Wait for health check at :3000/health                         │
│  • Rollback if health check fails within 30s                     │
│                                                                  │
│  [ Edit Before Registering ] [ Register & Promote to Dev ]       │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Applications enabled by default, data stores disabled by default.** Database deployment scripts are risky and most users want to manage databases separately. The UI makes this safe default obvious with an info tooltip.

2. **Per-component options** are context-sensitive: Node.js apps get PM2/systemd restart options, nginx gets config-test-before-reload, databases get backup-before-migrate. The options shown depend on what was discovered.

3. **Script level selector** lets the user choose the safety/sophistication tradeoff. Level 0 is "just replicate what's there" — safest for first-time users. Level 1 adds operational best practices. Level 2 proposes architectural changes.

4. **"Register & Promote to Dev"** combines two steps: registering the script in the registry AND promoting it to the first environment. This eliminates the manual promotion step that currently requires navigating to the pipeline view.

5. **Preview pane** shows both the raw script AND a human-readable summary of what the script will do. This is critical for trust — users should understand what they're deploying before clicking "go."

#### Component Interface

```typescript
interface ScriptFromDiscoveryWizardProps {
  manifestName: string;
  environment: string;
  onComplete: (scripts: RegisteredScript[]) => void;
  onCancel: () => void;
}

interface DiscoveredComponent {
  name: string;
  type: "application" | "web-server" | "data-store" | "cache" | "message-queue";
  runtime?: string;
  framework?: string;
  icon: string;
  enabled: boolean; // default: true for apps/web, false for data/cache
  options: ComponentDeployOption[];
}

interface ComponentDeployOption {
  key: string;
  label: string;
  type: "toggle" | "select";
  value: any;
  choices?: Array<{ value: string; label: string }>;
}
```

---

## 5. Tier 2: High Value — Monitoring & Observability

### 5.1 `MonitoringSetupWizard.tsx` — One-Click Monitoring Activation

**Replaces the current MonitoringConfig form** (8 checkboxes and text fields) with a discovery-driven setup.

#### Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  Set Up Monitoring for Production                                │
│                                                                  │
│  We discovered these services on your servers:                   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Service            │ Monitor? │ Checks                  │   │
│  │─────────────────────┼──────────┼─────────────────────────│   │
│  │  nginx 1.24         │  [✓]     │ process, config, access │   │
│  │  my-api (Express)   │  [✓]     │ process, HTTP, logs     │   │
│  │  my-frontend (Next) │  [✓]     │ process, HTTP, logs     │   │
│  │  PostgreSQL 15      │  [✓]     │ connection, replication  │   │
│  │  Redis 7.2          │  [✓]     │ ping, memory            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Check interval: [ Every 60 seconds ▾ ] (recommended for prod)  │
│                                                                  │
│  Alerting:                                                       │
│  ☑ File GitHub issues automatically                              │
│    Repo: [ org/infrastructure   ]                                │
│    Labels: [ monitoring, prod   ]                                │
│                                                                  │
│  [ Enable Monitoring ]                 [ Customize per-service ] │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Service list comes from the discovery manifest.** No manual entry. If a user hasn't run discovery on their servers, the wizard prompts them to do so first (or offers to run it now).

2. **Smart defaults per environment:**
   - Production: 60s interval, auto-file issues, all checks enabled
   - Staging/UAT: 5min interval, auto-file issues, all checks enabled
   - Dev/QA: 5min interval, no auto-filing, basic checks only

3. **"Customize per-service" expander** reveals fine-grained controls: individual check toggles, custom health check URLs, log patterns to watch, alert thresholds. Most users never need this.

4. **One button: "Enable Monitoring."** This:
   - Updates the environment's monitoring config in STDB
   - Registers health check scripts for each service
   - Starts the monitoring cycle immediately
   - Shows confirmation: "Monitoring active. First results in ~60 seconds."

---

### 5.2 `LiveLogViewer.tsx` — Real-Time Log Streaming

**A Grafana-Loki-inspired log viewer** that tails remote logs in the browser.

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Logs — Production                                               │
│                                                                  │
│  Server: [ prod-web-01 ▾ ]   Source: [ All ▾ ]                  │
│  Since: [ Last 15 minutes ▾ ]   Search: [ ___________  🔍 ]    │
│                                                                  │
│  Filter: [ All ] [ Errors ] [ Warnings ]          ● Live tail    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 12:01:23 nginx    INFO  GET /api/users 200 12ms          │   │
│  │ 12:01:24 my-api   INFO  Request handled: GET /users      │   │
│  │ 12:01:25 my-api   WARN  Slow query: SELECT * FROM u...   │   │
│  │ 12:01:26 nginx    INFO  GET /health 200 1ms              │   │
│  │ 12:01:28 my-api   ERROR Connection refused: Redis at     │   │
│  │                         10.0.1.55:6379                    │   │
│  │ 12:01:28 my-api   ERROR Retry 1/3: Redis connection      │   │
│  │ 12:01:29 my-api   ERROR Retry 2/3: Redis connection      │   │
│  │ 12:01:30 my-api   INFO  Redis reconnected                │   │
│  │ 12:01:31 redis    WARN  Client reconnected from          │   │
│  │                         10.0.1.50                         │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Clicked line context ───────────────────────────────────┐   │
│  │  ERROR Connection refused: Redis at 10.0.1.55:6379       │   │
│  │                                                          │   │
│  │  Fingerprint: a3f7b2e1                                   │   │
│  │  Open issues matching this error: 0                      │   │
│  │                                                          │   │
│  │  [ File Issue ] [ Copy Line ] [ Show in Context ]        │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Polling-based tail, not WebSocket.** The backend's `log-check` broker action collects logs via SSH. The viewer polls every 5s with a byte offset (same pattern as `readLog()` in `log-stream.ts`). This avoids maintaining persistent SSH sessions.

2. **Color-coded severity:** `ERROR`/`FATAL` = red, `WARN` = yellow, `INFO` = default, `DEBUG` = muted gray.

3. **Click any error line** to see its fingerprint, whether there's an existing GitHub issue, and a one-click "File Issue" button that pre-populates the issue body using `formatIssueBody()`.

4. **"Live tail" toggle** — when on, the viewer auto-scrolls to newest. When off, the user can scroll freely and search without losing position.

5. **Multi-source aggregation** — logs from journalctl, Docker, nginx, and application stdout are interleaved chronologically with source labels.

#### Component Interface

```typescript
interface LiveLogViewerProps {
  environment: string;
  resources: Array<{ id: string; name: string }>;
  defaultResource?: string;
}

interface LogEntry {
  timestamp: string;
  source: string;
  level: "error" | "warn" | "info" | "debug";
  message: string;
  raw: string;
}
```

#### Polling Strategy

```typescript
// Poll every 5 seconds, pass byte offset to get only new content
const poll = async () => {
  const res = await fetch(
    `${GATEWAY_API}/deployments/logs/${env}/${date}?offset=${offset}`
  );
  const data = await res.json();
  if (data.content) {
    appendLines(parseLogLines(data.content));
    setOffset(data.offset); // next poll starts from here
  }
};
```

---

### 5.3 `InfraMap.tsx` — Full Infrastructure Topology

**Upgrades the current `TopologyGraph.tsx`** (single environment, static layout) into a full infrastructure map across all environments.

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Infrastructure Map                    [ All Envs ▾ ] [ 🔍 ]    │
│                                                                  │
│  ┌─ CDN / Edge ─────────────────────────────────────────────┐   │
│  │                   ┌──────────────┐                       │   │
│  │                   │ CloudFront   │                       │   │
│  │                   │ d111.cf.net  │                       │   │
│  │                   └──────┬───────┘                       │   │
│  └──────────────────────────┼───────────────────────────────┘   │
│                             │ HTTPS                              │
│  ┌─ Application ────────────┼───────────────────────────────┐   │
│  │                   ┌──────┴───────┐                       │   │
│  │                   │ 🌐 nginx     │                       │   │
│  │                   │ prod-web-01  │                       │   │
│  │                   │ ● healthy    │                       │   │
│  │                   └──┬────────┬──┘                       │   │
│  │            :3000 ┌───┘        └───┐ :3001                │   │
│  │           ┌──────┴──────┐ ┌──────┴──────┐               │   │
│  │           │ 📦 my-api   │ │ 📦 frontend │               │   │
│  │           │ Node.js     │ │ Next.js     │               │   │
│  │           │ ● healthy   │ │ ● healthy   │               │   │
│  │           └──────┬──────┘ └─────────────┘               │   │
│  └──────────────────┼──────────────────────────────────────┘   │
│                     │ :5432                                      │
│  ┌─ Data ───────────┼──────────────────────────────────────┐   │
│  │           ┌──────┴──────┐  ┌─────────────┐              │   │
│  │           │ 🐘 Postgres │  │ 🔴 Redis    │              │   │
│  │           │ prod-db-01  │  │ prod-web-01 │              │   │
│  │           │ ● healthy   │  │ ● healthy   │              │   │
│  │           │ 4 databases │  │ 256MB/512MB │              │   │
│  │           └─────────────┘  └─────────────┘              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Legend: ● healthy  ◐ degraded  ○ offline  ⊘ unknown             │
│  [ Refresh ] [ Export SVG ] [ Full screen ]                      │
└──────────────────────────────────────────────────────────────────┘
```

#### Node Interaction

```
┌─ Clicked: prod-web-01 ──────────────────────────┐
│                                                  │
│  🖥 prod-web-01 (linux-server)                  │
│  10.0.1.50:22 · Ubuntu 22.04                    │
│                                                  │
│  CPU: ███████░░░ 67%    Uptime: 47 days         │
│  RAM: ████████░░ 78%    Last deploy: 2h ago     │
│  Disk:████░░░░░░ 42%    Agent: deploy-prod      │
│                                                  │
│  Services: nginx, my-api, my-frontend, redis    │
│  Open alerts: 1 (SSL expiring)                  │
│                                                  │
│  [ View Details ] [ View Logs ] [ Run Discovery ]│
└──────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Tiered layout** (not force-directed). Nodes are placed in horizontal swim lanes: CDN/Edge at top, Application in middle, Data at bottom. This matches how infrastructure actually flows. Force-directed layouts look pretty but are hard to read.

2. **SVG-based, no external dependencies.** The existing `TopologyGraph` approach works; this extends it with richer interaction. No D3, no vis.js — pure React + SVG.

3. **Environment filtering** — dropdown to show one environment or all. When showing all, environments are color-coded swim lanes stacked vertically (Dev at top, Prod at bottom) showing the progression.

4. **Node popups** show live stats + quick actions. The popup data comes from the same `resource-usage` calls used by `EnvironmentDashboard`.

5. **Edge labels** show protocol and port. Edges are colored by health: green if the connection was reachable in the last topology probe, red if unreachable, gray if unknown.

6. **Deployment animation** — when a deployment is in progress (receipt status = "deploying"), the target node pulses with a blue ring. Completed deploys flash green briefly.

#### Data Sources

- **Nodes**: `GET /deployments/resources?environment={env}` + parsed `state_json` for capabilities
- **Edges**: Discovery manifest topology section + `POST /broker/deploy` action:"discover-topology" results
- **Live stats**: `POST /broker/deploy` action:"resource-usage" per node (30s polling)
- **Health**: `GET /deployments/health/{env}`
- **Active deploys**: `GET /deployments/receipts?environment={env}&status=deploying`

---

## 6. Tier 3: Power User — Reduce Toil

### 6.1 `DeploymentTimeline.tsx` — Cross-Environment Deployment History

**An Argo CD-inspired timeline** showing every deployment across all environments, enabling at-a-glance "what deployed when and where."

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Deployment Timeline              [ Last 7 days ▾ ] [ Filter ▾ ]│
│                                                                  │
│  Time  ─────┬────────┬────────┬────────┬────────┬──────── now   │
│             Mar 10   Mar 11   Mar 12   Mar 13   Mar 14          │
│                                                                  │
│  dev     ●──●────●────●──●──────────●────●──────●───             │
│                                                                  │
│  qa         ●────────●────────●──────────●──────────             │
│                                                                  │
│  staging            ●─────────●──────────────────●──             │
│                                                                  │
│  uat                     ●─────────────●────────────             │
│                                                                  │
│  prod                         ●──────────────●──────             │
│                               ↑              ↑                   │
│                          deploy-api v3   deploy-api v4           │
│                          (rolled back)   (success)               │
│                                                                  │
│  ● success  ✗ failed  ↩ rolled back  ◐ in progress               │
│                                                                  │
│  ┌─ Hover: deploy-api v4 → prod (Mar 14, 14:23) ───────────┐   │
│  │  Status: success · Duration: 34s · Agent: deploy-prod    │   │
│  │  Receipt: rec_01HXYZ · SHA: abc123                        │   │
│  │  [ View Receipt ] [ View Diff from v3 ]                  │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Horizontal time axis, vertical environment lanes.** This visually represents the promotion flow: scripts appear first in dev (top), then propagate down to prod (bottom).

2. **Dots, not bars.** Each deployment is a colored dot. Clustered deployments (multiple scripts in one session) can be grouped with a bracket.

3. **Hover reveals details.** No click required — hover shows receipt summary, duration, agent, and quick links.

4. **"View Diff from v3"** opens a diff view comparing two receipt outputs. This lets users understand what changed between deployments.

5. **Filter by script** — dropdown to isolate one script's journey through environments. Shows promotion status at each stage.

#### Component Interface

```typescript
interface DeploymentTimelineProps {
  environments: Array<{ name: string; display_name: string; order: number }>;
  timeRange?: { start: Date; end: Date };
  filterScript?: string;
}

interface TimelineDot {
  receipt_id: string;
  script_id: string;
  script_version: string;
  environment: string;
  timestamp: string;
  status: "success" | "failed" | "rolled_back" | "deploying";
  duration_ms: number;
  agent_id: string;
}
```

---

### 6.2 `AlertRulesEditor.tsx` — Custom Alert Thresholds

**Beyond binary healthy/unhealthy.** Users can define custom conditions that trigger alerts and optionally auto-file GitHub issues.

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Alert Rules — Production                        [ + Add Rule ] │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  CPU Overload                                 ● Active   │   │
│  │  When: CPU > 85% for 5 minutes                           │   │
│  │  Severity: High · Auto-file: Yes                         │   │
│  │  Triggered: 2 times this week                             │   │
│  │                                      [ Edit ] [ Disable ] │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │  Disk Space Critical                          ● Active   │   │
│  │  When: Disk usage > 90%                                  │   │
│  │  Severity: Critical · Auto-file: Yes                     │   │
│  │  Triggered: never                                         │   │
│  │                                      [ Edit ] [ Disable ] │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │  SSL Certificate Expiry                       ● Active   │   │
│  │  When: SSL cert expires within 14 days                   │   │
│  │  Severity: Warning · Auto-file: Yes                      │   │
│  │  Triggered: 1 time (active now)                           │   │
│  │                                      [ Edit ] [ Disable ] │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │  High Error Rate                              ○ Disabled │   │
│  │  When: Error count > 50/minute in logs                   │   │
│  │  Severity: High · Auto-file: No                          │   │
│  │                                      [ Edit ] [ Enable ] │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Rule Editor (Expanded)

```
┌──────────────────────────────────────────────────────────────────┐
│  Edit Rule: CPU Overload                                         │
│                                                                  │
│  Name:       [ CPU Overload                     ]               │
│                                                                  │
│  Condition:                                                      │
│    Metric:   [ CPU usage (%) ▾ ]                                │
│    Operator: [ greater than ▾  ]                                │
│    Value:    [ 85              ]                                 │
│    Duration: [ 5               ] minutes                        │
│                                                                  │
│  Severity:   (•) Critical ( ) High ( ) Medium ( ) Low           │
│                                                                  │
│  Actions:                                                        │
│    ☑ Emit monitoring alert                                       │
│    ☑ Auto-file GitHub issue                                      │
│    ☐ Send notification to channel: [ _________ ]                │
│    ☐ Run custom script: [ _________ ]                           │
│                                                                  │
│  Applies to: (•) All servers in Production                       │
│              ( ) Specific servers: [ _________ ]                │
│                                                                  │
│                               [ Cancel ] [ Save Rule ]          │
└──────────────────────────────────────────────────────────────────┘
```

#### Available Metrics

| Metric | Source | Check Method |
|---|---|---|
| CPU usage (%) | `resource-usage` broker action | `cat /proc/loadavg` |
| Memory usage (%) | `resource-usage` broker action | `free` |
| Disk usage (%) | `resource-usage` broker action | `df /` |
| Error count (per minute) | `log-check` broker action | grep count in logs |
| Health check status | `health-check` broker action | exit code |
| SSL days to expiry | Discovery manifest or openssl probe | `openssl s_client` |
| Process running | `resource-usage` broker action | `pgrep` |
| Port reachable | `discover-topology` broker action | TCP connect test |
| Custom command exit code | `exec` broker action | arbitrary command |

#### Storage

Alert rules stored in SpacetimeDB:

```sql
CREATE TABLE deployment_alert_rules (
  id TEXT PRIMARY KEY,
  environment TEXT NOT NULL,
  name TEXT NOT NULL,
  metric TEXT NOT NULL,
  operator TEXT NOT NULL,      -- gt, lt, eq, neq
  threshold REAL NOT NULL,
  duration_minutes INTEGER DEFAULT 0,
  severity TEXT DEFAULT 'medium',
  enabled BOOLEAN DEFAULT true,
  auto_file_issue BOOLEAN DEFAULT false,
  custom_script_id TEXT,
  applies_to_resources TEXT,   -- JSON array, empty = all
  triggered_count INTEGER DEFAULT 0,
  last_triggered_at BIGINT,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL
);
```

---

### 6.3 `SecretManager.tsx` — Environment Secrets UI

**Currently secrets are managed via CLI/YAML files.** This component brings them into the UI with proper security.

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Secrets — Production                                            │
│                                                                  │
│  🔒 Encrypted at rest (AES-256-GCM)                             │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  Key                    │ Value          │ Source │ Actions  ││
│  │─────────────────────────┼────────────────┼────────┼──────────││
│  │  DATABASE_URL           │ ●●●●●●●●●●●●●● │ manual │ 👁 ✏ 🗑 ││
│  │  REDIS_URL              │ ●●●●●●●●●●●●●● │ manual │ 👁 ✏ 🗑 ││
│  │  API_SECRET_KEY         │ ●●●●●●●●●●●●●● │ manual │ 👁 ✏ 🗑 ││
│  │  NODE_ENV               │ production     │ manual │    ✏ 🗑 ││
│  │  LOG_LEVEL              │ info           │ manual │    ✏ 🗑 ││
│  │  PORT                   │ 3000           │ discov │    ✏ 🗑 ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  [ + Add Secret ]  [ Import from .env ]  [ Rotate Encryption ]  │
│                                                                  │
│  ┌─ Discovered but not managed ─────────────────────────────┐   │
│  │  These variables were found on prod-web-01 but are not   │   │
│  │  in Bond's secret store:                                 │   │
│  │                                                          │   │
│  │  STRIPE_API_KEY  (my-api/.env)         [ Import ]        │   │
│  │  SENDGRID_KEY    (my-api/.env)         [ Import ]        │   │
│  │  SENTRY_DSN      (my-frontend/.env)    [ Import ]        │   │
│  │                                                          │   │
│  │  [ Import All ]                                          │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Secret values are masked by default.** The 👁 (reveal) button shows the value temporarily (auto-hides after 10 seconds). Reveal is logged in the audit trail.

2. **"Source" column** distinguishes between manually-created secrets and those discovered from .env files on servers. Discovered secrets can be imported with one click.

3. **"Import from .env"** reads the discovery manifest's env_files section and offers to import discovered variables (values were masked during discovery, so the user must enter or confirm them).

4. **"Rotate Encryption"** calls the existing `rotateSecretsEncryption()` function from `secrets.ts`. Shows a confirmation dialog first.

5. **Non-secret values** (NODE_ENV, PORT, LOG_LEVEL) are shown in plain text since they're not sensitive. The system auto-detects this based on variable name patterns (same rules as the discovery script secret masking).

#### API Calls

| Action | Endpoint |
|---|---|
| List secrets | `GET /deployments/secrets/{env}` (new endpoint — returns keys + masked values) |
| Reveal value | `POST /deployments/secrets/{env}/{key}/reveal` (new endpoint — audit-logged) |
| Set secret | `PUT /deployments/secrets/{env}/{key}` (new endpoint) |
| Delete secret | `DELETE /deployments/secrets/{env}/{key}` (new endpoint) |
| Import from .env | `POST /deployments/secrets/{env}/import` (new endpoint — reads manifest) |
| Rotate encryption | `POST /deployments/secrets/{env}/encrypt` (existing endpoint) |

---

### 6.4 `CompareEnvironments.tsx` — Environment Diff View

**Shows what's different between two environments** — the critical question before promoting a deployment.

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Compare Environments                                            │
│                                                                  │
│  [ Staging ▾ ]  ⟷  [ Production ▾ ]                             │
│                                                                  │
│  ┌─ Software Versions ──────────────────────────────────────┐   │
│  │                      Staging         Production          │   │
│  │  nginx              1.24.0          1.24.0      ✓ same   │   │
│  │  Node.js            20.11.1         20.10.0     ⚠ diff   │   │
│  │  PostgreSQL         15.4            15.4        ✓ same   │   │
│  │  Redis              7.2.3           7.2.1       ⚠ diff   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Script Versions ────────────────────────────────────────┐   │
│  │                      Staging         Production          │   │
│  │  deploy-api          v4 (success)   v3 (success)  ⚠      │   │
│  │  setup-nginx         v2 (success)   v2 (success)  ✓      │   │
│  │  setup-redis         v1 (success)   v1 (success)  ✓      │   │
│  │  deploy-frontend     v5 (success)   v4 (success)  ⚠      │   │
│  │                                                          │   │
│  │  2 scripts are ahead in Staging. [ Promote All to Prod ] │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Configuration ──────────────────────────────────────────┐   │
│  │                      Staging         Production          │   │
│  │  Environment Vars:   14 keys        12 keys     ⚠ diff   │   │
│  │    Missing in prod:  NEW_FEATURE_FLAG, CACHE_TTL         │   │
│  │    Different values: LOG_LEVEL (debug vs info)           │   │
│  │                                                          │   │
│  │  Server count:       1              2            ⚠ diff   │   │
│  │  Health interval:    300s           60s          ⚠ diff   │   │
│  │  Auto-file issues:   Yes            Yes          ✓ same   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ Server Resources ───────────────────────────────────────┐   │
│  │                      Staging         Production          │   │
│  │  Total CPUs:         2              8            ⚠        │   │
│  │  Total RAM:          4GB            24GB         ⚠        │   │
│  │  Total Disk:         50GB           250GB        ⚠        │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

#### Key Design Decisions

1. **Side-by-side with diff markers.** Every row shows both environments and a ✓/⚠ indicator. Differences are highlighted — users immediately see what's out of sync.

2. **"Promote All to Prod" button** when scripts are ahead in the source environment. This is the primary action: the diff view exists to answer "is it safe to promote?" and then let you do it.

3. **Variable comparison** shows key-level diff without revealing secret values. "Missing in prod" and "Different values" sections tell the user exactly what needs attention before promoting.

4. **Server resource comparison** shows infrastructure parity (or lack of it). If staging has 2 CPUs and prod has 8, the user knows performance behavior may differ.

#### Data Sources

- **Software versions**: Discovery manifests for each environment
- **Script versions**: `GET /deployments/promotions?environment={env}`
- **Configuration**: Environment config from STDB + secret key lists
- **Server resources**: Resource capabilities/state from STDB

---

## 7. Navigation Restructure

### 7.1 New Deployment Tab Layout

The Deployment tab currently shows:
```
Agent Card Grid → Pipeline Section → Quick Deploy / Register Script buttons
```

The new layout:

```
┌──────────────────────────────────────────────────────────────────┐
│  Deployment                                                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Environment tabs:                                          │ │
│  │ [ Dev ] [ QA ] [ Staging ] [ UAT ] [ Prod ] │ [ Map ] [ ⏱ ]│ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Selected tab loads:                                             │
│  • Dev/QA/Staging/UAT/Prod → EnvironmentDashboard               │
│  • Map → InfraMap (full topology)                                │
│  • ⏱ → DeploymentTimeline (cross-env)                           │
│                                                                  │
│  Within each EnvironmentDashboard:                               │
│  • Quick Actions open: OnboardServerWizard, LiveLogViewer,       │
│    ScriptFromDiscoveryWizard, MonitoringSetupWizard,             │
│    AlertRulesEditor, SecretManager, CompareEnvironments           │
│                                                                  │
│  Global actions (always visible):                                │
│  • [ + Add Server ] → OnboardServerWizard                        │
│  • [ Quick Deploy ] → QuickDeployForm (existing)                 │
│  • [ Register Script ] → ScriptRegistration (existing)           │
│  • [ Agent Settings ] → AgentCardGrid (moved from default view)  │
└──────────────────────────────────────────────────────────────────┘
```

### 7.2 View Hierarchy

```
DeploymentTab (top-level)
├── EnvironmentDashboard (per-env, default view)
│   ├── Server list with live stats
│   ├── Recent deployments
│   ├── Active alerts
│   └── Quick Actions
│       ├── OnboardServerWizard (add & discover)
│       ├── ScriptFromDiscoveryWizard (generate scripts)
│       ├── MonitoringSetupWizard (enable monitoring)
│       ├── LiveLogViewer (tail logs)
│       ├── AlertRulesEditor (custom thresholds)
│       ├── SecretManager (manage env vars)
│       ├── CompareEnvironments (diff with another env)
│       ├── ResourceDetail (existing — drill into resource)
│       └── ReceiptViewer (existing — drill into receipt)
├── InfraMap (cross-env topology)
├── DeploymentTimeline (cross-env history)
├── AgentCardGrid (existing — agent management, moved to secondary)
├── PipelineSection (existing — promotion pipeline)
├── QuickDeployForm (existing)
└── ScriptRegistration (existing)
```

### 7.3 Entry Points Inventory

Every new component should be reachable in ≤ 2 clicks from the Deployment tab:

| Component | Click 1 | Click 2 |
|---|---|---|
| OnboardServerWizard | "+ Add Server" button (always visible) | — |
| EnvironmentDashboard | Environment tab | — |
| ScriptFromDiscoveryWizard | Environment tab | Quick Actions → "Generate Scripts" |
| MonitoringSetupWizard | Environment tab | Quick Actions → "Setup Monitoring" |
| LiveLogViewer | Environment tab | Quick Actions → "View Logs" |
| AlertRulesEditor | Environment tab | Quick Actions → "Alert Rules" |
| SecretManager | Environment tab | Quick Actions → "Secrets" |
| CompareEnvironments | Environment tab | Quick Actions → "Compare" |
| InfraMap | "Map" tab | — |
| DeploymentTimeline | "⏱" tab | — |

---

## 8. Backend API Additions

Several new endpoints are needed to support the UI components:

### 8.1 Secrets Management API

```
GET    /deployments/secrets/:env                    → list keys (values masked)
GET    /deployments/secrets/:env/:key/reveal        → reveal value (audit-logged)
PUT    /deployments/secrets/:env/:key               → set/update secret
DELETE /deployments/secrets/:env/:key               → delete secret
POST   /deployments/secrets/:env/import             → import from discovery manifest
```

### 8.2 Alert Rules API

```
GET    /deployments/alert-rules/:env                → list rules
POST   /deployments/alert-rules/:env                → create rule
PUT    /deployments/alert-rules/:env/:id            → update rule
DELETE /deployments/alert-rules/:env/:id            → delete rule
PUT    /deployments/alert-rules/:env/:id/enable     → enable rule
PUT    /deployments/alert-rules/:env/:id/disable    → disable rule
```

### 8.3 Environment Comparison API

```
GET    /deployments/compare/:envA/:envB             → full diff
GET    /deployments/compare/:envA/:envB/scripts     → script version diff
GET    /deployments/compare/:envA/:envB/secrets     → key-level secret diff
```

### 8.4 Live Log Tailing API

```
GET    /deployments/logs/:env/live?resource=:id&since=:min&offset=:byte
       → returns new log content since byte offset
POST   /deployments/logs/:env/collect?resource=:id&since=:min
       → triggers fresh log collection from remote (uses log-check broker action)
```

---

## 9. Implementation Phases

### Phase 1: Core Guided Flows (Tier 1)
**Target: Reduce onboarding from 8 steps to 1 wizard.**

| Component | Priority | Complexity | Dependencies |
|---|---|---|---|
| OnboardServerWizard | P0 | High | ResourceForm (existing), discovery.ts, proposal-generator.ts |
| EnvironmentDashboard | P0 | High | resource-usage broker action, receipts API, monitoring alerts |
| ScriptFromDiscoveryWizard | P0 | Medium | manifest.ts, proposal-generator.ts, scripts API |
| Navigation restructure | P0 | Medium | EnvironmentDashboard must exist first |

### Phase 2: Zero-Config Monitoring (Tier 2)
**Target: Monitoring "just works" for discovered services.**

| Component | Priority | Complexity | Dependencies |
|---|---|---|---|
| MonitoringSetupWizard | P1 | Medium | Discovery manifest, monitoring.ts |
| LiveLogViewer | P1 | Medium | log-collector.ts, log-stream.ts |
| InfraMap | P1 | High | TopologyGraph (existing), discovery manifests, resource-usage |

### Phase 3: Power User Tools (Tier 3)
**Target: Full operational control from the UI.**

| Component | Priority | Complexity | Dependencies |
|---|---|---|---|
| DeploymentTimeline | P2 | Medium | receipts API |
| AlertRulesEditor | P2 | Medium | New STDB table, monitoring.ts integration |
| SecretManager | P2 | Medium | New secrets API endpoints, secrets.ts |
| CompareEnvironments | P2 | Medium | New comparison API, manifests, promotions |

### Phase 4: Polish
- Responsive layout for all new components
- Keyboard navigation (Tab/Enter through wizards)
- Loading skeletons for async panels
- Error boundaries per component
- Unit tests for all new components

---

## 10. Success Criteria

1. **Onboarding time:** A user with a running server can go from "zero Bond deployment agents" to "server managed, monitoring active, first script deployed" in under 5 minutes. (Today: 30+ minutes.)

2. **Click depth:** Every major action is reachable in ≤ 2 clicks from the Deployment tab.

3. **Zero-config monitoring:** After running `OnboardServerWizard`, monitoring is active with smart defaults. No manual configuration required.

4. **Environment visibility:** One glance at `EnvironmentDashboard` tells the user: are my servers healthy? What deployed recently? Are there open issues?

5. **Competitive parity:** The guided wizard flow matches or exceeds Coolify/Railway's onboarding experience while providing deeper discovery than any comparable open-source tool.

---

## 11. Non-Goals

- **Real-time WebSocket streaming** — Polling is sufficient for MVP. WebSocket can be added in a future phase.
- **Multi-tenant access control** — This doc assumes a single-user or small-team deployment. RBAC is out of scope.
- **Mobile-responsive design** — The deployment UI is a desktop tool. Responsive layout is nice-to-have, not required.
- **Custom dashboard builder** — Unlike Grafana, we don't need user-defined dashboards. The layout is opinionated.

---

## 12. Files to Create / Modify

### New Frontend Components (10)
```
frontend/src/app/settings/deployment/
├── OnboardServerWizard.tsx          (§4.1)
├── EnvironmentDashboard.tsx         (§4.2)
├── ScriptFromDiscoveryWizard.tsx    (§4.3)
├── MonitoringSetupWizard.tsx        (§5.1)
├── LiveLogViewer.tsx                (§5.2)
├── InfraMap.tsx                     (§5.3)
├── DeploymentTimeline.tsx           (§6.1)
├── AlertRulesEditor.tsx             (§6.2)
├── SecretManager.tsx                (§6.3)
└── CompareEnvironments.tsx          (§6.4)
```

### Modified Frontend Components
```
frontend/src/app/settings/deployment/
├── DeploymentTab.tsx                (§7 — navigation restructure)
└── TopologyGraph.tsx                (→ replaced by InfraMap)
```

### New Backend Files
```
gateway/src/deployments/
├── secrets-router.ts                (§8.1 — secrets management API)
├── alert-rules.ts                   (§8.2 — alert rule CRUD + STDB)
├── alert-rules-router.ts            (§8.2 — Express routes)
├── compare.ts                       (§8.3 — environment diff logic)
└── compare-router.ts                (§8.3 — Express routes)
```

### Modified Backend Files
```
gateway/src/deployments/
├── router.ts                        (mount new sub-routers)
├── stdb.ts                          (deployment_alert_rules table)
└── log-stream.ts                    (add live-tail offset support)
```

### New SpacetimeDB Table
```
deployment_alert_rules               (§6.2)
```
