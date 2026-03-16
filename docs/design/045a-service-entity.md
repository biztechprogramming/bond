# Design Doc 045a: The Component Entity

**Status:** Draft  
**Date:** 2026-03-16  
**Extends:** 045 (Guided Workflows)

---

## 1. The Problem

Bond's deployment system has five independent data stores that don't know about each other:

```
SpacetimeDB                          Filesystem
─────────────                        ──────────
deployment_resources  (servers)      scripts/registry/     (deploy actions)
deployment_promotions (per-env)      receipts/{env}/       (deploy history)
deployment_environments              discovery/manifests/  (snapshots)
monitoring_alerts                    secrets/{env}/        (env vars)
```

There's no entity for "the thing I'm deploying." Scripts are named `deploy-my-api` by convention but aren't formally linked to anything. Discovery finds services but they're just JSON blobs inside manifest files. Monitoring alerts reference a "component" string but it's not a foreign key. Secrets are per-environment but not per-service.

**The result:** Every UI component has to guess relationships by string matching names, and users see disconnected lists instead of a coherent picture of what they're managing.

---

## 2. What is a Component?

A **component** is any discrete thing Bond manages. It's the anchor entity that connects:

- **Where it runs** → resource(s), per environment
- **How it's deployed** → script(s)
- **What it needs** → secrets/env vars, per environment
- **How it's monitored** → health checks, alert rules
- **What was discovered** → manifest reference
- **Its current state** → which version is deployed where

### Examples

| Component | Type | In Prod | In Dev |
|---|---|---|---|
| my-api | application | prod-web-01:3000 | dev-box:3000 |
| my-frontend | application | prod-web-01:3001 | dev-box:3001 |
| nginx | web-server | prod-web-01 | dev-box |
| PostgreSQL | data-store | prod-db-01 | dev-box |
| Redis | cache | prod-web-01 | dev-box |
| my-platform | system | (groups the above) | (groups the above) |

---

## 3. Design Decisions

### 3.1 Components are global

A component is a logical entity — "my-api" is the same thing whether it's deployed to dev, staging, or prod. What differs per-environment is the *deployment instance*: which server it runs on, which secrets it uses, which version is deployed.

```
deployment_components (global)           Link tables (per-environment)
──────────────────────────────           ────────────────────────────
my-api                               →   prod: runs on prod-web-01:3000, 5 secrets, v4
  type: application                       staging: runs on staging-01:3000, 5 secrets, v4
  runtime: node                           dev: runs on dev-box:3000, 3 secrets, v5
  framework: express
```

**Why global:**
- A component IS the same logical thing across environments — same codebase, same purpose
- Comparison is natural: "my-api is at v4 in staging, v3 in prod" is one query
- Promotion follows the component: "promote my-api from staging to prod"
- No duplication of identity (name, type, runtime, framework, icon, repo URL)
- Per-environment differences are captured by the link tables, where they belong

**What about dev-only components?** A component can exist globally even if it's only deployed to one environment. It just won't have resource/secret links in other environments. The EnvironmentDashboard shows components that have at least one resource link in that environment — a dev-only experiment simply doesn't appear in the prod view.

### 3.2 Components form a flexible tree

Components can have a parent. The hierarchy is **organizational, not deployment-coupled** — each component is independently deployable regardless of where it sits in the tree.

This supports both directions:

**Top-down: App gets complicated, split into sub-components**
```
Before:                        After:
my-app                         my-app (system)
  deploys everything             ├── my-api (application)
                                 ├── my-frontend (application)
                                 └── my-worker (application)
```
You start with one component "my-app" that deploys everything. As it grows, you create child components for the API, frontend, and worker. The parent becomes a "system" type — an organizational container. Each child gets its own deploy script. The parent can keep a deploy script too (for "deploy everything in this system" orchestration) and it *can* have secrets (shared across children).

**Bottom-up: Standalone component joins a bigger system**
```
Before:                        After:
my-api (standalone)            my-platform (system)
my-frontend (standalone)         ├── my-api (reparented)
                                 ├── my-frontend (reparented)
                                 └── shared-db (new)
```
You've been deploying my-api and my-frontend independently. Now you realize they're part of a bigger system. Create "my-platform" as a parent, set `parent_id` on the existing components. Nothing about their deployment changes — they still deploy independently. The parent just provides organizational context.

**Reparenting is always safe** because the hierarchy doesn't affect how things deploy. Moving a component from one parent to another (or to no parent) doesn't change its resources, scripts, secrets, or monitoring.

### 3.3 The tree is global, visibility is per-environment

The component tree itself is global — "my-platform contains my-api, my-frontend, and shared-db" is a fact independent of environment. But what you *see* in each environment's dashboard depends on which components have resource links there:

```
Component tree (global):         Visible in prod:      Visible in dev:
my-platform                      my-platform           my-platform
  ├── my-api                       ├── my-api            ├── my-api
  ├── my-frontend                  ├── my-frontend       ├── my-frontend
  ├── my-worker                    └── shared-db         ├── my-worker
  └── shared-db                                          ├── shared-db
                                                         └── experimental
experimental (top-level)
```

"my-worker" exists in the tree but has no prod resource links, so it doesn't appear in the prod dashboard. "experimental" is top-level and only deployed to dev. The tree structure is the same everywhere — the filtering is just "which components are actually deployed here?"

A system-type parent is visible in an environment if *any* of its children are visible there.

---

## 4. Data Model

### 4.1 `deployment_components` (SpacetimeDB)

```sql
CREATE TABLE deployment_components (
  id              TEXT PRIMARY KEY,         -- ULID
  name            TEXT NOT NULL UNIQUE,     -- e.g. "my-api" (globally unique)
  display_name    TEXT NOT NULL,            -- e.g. "My API"
  component_type  TEXT NOT NULL,            -- application | web-server | data-store | cache |
                                            -- message-queue | infrastructure | system
  parent_id       TEXT,                     -- FK → deployment_components (null = top-level)
  runtime         TEXT,                     -- e.g. "node", "python", "nginx", "postgresql"
  framework       TEXT,                     -- e.g. "express", "next.js", "django"
  repository_url  TEXT,                     -- e.g. "github.com/org/my-api"
  icon            TEXT,                     -- emoji, e.g. "📦"
  description     TEXT,
  is_active       BOOLEAN DEFAULT true,
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL,
  discovered_from TEXT                      -- manifest name if auto-discovered
);
```

**`component_type: "system"`** is a component that exists purely to group other components. It has no runtime, no port, no health check — just children. But it *can* have a deploy script (for "deploy everything in this system" orchestration) and it *can* have secrets (shared across children).

### 4.2 `deployment_component_resources` (SpacetimeDB)

Links a component to the resource(s) it runs on, per environment.

```sql
CREATE TABLE deployment_component_resources (
  id              TEXT PRIMARY KEY,
  component_id    TEXT NOT NULL,            -- FK → deployment_components
  resource_id     TEXT NOT NULL,            -- FK → deployment_resources
  environment     TEXT NOT NULL,            -- FK → deployment_environments
  port            INTEGER,                  -- primary port this component listens on
  process_name    TEXT,                     -- e.g. "node", "nginx", "redis-server"
  health_check    TEXT,                     -- e.g. "http://localhost:3000/health"
  created_at      BIGINT NOT NULL,
  UNIQUE(component_id, resource_id, environment)
);
```

This is the key per-environment link. "my-api runs on prod-web-01:3000 in production" and "my-api runs on dev-box:3000 in dev" are two rows in this table, both pointing to the same component.

### 4.3 `deployment_component_scripts` (SpacetimeDB)

Links scripts to the component they manage. Scripts are global (not per-env) — the same `deploy-my-api` script is promoted through environments.

```sql
CREATE TABLE deployment_component_scripts (
  id              TEXT PRIMARY KEY,
  component_id    TEXT NOT NULL,            -- FK → deployment_components
  script_id       TEXT NOT NULL,            -- matches script_id in filesystem registry
  role            TEXT DEFAULT 'deploy',    -- deploy | setup | rollback | migrate | backup
  created_at      BIGINT NOT NULL,
  UNIQUE(component_id, script_id)
);
```

### 4.4 `deployment_component_secrets` (SpacetimeDB)

Tracks which secrets belong to which component, per environment. Values stay in the encrypted filesystem store.

```sql
CREATE TABLE deployment_component_secrets (
  id              TEXT PRIMARY KEY,
  component_id    TEXT NOT NULL,            -- FK → deployment_components
  secret_key      TEXT NOT NULL,            -- e.g. "DATABASE_URL"
  environment     TEXT NOT NULL,            -- FK → deployment_environments
  is_sensitive    BOOLEAN DEFAULT true,     -- false for PORT, NODE_ENV etc.
  created_at      BIGINT NOT NULL,
  UNIQUE(component_id, secret_key, environment)
);
```

---

## 5. How Components Get Created

### 5.1 From Discovery (automatic)

When the OnboardServerWizard runs discovery on a server, each discovered application becomes a component (or links to an existing one if the name matches):

```
Discovery on prod-web-01 finds:        Result:
───────────────────────────            ──────────────────────────────
Node.js app "my-api"               →   Component "my-api" created (or found)
                                        + resource link: prod-web-01, port 3000, env "prod"

nginx reverse proxy                →   Component "nginx" created (or found)
                                        + resource link: prod-web-01, env "prod"
```

The wizard's Review step lets users:
- Rename components before creation
- Set parent relationships (group under a system)
- Skip components they don't want Bond to manage
- Link to an existing component (if re-discovering, or if the component already exists from another environment)

### 5.2 Manual Creation

Users create components from the UI for:
- Services not yet deployed (planning a new microservice)
- Services on platforms Bond can't SSH into
- System-type components (organizational containers)

### 5.3 From Script Registration

When registering `deploy-my-api`, the UI suggests linking it to the "my-api" component or creating one.

---

## 6. How This Changes the UI

### 6.1 EnvironmentDashboard — Component-Centric View

The primary view becomes **a tree of components deployed to this environment**:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Production                                        ● Healthy        │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ▼ 🏗 my-platform                                                    │
│                                                                      │
│    📦 my-api                                            ● healthy   │
│    Node.js / Express · prod-web-01:3000                             │
│    deploy-my-api v4 · 2h ago ✓ · 5 secrets                         │
│                                                                      │
│    📦 my-frontend                                       ● healthy   │
│    Next.js · prod-web-01:3001                                       │
│    deploy-frontend v5 · 1d ago ✓ · 3 secrets                       │
│                                                                      │
│    🐘 PostgreSQL                                        ● healthy   │
│    PostgreSQL 15 · prod-db-01                                        │
│    (monitoring only)                                                 │
│                                                                      │
│  🌐 nginx                                               ● healthy   │
│  nginx 1.24 · prod-web-01 · ⚠ SSL cert expires in 12 days          │
│                                                                      │
│  🔴 Redis                                               ● healthy   │
│  Redis 7.2 · prod-web-01 · 256MB/512MB                             │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│  Infrastructure: 2 servers  │  0 critical  │  1 warning             │
│  [ + Component ] [ + Server ] [ Deploy ] [ View Topology ]         │
└──────────────────────────────────────────────────────────────────────┘
```

A component appears here if it has at least one `component_resources` link with `environment = "prod"`. System-type parents appear if any of their children are visible.

### 6.2 Component Detail View (click a component)

Full cross-environment view for one component:

```
┌──────────────────────────────────────────────────────────────────┐
│  📦 my-api                                    [ Edit ] [ ⋯ ]    │
│  Node.js 20 / Express · github.com/org/my-api                   │
│  Part of: my-platform                                            │
├──────────┬───────────────────────────────────────────────────────┤
│          │                                                       │
│  Envs    │  Deployment History                                   │
│          │                                                       │
│  dev  ●  │  v4  ✓ prod    Mar 14   34s   deploy-prod            │
│  qa   ●  │  v4  ✓ staging Mar 13   28s   deploy-staging         │
│  stg  ●  │  v3  ✗ prod    Mar 12   12s   deploy-prod (→ v2)     │
│  prod ●  │  v3  ✓ staging Mar 11   31s   deploy-staging         │
│          │                                                       │
├──────────┤  Secrets                                              │
│ Scripts  │                                                       │
│          │  prod (5 keys)  staging (5 keys)  dev (3 keys)       │
│ deploy   │  DATABASE_URL    ●●●●●●                               │
│ rollback │  API_SECRET_KEY  ●●●●●●                               │
│ migrate  │  REDIS_URL       ●●●●●●                               │
│          │  NODE_ENV        production                            │
│          │  PORT            3000                                  │
├──────────┤                                                       │
│ Runs On  │  Monitoring                                           │
│          │  Health: ✓ :3000/health (1.2s)                        │
│ prod-01  │  CPU rule: < 85% ✓                                    │
│ dev-box  │  Error rate: < 50/min ✓                               │
└──────────┴───────────────────────────────────────────────────────┘
```

The left sidebar shows all environments the component is deployed to, all linked scripts, and all resources. The main area shows deployment history across all environments, secrets grouped by environment, and monitoring status.

### 6.3 Other Component Updates

| UI Component | Change |
|---|---|
| OnboardServerWizard | Step 3 → "Review & Name Components"; creates/links components |
| ScriptFromDiscoveryWizard | Scoped to a selected component |
| SecretManager | Grouped by component |
| AlertRulesEditor | Rules linked to components |
| CompareEnvironments | Shows component version diffs across envs |
| InfraMap | Nodes are components (resources shown as hosts underneath) |
| DeploymentTimeline | Filter by component |

---

## 7. API

### 7.1 Component CRUD

```
GET    /deployments/components                                → list all
GET    /deployments/components?environment=prod                → filtered: only those deployed to prod
GET    /deployments/components?tree=true                       → nested tree structure
GET    /deployments/components?environment=prod&tree=true      → tree filtered to env visibility
GET    /deployments/components/:id                             → single component + all links
POST   /deployments/components                                 → create
PUT    /deployments/components/:id                             → update (including reparent via parent_id)
DELETE /deployments/components/:id                             → deactivate
```

### 7.2 Component Links

```
GET    /deployments/components/:id/resources               → all resource links (all envs)
POST   /deployments/components/:id/resources               → link resource (body: resource_id, environment, port, ...)
DELETE /deployments/components/:id/resources/:linkId        → unlink

GET    /deployments/components/:id/scripts                 → all script links
POST   /deployments/components/:id/scripts                 → link script (body: script_id, role)
DELETE /deployments/components/:id/scripts/:linkId          → unlink

GET    /deployments/components/:id/secrets                 → all secret links (all envs)
GET    /deployments/components/:id/secrets?environment=prod → secret links for one env
POST   /deployments/components/:id/secrets                 → link secret (body: secret_key, environment)
DELETE /deployments/components/:id/secrets/:linkId          → unlink
```

### 7.3 Component Status (aggregated)

```
GET    /deployments/components/:id/status?environment=prod
```

Returns:
```json
{
  "component_id": "01HXYZ...",
  "environment": "prod",
  "health": "healthy",
  "last_deploy": { "script": "deploy-my-api", "version": "v4", "status": "success", "when": "2h ago" },
  "resources": [{ "name": "prod-web-01", "status": "online", "port": 3000, "cpu": 23, "ram": 61 }],
  "secrets_count": 5,
  "active_alerts": 0,
  "children": [
    { "name": "my-api", "health": "healthy" },
    { "name": "my-frontend", "health": "healthy" }
  ]
}
```

For system-type components, `children` aggregates child status. The system is "healthy" only if all children are healthy.

---

## 8. Migration Path

Fully additive — nothing breaks:

1. **Add 4 tables** to SpacetimeDB
2. **Add component CRUD + link endpoints** to gateway
3. **Auto-create components from discovery** — when a manifest is written, create/link component entities
4. **Update EnvironmentDashboard** to show component tree instead of raw server/receipt/alert columns
5. **Add ComponentDetail view**
6. **Update OnboardServerWizard** Step 3 for component naming
7. **Backfill** — for existing setups, infer components from manifest application names and script naming conventions

---

## 9. Files to Create / Modify

### New Backend
```
gateway/src/deployments/
├── components.ts              — CRUD logic + STDB queries for all 4 tables
└── components-router.ts       — Express routes
```

### Modified Backend
```
gateway/src/deployments/
├── router.ts                  — mount /components router
└── discovery.ts               — auto-create components on discovery
```

### New Frontend
```
frontend/src/app/settings/deployment/
└── ComponentDetail.tsx         — full component detail view
```

### Modified Frontend
```
frontend/src/app/settings/deployment/
├── DeploymentTab.tsx           — add component-detail view mode
├── EnvironmentDashboard.tsx    — switch to component-centric tree view
├── OnboardServerWizard.tsx     — Step 3 component naming/linking
├── SecretManager.tsx           — group by component
├── AlertRulesEditor.tsx        — link rules to components
├── CompareEnvironments.tsx     — component version comparison
├── InfraMap.tsx                — component nodes
└── DeploymentTimeline.tsx      — filter by component
```
