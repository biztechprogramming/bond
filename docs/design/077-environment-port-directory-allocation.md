# Design Doc 077: Per-Environment Port & Directory Allocation

**Status:** Draft
**Date:** 2026-03-27
**Depends on:** 061 (Deployment Simplification), 071 (Agent-Driven Deployment Discovery), 072 (Discovery UI Integration)

---

## TL;DR

When multiple environments (dev, staging, production) share a server, ports and directories collide. This doc adds a **per-environment allocation model**: each server+environment pair gets an explicit record of its ports, directories, and service bindings. The allocation integrates into the Discovery Wizard as a new step, feeds structured data into script generation, and enforces collision detection before deployment — replacing manual spreadsheet tracking with a system that prevents "address already in use" errors by design.

**Key decisions:**
- Allocations are scoped to **(app, server, environment)** — multi-app-per-server is supported via app-level namespacing.
- Default ports use a **base + environment offset** pattern (prod +0, staging +100, dev +200).
- Directories follow `/{base}/{app_name}/{env}` templates.
- Collision detection runs both client-side (real-time in the wizard) and server-side (in SpacetimeDB reducers).
- The model is opt-in for existing deployments; new deployments are encouraged but not forced in v1.

---

## 1. Problem Statement

Bond's deployment system today treats every environment (dev, staging, production) as a named label with scheduling and approval metadata (`DeploymentEnvironment` in `gateway/src/deployments/stdb.ts`). It stores display names, deployment windows, health-check intervals, and approval chains — but it has **no concept of where things actually live on the server**.

When a user deploys the same application to dev and production on the same server (a common pattern for small teams), both environments collide:

- **Ports:** The app defaults to port 3000 in both environments. The user must manually figure out "dev gets 3000, staging gets 3100, production gets 3200" and remember to set this everywhere.
- **Directories:** Install paths default to `/opt/app` or `~/app`. Two environments on the same host stomp each other's files.
- **Reverse proxies:** Nginx/Caddy configs need different `proxy_pass` targets per environment, but there's no structured data to generate them from.
- **Databases:** Data directories and ports for per-environment databases (e.g., PostgreSQL dev on 5432 vs. staging on 5433) are untracked.

The Discover Stack Wizard ([Doc 071](071-agent-driven-deployment-discovery.md)) already detects ports, frameworks, and services on a server. But after discovery, there's no place to **assign** those ports and directories per environment. The wizard finds "port 3000 is in use" but can't help the user say "give dev port 3100 and staging port 3200."

### Impact

- Users manually track port assignments in spreadsheets or their heads
- Port collisions cause deployment failures with cryptic "address already in use" errors
- Directory collisions cause one environment to overwrite another's files
- No structured data exists for script generation to template port/directory values per environment

---

## 2. Proposed Solution

Add a **per-environment resource allocation model** to the deployment system. Each (app, server, environment) combination gets an explicit allocation record that defines ports, directories, and service bindings. This integrates into the Discover Stack Wizard as a new step after discovery and before script generation.

### 2.1 Core Concept: Environment Allocation

An allocation binds an **application** to a **server** (resource) and an **environment**, declaring what ports and directories that app's environment uses on that server.

```
Server: web-01.example.com
├── App: myapp
│   ├── Environment: dev
│   │   ├── base_port: 3100
│   │   ├── app_dir: /opt/myapp/dev
│   │   ├── data_dir: /var/data/myapp/dev
│   │   ├── log_dir: /var/log/myapp/dev
│   │   └── services:
│   │       ├── app:        port 3100
│   │       ├── postgres:   port 5433, data_dir: /var/data/myapp/dev/pg
│   │       └── redis:      port 6380
│   ├── Environment: staging
│   │   ├── base_port: 3200
│   │   ├── app_dir: /opt/myapp/staging
│   │   └── ...
│   └── Environment: production
│       ├── base_port: 3000
│       ├── app_dir: /opt/myapp/production
│       └── ...
└── App: otherapp
    └── Environment: production
        ├── base_port: 4000
        ├── app_dir: /opt/otherapp/production
        └── services:
            ├── app:        port 4000
            └── postgres:   port 5440
```

This model supports **multiple applications on the same server** — each app has its own allocation namespace, and collision detection spans all apps on a given server to prevent port conflicts between them.

### 2.2 Design Principles

1. **Convention with override** — Smart defaults based on environment name and order (dev → base+100, staging → base+200, prod → base+0). Users can override any value.
2. **Collision detection** — The system validates that no two environments *or applications* on the same server share a port or directory. Violations are surfaced before deployment, not at runtime.
3. **Discovery-aware** — The Discover Stack Wizard populates initial allocations from what it finds on the server. Existing ports/directories are pre-filled; new environments get suggested values that avoid conflicts.
4. **Template-ready** — Allocations are structured data that script generation ([Doc 071 §6](071-agent-driven-deployment-discovery.md)) can consume directly. A deploy script template can reference `{{app_port}}` and get the right value per environment.
5. **Confidence-attributed** — Following the confidence model from [Doc 071 §5.2](071-agent-driven-deployment-discovery.md), allocation values carry source attribution (detected from server, inferred from conventions, user-provided) so the UI can indicate certainty levels using the same ✓/~/👤 indicators from [Doc 072 §5.2](072-discovery-ui-integration.md).

---

## 3. Data Model

### 3.1 New Table: `environment_allocation`

Stored in SpacetimeDB alongside existing deployment tables.

```rust
#[spacetimedb::table(name = environment_allocation, public)]
pub struct EnvironmentAllocation {
    #[primary_key]
    pub id: String,                    // ULID
    pub resource_id: String,           // FK → deployment_resource (the server)
    pub app_name: String,              // Application identifier (from discovery or user input)
    pub environment_name: String,      // FK → deployment_environment (dev/staging/prod)
    pub base_port: u32,                // Starting port for auto-assignment
    pub app_dir: String,               // e.g., /opt/myapp/dev
    pub data_dir: String,              // e.g., /var/data/myapp/dev
    pub log_dir: String,               // e.g., /var/log/myapp/dev
    pub config_dir: String,            // e.g., /etc/myapp/dev
    pub tls_cert_path: String,         // e.g., /etc/letsencrypt/live/dev.example.com (empty = no TLS)
    pub tls_key_path: String,          // e.g., /etc/letsencrypt/live/dev.example.com/privkey.pem
    pub revision: u32,                 // Monotonically increasing, incremented on every update
    pub created_at: u64,
    pub updated_at: u64,
    pub is_active: bool,               // See §3.6 for lifecycle semantics
}
```

**Unique constraint:** `(resource_id, app_name, environment_name)` — one allocation per app per server per environment.

### 3.2 New Table: `service_port_assignment`

Per-service port and directory overrides within an allocation.

```rust
#[spacetimedb::table(name = service_port_assignment, public)]
pub struct ServicePortAssignment {
    #[primary_key]
    pub id: String,                    // ULID
    pub allocation_id: String,         // FK → environment_allocation
    pub service_name: String,          // e.g., "app", "postgres", "redis", "nginx"
    pub port: u32,                     // The assigned port
    pub protocol: String,              // "tcp" or "udp"
    pub data_dir: String,              // Service-specific data directory (optional, empty = use allocation default)
    pub health_endpoint: String,       // e.g., "http://localhost:3100/health" — used by health checks (see §3.7)
    pub description: String,           // Human-readable, e.g., "Main application HTTP"
    pub created_at: u64,
    pub updated_at: u64,
}
```

**Unique constraint:** `(allocation_id, service_name)` — one port assignment per service per allocation. Additionally, a cross-allocation constraint: no two `service_port_assignment` rows on the same `resource_id` (via their parent allocation) may share the same `(port, protocol)`.

### 3.3 New Table: `allocation_history`

Tracks changes to allocations for audit and rollback support.

```rust
#[spacetimedb::table(name = allocation_history, public)]
pub struct AllocationHistory {
    #[primary_key]
    pub id: String,                    // ULID
    pub allocation_id: String,         // FK → environment_allocation
    pub revision: u32,                 // Matches the revision that was created
    pub change_type: String,           // "created" | "updated" | "deactivated" | "reactivated"
    pub changed_fields: String,        // JSON: {"base_port": {"old": 3100, "new": 3200}}
    pub changed_by: String,            // User identity or "system" for auto-operations
    pub timestamp: u64,
}
```

This follows the existing `deployment_environment_history` pattern in `stdb.ts` and provides concrete audit data for compliance (resolving open question §9.5).

### 3.4 New Reducers

```
create_environment_allocation(resource_id, app_name, environment_name, base_port, app_dir, data_dir, log_dir, config_dir, tls_cert_path, tls_key_path)
update_environment_allocation(id, base_port?, app_dir?, data_dir?, log_dir?, config_dir?, tls_cert_path?, tls_key_path?)
deactivate_environment_allocation(id)     // Sets is_active=false, preserves record
reactivate_environment_allocation(id)     // Sets is_active=true, re-validates constraints
delete_environment_allocation(id)         // Hard delete, only when is_active=false

create_service_port_assignment(allocation_id, service_name, port, protocol, data_dir, health_endpoint, description)
update_service_port_assignment(id, port?, protocol?, data_dir?, health_endpoint?, description?)
delete_service_port_assignment(id)
```

### 3.5 Validation Rules (Enforced in Reducers)

1. **Port range:** Ports must be 1–65535. Ports below 1024 emit a warning (require root).
2. **Port uniqueness:** No two service port assignments on the same server (across all active allocations, across all apps) may use the same port+protocol combination. Enforcement strategy: the `create_service_port_assignment` and `update_service_port_assignment` reducers query all `service_port_assignment` rows whose parent `environment_allocation` shares the same `resource_id` and has `is_active = true`, then reject if any match on `(port, protocol)`.
3. **Directory uniqueness:** No two active allocations on the same server may share `app_dir`, `data_dir`, `log_dir`, or `config_dir`. Substring containment is also rejected (e.g., `/opt/app` and `/opt/app/dev` conflict because one is a parent of the other). Enforcement: same query pattern as port uniqueness — scan all active allocations for the resource and compare directory paths.
4. **Environment existence:** `environment_name` must reference an existing `deployment_environment`.
5. **Resource existence:** `resource_id` must reference an existing `deployment_resource`.
6. **TLS path validation:** If `tls_cert_path` is non-empty, `tls_key_path` must also be non-empty (and vice versa).

### 3.6 Allocation Lifecycle (`is_active`)

An allocation can be in two states:

- **Active (`is_active = true`):** The allocation is in use. Its ports and directories participate in collision detection. This is the default state on creation.
- **Inactive (`is_active = false`):** The allocation is preserved for history but no longer reserves ports or directories. Other allocations can now use those ports/directories.

Allocations are deactivated (not deleted) when:
- An environment is removed from a server but may be re-added later
- A deployment is rolled back and the environment is temporarily taken offline (see §3.8)
- A user explicitly disables an environment's allocation without destroying the record

Hard deletion is only permitted on inactive allocations (to prevent accidental loss of active configuration).

### 3.7 Health Check Integration

The `DeploymentEnvironment` model ([Doc 061](061-deployment-simplification.md)) already has health check intervals and configuration. Allocations bridge the gap between "check health every 60s" and "check *what* at *where*":

- Each `ServicePortAssignment` has a `health_endpoint` field. For HTTP services, this is a full URL (e.g., `http://localhost:3100/health`). For TCP services (postgres, redis), this is the `host:port` to probe.
- When health checks run, they look up the active allocation for the target server+environment, then probe each service's `health_endpoint`.
- If no allocation exists, health checks fall back to the existing behavior (probing the `DeploymentEnvironment`'s configured endpoint).

This means the allocation model defines *what to probe*, while `DeploymentEnvironment` defines *how often and what to do on failure* (alert, rollback, etc.).

### 3.8 Rollback Behavior

When a deployment is rolled back:

1. **Allocations are NOT automatically deleted or modified.** Ports and directories don't change on rollback — the *code* changes, but the allocation (where things live) stays the same.
2. If a rollback involves switching to a completely different server or port layout, the user must update allocations manually (or the system suggests updates based on the rollback target's last known allocation).
3. If an environment is fully torn down during rollback, the allocation is **deactivated** (not deleted), preserving the record. If the environment is redeployed later, the allocation can be reactivated, restoring the previous port/directory layout without reconfiguration.
4. The `allocation_history` table (§3.3) records all changes, so the state at any point in time can be reconstructed.

### 3.9 Gateway Query Helpers

Added to `gateway/src/deployments/stdb.ts`:

```typescript
export interface EnvironmentAllocation {
  id: string;
  resource_id: string;
  app_name: string;
  environment_name: string;
  base_port: number;
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
  tls_cert_path: string;
  tls_key_path: string;
  revision: number;
  created_at: number;
  updated_at: number;
  is_active: boolean;
}

export interface ServicePortAssignment {
  id: string;
  allocation_id: string;
  service_name: string;
  port: number;
  protocol: string;
  data_dir: string;
  health_endpoint: string;
  description: string;
  created_at: number;
  updated_at: number;
}

// Get all allocations for a server (all apps, all environments)
async function getAllocationsForResource(cfg, resourceId): Promise<EnvironmentAllocation[]>

// Get allocations for a specific app on a server
async function getAllocationsForApp(cfg, resourceId, appName): Promise<EnvironmentAllocation[]>

// Get allocation for a specific app+server+environment
async function getAllocation(cfg, resourceId, appName, envName): Promise<EnvironmentAllocation | null>

// Get port assignments for an allocation
async function getPortAssignments(cfg, allocationId): Promise<ServicePortAssignment[]>

// Get ALL port assignments across all apps and environments for a server (for collision detection)
async function getServerPortMap(cfg, resourceId): Promise<Map<number, { app: string; env: string; service: string; protocol: string }>>

// Get allocation history for audit
async function getAllocationHistory(cfg, allocationId): Promise<AllocationHistory[]>
```

---

## 4. Default Allocation Strategy

When a user creates or discovers an environment on a server, the system suggests defaults using the following strategy:

### 4.1 Port Defaults

The system uses a **base port + environment offset** pattern:

| Environment Order | Offset | Example (base=3000) |
|---|---|---|
| 1 (production) | +0 | 3000 |
| 2 (staging) | +100 | 3100 |
| 3 (dev) | +200 | 3200 |
| 4+ (custom) | +N*100 | 3300, 3400, ... |

The base port comes from:
1. **Discovery** — if the wizard detected the app running on port 3000, that becomes the base for the environment where it was found.
2. **Framework convention** — Next.js defaults to 3000, Django to 8000, Rails to 3000, Express to 3000, etc.
3. **User override** — the user can set any base port.

Service ports are offset from the environment's base port:

| Service | Offset from base | Example (base=3100) |
|---|---|---|
| app (HTTP) | +0 | 3100 |
| app (HTTPS/TLS) | +443 relative or user-set | user-set |
| postgres | well-known + env offset | 5432 + (env_order * 1) → 5433 |
| redis | well-known + env offset | 6379 + (env_order * 1) → 6380 |
| custom | user-defined | user-defined |

For well-known services (postgres, redis, mysql, mongodb, rabbitmq, etc.), the system uses the standard port as the base and adds the environment order as offset. This keeps ports recognizable (5433 is obviously "postgres, not default").

**Multi-app offset:** When multiple apps share a server, the second app's base port starts at the next available block of 1000 (e.g., first app uses 3000–3999, second app uses 4000–4999). This is a suggestion — users can override.

### 4.2 Directory Defaults

Directories follow a `{base}/{app_name}/{environment_name}` pattern:

| Directory | Template | Example (app=myapp, env=staging) |
|---|---|---|
| app_dir | `/opt/{app_name}/{env}` | `/opt/myapp/staging` |
| data_dir | `/var/data/{app_name}/{env}` | `/var/data/myapp/staging` |
| log_dir | `/var/log/{app_name}/{env}` | `/var/log/myapp/staging` |
| config_dir | `/etc/{app_name}/{env}` | `/etc/myapp/staging` |

The `{app_name}` is derived from:
1. **Discovery** — repository name, `package.json` name, or detected project name
2. **User input** — the application name set during onboarding

### 4.3 Auto-Assignment on Environment Creation

When a user adds a new environment to a server that already has allocations:
1. Query existing allocations for that server (across all apps)
2. Find the highest used port offsets and directory suffixes
3. Suggest the next available slot
4. Run collision detection before committing

---

## 5. Discovery Wizard Integration

The Discover Stack Wizard (`DiscoverStackWizard.tsx`, see [Doc 072](072-discovery-ui-integration.md)) currently has these steps:

```
select-server → discovery → review → environment → scripts → done
```

This design adds an **"allocate"** step between **environment** and **scripts**:

```
select-server → discovery → review → environment → allocate → scripts → done
```

### 5.1 The "Allocate" Step

After the user selects which environment(s) to deploy to, the allocate step presents a port and directory assignment form.

#### Initial State

The form is pre-populated using:
1. **Discovery findings** — ports and directories the agent found in use on the server (from `DiscoveryState.findings`)
2. **Existing allocations** — if other environments or apps already have allocations on this server, those are loaded to inform collision detection
3. **Default strategy** — any unfilled fields get defaults per §4

Allocation values carry confidence attribution following the pattern from [Doc 071 §5.2](071-agent-driven-deployment-discovery.md): detected (✓), inferred (~), or user-provided (👤). The UI uses the same indicators as the DeploymentPlanPanel ([Doc 072 §5.2](072-discovery-ui-integration.md)).

#### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Port & Directory Allocation — web-01.example.com               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Environment: [staging ▼]          Base Port: [3100]            │
│                                                                 │
│  ┌─ Directories ──────────────────────────────────────────────┐ │
│  │  Application:  [/opt/myapp/staging        ]  ~             │ │
│  │  Data:         [/var/data/myapp/staging    ]  ~             │ │
│  │  Logs:         [/var/log/myapp/staging     ]  ~             │ │
│  │  Config:       [/etc/myapp/staging         ]  ~             │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ TLS/SSL (optional) ────────────────────────────────────────┐│
│  │  Certificate:  [/etc/letsencrypt/live/staging.example.com] ││
│  │  Private Key:  [.../privkey.pem                           ] ││
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Service Ports ────────────────────────────────────────────┐ │
│  │  Service       Port    Protocol   Health           Status  │ │
│  │  ─────────────────────────────────────────────────────     │ │
│  │  app           3100    TCP        /health     ✓  ✓ Avail  │ │
│  │  postgres      5433    TCP        :5433       ~  ✓ Avail  │ │
│  │  redis         6380    TCP        :6380       ~  ✓ Avail  │ │
│  │  [+ Add Service]                                           │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Other Allocations on This Server ───────────────────────────┐│
│  │  myapp/production:  app=3000, postgres=5432, redis=6379    ││
│  │  myapp/dev:         app=3200, postgres=5434, redis=6381    ││
│  │  otherapp/prod:     app=4000, postgres=5440                ││
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ⚠ No conflicts detected                                       │
│                                                                 │
│                                    [Back]  [Next: Scripts →]    │
└─────────────────────────────────────────────────────────────────┘
```

#### Batch Allocation (Multiple Environments)

When the user selects multiple environments in the environment step (e.g., both "staging" and "dev"), the allocate step shows a **tabbed or accordion view** with one section per environment. The system pre-populates all environments simultaneously using the default strategy (§4), ensuring the suggestions are mutually conflict-free. The user can edit any environment's allocation, and collision detection runs across all environments in the batch.

The "Other Allocations" panel always shows allocations from environments *not* in the current batch, plus the current batch's other tabs, so the user sees the full picture.

#### Collision Detection and Resolution

When a conflict is detected (same port+protocol or overlapping directory on the same server), the UI provides resolution options:

```
  app           3000    TCP        ⚠ Conflict: used by production (myapp)
                                   ┌──────────────────────────────────┐
                                   │  ● Use suggested: 3100           │
                                   │  ○ Enter custom port: [    ]     │
                                   │  ○ Skip this service             │
                                   └──────────────────────────────────┘
```

Resolution options:
1. **Auto-suggest alternative** — The system proposes the next available port using the default strategy. This is the pre-selected option.
2. **Manual edit** — The user enters a custom port. Real-time validation runs on each keystroke (debounced).
3. **Skip service** — The user can skip allocating this service (e.g., if they'll handle it manually or the service isn't needed in this environment).

The "Next" button is disabled while any hard conflicts exist (same port+protocol on same server). Soft warnings (port < 1024, directory is a parent of another env's directory) allow proceeding but show a confirmation dialog.

#### Error States

| Scenario | Behavior |
|---|---|
| **Server unreachable during allocation** | The allocate step works without server connectivity — it only needs SpacetimeDB data for existing allocations plus the discovery findings already gathered. A banner shows "Server offline — port availability not verified" but the user can proceed. |
| **User changes environment mid-wizard** | If the user navigates back to the environment step and changes selections, returning to the allocate step resets any unconfirmed allocations for removed environments and generates fresh suggestions for newly added ones. Confirmed (saved) allocations are preserved. |
| **Discovery found no ports** | The form shows empty service port list with the message "No services detected. Add services manually or re-run discovery." All fields use default strategy values. |
| **SpacetimeDB unavailable** | The allocate step enters a degraded mode: no existing allocations are shown, collision detection is limited to within the current batch, and a warning banner explains the limitation. |

### 5.2 Discovery Agent Enhancement

The discovery agent ([Doc 071](071-agent-driven-deployment-discovery.md)) gains a new tool and an enhancement to its completeness model:

#### New Tool: `check_port_available`

```typescript
interface CheckPortAvailableTool {
  port: number;
  protocol?: "tcp" | "udp";  // default: tcp
}
// Returns: { available: boolean, process?: string, pid?: number }
```

Implemented via `ss -tlnp | grep :PORT` on the target server. Added to the `ALLOWED_SSH_COMMANDS` allowlist ([Doc 071 §4.1](071-agent-driven-deployment-discovery.md)).

#### New Tool: `detect_environment_directories`

```typescript
interface DetectEnvironmentDirectoriesTool {
  base_paths?: string[];  // default: ["/opt", "/var/www", "/home"]
}
// Returns: Array<{ path: string; app_name?: string; env_hint?: string; exists: boolean }>
```

Scans common installation directories for environment-patterned subdirectories (e.g., `/opt/myapp/staging`). Uses `ls`, `stat`, and systemd `WorkingDirectory=` parsing — all already in the SSH allowlist.

#### Completeness Model Addition

The completeness model ([Doc 071 §5](071-agent-driven-deployment-discovery.md)) gains a new section:

```typescript
interface CompletenessReport {
  // ... existing fields ...

  allocation: {
    status: "complete" | "partial" | "missing";
    ports_detected: Array<{ port: number; service: string; in_use: boolean }>;
    directories_detected: Array<{ path: string; service: string; exists: boolean }>;
    suggested_base_port: number | null;
    conflicts: Array<{ port: number; conflict_with: string }>;
  };
}
```

During discovery, the agent:
1. Scans for listening ports (`ss -tlnp`)
2. Identifies which services own which ports
3. Detects install directories (`/opt/*`, `/var/www/*`, systemd `WorkingDirectory=`, docker volume mounts)
4. Records findings in the completeness report
5. Suggests a base port and directory layout for the target environment

#### Degraded Mode Behavior

Following the degraded mode patterns from [Doc 071 §8.1](071-agent-driven-deployment-discovery.md):

| Mode | Allocation Behavior |
|---|---|
| **Full (repo + server)** | Ports/directories detected from server, cross-referenced with repo config |
| **Repo-only** | Ports inferred from framework defaults and config files; directories use templates |
| **Server-only** | Ports/directories detected from running services; app name inferred from paths |
| **Interview** | All allocation values prompted via `ask_user` questions |

### 5.3 Script Generation Integration

The script generation step (currently `ScriptFromDiscoveryWizard.tsx`) already templates deploy scripts. With allocations, the template variables expand:

```bash
# Before (hardcoded)
APP_PORT=3000
APP_DIR=/opt/app

# After (from allocation)
APP_PORT={{allocation.services.app.port}}        # 3100
APP_DIR={{allocation.app_dir}}                    # /opt/myapp/staging
DATA_DIR={{allocation.data_dir}}                  # /var/data/myapp/staging
LOG_DIR={{allocation.log_dir}}                    # /var/log/myapp/staging
PG_PORT={{allocation.services.postgres.port}}     # 5433
REDIS_PORT={{allocation.services.redis.port}}     # 6380

# TLS (if configured)
TLS_CERT={{allocation.tls_cert_path}}             # /etc/letsencrypt/live/staging.example.com/fullchain.pem
TLS_KEY={{allocation.tls_key_path}}               # /etc/letsencrypt/live/staging.example.com/privkey.pem
```

The `generate_plan` API ([Doc 071](071-agent-driven-deployment-discovery.md)) is extended to accept an `allocation_id` parameter. When present, the plan generator pulls all port and directory values from the allocation record rather than using hardcoded defaults.

---

## 6. API Endpoints

Added to `backend/app/api/v1/deployments.py` and `gateway/src/deployments/`:

### 6.1 Allocation CRUD

```
GET    /api/v1/deployments/allocations?resource_id=X[&app_name=Y]
GET    /api/v1/deployments/allocations/:id
POST   /api/v1/deployments/allocations
PUT    /api/v1/deployments/allocations/:id
DELETE /api/v1/deployments/allocations/:id
```

### 6.2 Port Assignment CRUD

```
GET    /api/v1/deployments/allocations/:id/ports
POST   /api/v1/deployments/allocations/:id/ports
PUT    /api/v1/deployments/ports/:id
DELETE /api/v1/deployments/ports/:id
```

### 6.3 Collision Check (Read-Only)

```
POST   /api/v1/deployments/allocations/check-conflicts
Body:  { resource_id, app_name, environment_name, ports: [...], directories: {...} }
Response: { conflicts: [...], warnings: [...], suggestions: {...} }
```

This endpoint is called by the UI on every field change (debounced 300ms) to provide real-time collision feedback without requiring a save. The `suggestions` field in the response provides auto-fix options for each conflict (next available port, adjusted directory path).

### 6.4 Suggest Defaults

```
POST   /api/v1/deployments/allocations/suggest
Body:  { resource_id, app_name, environment_name, services: [...] }
Response: { base_port, app_dir, data_dir, log_dir, config_dir, service_ports: [...] }
```

Called when the user selects an environment in the allocate step. Returns suggested defaults that avoid conflicts with existing allocations across all apps on the server.

### 6.5 Allocation History

```
GET    /api/v1/deployments/allocations/:id/history
Response: { entries: [{ revision, change_type, changed_fields, changed_by, timestamp }] }
```

---

## 7. Migration Path

### 7.1 Existing Deployments

Existing deployments have no allocation records. The system handles this gracefully:

- **Script generation** falls back to current behavior (hardcoded or user-provided values) when no allocation exists.
- **A migration banner** appears on the Deployment tab: "Port & directory allocations are now available. Set up allocations for [server] to enable conflict detection and auto-templating."
- **No forced migration** — existing setups continue to work. Allocations are opt-in until a future version makes them required for new deployments.

### 7.2 Schema Migration

The three new SpacetimeDB tables (`environment_allocation`, `service_port_assignment`, `allocation_history`) are additive — no existing tables are modified. The migration is a simple `spacetime publish` with the updated module.

### 7.3 Feature Flag

The allocation feature is gated behind a feature flag `BOND_PER_ENV_ALLOCATION` (following the pattern of `BOND_AGENT_DISCOVERY` from [Doc 072 §7.1](072-discovery-ui-integration.md)). When disabled, the wizard skips the "allocate" step and script generation uses the pre-allocation behavior.

---

## 8. Operational Concerns

### 8.1 Monitoring & Observability

Allocations define where services *should* be — but services can crash, be misconfigured, or never start. Bond should surface allocation health:

- **Port liveness check:** Periodically (configurable, default every 5 minutes), Bond probes each active allocation's service ports to verify something is listening. Results are stored and surfaced in the UI:
  - ✓ Listening — service is responding on the allocated port
  - ⚠ Not listening — port is allocated but nothing is bound to it
  - ✗ Conflict — a different process is using the allocated port
- **Dashboard integration:** The Deployment tab's per-environment view shows allocation health alongside existing deployment status. A new "Allocation Health" section lists each service's port and its current state.
- **Alerting:** Allocation health feeds into the existing alerting pipeline. If a service's allocated port goes from "listening" to "not listening," this triggers the same alert path as a health check failure.

### 8.2 Backup & Restore Reconciliation

If a server is restored from a backup, the actual ports and directories may not match what SpacetimeDB records:

- After a server restore, Bond can run a **reconciliation scan** (reusing discovery agent tools) that compares actual port usage against allocations and flags discrepancies.
- The UI shows a "Reconcile" action on the server's allocation page that triggers this scan.
- Discrepancies are shown as warnings, not auto-corrected — the user decides whether to update allocations to match reality or redeploy to match allocations.

### 8.3 Firewall & Security Group Awareness

The doc currently requires users to manually configure firewall rules for allocated ports. While full firewall automation is out of scope for v1, the system provides:

- **Port summary export:** A "Copy firewall rules" button generates a list of allocated ports in common firewall formats (iptables, ufw, AWS security group JSON) for the user to apply manually.
- **Future integration point:** The `ServicePortAssignment` model includes enough data (port, protocol) for a future firewall automation module to consume directly. This is flagged as a candidate for a follow-up design doc.

---

## 9. Implementation Plan

### Phase 1: Data Model & API (1–2 days)
- [ ] Add `environment_allocation`, `service_port_assignment`, and `allocation_history` tables to SpacetimeDB module
- [ ] Add reducers with validation (port range, uniqueness, directory containment, TLS path pairing)
- [ ] Implement cross-allocation port uniqueness enforcement (query pattern per §3.5)
- [ ] Add gateway query helpers to `stdb.ts`
- [ ] Add REST endpoints for CRUD, conflict check, suggest, and history
- [ ] Unit tests for collision detection logic

### Phase 2: Discovery Integration (1 day)
- [ ] Add `check_port_available` tool to discovery-tools.ts
- [ ] Add `detect_environment_directories` tool to discovery-tools.ts
- [ ] Extend completeness model with allocation section
- [ ] Update discovery agent prompt to scan for ports and directories
- [ ] Wire discovery findings into allocation suggestions
- [ ] Handle degraded modes (repo-only, server-only, interview)

### Phase 3: Wizard UI (1–2 days)
- [ ] Add `BOND_PER_ENV_ALLOCATION` feature flag
- [ ] Add "allocate" step to `DiscoverStackWizard.tsx`
- [ ] Build allocation form component with collision indicators and confidence badges
- [ ] Implement batch allocation view (tabs/accordion for multiple environments)
- [ ] Implement conflict resolution UI (auto-suggest, manual edit, skip)
- [ ] Integrate real-time conflict checking via debounced API calls
- [ ] Pre-populate from discovery findings and default strategy
- [ ] Show cross-environment, cross-app port map for the server
- [ ] Handle error states (server offline, SpacetimeDB unavailable, no ports detected)

### Phase 4: Script Generation (0.5 day)
- [ ] Extend script templates to consume allocation variables (including TLS paths)
- [ ] Update `generate_plan` API to accept `allocation_id`
- [ ] Update `ScriptFromDiscoveryWizard.tsx` to pass allocation context

### Phase 5: Operational Features (1 day)
- [ ] Port liveness monitoring (periodic probe of allocated ports)
- [ ] Allocation health display on Deployment tab
- [ ] Reconciliation scan for backup/restore scenarios
- [ ] Port summary export (firewall rule format)
- [ ] Allocation history UI

### Phase 6: Polish & Edge Cases (0.5 day)
- [ ] Migration banner for existing deployments
- [ ] Handle server removal (cascade-deactivate allocations)
- [ ] Handle environment removal (cascade-deactivate allocations for that env)
- [ ] Handle app deletion (cascade-deactivate allocations for that app)
- [ ] E2E test: discover → allocate → generate script → verify ports in output

---

## 10. Open Questions

1. **Multi-server environments** — Should an environment allocation span multiple servers (e.g., "staging uses web-01 for the app and db-01 for postgres")?

   **Decision:** No for v1. Each allocation is server-scoped. Cross-server environments are modeled as separate allocations on each server, linked by the shared `(app_name, environment_name)` tuple. This is the simpler model and matches how small teams actually deploy (one server per environment, or all environments on one server). A future "environment topology" view could visualize cross-server relationships by querying all allocations with the same app+environment across different resources.

2. **Docker port mapping** — When the app runs in Docker, the container port and host port differ. Should allocations track both?

   **Decision:** Track the host port (what's visible on the server). Container-internal ports are part of the Docker Compose config, not the allocation. The script generator handles the mapping (e.g., `-p 3100:3000`). The `description` field on `ServicePortAssignment` can note the container port for reference (e.g., "Main app HTTP — container :3000 → host :3100").

3. **Dynamic port ranges** — Some services (e.g., Erlang/Elixir nodes, RPC services) use port ranges rather than single ports.

   **Decision:** Defer to v2. For now, users can add multiple `service_port_assignment` entries (e.g., `epmd: 4369`, `beam-range-start: 9100`, `beam-range-end: 9155`). This is clunky but functional. A proper `port_range_start`/`port_range_end` column pair on `ServicePortAssignment` is the v2 solution — it requires updating collision detection to handle range-vs-range and range-vs-single overlaps, which is non-trivial.

4. **Reverse proxy config generation** — Should the system auto-generate nginx/caddy configs from allocations?

   **Decision:** Yes, but as a separate design doc. The allocation model provides the structured data (ports, directories, TLS cert paths); config generation is a consumer of that data. The TLS fields added in this doc (§3.1) are forward-looking — they provide the cert/key paths that a reverse proxy config generator would need.

5. **Audit trail** — Should port/directory changes be logged for compliance?

   **Decision:** Yes, implemented in this doc via the `allocation_history` table (§3.3). Every create, update, deactivate, and reactivate operation records the change, the changed fields (with old/new values), and the acting user. This follows the existing `deployment_environment_history` pattern and provides the data needed for compliance audits without requiring a separate follow-up.

---

## 11. Rejected Alternatives

### 11.1 Environment Variables Only

Store port and directory assignments as key-value pairs in the existing environment config. Rejected because:
- No structure — can't validate port uniqueness across environments
- No type safety — ports are strings, directories are strings, no distinction
- Can't build a visual port map or collision detector from unstructured KV pairs

### 11.2 Per-Service Config Files on the Server

Let each service manage its own port via config files on the server (e.g., `.env` files per environment). Rejected because:
- Bond can't detect or prevent collisions — it doesn't know what's in those files until deployment fails
- Requires SSH access to read/write configs, adding latency and failure modes
- Doesn't help with the wizard flow — the user still has to manually figure out ports

### 11.3 Automatic Port Assignment Without User Visibility

Auto-assign ports behind the scenes without showing the user. Rejected because:
- Users need to know ports for debugging, firewall rules, and external integrations
- "Magic" port numbers cause confusion when users SSH into the server and see unfamiliar ports
- Firewall and security group rules require knowing exact ports upfront
