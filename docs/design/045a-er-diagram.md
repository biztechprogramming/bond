# Design Doc 045a: ER Diagram & Change Plan

**Companion to:** 045a-service-entity.md  
**Date:** 2026-03-16

---

## 1. Current State — Entity Relationship Diagram

Everything Bond's deployment system tracks today. SpacetimeDB tables are marked `[STDB]`, filesystem stores are marked `[FS]`.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          CURRENT STATE                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  [STDB] deployment_environments                                                 │
│  ┌──────────────────────────────────────┐                                       │
│  │ name (PK)           TEXT             │                                       │
│  │ display_name         TEXT             │                                       │
│  │ order                INTEGER          │                                       │
│  │ is_active            BOOLEAN          │                                       │
│  │ max_script_timeout   INTEGER          │                                       │
│  │ health_check_interval INTEGER         │                                       │
│  │ window_days          TEXT (JSON)      │                                       │
│  │ window_start         TEXT             │                                       │
│  │ window_end           TEXT             │                                       │
│  │ window_timezone      TEXT             │                                       │
│  │ required_approvals   INTEGER          │                                       │
│  │ created_at           BIGINT           │                                       │
│  │ updated_at           BIGINT           │                                       │
│  └──────┬───────────────────────────────┘                                       │
│         │                                                                       │
│         │ 1:N                                                                   │
│         ▼                                                                       │
│  [STDB] deployment_environment_approvers                                        │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ environment_name (FK) TEXT            │──→ deployment_environments.name       │
│  │ user_id              TEXT             │                                       │
│  │ added_at             BIGINT           │                                       │
│  │ added_by             TEXT             │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [STDB] deployment_environment_history                                          │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ environment_name (FK) TEXT            │──→ deployment_environments.name       │
│  │ action               TEXT             │                                       │
│  │ changed_by           TEXT             │                                       │
│  │ changed_at           BIGINT           │                                       │
│  │ before_snapshot      TEXT             │                                       │
│  │ after_snapshot       TEXT             │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│                                                                                 │
│  [STDB] deployment_resources                                                    │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ name                 TEXT             │                                       │
│  │ display_name         TEXT             │                                       │
│  │ resource_type        TEXT             │   (local, linux-server, kubernetes,   │
│  │ environment (FK)     TEXT             │──→  docker-host, aws-ecs, custom)     │
│  │ connection_json      TEXT             │   deployment_environments.name        │
│  │ capabilities_json    TEXT             │                                       │
│  │ state_json           TEXT             │                                       │
│  │ tags_json            TEXT             │                                       │
│  │ recommendations_json TEXT             │                                       │
│  │ is_active            BOOLEAN          │                                       │
│  │ created_at           BIGINT           │                                       │
│  │ updated_at           BIGINT           │                                       │
│  │ last_probed_at       BIGINT           │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│                                                                                 │
│  [STDB] deployment_promotions                                                   │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ script_id            TEXT             │──→ [FS] scripts/registry/{id}/        │
│  │ script_version       TEXT             │                                       │
│  │ script_sha256        TEXT             │                                       │
│  │ environment_name (FK) TEXT            │──→ deployment_environments.name       │
│  │ status               TEXT             │   (promoted, approved, deploying,     │
│  │ initiated_by         TEXT             │    deployed, failed, rolled_back)     │
│  │ initiated_at         BIGINT           │                                       │
│  │ promoted_at          BIGINT           │                                       │
│  │ deployed_at          BIGINT           │                                       │
│  │ receipt_id           TEXT             │──→ [FS] receipts/{env}/{id}.json      │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [STDB] deployment_approvals                                                    │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ promotion_id (FK)    TEXT             │──→ deployment_promotions.id           │
│  │ script_id            TEXT             │                                       │
│  │ script_version       TEXT             │                                       │
│  │ environment_name     TEXT             │                                       │
│  │ user_id              TEXT             │                                       │
│  │ approved_at          BIGINT           │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│                                                                                 │
│  [STDB] monitoring_alerts                                                       │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ environment (FK)     TEXT             │──→ deployment_environments.name       │
│  │ category             TEXT             │                                       │
│  │ component            TEXT             │   ← string, not a FK (problem!)      │
│  │ fingerprint_hash     TEXT             │                                       │
│  │ severity             TEXT             │                                       │
│  │ message              TEXT             │                                       │
│  │ detected_at          BIGINT           │                                       │
│  │ issue_number         INTEGER          │                                       │
│  │ issue_action         TEXT             │                                       │
│  │ resolved_at          BIGINT           │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│                                                                                 │
│  [STDB] deployment_alert_rules                      ← NEW from 045             │
│  ┌──────────────────────────────────────┐                                       │
│  │ id (PK)              TEXT             │                                       │
│  │ environment (FK)     TEXT             │──→ deployment_environments.name       │
│  │ name                 TEXT             │                                       │
│  │ metric               TEXT             │                                       │
│  │ operator             TEXT             │                                       │
│  │ threshold            REAL             │                                       │
│  │ duration_minutes     INTEGER          │                                       │
│  │ severity             TEXT             │                                       │
│  │ enabled              BOOLEAN          │                                       │
│  │ auto_file_issue      BOOLEAN          │                                       │
│  │ custom_script_id     TEXT             │                                       │
│  │ applies_to_resources TEXT (JSON)      │   ← string, not a FK (problem!)      │
│  │ triggered_count      INTEGER          │                                       │
│  │ last_triggered_at    BIGINT           │                                       │
│  │ created_at           BIGINT           │                                       │
│  │ updated_at           BIGINT           │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│                                                                                 │
│  ═══════════════════════════  FILESYSTEM  ══════════════════════════════════     │
│                                                                                 │
│  [FS] ~/.bond/deployments/scripts/registry/{script_id}/{version}/               │
│  ┌──────────────────────────────────────┐                                       │
│  │ manifest.json:                       │                                       │
│  │   script_id           string         │                                       │
│  │   version             string         │                                       │
│  │   name                string         │                                       │
│  │   description         string         │                                       │
│  │   timeout             number         │                                       │
│  │   depends_on          string[]       │                                       │
│  │   rollback            string         │                                       │
│  │   sha256              string         │                                       │
│  │   registered_at       string         │                                       │
│  │   registered_by       string         │                                       │
│  │   files               string[]       │                                       │
│  │ deploy.sh             (executable)   │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [FS] ~/.bond/deployments/receipts/{env}/{receipt_id}.json                      │
│  ┌──────────────────────────────────────┐                                       │
│  │ receipt_id            string         │                                       │
│  │ type                  string         │                                       │
│  │ script_id             string         │──→ [FS] scripts/registry/{id}/        │
│  │ script_version        string         │                                       │
│  │ script_sha256         string         │                                       │
│  │ environment           string         │──→ [STDB] deployment_environments     │
│  │ agent_id              string         │                                       │
│  │ timestamp_start       string         │                                       │
│  │ timestamp_end         string         │                                       │
│  │ duration_ms           number         │                                       │
│  │ status                string         │                                       │
│  │ phases                object         │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [FS] ~/.bond/deployments/secrets/{env}.yaml                                    │
│  ┌──────────────────────────────────────┐                                       │
│  │ (key-value pairs, AES-256-GCM)      │                                       │
│  │ DATABASE_URL: BOND_ENC_V1:...       │                                       │
│  │ API_KEY: BOND_ENC_V1:...            │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [FS] ~/.bond/deployments/discovery/manifests/{app}.json                        │
│  ┌──────────────────────────────────────┐                                       │
│  │ manifest_version      string         │                                       │
│  │ application           string         │                                       │
│  │ discovered_at         string         │                                       │
│  │ discovered_by         string         │                                       │
│  │ entrypoint            object         │                                       │
│  │ servers[]             ManifestServer  │                                       │
│  │ topology              object         │                                       │
│  │ security_observations object[]       │                                       │
│  └──────────────────────────────────────┘                                       │
│                                                                                 │
│  [FS] ~/.bond/deployments/logs/{env}/{date}.log                                 │
│  [FS] ~/.bond/deployments/discovery/proposals/{app}/{level}/                    │
│  [FS] ~/.bond/deployments/locks/{agentId}.pause|.abort                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Current Relationship Summary

```
deployment_environments
  │
  ├──1:N──→ deployment_environment_approvers
  ├──1:N──→ deployment_environment_history
  ├──1:N──→ deployment_resources
  ├──1:N──→ deployment_promotions ──1:N──→ deployment_approvals
  ├──1:N──→ monitoring_alerts
  ├──1:N──→ deployment_alert_rules
  ├──1:N──→ [FS] receipts/{env}/
  └──1:N──→ [FS] secrets/{env}.yaml

deployment_promotions
  ├── script_id ─ ─ ─(convention)─ ─ ─→ [FS] scripts/registry/{id}/
  └── receipt_id ─ ─(convention)─ ─ ─→ [FS] receipts/{env}/{id}.json

monitoring_alerts.component ─ ─ ─ ─(string, no FK!)─ ─ ─→ ???
deployment_alert_rules.applies_to_resources ─ ─(JSON string, no FK!)─ ─ ─→ ???
```

### Problems Visible in Current State

1. **No component entity.** `monitoring_alerts.component` and `deployment_alert_rules.applies_to_resources` point at nothing — they're free-form strings.
2. **Scripts are disconnected.** There's no link between a script and what it deploys. The name `deploy-my-api` is a convention, not a relationship.
3. **Secrets are opaque.** A flat YAML file per environment. No way to know which secret belongs to which application.
4. **Discovery manifests are orphaned.** JSON files on disk not linked to any entity in the database.
5. **Resources (servers) are the finest-grained entity**, but users care about what *runs on* those servers.

---

## 2. Proposed State — What 045a Adds

### New Tables (4)

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                        NEW: Component Entity                        │
  │                                                                     │
  │  [STDB] deployment_components                                       │
  │  ┌────────────────────────────────────────┐                         │
  │  │ id (PK)              TEXT              │                         │
  │  │ name                 TEXT (UNIQUE)     │  globally unique        │
  │  │ display_name         TEXT              │                         │
  │  │ component_type       TEXT              │  application |          │
  │  │                                        │  web-server |           │
  │  │                                        │  data-store | cache |   │
  │  │                                        │  message-queue |        │
  │  │                                        │  infrastructure |       │
  │  │                                        │  system                 │
  │  │ parent_id (FK, self) TEXT              │──→ deployment_components│
  │  │ runtime              TEXT              │  node, python, nginx... │
  │  │ framework            TEXT              │  express, next.js...    │
  │  │ repository_url       TEXT              │                         │
  │  │ icon                 TEXT              │  emoji                  │
  │  │ description          TEXT              │                         │
  │  │ is_active            BOOLEAN           │                         │
  │  │ created_at           BIGINT            │                         │
  │  │ updated_at           BIGINT            │                         │
  │  │ discovered_from      TEXT              │  manifest name          │
  │  └──────────┬─────────────────────────────┘                         │
  │             │                                                       │
  │             │ 1:N (per-environment links)                           │
  │             │                                                       │
  │  ┌──────────▼─────────────────────────────────────────────────┐    │
  │  │  [STDB] deployment_component_resources                      │    │
  │  │  ┌────────────────────────────────────────┐                 │    │
  │  │  │ id (PK)              TEXT              │                 │    │
  │  │  │ component_id (FK)    TEXT              │──→ components   │    │
  │  │  │ resource_id (FK)     TEXT              │──→ resources    │    │
  │  │  │ environment (FK)     TEXT              │──→ environments │    │
  │  │  │ port                 INTEGER           │                 │    │
  │  │  │ process_name         TEXT              │                 │    │
  │  │  │ health_check         TEXT              │                 │    │
  │  │  │ created_at           BIGINT            │                 │    │
  │  │  │ UNIQUE(component_id, resource_id, environment)           │    │
  │  │  └────────────────────────────────────────┘                 │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  │                                                                     │
  │  ┌──────────▼─────────────────────────────────────────────────┐    │
  │  │  [STDB] deployment_component_scripts                        │    │
  │  │  ┌────────────────────────────────────────┐                 │    │
  │  │  │ id (PK)              TEXT              │                 │    │
  │  │  │ component_id (FK)    TEXT              │──→ components   │    │
  │  │  │ script_id            TEXT              │──→ [FS] scripts │    │
  │  │  │ role                 TEXT              │  deploy | setup │    │
  │  │  │                                        │  rollback |     │    │
  │  │  │                                        │  migrate |      │    │
  │  │  │                                        │  backup         │    │
  │  │  │ created_at           BIGINT            │                 │    │
  │  │  │ UNIQUE(component_id, script_id)        │                 │    │
  │  │  └────────────────────────────────────────┘                 │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  │                                                                     │
  │  ┌──────────▼─────────────────────────────────────────────────┐    │
  │  │  [STDB] deployment_component_secrets                        │    │
  │  │  ┌────────────────────────────────────────┐                 │    │
  │  │  │ id (PK)              TEXT              │                 │    │
  │  │  │ component_id (FK)    TEXT              │──→ components   │    │
  │  │  │ secret_key           TEXT              │──→ [FS] secrets │    │
  │  │  │ environment (FK)     TEXT              │──→ environments │    │
  │  │  │ is_sensitive         BOOLEAN           │                 │    │
  │  │  │ created_at           BIGINT            │                 │    │
  │  │  │ UNIQUE(component_id, secret_key, environment)            │    │
  │  │  └────────────────────────────────────────┘                 │    │
  │  └─────────────────────────────────────────────────────────────┘    │
  │                                                                     │
  └─────────────────────────────────────────────────────────────────────┘
```

### Complete Relationship Diagram (After 045a)

```
                            deployment_components
                           ┌──────────────────────┐
                      ┌────│ id                    │←──── parent_id (self-ref, tree)
                      │    │ name (unique)         │
                      │    │ component_type        │
                      │    │ parent_id ────────────│───┘
                      │    │ runtime, framework    │
                      │    │ repository_url        │
                      │    │ discovered_from ──────│─ ─ ─→ [FS] manifests/{name}.json
                      │    └──────────────────────┘
                      │              │
          ┌───────────┼──────────────┼──────────────┐
          │           │              │              │
          ▼           ▼              ▼              ▼
    component_     component_    component_    monitoring_alerts
    resources      scripts      secrets       (component_id FK
    ┌──────────┐   ┌──────────┐  ┌──────────┐   replaces string)
    │component │   │component │  │component │  ┌──────────┐
    │resource  │   │script_id │  │secret_key│  │component │
    │environmt │   │role      │  │environmt │  │  _id (FK)│
    │port      │   └────┬─────┘  │sensitive │  └──────────┘
    │health_chk│        │        └────┬─────┘
    └──┬───┬───┘        │             │
       │   │            │             │
       │   │            ▼             ▼
       │   │     [FS] scripts/    [FS] secrets/
       │   │     registry/        {env}.yaml
       │   │     {id}/{ver}/
       ▼   │
  deployment│    deployment_environments ◄──────────────────────────┐
  _resources│    ┌──────────────────────┐                           │
  ┌─────────┤    │ name (PK)            │                           │
  │ id      │    │ display_name         │                           │
  │ name    │    │ order                │                           │
  │ type    │    │ health_check_interval│                           │
  │ environm│──→ │ window_*, approvals  │                           │
  │ connect │    └──────┬───────────────┘                           │
  │ state   │           │                                           │
  └─────────┘    ┌──────┼──────────┬────────────┬──────────────┐   │
                 │      │          │            │              │   │
                 ▼      ▼          ▼            ▼              ▼   │
           approvers  history  promotions   alert_rules    [FS]   │
                                  │                       receipts│
                                  ▼                       secrets │
                              approvals                   logs    │
                                                                  │
                 component_resources.environment ──────────────────┘
                 component_secrets.environment ────────────────────┘
```

---

## 3. Change Plan

### 3.1 What Gets CREATED

| # | Type | Name | Description |
|---|---|---|---|
| 1 | STDB table | `deployment_components` | The component entity — global, hierarchical |
| 2 | STDB table | `deployment_component_resources` | Component ↔ resource link, per environment |
| 3 | STDB table | `deployment_component_scripts` | Component ↔ script link (global) |
| 4 | STDB table | `deployment_component_secrets` | Component ↔ secret key link, per environment |
| 5 | Backend file | `gateway/src/deployments/components.ts` | CRUD logic + STDB queries for all 4 tables |
| 6 | Backend file | `gateway/src/deployments/components-router.ts` | Express routes for component API |
| 7 | Frontend file | `ComponentDetail.tsx` | Full component detail view (cross-env) |

### 3.2 What Gets MODIFIED

| # | File | Change |
|---|---|---|
| 1 | `gateway/src/deployments/router.ts` | Mount `/components` sub-router |
| 2 | `gateway/src/deployments/discovery.ts` | Auto-create/link components when discovery runs |
| 3 | `frontend/.../DeploymentTab.tsx` | Add `component-detail` view mode, pass component context to child views |
| 4 | `frontend/.../EnvironmentDashboard.tsx` | **Major**: Replace server/receipt/alert columns with component tree view. Fetch from `/components?environment={env}&tree=true`. Show component cards with status, deploy info, secrets count. Infrastructure summary moves to footer. |
| 5 | `frontend/.../OnboardServerWizard.tsx` | Step 3 becomes "Review & Name Components" — discovered items create/link components |
| 6 | `frontend/.../SecretManager.tsx` | Group secrets by component; add component selector |
| 7 | `frontend/.../AlertRulesEditor.tsx` | Replace `applies_to_resources` string with `component_id` FK; add component selector |
| 8 | `frontend/.../CompareEnvironments.tsx` | Match components by name across envs; show version diffs per component |
| 9 | `frontend/.../InfraMap.tsx` | Show components as primary nodes; resources as host containers |
| 10 | `frontend/.../DeploymentTimeline.tsx` | Add component filter; show component name alongside script name |

### 3.3 What Gets MODIFIED in existing STDB tables

| Table | Change | Migration |
|---|---|---|
| `monitoring_alerts` | Add `component_id TEXT` column (nullable FK → components) | Additive — existing rows keep `component` string, new rows get both |
| `deployment_alert_rules` | Add `component_id TEXT` column (nullable FK → components), deprecate `applies_to_resources` string | Additive — old rules still work, new rules use component_id |

### 3.4 What does NOT change

| Thing | Why |
|---|---|
| `deployment_environments` | No schema change needed |
| `deployment_resources` | Stays as-is — resources are servers, components link to them |
| `deployment_promotions` | Stays as-is — promotions track scripts per env, component link is via `component_scripts` |
| `deployment_approvals` | No change |
| `deployment_environment_approvers` | No change |
| `deployment_environment_history` | No change |
| `[FS] scripts/registry/` | No change — scripts stay on filesystem |
| `[FS] receipts/` | No change — receipts stay on filesystem |
| `[FS] secrets/` | No change — encrypted YAML stays, component_secrets just indexes the keys |
| `[FS] discovery/manifests/` | No change — manifests stay on filesystem, components reference them by name |
| `[FS] logs/` | No change |

---

## 4. Query Examples

### "Show me all components deployed to production" (EnvironmentDashboard)

```sql
SELECT c.*, cr.resource_id, cr.port, cr.health_check, r.name as resource_name
FROM deployment_components c
JOIN deployment_component_resources cr ON c.id = cr.component_id
JOIN deployment_resources r ON cr.resource_id = r.id
WHERE cr.environment = 'prod' AND c.is_active = true
ORDER BY c.parent_id NULLS FIRST, c.name
```

### "Show me my-api across all environments" (ComponentDetail)

```sql
-- Resources per env
SELECT cr.environment, r.name as resource_name, cr.port, cr.health_check
FROM deployment_component_resources cr
JOIN deployment_resources r ON cr.resource_id = r.id
WHERE cr.component_id = '{id}'

-- Scripts
SELECT cs.script_id, cs.role
FROM deployment_component_scripts cs
WHERE cs.component_id = '{id}'

-- Secrets per env
SELECT cse.secret_key, cse.environment, cse.is_sensitive
FROM deployment_component_secrets cse
WHERE cse.component_id = '{id}'

-- Latest deploy per env (join scripts → promotions → receipts)
SELECT p.environment_name, p.script_version, p.status, p.deployed_at
FROM deployment_promotions p
JOIN deployment_component_scripts cs ON p.script_id = cs.script_id
WHERE cs.component_id = '{id}'
ORDER BY p.deployed_at DESC
```

### "Build the component tree for production" (tree=true)

```sql
-- 1. Get all component IDs visible in prod
SELECT DISTINCT cr.component_id
FROM deployment_component_resources cr
WHERE cr.environment = 'prod'

-- 2. Expand to include parents (recursive ancestor walk)
-- In code: walk parent_id chain until null, collect all ancestors

-- 3. Fetch full component data for all collected IDs
SELECT * FROM deployment_components WHERE id IN (...)
ORDER BY parent_id NULLS FIRST, name

-- 4. Build tree in application code (nest children under parents)
```
