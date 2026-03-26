# Design Doc 077: Per-Environment Port & Directory Allocation

**Status:** Draft
**Date:** 2026-03-27
**Depends on:** 061 (Deployment Simplification), 071 (Agent-Driven Deployment Discovery), 072 (Discovery UI Integration)

---

## 1. Problem Statement

Bond's deployment system today treats every environment (dev, staging, production) as a named label with scheduling and approval metadata (`DeploymentEnvironment` in `gateway/src/deployments/stdb.ts`). It stores display names, deployment windows, health-check intervals, and approval chains — but it has **no concept of where things actually live on the server**.

When a user deploys the same application to dev and production on the same server (a common pattern for small teams), both environments collide:

- **Ports:** The app defaults to port 3000 in both environments. The user must manually figure out "dev gets 3000, staging gets 3100, production gets 3200" and remember to set this everywhere.
- **Directories:** Install paths default to `/opt/app` or `~/app`. Two environments on the same host stomp each other's files.
- **Reverse proxies:** Nginx/Caddy configs need different `proxy_pass` targets per environment, but there's no structured data to generate them from.
- **Databases:** Data directories and ports for per-environment databases (e.g., PostgreSQL dev on 5432 vs. staging on 5433) are untracked.

The Discover Stack Wizard (Doc 071) already detects ports, frameworks, and services on a server. But after discovery, there's no place to **assign** those ports and directories per environment. The wizard finds "port 3000 is in use" but can't help the user say "give dev port 3100 and staging port 3200."

### Impact

- Users manually track port assignments in spreadsheets or their heads
- Port collisions cause deployment failures with cryptic "address already in use" errors
- Directory collisions cause one environment to overwrite another's files
- No structured data exists for script generation to template port/directory values per environment

---

## 2. Proposed Solution

Add a **per-environment resource allocation model** to the deployment system. Each server+environment combination gets an explicit allocation record that defines ports, directories, and service bindings. This integrates into the Discover Stack Wizard as a new step after discovery and before script generation.

### 2.1 Core Concept: Environment Allocation

An allocation binds a **server** (resource) to an **environment** and declares what ports and directories that environment uses on that server.

```
Server: web-01.example.com
├── Environment: dev
│   ├── base_port: 3100
│   ├── app_dir: /opt/app/dev
│   ├── data_dir: /var/data/dev
│   ├── log_dir: /var/log/app/dev
│   └── services:
│       ├── app:        port 3100
│       ├── postgres:   port 5433, data_dir: /var/data/dev/pg
│       └── redis:      port 6380
├── Environment: staging
│   ├── base_port: 3200
│   ├── app_dir: /opt/app/staging
│   ├── data_dir: /var/data/staging
│   ├── log_dir: /var/log/app/staging
│   └── services:
│       ├── app:        port 3200
│       ├── postgres:   port 5434, data_dir: /var/data/staging/pg
│       └── redis:      port 6381
└── Environment: production
    ├── base_port: 3000
    ├── app_dir: /opt/app/production
    ├── data_dir: /var/data/production
    ├── log_dir: /var/log/app/production
    └── services:
        ├── app:        port 3000
        ├── postgres:   port 5432, data_dir: /var/data/production/pg
        └── redis:      port 6379
```

### 2.2 Design Principles

1. **Convention with override** — Smart defaults based on environment name and order (dev → base+100, staging → base+200, prod → base+0). Users can override any value.
2. **Collision detection** — The system validates that no two environments on the same server share a port or directory. Violations are surfaced before deployment, not at runtime.
3. **Discovery-aware** — The Discover Stack Wizard populates initial allocations from what it finds on the server. Existing ports/directories are pre-filled; new environments get suggested values that avoid conflicts.
4. **Template-ready** — Allocations are structured data that script generation (Doc 071 §6) can consume directly. A deploy script template can reference `{{app_port}}` and get the right value per environment.

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
    pub environment_name: String,      // FK → deployment_environment (dev/staging/prod)
    pub base_port: u32,                // Starting port for auto-assignment
    pub app_dir: String,               // e.g., /opt/app/dev
    pub data_dir: String,              // e.g., /var/data/dev
    pub log_dir: String,               // e.g., /var/log/app/dev
    pub config_dir: String,            // e.g., /etc/app/dev
    pub created_at: u64,
    pub updated_at: u64,
    pub is_active: bool,
}
```

**Unique constraint:** `(resource_id, environment_name)` — one allocation per server per environment.

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
    pub description: String,           // Human-readable, e.g., "Main application HTTP"
    pub created_at: u64,
    pub updated_at: u64,
}
```

**Unique constraint:** `(allocation_id, service_name)` — one port assignment per service per allocation. Additionally, a cross-allocation constraint: no two `service_port_assignment` rows on the same `resource_id` (via their parent allocation) may share the same `(port, protocol)`.

### 3.3 New Reducers

```
create_environment_allocation(resource_id, environment_name, base_port, app_dir, data_dir, log_dir, config_dir)
update_environment_allocation(id, base_port?, app_dir?, data_dir?, log_dir?, config_dir?)
delete_environment_allocation(id)

create_service_port_assignment(allocation_id, service_name, port, protocol, data_dir, description)
update_service_port_assignment(id, port?, protocol?, data_dir?, description?)
delete_service_port_assignment(id)
```

### 3.4 Validation Rules (Enforced in Reducers)

1. **Port range:** Ports must be 1–65535. Ports below 1024 emit a warning (require root).
2. **Port uniqueness:** No two service port assignments on the same server (across all environments) may use the same port+protocol combination.
3. **Directory uniqueness:** No two allocations on the same server may share `app_dir`, `data_dir`, `log_dir`, or `config_dir`. Substring containment is also rejected (e.g., `/opt/app` and `/opt/app/dev` conflict because one is a parent of the other).
4. **Environment existence:** `environment_name` must reference an existing `deployment_environment`.
5. **Resource existence:** `resource_id` must reference an existing `deployment_resource`.

### 3.5 Gateway Query Helpers

Added to `gateway/src/deployments/stdb.ts`:

```typescript
export interface EnvironmentAllocation {
  id: string;
  resource_id: string;
  environment_name: string;
  base_port: number;
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
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
  description: string;
  created_at: number;
  updated_at: number;
}

// Get all allocations for a server
async function getAllocationsForResource(cfg, resourceId): Promise<EnvironmentAllocation[]>

// Get allocation for a specific server+environment
async function getAllocation(cfg, resourceId, envName): Promise<EnvironmentAllocation | null>

// Get port assignments for an allocation
async function getPortAssignments(cfg, allocationId): Promise<ServicePortAssignment[]>

// Get ALL port assignments across all environments for a server (for collision detection)
async function getServerPortMap(cfg, resourceId): Promise<Map<number, { env: string; service: string; protocol: string }>>
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

### 4.2 Directory Defaults

Directories follow a `{base}/{environment_name}` pattern:

| Directory | Template | Example (env=staging) |
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
1. Query existing allocations for that server
2. Find the highest used port offsets and directory suffixes
3. Suggest the next available slot
4. Run collision detection before committing

---

## 5. Discovery Wizard Integration

The Discover Stack Wizard (`DiscoverStackWizard.tsx`) currently has these steps:

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
2. **Existing allocations** — if other environments already have allocations on this server, those are loaded to inform collision detection
3. **Default strategy** — any unfilled fields get defaults per §4

#### UI Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Port & Directory Allocation — web-01.example.com               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Environment: [staging ▼]          Base Port: [3100]            │
│                                                                 │
│  ┌─ Directories ──────────────────────────────────────────────┐ │
│  │  Application:  [/opt/myapp/staging        ]                │ │
│  │  Data:         [/var/data/myapp/staging    ]                │ │
│  │  Logs:         [/var/log/myapp/staging     ]                │ │
│  │  Config:       [/etc/myapp/staging         ]                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Service Ports ────────────────────────────────────────────┐ │
│  │  Service       Port    Protocol   Status                   │ │
│  │  ─────────────────────────────────────────────             │ │
│  │  app           3100    TCP        ✓ Available              │ │
│  │  postgres      5433    TCP        ✓ Available              │ │
│  │  redis         6380    TCP        ✓ Available              │ │
│  │  [+ Add Service]                                           │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Other Environments on This Server ────────────────────────┐ │
│  │  production:  app=3000, postgres=5432, redis=6379          │ │
│  │  dev:         app=3200, postgres=5434, redis=6381          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ⚠ No conflicts detected                                       │
│                                                                 │
│                                    [Back]  [Next: Scripts →]    │
└─────────────────────────────────────────────────────────────────┘
```

#### Collision Warnings

If a user enters a port or directory that conflicts with another environment, the UI shows an inline warning:

```
  app           3000    TCP        ⚠ Conflict: used by production
```

The "Next" button is disabled while any hard conflicts exist (same port+protocol on same server). Soft warnings (port < 1024, directory is a parent of another env's directory) allow proceeding but show a confirmation dialog.

### 5.2 Discovery Agent Enhancement

The discovery agent (Doc 071) gains a new tool and an enhancement to its completeness model:

#### New Tool: `check_port_available`

```typescript
interface CheckPortAvailableTool {
  port: number;
  protocol?: "tcp" | "udp";  // default: tcp
}
// Returns: { available: boolean, process?: string, pid?: number }
```

Implemented via `ss -tlnp | grep :PORT` on the target server. Added to the `ALLOWED_SSH_COMMANDS` allowlist.

#### Completeness Model Addition

The completeness model (Doc 071 §5) gains a new section:

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
```

The `generate_plan` API (Doc 071) is extended to accept an `allocation_id` parameter. When present, the plan generator pulls all port and directory values from the allocation record rather than using hardcoded defaults.

---

## 6. API Endpoints

Added to `backend/app/api/v1/deployments.py` and `gateway/src/deployments/`:

### 6.1 Allocation CRUD

```
GET    /api/v1/deployments/allocations?resource_id=X
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
Body:  { resource_id, environment_name, ports: [...], directories: {...} }
Response: { conflicts: [...], warnings: [...] }
```

This endpoint is called by the UI on every field change (debounced 300ms) to provide real-time collision feedback without requiring a save.

### 6.4 Suggest Defaults

```
POST   /api/v1/deployments/allocations/suggest
Body:  { resource_id, environment_name, services: [...] }
Response: { base_port, app_dir, data_dir, log_dir, config_dir, service_ports: [...] }
```

Called when the user selects an environment in the allocate step. Returns suggested defaults that avoid conflicts with existing allocations.

---

## 7. Migration Path

### 7.1 Existing Deployments

Existing deployments have no allocation records. The system handles this gracefully:

- **Script generation** falls back to current behavior (hardcoded or user-provided values) when no allocation exists.
- **A migration banner** appears on the Deployment tab: "Port & directory allocations are now available. Set up allocations for [server] to enable conflict detection and auto-templating."
- **No forced migration** — existing setups continue to work. Allocations are opt-in until a future version makes them required for new deployments.

### 7.2 Schema Migration

The two new SpacetimeDB tables (`environment_allocation`, `service_port_assignment`) are additive — no existing tables are modified. The migration is a simple `spacetime publish` with the updated module.

---

## 8. Implementation Plan

### Phase 1: Data Model & API (1–2 days)
- [ ] Add `environment_allocation` and `service_port_assignment` tables to SpacetimeDB module
- [ ] Add reducers with validation (port range, uniqueness, directory containment)
- [ ] Add gateway query helpers to `stdb.ts`
- [ ] Add REST endpoints for CRUD, conflict check, and suggest
- [ ] Unit tests for collision detection logic

### Phase 2: Discovery Integration (1 day)
- [ ] Add `check_port_available` tool to discovery-tools.ts
- [ ] Extend completeness model with allocation section
- [ ] Update discovery agent prompt to scan for ports and directories
- [ ] Wire discovery findings into allocation suggestions

### Phase 3: Wizard UI (1–2 days)
- [ ] Add "allocate" step to `DiscoverStackWizard.tsx`
- [ ] Build allocation form component with collision indicators
- [ ] Integrate real-time conflict checking via debounced API calls
- [ ] Pre-populate from discovery findings and default strategy
- [ ] Show cross-environment port map for the server

### Phase 4: Script Generation (0.5 day)
- [ ] Extend script templates to consume allocation variables
- [ ] Update `generate_plan` API to accept `allocation_id`
- [ ] Update `ScriptFromDiscoveryWizard.tsx` to pass allocation context

### Phase 5: Polish & Edge Cases (0.5 day)
- [ ] Migration banner for existing deployments
- [ ] Handle server removal (cascade-delete allocations)
- [ ] Handle environment removal (cascade-delete allocations for that env)
- [ ] E2E test: discover → allocate → generate script → verify ports in output

---

## 9. Open Questions

1. **Multi-server environments** — Should an environment allocation span multiple servers (e.g., "staging uses web-01 for the app and db-01 for postgres")? **Recommendation:** No for v1. Each allocation is server-scoped. Cross-server environments are modeled as separate allocations on each server, linked by the shared environment name. A future "environment topology" view could visualize this.

2. **Docker port mapping** — When the app runs in Docker, the container port and host port differ. Should allocations track both? **Recommendation:** Track the host port (what's visible on the server). Container-internal ports are part of the Docker Compose config, not the allocation. The script generator handles the mapping (e.g., `-p 3100:3000`).

3. **Dynamic port ranges** — Some services (e.g., Erlang/Elixir nodes, RPC services) use port ranges rather than single ports. **Recommendation:** Defer to v2. For now, users can add multiple `service_port_assignment` entries (e.g., `epmd: 4369`, `beam-range-start: 9100`, `beam-range-end: 9155`). A proper range type can be added later.

4. **Reverse proxy config generation** — Should the system auto-generate nginx/caddy configs from allocations? **Recommendation:** Yes, but as a separate design doc. The allocation model provides the structured data; config generation is a consumer of that data.

5. **Audit trail** — Should port/directory changes be logged for compliance? **Recommendation:** Yes. The existing `deployment_environment_history` pattern (in `stdb.ts`) should be extended to cover allocation changes. Add `allocation_history` table in a follow-up.

---

## 10. Rejected Alternatives

### 10.1 Environment Variables Only

Store port and directory assignments as key-value pairs in the existing environment config. Rejected because:
- No structure — can't validate port uniqueness across environments
- No type safety — ports are strings, directories are strings, no distinction
- Can't build a visual port map or collision detector from unstructured KV pairs

### 10.2 Per-Service Config Files on the Server

Let each service manage its own port via config files on the server (e.g., `.env` files per environment). Rejected because:
- Bond can't detect or prevent collisions — it doesn't know what's in those files until deployment fails
- Requires SSH access to read/write configs, adding latency and failure modes
- Doesn't help with the wizard flow — the user still has to manually figure out ports

### 10.3 Automatic Port Assignment Without User Visibility

Auto-assign ports behind the scenes without showing the user. Rejected because:
- Users need to know ports for debugging, firewall rules, and external integrations
- "Magic" port numbers cause confusion when users SSH into the server and see unfamiliar ports
- Firewall and security group rules require knowing exact ports upfront
