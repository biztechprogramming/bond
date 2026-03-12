# Design Doc 039: Deployment Agents

**Status:** Draft  
**Date:** 2026-03-12  
**Depends on:** 035 (Secure Agent Execution Architecture), 036 (Permission Broker)

---

## 1. The Problem

Bond agents today can write code, create PRs, and run tests — but there is no structured deployment pipeline. Deploying changes to environments (Dev, QA, Staging, UAT, Prod) is a manual process that happens outside the agent system. This creates gaps:

- No audit trail for what was deployed where and when.
- No structured promotion flow — changes jump between environments ad hoc.
- No separation of concerns — the same agents that write code could theoretically deploy it.
- No environment-aware agents that understand the health and state of a specific target.

This document describes a deployment agent architecture where:

- **Deployment agents cannot modify code.** They can only read it for troubleshooting.
- **Each environment gets its own agent** with isolated permissions.
- **Deployment scripts are environment-agnostic** but access-controlled per environment.
- **Promotion is user-controlled** — agents propose, humans approve.
- **Agents cannot self-escalate** — they have no write access to the promotion database.

---

## 2. Architecture Overview

**Core security principle: The entire `~/.bond/deployments/` directory is HOST-ONLY. It is never mounted into any agent container. The broker and Promotion API are the only code paths that touch deployment scripts, secrets, and promotion state. Agents interact exclusively through the broker's `/deploy` endpoint by sending a script ID — never a command string, never a file path.**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  BOND HOST                                                               │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  Gateway Process                                                  │    │
│  │                                                                   │    │
│  │  ┌─────────────────────┐    ┌──────────────────────────────┐     │    │
│  │  │  Promotion API       │    │  Broker /deploy endpoint      │    │    │
│  │  │  POST /promote       │    │  POST /broker/deploy          │    │    │
│  │  │                      │    │                               │    │    │
│  │  │  Auth: user session  │    │  Auth: agent broker token     │    │    │
│  │  │  (rejects agent      │──►│  Reads: promotion DB          │    │    │
│  │  │   tokens)            │    │  Reads: script from registry  │    │    │
│  │  │                      │    │  Reads: env secrets           │    │    │
│  │  │  Writes: promotion DB │    │  Executes: script on host    │    │    │
│  │  │  Writes: env config  │    │  Returns: stdout/stderr      │    │    │
│  │  └─────────────────────┘    └──────────────────────────────┘     │    │
│  │              │                              │                      │    │
│  │              ▼                              ▼                      │    │
│  │  ┌───────────────────────────────────────────────────────────┐    │    │
│  │  │  ~/.bond/deployments/  (HOST ONLY — NEVER MOUNTED)        │    │    │
│  │  │  ├── scripts/registry/   (immutable snapshots)            │    │    │
│  │  │  Database: environments, promotions, approvals (Gateway)   │    │    │
│  │  │  ├── secrets/{env}.yaml  (encrypted at rest)              │    │    │
│  │  │  ├── receipts/{env}/     (append-only)                    │    │    │
│  │  │  └── locks/{env}.lock                                     │    │    │
│  │  └───────────────────────────────────────────────────────────┘    │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │ Deploy Agent  │ │ Deploy Agent  │ │ Deploy Agent  │ │ Deploy Agent  │  │
│  │ ENV: dev      │ │ ENV: qa       │ │ ENV: staging  │ │ ENV: uat      │  │
│  │               │ │               │ │               │ │               │  │
│  │ RO: code only │ │ RO: code only │ │ RO: code only │ │ RO: code only │  │
│  │ (workspaces)  │ │ (workspaces)  │ │ (workspaces)  │ │ (workspaces)  │  │
│  │               │ │               │ │               │ │               │  │
│  │ NO scripts    │ │ NO scripts    │ │ NO scripts    │ │ NO scripts    │  │
│  │ NO secrets    │ │ NO secrets    │ │ NO secrets    │ │ NO secrets    │  │
│  │ NO deploy DB  │ │ NO deploy DB  │ │ NO deploy DB  │ │ NO deploy DB  │  │
│  │               │ │               │ │               │ │               │  │
│  │ Talks to      │ │ Talks to      │ │ Talks to      │ │ Talks to      │  │
│  │ broker only   │ │ broker only   │ │ broker only   │ │ broker only   │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
│                                                                          │
│  ┌──────────────┐                                                        │
│  │ Deploy Agent  │    ┌────────────────────────────────────────────┐     │
│  │ ENV: prod     │    │ Code Agents (existing)                     │     │
│  │               │    │ Workspaces mounted RO into deploy agents   │     │
│  │ NO scripts    │    │ No access to deployment broker endpoints   │     │
│  │ NO secrets    │    └────────────────────────────────────────────┘     │
│  │ broker only   │                                                       │
│  └──────────────┘                                                        │
└──────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────────────────┐
                    │  Frontend (Promotion UI)          │
                    │                                    │
                    │  [Dev ✅] → [QA ⏳] → [Stg ○]     │
                    │       [Promote to QA]              │
                    │       [Promote to All]             │
                    │                                    │
                    │  Health dashboard per environment  │
                    │  Deployment receipts viewer        │
                    │  Drift alerts                      │
                    └──────────────────────────────────┘
```

---

## 3. Environments

### 3.1 Storage: SpacetimeDB, API-Managed

Environment definitions are **not stored in config files.** They are managed exclusively through the Gateway API, stored in SpacetimeDB — the same database Bond uses for agents, conversations, settings, and everything else. This gives them the same security properties as promotion state — agents cannot read or modify them.

**Why SpacetimeDB?**
- **One data layer.** Bond already uses SpacetimeDB for all state. Deployment state shouldn't be different.
- **Real-time subscriptions.** The frontend gets instant updates when promotion state changes, approvals come in, deployments complete — no polling.
- **Agents can't access it.** As of 2026-03-12, `SPACETIMEDB_TOKEN` is no longer injected into agent containers (§20.2). Only the Gateway process has the token.

**Why not YAML?** A config file on the host filesystem is the weakest link. If an agent ever gets host-level access (through a broker bug, exploit, or misconfigured policy), it could modify environment definitions. API-managed config with user-session auth closes this gap.

**Why not SQLite?** It would work for security, but it's a second data store. No real-time subscriptions for the frontend. And you'd eventually migrate to SpacetimeDB anyway.

### 3.2 Environment Schema (SpacetimeDB Tables)

```rust
// SpacetimeDB module — bond-core (or new deployment tables in existing module)

#[spacetimedb::table(name = deployment_environments, public)]
pub struct DeploymentEnvironment {
    #[primary_key]
    pub name: String,                    // 'dev', 'qa', 'staging', 'uat', 'prod'
    pub display_name: String,            // 'Development', 'QA', etc.
    pub order: u32,                      // promotion order (1 = first)
    pub is_active: bool,                 // soft delete

    // Deployment settings
    pub max_script_timeout: u32,         // seconds (default 600)
    pub health_check_interval: u32,      // seconds (default 300)

    // Deployment window (empty string = no restrictions)
    pub window_days: String,             // JSON array: '["mon","tue","wed","thu","fri"]'
    pub window_start: String,            // "06:00" or ""
    pub window_end: String,              // "22:00" or ""
    pub window_timezone: String,         // "America/New_York"

    // Approvals
    pub required_approvals: u32,         // how many approvers needed (default 1)

    pub created_at: u64,                 // unix millis
    pub updated_at: u64,
}

#[spacetimedb::table(name = deployment_environment_approvers, public)]
pub struct DeploymentEnvironmentApprover {
    #[primary_key]
    pub id: u64,                         // auto-increment
    pub environment_name: String,
    pub user_id: String,                 // Bond user ID or GitHub username
    pub added_at: u64,
    pub added_by: String,                // who added this approver
}

#[spacetimedb::table(name = deployment_environment_history, public)]
pub struct DeploymentEnvironmentHistory {
    #[primary_key]
    #[auto_inc]
    pub id: u64,
    pub environment_name: String,
    pub action: String,                  // 'created', 'updated', 'deactivated', etc.
    pub changed_by: String,              // user session identity
    pub changed_at: u64,
    pub before_state: String,            // JSON snapshot
    pub after_state: String,             // JSON snapshot
}
```

**Reducers** (write operations — called by Gateway only):

```rust
#[spacetimedb::reducer]
pub fn create_deployment_environment(ctx: &ReducerContext, env: DeploymentEnvironment) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn update_deployment_environment(ctx: &ReducerContext, name: String, updates: String) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn deactivate_deployment_environment(ctx: &ReducerContext, name: String, changed_by: String) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn add_deployment_approver(ctx: &ReducerContext, environment_name: String, user_id: String, added_by: String) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn remove_deployment_approver(ctx: &ReducerContext, environment_name: String, user_id: String) -> Result<(), String> { ... }
```

**Note:** SpacetimeDB does not have per-table access control. Security is enforced at the Gateway API layer — only user session tokens can trigger these reducers via the Gateway. Agents don't have the SpacetimeDB token (removed from containers) so they can't call reducers directly.

### 3.3 Environment Management API

All endpoints require **user session auth** — agent broker tokens are rejected.

```
# List all environments (ordered)
GET /api/v1/deployments/environments
→ [{ name, display_name, order, is_active, window, approvers, required_approvals, ... }]

# Create a new environment
POST /api/v1/deployments/environments
Authorization: Bearer <user-session-token>
{
  "name": "staging",
  "display_name": "Staging",
  "order": 3,
  "max_script_timeout": 1200,
  "health_check_interval": 600,
  "deployment_window": {
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "start": "06:00",
    "end": "22:00",
    "timezone": "America/New_York"
  },
  "required_approvals": 1,
  "approvers": []
}

# Update an environment
PUT /api/v1/deployments/environments/:name
Authorization: Bearer <user-session-token>
{ "max_script_timeout": 1800, "required_approvals": 2 }

# Deactivate an environment (soft delete — preserves history)
DELETE /api/v1/deployments/environments/:name
Authorization: Bearer <user-session-token>

# Manage approvers
POST   /api/v1/deployments/environments/:name/approvers   { "user_id": "sarah" }
DELETE /api/v1/deployments/environments/:name/approvers/:user_id
```

**Validation on create/update:**
- `name` must be lowercase alphanumeric + hyphens, unique
- `order` must be unique across active environments
- `deployment_window` times must be valid, end > start
- `required_approvals` must be ≤ number of approvers (or 1 if no approvers = owner-only)
- History record written for every mutation

### 3.4 Default Environments (Bootstrap)

On first run (empty database), the Gateway seeds default environments:

```typescript
const DEFAULT_ENVIRONMENTS = [
  { name: "dev",     display_name: "Development", order: 1, max_script_timeout: 600,  health_check_interval: 300 },
  { name: "qa",      display_name: "QA",          order: 2, max_script_timeout: 900,  health_check_interval: 300 },
  { name: "staging", display_name: "Staging",      order: 3, max_script_timeout: 1200, health_check_interval: 600,
    window_days: '["mon","tue","wed","thu","fri"]', window_start: "06:00", window_end: "22:00" },
  { name: "uat",     display_name: "UAT",          order: 4, max_script_timeout: 1200, health_check_interval: 600,
    window_days: '["mon","tue","wed","thu","fri"]', window_start: "06:00", window_end: "22:00" },
  { name: "prod",    display_name: "Production",    order: 5, max_script_timeout: 1800, health_check_interval: 60,
    window_days: '["tue","wed","thu"]', window_start: "09:00", window_end: "16:00" },
];
```

These can be modified or deleted immediately via the UI/API. They're just starting points.

### 3.5 CLI Escape Hatch (Bootstrap & Disaster Recovery)

For initial setup, migration, or recovery when the system isn't running:

```bash
# Export current config to YAML (backup / human review)
bond environments export > environments-backup.yaml

# Import from YAML (bootstrap / restore)
bond environments import environments-backup.yaml

# Quick-add from command line
bond environments add staging --display-name "Staging" --order 3
```

The CLI writes directly to the database — same validation as the API. Export/import is for backup and portability, not day-to-day management.

### 3.6 Adding/Removing Environments

**Adding** (via UI or API):

1. User creates environment via `POST /api/v1/deployments/environments`
2. Gateway validates and stores in database
3. Gateway spawns a new deployment agent container with the appropriate broker token
4. Broker registers the new environment in its deploy handler (reads from DB)
5. New environment appears in the promotion pipeline UI
6. History record: `{ action: "created", changed_by: "andrew", after_state: {...} }`

**Removing** (via UI or API):

1. User deactivates environment via `DELETE /api/v1/deployments/environments/:name`
2. Gateway soft-deletes (sets `is_active = false`) — preserves all history and receipts
3. Gateway tears down the deployment agent container
4. Broker stops accepting deploy requests for this environment
5. Environment grayed out in UI with "Deactivated" label — receipts still viewable
6. History record: `{ action: "deactivated", changed_by: "andrew", before_state: {...} }`

**Reactivating:**

```
PUT /api/v1/deployments/environments/staging
{ "is_active": true }
```

Brings back the environment, spawns a new agent, resumes accepting deploys. Full history preserved.

### 3.7 How the Broker Reads Environment Config

The broker's `/deploy` endpoint needs to know environment settings (deployment windows, timeouts, etc.). It reads from the database, not from a file:

```typescript
// In broker deploy handler
async function handleDeploy(req) {
  const agent = validateToken(req.token);
  const env = deriveEnvironment(agent.sub);

  // Read environment config from SpacetimeDB
  const rows = await stdb.query(
    `SELECT * FROM deployment_environments WHERE name = '${env}' AND is_active = true`
  );
  const envConfig = rows[0];

  if (!envConfig) {
    return { status: "denied", reason: "Environment not found or deactivated" };
  }

  // Check deployment window
  if (envConfig.window_days && !isWithinWindow(envConfig)) {
    return { status: "denied", reason: "Outside deployment window" };
  }

  // Check script timeout against environment max
  const timeout = Math.min(req.body.timeout || 60, envConfig.max_script_timeout);

  // ... continue with promotion check, execution, etc.
}
```

The database is on the host, read by the Gateway process. Agents never see it.

---

## 4. Deployment Agents

### 4.1 Agent Identity

Each deployment agent is a Bond agent with a specific role and constrained permissions.

```
Agent ID format: deploy-{environment}
Examples: deploy-dev, deploy-qa, deploy-staging, deploy-uat, deploy-prod
```

### 4.2 What Deployment Agents CAN Do

- **Read code** from all agent workspace volumes (read-only mounts)
- **Execute deployment scripts** that have been promoted to their environment
- **Run health checks** against their environment
- **Run dry-run / validation** on scripts before execution
- **File bug tickets** (GitHub issues) with detailed diagnostic information
- **Read deployment receipts** from lower environments (context passing)
- **Report deployment status** (started, succeeded, failed, rolled back)

### 4.3 What Deployment Agents CANNOT Do

- **Modify code.** All workspace mounts are read-only.
- **Run scripts not promoted to their environment.** The broker policy restricts execution to scripts in the agent's environment directory.
- **Promote scripts.** Promotion is a user action via the UI/API. Agents have no write access to the promotion database.
- **Access secrets for other environments.** The Dev agent cannot see Prod connection strings.
- **Modify deployment receipts or audit logs.** These are append-only, outside agent mounts.
- **Override deployment windows.** If it's outside the window, the agent waits or reports that it can't deploy.

### 4.4 Container Configuration

```yaml
# Per deployment agent container
volumes:
  # Read-only access to all unique agent workspace mounts (for troubleshooting)
  - /home/andrew/bond:/workspaces/bond:ro
  - /home/andrew/other-repo:/workspaces/other-repo:ro
  # ... (deduplicated — if 3 agents mount /bond, it appears once)

  # Agent's own scratch space (writable — for temp files, local logs)
  - deploy-{env}-data:/data:rw

  # NOTHING from ~/.bond/deployments/
  # No scripts. No state. No secrets. No registry. No receipts.
  # All deployment operations go through the broker.

environment:
  BOND_DEPLOY_ENV: "{env}"
  BOND_BROKER_TOKEN: "{agent-scoped-token}"
  BOND_BROKER_URL: "http://host.docker.internal:18789"
```

### 4.5 What The Agent Can See vs. What It Can't

| Visible to agent | NOT visible to agent |
|---|---|
| Code workspaces (read-only) | Deployment scripts (host-only) |
| Its own scratch space (writable) | Promotion state (host-only) |
| Broker URL + its own token | Environment secrets (host-only) |
| Its environment name | Other environments' secrets |
| Deployment receipts (via broker) | Script registry (host-only) |
| Health check results (via broker) | Other agents' tokens |

### 4.6 Workspace Mount Deduplication

Multiple code agents may mount the same host directory. Deployment agents receive a deduplicated set:

```python
def collect_workspace_mounts(running_agents: list[AgentConfig]) -> list[Mount]:
    """Collect unique workspace mounts from all running agents."""
    seen: set[str] = set()
    mounts: list[Mount] = []
    for agent in running_agents:
        for mount in agent.workspace_mounts:
            host_path = os.path.realpath(mount.host_path)
            if host_path not in seen:
                seen.add(host_path)
                mounts.append(Mount(
                    host_path=host_path,
                    container_path=f"/workspaces/{os.path.basename(host_path)}",
                    read_only=True,
                ))
    return mounts
```

---

## 5. Deployment Scripts

### 5.1 Script Structure

Deployment scripts are environment-agnostic. They receive the target environment as a parameter and use environment-specific configuration (connection strings, endpoints, etc.) injected by the broker.

```bash
#!/usr/bin/env bash
# deploy-scripts/001-migrate-user-table.sh
#
# meta:name: Migrate user table — add email_verified column
# meta:version: 1
# meta:depends_on: none
# meta:timeout: 300
# meta:rollback: 001-migrate-user-table-rollback.sh

set -euo pipefail

ENV="${BOND_DEPLOY_ENV:?Environment not set}"
echo "Running migration on environment: $ENV"

# Environment-specific config is injected by the broker
DB_URL="${DEPLOY_DB_URL:?Database URL not set}"

# The actual work
echo "Applying migration..."
# SCRIPT_DIR is set by the broker to the script's registry directory on the host
psql "$DB_URL" -f "$SCRIPT_DIR/sql/001-add-email-verified.sql"

echo "Verifying..."
RESULT=$(psql "$DB_URL" -t -c "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='email_verified'")

if [[ -z "$RESULT" ]]; then
    echo "ERROR: Column not found after migration"
    exit 1
fi

echo "Migration successful"
```

### 5.2 Script Metadata

Each script includes metadata as comments (parsed by the system):

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Human-readable description |
| `version` | Yes | Incremented on script changes |
| `depends_on` | No | Script ID(s) that must run first |
| `timeout` | No | Max execution time in seconds (overrides env default) |
| `rollback` | No | Path to rollback script (relative to script dir) |
| `dry_run` | No | If `true`, script supports `--dry-run` flag |
| `health_check` | No | Path to post-deployment health check script |

### 5.3 Script Validation

Before a script runs — even in Dev — it goes through validation:

```
1. Syntax check     — bash -n / shellcheck (for shell scripts)
2. Metadata parse   — all required fields present
3. Dependency check  — depends_on scripts have already run in this environment
4. Timeout check    — script timeout ≤ environment max_script_timeout
5. Dry-run          — if script supports --dry-run, execute it first
```

If validation fails, the agent reports the error and does not execute.

### 5.4 Rollback Scripts

Rollback scripts follow the same structure but are linked to their parent deployment script via the `rollback` metadata field.

```bash
#!/usr/bin/env bash
# deploy-scripts/001-migrate-user-table-rollback.sh
#
# meta:name: Rollback — remove email_verified column
# meta:version: 1
# meta:timeout: 300

set -euo pipefail

ENV="${BOND_DEPLOY_ENV:?Environment not set}"
DB_URL="${DEPLOY_DB_URL:?Database URL not set}"

echo "Rolling back migration on environment: $ENV"
psql "$DB_URL" -c "ALTER TABLE users DROP COLUMN IF EXISTS email_verified"
echo "Rollback complete"
```

Rollback scripts are promoted alongside their parent script. If Script A is available in QA, its rollback script is also available in QA.

### 5.5 Pre/Post Hooks

Environment-specific hooks run before and after every deployment script:

```
~/.bond/deployments/hooks/
├── dev/
│   ├── pre_deploy.sh      # e.g., start local services
│   └── post_deploy.sh     # e.g., seed test data
├── qa/
│   ├── pre_deploy.sh      # e.g., snapshot DB
│   └── post_deploy.sh     # e.g., run integration tests
├── staging/
│   ├── pre_deploy.sh      # e.g., open VPN tunnel
│   └── post_deploy.sh     # e.g., run smoke tests
├── uat/
│   ├── pre_deploy.sh
│   └── post_deploy.sh
└── prod/
    ├── pre_deploy.sh      # e.g., enable maintenance mode
    └── post_deploy.sh     # e.g., disable maintenance mode, notify team
```

Hooks are optional. If a hook doesn't exist for an environment, it's skipped. Hook failures abort the deployment (pre) or trigger an alert (post).

---

## 6. Promotion Pipeline

### 6.1 Core Principle

**Agents cannot promote scripts. Only users can.**

The promotion state lives in SpacetimeDB — the same database Bond uses for everything else. Agents don't have the SpacetimeDB token (removed from containers as of §20.2), so they have no access. The Gateway's Promotion API is the only writer, and it only accepts requests from authenticated UI sessions — not from agent broker tokens.

### 6.2 Promotion Lifecycle

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│   Dev   │───►│   QA    │───►│ Staging │───►│   UAT   │───►│  Prod   │
│         │    │         │    │         │    │         │    │         │
│ Script  │    │ Script  │    │ Script  │    │ Script  │    │ Script  │
│ created │    │promoted │    │promoted │    │promoted │    │promoted │
│ here    │    │ by user │    │ by user │    │ by user │    │ by user │
│         │    │         │    │         │    │         │    │         │
│ Tests → │    │ Tests → │    │ Tests → │    │ Tests → │    │ Tests → │
│ Report  │    │ Report  │    │ Report  │    │ Report  │    │ Report  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘

User sees results at each stage.
User clicks [Promote] to advance.
User can click [Promote to All] to advance through all remaining environments.
```

### 6.3 Script Snapshots (Immutability)

When a script is registered, the Gateway creates an immutable snapshot in the host-only registry:

```
~/.bond/deployments/scripts/
└── registry/                        # HOST-ONLY — never mounted into containers
    ├── 001-migrate-user-table/
    │   ├── v1/
    │   │   ├── deploy.sh           # the script
    │   │   ├── rollback.sh         # rollback script
    │   │   ├── sql/                # supporting files
    │   │   │   └── 001-add-email-verified.sql
    │   │   ├── manifest.json       # parsed metadata + SHA-256 hash
    │   │   └── .sha256             # hash of all files in this version
    │   └── v2/                     # new version = new snapshot
    │       └── ...
    └── 002-add-notifications/
        └── v1/
            └── ...
```

There are **no per-environment directories or symlinks.** Environment access is controlled entirely by the promotion database — which only the Gateway writes via the Promotion API. When an agent asks the broker to deploy script `001-migrate`, the broker:

1. Queries the `deployment_promotions` table to check if that script is promoted to the agent's environment
2. If yes, reads the script from the registry and executes it on the host
3. If no, denies the request

The script content is immutable once snapshotted. The `.sha256` file contains a hash of the entire script bundle. The **broker** verifies this hash before execution (not the agent — the agent never sees the script). If someone tampers with the registry on the host filesystem, the hash check fails and the broker refuses to execute.

### 6.4 Promotion State (SpacetimeDB)

Promotion state lives in SpacetimeDB alongside environment config. Same security model: Gateway-only access, agents have no token.

```rust
#[spacetimedb::table(name = deployment_promotions, public)]
pub struct DeploymentPromotion {
    #[primary_key]
    pub id: u64,                         // auto-increment
    pub script_id: String,
    pub script_version: String,
    pub script_sha256: String,
    pub environment_name: String,

    // Status: 'not_promoted', 'awaiting_approvals', 'promoted',
    //         'deploying', 'success', 'failed', 'rolled_back'
    pub status: String,

    // Promotion metadata
    pub initiated_by: String,            // user who started the promotion
    pub initiated_at: u64,               // unix millis
    pub promoted_at: u64,                // when all approvals were met (0 = not yet)
    pub deployed_at: u64,                // when deployment completed (0 = not yet)
    pub receipt_id: String,              // link to receipt (empty = none)
}

#[spacetimedb::table(name = deployment_approvals, public)]
pub struct DeploymentApproval {
    #[primary_key]
    pub id: u64,
    pub script_id: String,
    pub script_version: String,
    pub environment_name: String,
    pub user_id: String,                 // who approved
    pub approved_at: u64,
}

// Reducers (called by Gateway only)

#[spacetimedb::reducer]
pub fn initiate_promotion(ctx: &ReducerContext, script_id: String, script_version: String,
    script_sha256: String, environment_name: String, initiated_by: String) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn record_approval(ctx: &ReducerContext, script_id: String, script_version: String,
    environment_name: String, user_id: String) -> Result<(), String> { ... }

#[spacetimedb::reducer]
pub fn update_promotion_status(ctx: &ReducerContext, script_id: String, script_version: String,
    environment_name: String, status: String, receipt_id: String) -> Result<(), String> { ... }
```

The broker's `/deploy` endpoint queries SpacetimeDB to check promotion status:

```typescript
// Broker deploy handler — checking promotion status
const rows = await stdb.query(
  `SELECT status FROM deployment_promotions
   WHERE script_id = '${scriptId}' AND script_version = '${version}'
   AND environment_name = '${env}'`
);

if (!promotion || promotion.status === 'not_promoted' || promotion.status === 'awaiting_approvals') {
  return { status: "denied", reason: "Script not promoted to this environment" };
}
```

### 6.5 Promotion API (With Approvals)

**Initiating a promotion:**

```
POST /api/v1/deployments/promote
Authorization: Bearer <user-session-token>  (NOT agent token)
Content-Type: application/json

{
  "script_id": "001-migrate-user-table",
  "version": "v1",
  "target_environments": ["qa"],         // or ["qa", "staging", "uat", "prod"] for promote-all
  "force": false                          // skip prerequisite checks (emergency only)
}
```

**What happens depends on `required_approvals` for the target environment:**

```
required_approvals = 1, approvers = [] (owner only):
  → You click Promote → promotion executes immediately
  → Same as today — single click, done

required_approvals = 1, approvers = ["andrew", "sarah"]:
  → Either of you clicks Promote → promotion executes immediately
  → Any listed approver is sufficient

required_approvals = 2, approvers = ["andrew", "sarah", "mike"]:
  → You click Promote → your approval is recorded
  → Status becomes "awaiting_approvals" (1/2)
  → Notification sent to remaining approvers
  → Sarah clicks Approve → threshold met (2/2)
  → Status becomes "promoted", agent notified
```

**Approving a pending promotion:**

```
POST /api/v1/deployments/promote/approve
Authorization: Bearer <user-session-token>

{
  "script_id": "001-migrate-user-table",
  "version": "v1",
  "environment": "prod"
}
```

**Validation before promotion:**

1. Script exists in registry at specified version
2. Script has been successfully deployed in the previous environment (unless `force: true`)
3. Target environment is active
4. Target environment health check is passing
5. No active deployment lock on target environment
6. Within deployment window (if configured) — warns if outside, doesn't block promotion (blocks execution)
7. Request comes from a user session, not an agent token
8. User is in the approvers list (or is the owner if approvers list is empty)
9. User hasn't already approved this promotion (no double-counting)
10. Required approval threshold check

**Response (immediate promotion — threshold met):**
```json
{
  "status": "promoted",
  "promoted": ["qa"],
  "skipped": [],
  "errors": [],
  "message": "Script 001-migrate-user-table v1 promoted to qa"
}
```

**Response (awaiting more approvals):**
```json
{
  "status": "awaiting_approvals",
  "environment": "prod",
  "approvals": { "received": 1, "required": 2 },
  "approved_by": ["andrew"],
  "pending_approvers": ["sarah", "mike"],
  "message": "Approval recorded. 1 more approval needed for prod."
}
```

### 6.6 Promote-to-All

When the user clicks "Promote to All," the API processes each environment sequentially in order:

1. For each target environment (in promotion order):
   a. Run validation checks
   b. If `required_approvals` = 1 and user is authorized → promote immediately
   c. If `required_approvals` > 1 → record user's approval, set status to "awaiting_approvals"
   d. If previous environment hasn't completed → skip (can't promote to Staging if QA hasn't deployed)

2. Response shows per-environment results:

```json
{
  "results": {
    "qa": { "status": "promoted", "message": "Promoted (1/1 approvals)" },
    "staging": { "status": "promoted", "message": "Promoted (1/1 approvals)" },
    "uat": { "status": "awaiting_approvals", "message": "Approval recorded (1/2 needed)" },
    "prod": { "status": "awaiting_approvals", "message": "Approval recorded (1/2 needed)" }
  }
}
```

This is not automatic deployment — it's automatic *access*. The script becomes available in each environment (once approvals are met), and each environment's agent picks it up and runs it. The user is giving permission for the script to be available, not triggering immediate execution.

For environments that require multiple approvals, "Promote to All" records the initiator's approval for every environment at once. Other approvers still need to approve each environment individually (or use their own "Approve All" action).

---

## 7. Authentication Separation: Why Agents Can't Self-Promote

This is the most important security property of the system. There are two completely separate authentication paths that never cross.

### 7.1 Two Token Types, Two Signing Keys

```
USER SESSION TOKEN                     AGENT BROKER TOKEN
─────────────────                      ──────────────────
Issued by: Gateway on user login       Issued by: Gateway at container creation
Signed with: User session secret       Signed with: Broker HMAC secret
  (~/.bond/data/.session_secret)         (~/.bond/data/.broker_secret)
Payload: { user_id, role, exp }        Payload: { sub: "deploy-qa", sid, exp }
Accepted by: Promotion API             Accepted by: Broker /deploy + /exec
Rejected by: Broker endpoints          Rejected by: Promotion API
```

The tokens are structurally different (different payload schemas, different signing keys). An agent cannot forge a user session token because it doesn't have the session signing secret. The session secret is in a host-only file that is never mounted into containers.

### 7.2 What Each Token Grants

| Action | User Session Token | Agent Broker Token |
|---|---|---|
| Promote a script | ✅ | ❌ 403 Forbidden |
| Request script deployment | ❌ | ✅ (if promoted to agent's env) |
| Pause/resume an agent | ✅ | ❌ |
| View promotion state | ✅ (via UI) | ❌ (only broker reads it internally) |
| File a bug ticket | ❌ (use GitHub directly) | ✅ (via broker exec) |
| Override deployment window | ✅ | ❌ |

### 7.3 What If an Agent Tries to Self-Promote

| Attack Vector | Why It Fails |
|---|---|
| Call Promotion API directly | API validates token type → agent token rejected → 403 |
| Forge a user session token | Different signing key (`session_secret` vs `broker_secret`). Agent doesn't have `session_secret` — it's a host-only file, never mounted. |
| Modify promotion database via broker exec | Database is managed by Gateway process. No SQL client available via broker exec. Catch-all deny on generic exec. |
| Modify promotion database via filesystem | Database file is on the host, not mounted. Agent's container has no path to it. |
| Ask broker to manipulate promotion records | `/broker/deploy` only reads the database — it has no write path for promotion status. Only the Promotion API writes promotions. |
| Call broker /deploy with a non-promoted script | Broker queries promotion DB → script not promoted to this env → denied. |
| Call broker /deploy pretending to be another environment | Environment derived from token payload (`sub: "deploy-qa"` → env: "qa"). Token is HMAC-signed. Agent can't modify it. |

---

## 8. Broker Endpoint Policies (Per-Environment)

### 8.1 Two Broker Endpoints, Two Policy Layers

Deployment agents interact with two broker endpoints:

1. **`POST /broker/deploy`** — The dedicated deployment endpoint. Access control is handled internally by the broker: it validates the agent's token, derives the environment, and queries the promotion database for status. No glob-pattern policy needed — the broker code IS the policy.

2. **`POST /broker/exec`** — The generic command executor (from Doc 036). Used only for non-deployment tasks like filing bug tickets (`gh issue create`). This endpoint still uses glob-pattern policies.

### 8.2 /broker/deploy Access Control (Built Into Broker Code)

The `/deploy` endpoint does NOT use the glob-pattern policy engine. It has its own hardcoded access control:

```typescript
// Pseudocode — broker deploy handler
async function handleDeploy(req) {
  const agent = validateToken(req.token);        // → { sub: "deploy-qa", sid: "..." }
  const env = deriveEnvironment(agent.sub);      // → "qa"
  const { script_id, action } = req.body;

  // Is this script promoted to this agent's environment?
  const promotion = await stdb.query(...);         // queries SpacetimeDB
  const scriptState = state.scripts[script_id]?.environments[env];

  if (!scriptState || scriptState.status === "not_promoted") {
    return { status: "denied", reason: "Script not promoted to this environment" };
  }

  // Environment derived from token, NOT from request body.
  // Agent cannot override its environment.
  switch (action) {
    case "deploy":   return executeScript(script_id, env);
    case "rollback": return executeRollback(script_id, env);
    case "dry-run":  return executeDryRun(script_id, env);
    case "validate": return validateScript(script_id, env);
    case "pre-hook": return executeHook("pre", env);
    case "post-hook":return executeHook("post", env);
    case "health-check": return executeHealthCheck(env);
    case "info":     return getScriptInfo(script_id, env);
    case "receipt":  return getReceipt(script_id, req.body.environment);
  }
}
```

The agent cannot:
- Specify a different environment (derived from token)
- Run a script that hasn't been promoted (checked against promotion DB)
- Access the script content (only the broker reads and executes it)
- Access secrets (only the broker loads and injects them)

### 8.3 /broker/exec Policy (For Non-Deployment Commands)

The generic exec endpoint is locked down for deployment agents. They can only use it for bug tickets and read-only diagnostics:

```yaml
# ~/.bond/policies/agents/deploy-dev.yaml
version: "1"
name: deploy-dev
agent_id: "deploy-dev"

rules:
  # Can create GitHub issues (bug tickets)
  - commands: ["gh issue create*"]
    decision: allow

  # Deny everything else on the generic exec endpoint
  # All deployment operations go through /broker/deploy, not /broker/exec
  - commands: ["*"]
    decision: deny
    reason: "Deployment agents use /broker/deploy for deployments. Generic exec denied."
```

```yaml
# ~/.bond/policies/agents/deploy-prod.yaml
version: "1"
name: deploy-prod
agent_id: "deploy-prod"

rules:
  # Can create GitHub issues (bug tickets)
  - commands: ["gh issue create*"]
    decision: allow

  # Deny everything else
  - commands: ["*"]
    decision: deny
    reason: "Deployment agents use /broker/deploy for deployments. Generic exec denied."
```

**Note:** Deployment agents do NOT get `cat`, `curl`, `psql`, or any other diagnostic commands via the generic exec endpoint. If they need to run diagnostics, that capability is added to the `/broker/deploy` endpoint as a specific action (e.g., `action: "diagnose"`) with its own access control — not as a raw shell command.

### 8.4 Environment Secrets

The broker injects environment-specific secrets when executing deployment scripts. The agent never sees the raw values — they appear as environment variables only during script execution.

```yaml
# ~/.bond/deployments/secrets/dev.yaml (encrypted at rest)
DEPLOY_DB_URL: "postgresql://dev-user:xxx@dev-db:5432/app"
DEPLOY_API_URL: "https://dev-api.example.com"
DEPLOY_API_KEY: "dev-key-xxx"

# ~/.bond/deployments/secrets/prod.yaml
DEPLOY_DB_URL: "postgresql://prod-user:xxx@prod-db:5432/app"
DEPLOY_API_URL: "https://api.example.com"
DEPLOY_API_KEY: "prod-key-xxx"
```

The broker reads the appropriate secrets file based on the agent's environment and injects them into the command execution environment. The Dev agent's broker policy can never trigger loading of `prod.yaml`.

---

## 9. Deployment Execution Flow

### 9.1 Happy Path

```
1. User promotes script "001-migrate-user-table" to QA via UI
   └── Promotion API updates promotion DB: qa.status = "promoted"
   └── Sends notification to deploy-qa agent: "New script available"

2. deploy-qa agent receives notification
   └── Agent calls broker: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "info" }
   └── Broker queries promotion DB → promoted to qa → returns metadata
         (name, version, depends_on, timeout, dry_run support)
   └── Agent calls broker: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "receipt",
           environment: "dev" }
   └── Broker returns Dev deployment receipt (context passing)

3. Validation phase (ALL done by the broker, not the agent)
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "validate" }
   └── Broker performs:
       - Syntax check: bash -n on the script (host-side)
       - Dependency check: depends_on scripts deployed in qa? → pass
       - Hash check: SHA-256 matches manifest → pass
       - Deployment window check: within allowed hours → pass
       - Deployment lock: acquire lock for QA environment
   └── Broker returns validation result to agent

4. Pre-deploy hook (broker executes on host)
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "pre-hook" }
   └── Broker runs hooks/qa/pre_deploy.sh on host
   └── (e.g., snapshot database)

5. Dry-run (if script supports it)
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "dry-run" }
   └── Broker runs the script with --dry-run flag on host
   └── Agent reviews returned output for warnings

6. Execution
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "deploy" }
   └── Broker loads secrets/qa.yaml, injects as env vars
   └── Broker runs the script on host with secrets
   └── Script runs, exits 0
   └── Broker returns stdout/stderr/exit_code to agent

7. Post-deploy hook
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "post-hook" }
   └── Broker runs hooks/qa/post_deploy.sh on host
   └── (e.g., run integration tests)

8. Health check
   └── Agent calls: POST /broker/deploy
         { script_id: "001-migrate-user-table", action: "health-check" }
   └── Broker runs health/qa/check.sh on host
   └── All checks pass

9. Receipt generated (by the broker, not the agent)
   └── Broker writes receipt to receipts/qa/ on host
   └── Broker updates promotion DB: qa.status = "success"
   └── Broker returns receipt summary to agent

10. Deployment lock released (by the broker)

11. Agent reports to user
    └── "Script 001-migrate-user-table deployed successfully to QA"
    └── Receipt summary: duration, checks passed, environment health status
```

**Note:** Every step goes through the broker. The agent sends a script ID and an action. The broker does everything else — reads the script, loads secrets, executes on the host, generates receipts, updates state. The agent never sees file paths, script content, or secrets.

### 9.2 Failure Path

```
1. Broker returns non-zero exit code from script execution
2. Agent receives stdout/stderr from broker response
3. Agent calls: POST /broker/deploy
     { script_id: "001-migrate", action: "rollback" }
   └── Broker checks: rollback script exists? If yes, executes on host
   └── Broker returns rollback result to agent
4. Agent calls post-hook and health-check via broker (same as happy path)
5. Broker generates failure receipt, updates promotion DB: qa.status = "failed"
6. Broker releases deployment lock
7. Agent files bug ticket (GitHub issue) with:
   └── Script name and version (from broker info response)
   └── Environment
   └── Error output (stdout + stderr from broker response)
   └── Relevant code context (agent reads source files from RO workspace mounts)
   └── Deployment receipt from previous environment (retrieved via broker)
   └── Suggested fix (if agent can determine one from reading the code)
8. Agent reports failure to user
```

**Key distinction:** The agent's value-add in failure scenarios is *diagnosis* — it reads the code from its RO workspace mounts, correlates the error output with the codebase, and writes a detailed bug ticket. It does NOT retry, modify, or fix anything. It files a ticket and reports.

### 9.3 Rollback

Rollback can be triggered in three ways:

1. **Automatic** — script fails, rollback script exists → agent runs it immediately
2. **Agent-initiated** — health check fails after deployment → agent runs rollback
3. **User-initiated** — user clicks [Rollback] in UI → agent receives rollback command

Rollback scripts go through the same broker policy checks and produce their own receipts.

---

## 10. Health Checks

### 10.1 Structure

Each environment has a health check script that the deployment agent runs periodically.

```
~/.bond/deployments/health/
├── common/
│   └── check.sh           # shared health check logic
├── dev/
│   └── check.sh           # dev-specific checks
├── qa/
│   └── check.sh
├── staging/
│   └── check.sh
├── uat/
│   └── check.sh
└── prod/
    └── check.sh
```

### 10.2 Health Check Output Format

Health checks output structured JSON for machine parsing:

```json
{
  "environment": "qa",
  "timestamp": "2026-03-12T10:30:00Z",
  "status": "healthy",
  "checks": [
    { "name": "api_responding", "status": "pass", "latency_ms": 45 },
    { "name": "db_connected", "status": "pass", "latency_ms": 12 },
    { "name": "queue_depth", "status": "pass", "value": 3, "threshold": 100 },
    { "name": "disk_usage", "status": "warn", "value": "82%", "threshold": "90%" },
    { "name": "last_error_rate", "status": "pass", "value": "0.01%", "threshold": "1%" }
  ]
}
```

### 10.3 Periodic Health Checks

Each deployment agent runs health checks at the interval configured for its environment. If a check fails:

1. Agent files a bug ticket with diagnostic details
2. Agent reports to user via configured Bond surface
3. Deployment lock is set (no new deployments until healthy)

### 10.4 Environment Drift Detection

Between deployments, the agent periodically compares the environment state against expected state:

- Are all expected services running?
- Does the database schema match what the last deployment left?
- Are configuration values what they should be?
- Are there unexpected processes or changes?

Drift detection runs as part of the regular health check cycle. If drift is detected:

1. Agent reports the drift with specifics
2. Agent files a bug ticket
3. Agent does NOT attempt to fix the drift — that's a human or code-agent decision

---

## 11. Bug Ticket Tool

### 11.1 Tool Definition

Deployment agents have a `file_bug_ticket` tool that creates GitHub issues with structured information.

```python
{
    "type": "function",
    "function": {
        "name": "file_bug_ticket",
        "description": (
            "Create a detailed GitHub issue for a deployment failure or environment problem. "
            "Include enough context for a developer to reproduce and fix the issue. "
            "The issue will be created in the configured repository with appropriate labels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Clear, specific issue title"
                },
                "environment": {
                    "type": "string",
                    "description": "Which environment this affects"
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low"],
                    "description": "Impact severity"
                },
                "script_id": {
                    "type": "string",
                    "description": "Deployment script that failed (if applicable)"
                },
                "error_output": {
                    "type": "string",
                    "description": "Relevant stdout/stderr from the failure"
                },
                "code_context": {
                    "type": "string",
                    "description": "Relevant code snippets from the workspace (read-only access)"
                },
                "steps_to_reproduce": {
                    "type": "string",
                    "description": "How to reproduce the issue"
                },
                "expected_behavior": {
                    "type": "string",
                    "description": "What should have happened"
                },
                "actual_behavior": {
                    "type": "string",
                    "description": "What actually happened"
                },
                "suggested_fix": {
                    "type": "string",
                    "description": "Agent's analysis and suggested fix (from reading the code)"
                },
                "receipt_id": {
                    "type": "string",
                    "description": "Deployment receipt ID for full context"
                }
            },
            "required": ["title", "environment", "severity", "actual_behavior"]
        }
    }
}
```

### 11.2 Issue Template

The tool generates a GitHub issue via the broker:

```markdown
## 🚨 Deployment Issue: {title}

**Environment:** {environment}
**Severity:** {severity}
**Script:** {script_id} (v{version})
**Deployment Receipt:** {receipt_id}
**Detected:** {timestamp}

### Error Output
```
{error_output}
```

### Steps to Reproduce
{steps_to_reproduce}

### Expected Behavior
{expected_behavior}

### Actual Behavior
{actual_behavior}

### Relevant Code
```
{code_context}
```

### Suggested Fix
{suggested_fix}

### Context
- Previous environment ({prev_env}) deployed successfully at {prev_deploy_time}
- Health check status at time of failure: {health_status}
- Rollback status: {rollback_status}

---
*Filed automatically by deploy-{environment} agent*
```

Labels: `deployment`, `env:{environment}`, `severity:{severity}`, `automated`

---

## 12. Deployment Receipts

### 12.1 Receipt Format

Every deployment (success or failure) produces an immutable receipt:

```json
{
  "receipt_id": "receipt-001-qa-20260312T103000Z",
  "script_id": "001-migrate-user-table",
  "script_version": "v1",
  "script_sha256": "a1b2c3d4...",
  "environment": "qa",
  "agent_id": "deploy-qa",
  "timestamp_start": "2026-03-12T10:30:00Z",
  "timestamp_end": "2026-03-12T10:31:23Z",
  "duration_ms": 83000,
  "status": "success",

  "phases": {
    "validation": { "status": "pass", "duration_ms": 200 },
    "pre_hook": { "status": "pass", "duration_ms": 5000, "output_summary": "DB snapshot created" },
    "dry_run": { "status": "pass", "duration_ms": 1200, "output_summary": "No errors in dry run" },
    "execution": { "status": "pass", "duration_ms": 45000, "exit_code": 0 },
    "post_hook": { "status": "pass", "duration_ms": 30000, "output_summary": "12/12 integration tests passed" },
    "health_check": { "status": "pass", "checks_passed": 5, "checks_total": 5 }
  },

  "health_before": { "status": "healthy", "checks_passed": 5 },
  "health_after": { "status": "healthy", "checks_passed": 5 },

  "rollback_triggered": false,
  "bug_ticket_filed": false,

  "context": {
    "promoted_by": "andrew",
    "promoted_at": "2026-03-12T10:15:00Z",
    "previous_environment_receipt": "receipt-001-dev-20260312T090000Z"
  }
}
```

### 12.2 Context Passing

When a deployment agent picks up a script, it reads the receipt from the previous environment:

```
deploy-qa agent calls: POST /broker/deploy { script_id: "001-migrate", action: "receipt", environment: "dev" }
Broker reads receipt from host: ~/.bond/deployments/receipts/dev/receipt-001-dev-20260312.json
Broker returns receipt content to agent (agent never sees the file path)
```

This gives the QA agent:
- What happened in Dev (success/failure, duration, health results)
- Any warnings or edge cases observed
- The exact SHA-256 of the script that ran (verifiable)

The agent uses this context to make better decisions — e.g., if Dev had a warning about slow query performance, the QA agent can monitor for the same issue.

---

## 13. Deployment Locks & Concurrency

### 13.1 Lock Mechanism

Each environment has a simple file-based lock:

```
~/.bond/deployments/locks/
├── dev.lock        # contains: {"agent": "deploy-dev", "script": "001-...", "since": "2026-..."}
├── qa.lock         # absent = unlocked
└── ...
```

Rules:
- One deployment per environment at a time
- Lock acquired before pre-hook, released after health check (or failure/rollback)
- If an agent crashes mid-deployment, the lock has a TTL (2x the script timeout). Stale locks are auto-released.
- Lock state is visible in the UI

### 13.2 Queue

If a second script is promoted to an environment while a deployment is in progress:

1. The script is queued
2. The agent processes it after the current deployment completes
3. Queue order matches promotion order
4. User can reorder or cancel queued deployments from the UI

---

## 14. Deployment Windows

### 14.1 Enforcement

Deployment windows are enforced at two levels:

1. **Promotion API** — warns the user if promoting outside a window (but doesn't block — the script becomes available, just won't execute until the window opens)
2. **Agent execution** — the agent checks the window before starting a deployment. If outside the window, it reports "Deployment queued — window opens at {time}" and waits

### 14.2 Emergency Override

The user can override deployment windows via the UI with an explicit "Deploy Outside Window" button. This is logged as an escalation in the audit trail.

---

## 15. Manual Intervention Escape Hatch

### 15.1 Pause/Resume

Each deployment agent supports a pause state:

```
POST /api/v1/deployments/agents/deploy-qa/pause
POST /api/v1/deployments/agents/deploy-qa/resume
```

When paused:
- Agent stops processing new deployments
- Current deployment (if any) completes (or can be force-stopped)
- Health checks continue running
- Agent reports its paused state in the UI

### 15.2 Force Stop

If a deployment is hanging:

```
POST /api/v1/deployments/agents/deploy-qa/abort
```

This:
1. Kills the running script
2. Runs the rollback script (if available)
3. Runs health check
4. Files a bug ticket
5. Releases the deployment lock
6. Agent remains active for future deployments

### 15.3 Manual Takeover

If the user needs to intervene directly:

1. Pause the environment agent
2. Do whatever manual work is needed
3. Optionally run the health check script manually to verify
4. Resume the agent
5. The agent picks up from its queue

The receipt for a manual intervention is logged as:
```json
{
  "receipt_id": "receipt-manual-qa-20260312T...",
  "type": "manual_intervention",
  "agent_id": "deploy-qa",
  "paused_at": "...",
  "resumed_at": "...",
  "health_before_resume": { "status": "healthy" }
}
```

---

## 16. Frontend — Promotion UI

### 16.1 Pipeline View

```
┌─────────────────────────────────────────────────────────────────┐
│  Deployment Pipeline                                             │
│                                                                  │
│  001-migrate-user-table (v1)                                     │
│  ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐         │
│  │ Dev  │──►│  QA  │──►│ Stg  │──►│ UAT  │──►│ Prod │         │
│  │  ✅  │   │  ✅  │   │  ⏳  │   │  ○   │   │  ○   │         │
│  └──────┘   └──────┘   └──────┘   └──────┘   └──────┘         │
│  Deployed    Deployed   Running    Locked      Locked           │
│  12m ago     3m ago     now        (needs stg) (needs uat)      │
│                                                                  │
│  [View Receipt: Dev] [View Receipt: QA]                          │
│  [Promote to UAT] [Promote to All Remaining]                    │
│                                                                  │
│  ─────────────────────────────────────────────                   │
│                                                                  │
│  002-add-notifications (v1)                                      │
│  ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐         │
│  │ Dev  │──►│  QA  │──►│ Stg  │──►│ UAT  │──►│ Prod │         │
│  │  ❌  │   │  ○   │   │  ○   │   │  ○   │   │  ○   │         │
│  └──────┘   └──────┘   └──────┘   └──────┘   └──────┘         │
│  Failed     Locked     Locked      Locked      Locked           │
│  8m ago     (needs dev)                                          │
│                                                                  │
│  [View Receipt: Dev] [View Bug Ticket: #47]                      │
│  [Retry Dev]                                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 16.2 Environment Health Dashboard

```
┌─────────────────────────────────────────────────────────────────┐
│  Environment Health                                              │
│                                                                  │
│  Dev      ● Healthy    Last check: 30s ago    [Pause Agent]     │
│  QA       ● Healthy    Last check: 2m ago     [Pause Agent]     │
│  Staging  ◐ Deploying  Script: 001-migrate    [Abort] [Pause]   │
│  UAT      ● Healthy    Last check: 5m ago     [Pause Agent]     │
│  Prod     ● Healthy    Last check: 15s ago    [Pause Agent]     │
│                                                                  │
│  ⚠ Staging: deployment window closes in 2h 15m                   │
│  ⚠ Dev: disk usage at 82% (threshold: 90%)                      │
└─────────────────────────────────────────────────────────────────┘
```

### 16.3 Receipt Viewer

Clicking "View Receipt" shows the full deployment receipt with expandable phases:

```
┌─────────────────────────────────────────────────────────────────┐
│  Receipt: 001-migrate-user-table → QA                           │
│  Status: ✅ Success | Duration: 1m 23s | Deployed: 3m ago      │
│                                                                  │
│  ▸ Validation ✅ (200ms)                                        │
│  ▸ Pre-Hook ✅ (5s) — DB snapshot created                       │
│  ▸ Dry Run ✅ (1.2s) — No errors                                │
│  ▾ Execution ✅ (45s)                                           │
│    │ Running migration on environment: qa                        │
│    │ Applying migration...                                       │
│    │ ALTER TABLE                                                 │
│    │ Verifying...                                                │
│    │ Migration successful                                        │
│  ▸ Post-Hook ✅ (30s) — 12/12 integration tests passed          │
│  ▸ Health Check ✅ (5/5 checks passed)                          │
│                                                                  │
│  Context: Promoted by andrew | Dev receipt: ✅ success           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 17. Notifications & Events

### 17.1 Event Types

Every significant action produces an event delivered through Bond's channel infrastructure:

| Event | Delivered To | Priority |
|---|---|---|
| Script promoted | User (UI) | Info |
| Deployment started | User (UI + configured surface) | Info |
| Deployment succeeded | User (UI + configured surface) | Info |
| Deployment failed | User (all surfaces) | High |
| Rollback triggered | User (all surfaces) | High |
| Health check failed | User (all surfaces) | High |
| Drift detected | User (all surfaces) | Medium |
| Bug ticket filed | User (UI + configured surface) | Medium |
| Deployment window closing soon | User (UI) | Info |
| Manual intervention needed | User (all surfaces) | Critical |
| Deployment lock stale / auto-released | User (UI) | Warning |

### 17.2 Event Format

```json
{
  "type": "deployment_event",
  "event": "deployment_succeeded",
  "environment": "qa",
  "script_id": "001-migrate-user-table",
  "agent_id": "deploy-qa",
  "receipt_id": "receipt-001-qa-20260312T103000Z",
  "summary": "Migration deployed to QA — 12/12 tests passed, environment healthy",
  "timestamp": "2026-03-12T10:31:23Z"
}
```

---

## 18. Script Dependencies & Ordering

### 18.1 Dependency Resolution

Scripts can declare dependencies via `depends_on` metadata:

```bash
# meta:depends_on: 001-migrate-user-table
```

Before executing a script, the agent checks:
1. All `depends_on` scripts have been successfully deployed in *this* environment
2. No `depends_on` scripts are currently being rolled back

If dependencies aren't met, the script is queued until they are.

### 18.2 Execution Order

When multiple scripts are promoted to an environment simultaneously:

1. Build a dependency graph
2. Execute in topological order (dependencies first)
3. If no dependencies between scripts, execute in promotion order (FIFO)
4. If any script fails, stop the queue (don't cascade failures)

---

## 19. File Structure

```
SpacetimeDB (bond-core module):
  Tables: deployment_environments, deployment_environment_approvers,
          deployment_environment_history, deployment_promotions,
          deployment_approvals
  Access: Gateway process only (agents have no SPACETIMEDB_TOKEN)

~/.bond/deployments/                 # HOST-ONLY — NEVER MOUNTED INTO CONTAINERS
├── scripts/
│   └── registry/                    # Immutable script snapshots
│       └── {script-id}/
│           └── v{n}/
│               ├── deploy.sh
│               ├── rollback.sh
│               ├── manifest.json
│               └── .sha256
├── hooks/
│   └── {env}/
│       ├── pre_deploy.sh
│       └── post_deploy.sh
├── health/
│   ├── common/
│   └── {env}/
│       └── check.sh
├── secrets/
│   └── {env}.yaml                   # Encrypted at rest
├── receipts/
│   └── {env}/
│       └── {receipt-id}.json
├── locks/
│   └── {env}.lock
└── logs/
    └── {env}/
        └── deploy-{date}.log

gateway/src/
├── broker/                          # Existing
│   ├── router.ts                    # Existing — add /deploy endpoint here
│   ├── deploy-handler.ts            # NEW — /deploy endpoint logic
│   │                                #   reads promotion DB, executes scripts,
│   │                                #   loads secrets, generates receipts
│   └── ...                          # Existing broker modules
├── deployments/
│   ├── router.ts                    # Promotion API + Environment Management API (user-auth only)
│   ├── environments.ts              # Environment CRUD + approver management
│   ├── promotion.ts                 # Promotion logic + approval workflow
│   ├── scripts.ts                   # Script registry + snapshot management
│   ├── receipts.ts                  # Receipt generation + storage
│   ├── locks.ts                     # Deployment lock management
│   ├── events.ts                    # Event emission
│   ├── stdb.ts                      # SpacetimeDB queries + reducer calls for deployment state
│   └── __tests__/
│       ├── environments.test.ts
│       ├── promotion.test.ts
│       ├── approvals.test.ts
│       ├── scripts.test.ts
│       ├── deploy-handler.test.ts
│       └── locks.test.ts

gateway/src/spacetimedb/
├── deployment_environments_table.ts     # Auto-generated SpacetimeDB bindings
├── deployment_promotions_table.ts
├── deployment_approvals_table.ts
├── deployment_environment_approvers_table.ts
├── deployment_environment_history_table.ts
├── create_deployment_environment_reducer.ts
├── update_deployment_environment_reducer.ts
├── deactivate_deployment_environment_reducer.ts
├── initiate_promotion_reducer.ts
├── record_approval_reducer.ts
├── update_promotion_status_reducer.ts
└── ... (added to existing spacetimedb/ directory)

frontend/
├── components/deployments/
│   ├── PipelineView.tsx             # Main pipeline visualization
│   ├── EnvironmentCard.tsx          # Per-environment status
│   ├── EnvironmentSettings.tsx      # Environment management (add/edit/deactivate)
│   ├── ApproverManager.tsx          # Manage approvers per environment
│   ├── ApprovalStatus.tsx           # Show approval progress (1/2 needed, etc.)
│   ├── ReceiptViewer.tsx            # Deployment receipt detail
│   ├── PromoteButton.tsx            # Promotion action (with approval awareness)
│   └── HealthDashboard.tsx          # Environment health overview

backend/app/agent/
├── deploy_agent.py                  # Deployment agent behavior
├── tools/
│   └── deploy_tools.py              # file_bug_ticket + deployment-specific tools
```

---

## 20. Security Model

### 20.1 Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│ USER (trusted)                                               │
│ Can: promote scripts, pause/resume agents, override windows  │
│ Via: Frontend UI → Gateway API (user session auth)           │
│ Token type: user session JWT (Gateway login)                 │
└─────────────────────────┬───────────────────────────────────┘
                          │ User session token
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ GATEWAY (trusted)                                            │
│ Promotion API: ONLY accepts user session tokens              │
│ /broker/deploy: ONLY accepts agent broker tokens             │
│ Database: ONLY Gateway process writes env config + promotions │
│ Script registry: ONLY Gateway process reads scripts          │
│ Secrets: ONLY Gateway process loads and injects secrets      │
│                                                              │
│ ~/.bond/deployments/ lives HERE, never mounted anywhere      │
└──────────┬──────────────────────────────────────────────────┘
           │ Agent broker token (HMAC-signed, env-scoped)
           ▼
┌─────────────────────────────────────────────────────────────┐
│ DEPLOYMENT AGENT (constrained)                               │
│ Can: request deployment of promoted scripts (via broker)     │
│ Can: read code workspaces (RO mounts, for troubleshooting)  │
│ Can: file bug tickets (via broker exec → gh issue create)    │
│ Can: receive deployment results (stdout/stderr from broker)  │
│                                                              │
│ Cannot: see script content (broker reads + executes)         │
│ Cannot: see secrets (broker loads + injects)                 │
│ Cannot: see promotion DB or env config (broker reads it)     │
│ Cannot: promote scripts (Promotion API rejects agent tokens) │
│ Cannot: run scripts not promoted to its env (broker denies)  │
│ Cannot: pretend to be a different environment (token-bound)  │
│ Cannot: modify code (all mounts are read-only)               │
└─────────────────────────────────────────────────────────────┘
```

### 20.2 SpacetimeDB Token Removal (Completed)

As of 2026-03-12, `SPACETIMEDB_TOKEN` injection into agent containers has been removed from both `SandboxManager` (`manager.py`) and `OpenSandboxAdapter` (`opensandbox_adapter.py`). The token was originally injected to debug an error but was never functional — containers don't have the SpacetimeDB CLI. Removing it closes a credential exposure risk identified in Doc 035 §3.2.

Agents now have **zero database credentials.** All database operations go through the broker or Gateway API.

### 20.3 Key Invariants

1. **Agents cannot self-escalate.** The promotion database is managed by the Gateway process — not a file on disk that could be edited, but database tables that only the Promotion API and broker deploy handler can access. The Promotion API rejects agent broker tokens (different token type, different signing key). There is no filesystem path, broker command, or API endpoint through which an agent can grant itself access to a script.
2. **Agents never see scripts.** The script registry is host-only. The broker reads scripts from the registry and executes them. The agent sends a script ID and receives stdout/stderr. It never sees the script content, file path, or supporting files.
3. **Agents never see secrets.** Environment secrets are host-only. The broker loads secrets and injects them as environment variables during script execution. The agent receives command output, not the environment that produced it.
4. **Environment is identity.** The agent's environment is derived from its cryptographic token, not from request parameters. An agent cannot request deployment to a different environment.
5. **Script immutability.** Once snapshotted, a script version cannot be modified. SHA-256 verified by the broker before execution.
6. **Audit everything.** Every promotion, deployment, rollback, health check, and bug ticket is logged with timestamps and actor identity.

---

## 21. Build Order

### Phase 1: Core Pipeline (~1.5 weeks)

1. SpacetimeDB tables + reducers (`deployment_environments`, `deployment_promotions`, `deployment_approvals`, `deployment_environment_approvers`, history tables — added to `bond-core` module)
2. Environment Management API (`/api/v1/deployments/environments` — CRUD, user-session-auth only)
3. Default environment seeding on first run (dev, qa, staging, uat, prod)
4. CLI escape hatch (`bond environments import/export` for bootstrap + DR)
5. Script registry + snapshot mechanism (registry, SHA-256, manifest)
6. Promotion API (`/api/v1/deployments/promote` + `/promote/approve` — user-session-auth only)
7. Approval workflow (record approvals, check thresholds, notify pending approvers)
8. User session token system (separate signing key from broker tokens, Promotion API validates token type)
9. **Broker `/deploy` endpoint** — queries SpacetimeDB for promotion status, loads scripts from registry, injects secrets, executes on host
10. Per-environment broker exec policies (lock down generic `/exec` to `gh issue create` only)
11. Deployment agent container creation (RO workspace mounts only, NO deployment files mounted)
12. Basic deployment execution flow (agent → broker `/deploy` → validate → execute → receipt)
13. Receipt generation (broker-side, written to host-only receipts directory)
14. `file_bug_ticket` tool (wraps `gh issue create` via broker `/exec`)

### Phase 2: Safety & Observability (~1 week)

15. Rollback scripts + automatic rollback on failure
16. Health checks (periodic + post-deployment)
17. Deployment locks + queue
18. Deployment windows (read from DB, enforce in broker)
19. Pre/post hooks
20. Dry-run support
21. Script validation (syntax check, dependency resolution)
22. Event notifications through Bond channels

### Phase 3: Frontend (~1.5 weeks)

23. Environment management settings page (add/edit/deactivate environments)
24. Approver management per environment
25. Pipeline view (script promotion visualization)
26. Promote button (single + promote-all) with approval status
27. Environment health dashboard
28. Receipt viewer
29. Pause/resume/abort controls
30. Deployment window override UI

### Phase 4: Hardening (~3-5 days)

31. Context passing between environment agents
32. Script dependency ordering
33. Environment drift detection
34. Secret encryption at rest
35. Stale lock auto-release
36. Deployment log streaming to UI
37. Environment history/changelog viewer in UI

---

## 22. Open Questions

1. **Script authoring.** Who writes deployment scripts — code agents or humans? If code agents, they'd need write access to the registry (only Dev). If humans, there should be a CLI or UI for script submission.

2. **Multi-repo.** If Bond manages multiple repositories, do deployment scripts live per-repo or in a central location? The current design assumes a central `~/.bond/deployments/scripts/` but per-repo might scale better.

3. **Database migrations vs. deployment scripts.** Are DB migrations a special case of deployment scripts, or a separate system? They have the same needs (ordering, rollback, environment scoping) but different tooling (Prisma, Alembic, raw SQL).

4. **Agent model.** Should deployment agents use a cheaper/faster model (they're running scripts, not writing code) or the same model as code agents (they need to troubleshoot and write detailed bug reports)?

5. **Shared environments.** What if two teams share a Staging environment? Current design assumes one owner with full control. Multi-tenant deployment would need additional access control.

6. **Parallel deployments across environments.** If "Promote to All" is clicked, should Dev→QA→Staging→UAT→Prod run sequentially (safer) or should independent environments run in parallel (faster)?
