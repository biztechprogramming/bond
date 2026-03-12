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

```
┌──────────────────────────────────────────────────────────────────────────┐
│  BOND HOST                                                               │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  Gateway + Permission Broker                                     │    │
│  │                                                                  │    │
│  │  ┌────────────────┐  ┌────────────────┐  ┌──────────────────┐   │    │
│  │  │ Broker Router   │  │ Policy Engine   │  │ Promotion API    │   │    │
│  │  │ /api/v1/broker  │  │ (per-env rules) │  │ /api/v1/promote  │   │    │
│  │  └────────────────┘  └────────────────┘  └──────────────────┘   │    │
│  │                                                                  │    │
│  │  ┌──────────────────────────────────────────────────────────┐   │    │
│  │  │ Deployment Script Store                                   │   │    │
│  │  │ ~/.bond/deployments/scripts/     (immutable snapshots)    │   │    │
│  │  │ ~/.bond/deployments/promotions/  (promotion state DB)     │   │    │
│  │  │ ~/.bond/deployments/receipts/    (deployment receipts)    │   │    │
│  │  └──────────────────────────────────────────────────────────┘   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │ Deploy Agent  │ │ Deploy Agent  │ │ Deploy Agent  │ │ Deploy Agent  │  │
│  │ ENV: dev      │ │ ENV: qa       │ │ ENV: staging  │ │ ENV: uat      │  │
│  │               │ │               │ │               │ │               │  │
│  │ RO: all agent │ │ RO: all agent │ │ RO: all agent │ │ RO: all agent │  │
│  │   workspaces  │ │   workspaces  │ │   workspaces  │ │   workspaces  │  │
│  │               │ │               │ │               │ │               │  │
│  │ Broker policy:│ │ Broker policy:│ │ Broker policy:│ │ Broker policy:│  │
│  │ scripts(dev)  │ │ scripts(qa)   │ │ scripts(stg)  │ │ scripts(uat)  │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
│                                                                          │
│  ┌──────────────┐                                                        │
│  │ Deploy Agent  │    ┌────────────────────────────────────────────┐     │
│  │ ENV: prod     │    │ Code Agents (existing)                     │     │
│  │               │    │ Workspaces mounted RO into deploy agents   │     │
│  │ Broker policy:│    │ No access to deployment scripts            │     │
│  │ scripts(prod) │    └────────────────────────────────────────────┘     │
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

### 3.1 Default Configuration

Environments are ordered. Promotion flows left to right. Each environment is a separate deployment agent.

```yaml
# ~/.bond/deployments/config.yaml
environments:
  - name: dev
    display_name: Development
    order: 1
    auto_promote: false
    deployment_window: null          # no restrictions
    health_check_interval: 300       # seconds (5 min)
    max_script_timeout: 600          # 10 min
    approvers: []                    # empty = owner only

  - name: qa
    display_name: QA
    order: 2
    auto_promote: false
    deployment_window: null
    health_check_interval: 300
    max_script_timeout: 900          # 15 min
    approvers: []

  - name: staging
    display_name: Staging
    order: 3
    auto_promote: false
    deployment_window:
      days: [mon, tue, wed, thu, fri]
      start: "06:00"
      end: "22:00"
      timezone: America/New_York
    health_check_interval: 600
    max_script_timeout: 1200
    approvers: []

  - name: uat
    display_name: UAT
    order: 4
    auto_promote: false
    deployment_window:
      days: [mon, tue, wed, thu, fri]
      start: "06:00"
      end: "22:00"
      timezone: America/New_York
    health_check_interval: 600
    max_script_timeout: 1200
    approvers: []

  - name: prod
    display_name: Production
    order: 5
    auto_promote: false
    deployment_window:
      days: [tue, wed, thu]
      start: "09:00"
      end: "16:00"
      timezone: America/New_York
    health_check_interval: 60        # 1 min — tighter monitoring
    max_script_timeout: 1800         # 30 min
    approvers: []
```

### 3.2 Adding/Removing Environments

The environment list is user-configurable. Adding an environment:

1. Add entry to `config.yaml`
2. Bond spawns a new deployment agent container with the appropriate broker policy
3. The new environment appears in the promotion pipeline UI

Removing an environment:

1. Remove from `config.yaml`
2. Bond tears down the agent container
3. Any scripts promoted to that environment are orphaned (visible in UI, can be cleaned up)

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
  # Read-only access to all unique agent workspace mounts
  - /home/andrew/bond:/workspaces/bond:ro
  - /home/andrew/other-repo:/workspaces/other-repo:ro
  # ... (deduplicated — if 3 agents mount /bond, it appears once)

  # Environment-specific deployment scripts (read-only — populated by promotion)
  - ~/.bond/deployments/scripts/{env}/:/deploy/scripts:ro

  # Environment-specific hooks
  - ~/.bond/deployments/hooks/{env}/:/deploy/hooks:ro

  # Receipts from all environments (read-only — for context)
  - ~/.bond/deployments/receipts/:/deploy/receipts:ro

  # Agent's own scratch space (writable — for temp files, logs)
  - deploy-{env}-data:/deploy/data:rw

environment:
  BOND_DEPLOY_ENV: "{env}"
  BOND_BROKER_TOKEN: "{agent-scoped-token}"
  BOND_BROKER_URL: "http://host.docker.internal:18789"
```

### 4.5 Workspace Mount Deduplication

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
psql "$DB_URL" -f /deploy/scripts/sql/001-add-email-verified.sql

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

The promotion state lives in a SQLite database (or JSON file) at `~/.bond/deployments/promotions/state.db`. Deployment agents have no write access to this file. The Gateway's Promotion API is the only writer, and it only accepts requests from authenticated UI sessions — not from agent broker tokens.

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

When a script is first registered (placed in the scripts directory), the system creates an immutable snapshot:

```
~/.bond/deployments/scripts/
├── registry/
│   ├── 001-migrate-user-table/
│   │   ├── v1/
│   │   │   ├── deploy.sh           # the script
│   │   │   ├── rollback.sh         # rollback script
│   │   │   ├── sql/                # supporting files
│   │   │   │   └── 001-add-email-verified.sql
│   │   │   ├── manifest.json       # parsed metadata + SHA-256 hash
│   │   │   └── .sha256             # hash of all files in this version
│   │   └── v2/                     # new version = new snapshot
│   │       └── ...
│   └── 002-add-notifications/
│       └── v1/
│           └── ...
├── dev/                             # symlinks to registry versions
│   ├── 001-migrate-user-table -> ../registry/001-migrate-user-table/v1/
│   └── 002-add-notifications -> ../registry/002-add-notifications/v1/
├── qa/                              # populated on promotion
│   └── 001-migrate-user-table -> ../registry/001-migrate-user-table/v1/
├── staging/                         # empty until promoted
├── uat/
└── prod/
```

Each environment directory contains symlinks to the registry. Promotion = creating a symlink. The actual script content is immutable in the registry. What ran in Dev is byte-for-byte what runs in QA.

The `.sha256` file contains a hash of the entire script bundle. The agent verifies this hash before execution. If someone tampers with the registry, the hash check fails.

### 6.4 Promotion State

```json
// ~/.bond/deployments/promotions/state.json
{
  "scripts": {
    "001-migrate-user-table": {
      "version": "v1",
      "sha256": "a1b2c3d4...",
      "environments": {
        "dev": {
          "promoted_at": "2026-03-12T09:00:00Z",
          "promoted_by": "system",
          "deployed_at": "2026-03-12T09:01:23Z",
          "status": "success",
          "receipt_id": "receipt-001-dev-20260312"
        },
        "qa": {
          "promoted_at": "2026-03-12T10:15:00Z",
          "promoted_by": "andrew",
          "deployed_at": null,
          "status": "pending",
          "receipt_id": null
        },
        "staging": {
          "promoted_at": null,
          "status": "not_promoted"
        },
        "uat": {
          "promoted_at": null,
          "status": "not_promoted"
        },
        "prod": {
          "promoted_at": null,
          "status": "not_promoted"
        }
      }
    }
  }
}
```

### 6.5 Promotion API

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

**Validation before promotion:**

1. Script exists in registry at specified version
2. Script has been successfully deployed in the previous environment (unless `force: true`)
3. Target environment health check is passing
4. No active deployment lock on target environment
5. Within deployment window (if configured)
6. Request comes from a user session, not an agent token

**Response:**
```json
{
  "promoted": ["qa"],
  "skipped": [],
  "errors": [],
  "message": "Script 001-migrate-user-table v1 promoted to qa"
}
```

### 6.6 Promote-to-All

When the user clicks "Promote to All," the API processes each environment sequentially in order. If any environment fails validation (e.g., previous environment hasn't completed), it stops and reports which environments were promoted and which were skipped.

This is not automatic deployment — it's automatic *access*. The script becomes available in each environment, and each environment's agent picks it up and runs it. The user is giving permission for the script to be available, not triggering immediate execution.

---

## 7. Broker Policies (Per-Environment)

### 7.1 Policy Scoping

Each deployment agent gets a broker policy that restricts which scripts it can execute.

```yaml
# ~/.bond/policies/agents/deploy-dev.yaml
version: "1"
name: deploy-dev
agent_id: "deploy-dev"

rules:
  # Can execute scripts in the dev environment directory
  - commands: ["bash /deploy/scripts/*", "sh /deploy/scripts/*"]
    decision: allow

  # Can execute hooks
  - commands: ["bash /deploy/hooks/*", "sh /deploy/hooks/*"]
    decision: allow

  # Can run health checks
  - commands: ["bash /deploy/health/*", "sh /deploy/health/*"]
    decision: allow

  # Can read files (troubleshooting)
  - commands: ["cat *", "head *", "tail *", "grep *", "find *", "ls *"]
    decision: allow

  # Can run diagnostic tools
  - commands: ["psql*--command*", "curl http://localhost*", "curl http://dev.*"]
    decision: allow

  # Can create GitHub issues (bug tickets)
  - commands: ["gh issue create*"]
    decision: allow

  # Deny everything else
  - commands: ["*"]
    decision: deny
    reason: "Deployment agents can only run promoted scripts and diagnostics"
```

```yaml
# ~/.bond/policies/agents/deploy-prod.yaml
version: "1"
name: deploy-prod
agent_id: "deploy-prod"

rules:
  # Same structure but scoped to prod
  - commands: ["bash /deploy/scripts/*", "sh /deploy/scripts/*"]
    decision: allow

  - commands: ["bash /deploy/hooks/*", "sh /deploy/hooks/*"]
    decision: allow

  - commands: ["bash /deploy/health/*", "sh /deploy/health/*"]
    decision: allow

  # More restrictive diagnostics — no direct DB access in prod
  - commands: ["curl http://prod.*"]
    decision: allow

  - commands: ["psql*"]
    decision: deny
    reason: "Direct database access not allowed in production — use deployment scripts"

  - commands: ["gh issue create*"]
    decision: allow

  - commands: ["*"]
    decision: deny
    reason: "Deployment agents can only run promoted scripts and diagnostics"
```

### 7.2 Environment Secrets

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

## 8. Deployment Execution Flow

### 8.1 Happy Path

```
1. User promotes script "001-migrate-user-table" to QA via UI
   └── Promotion API creates symlink in ~/.bond/deployments/scripts/qa/
   └── Sends notification to deploy-qa agent: "New script available"

2. deploy-qa agent picks up the new script
   └── Reads script metadata (name, version, depends_on, timeout, dry_run)
   └── Reads deployment receipt from Dev (context passing)

3. Validation phase
   └── Syntax check: bash -n /deploy/scripts/001-migrate-user-table/deploy.sh
   └── Dependency check: no dependencies → pass
   └── Hash check: SHA-256 matches registry → pass
   └── Deployment window check: within allowed hours → pass
   └── Deployment lock: acquire lock for QA environment

4. Pre-deploy hook
   └── broker.exec("bash /deploy/hooks/pre_deploy.sh")
   └── (e.g., snapshot database)

5. Dry-run (if script supports it)
   └── broker.exec("bash /deploy/scripts/001-migrate-user-table/deploy.sh --dry-run")
   └── Agent reviews output for warnings

6. Execution
   └── broker.exec("bash /deploy/scripts/001-migrate-user-table/deploy.sh")
   └── Broker injects DEPLOY_DB_URL, DEPLOY_API_URL from qa secrets
   └── Script runs, exits 0

7. Post-deploy hook
   └── broker.exec("bash /deploy/hooks/post_deploy.sh")
   └── (e.g., run integration tests)

8. Health check
   └── broker.exec("bash /deploy/health/check.sh")
   └── All checks pass

9. Generate deployment receipt
   └── Write receipt to ~/.bond/deployments/receipts/qa/001-migrate-user-table-v1-20260312.json

10. Release deployment lock

11. Report to user
    └── "Script 001-migrate-user-table deployed successfully to QA"
    └── Receipt summary: duration, checks passed, environment health status
```

### 8.2 Failure Path

```
1. Script execution fails (non-zero exit)
2. Agent captures stdout/stderr
3. If rollback script exists:
   └── Agent executes rollback script
   └── Agent verifies rollback succeeded
4. Post-deploy hook still runs (cleanup)
5. Health check runs (verify environment is stable after rollback)
6. Agent generates failure receipt
7. Agent files bug ticket (GitHub issue) with:
   └── Script name and version
   └── Environment
   └── Error output (stdout + stderr)
   └── Relevant code context (agent reads source files from RO workspace mounts)
   └── Deployment receipt from previous environment (what worked there)
   └── Suggested fix (if agent can determine one from reading the code)
8. Release deployment lock
9. Report failure to user with receipt link
```

### 8.3 Rollback

Rollback can be triggered in three ways:

1. **Automatic** — script fails, rollback script exists → agent runs it immediately
2. **Agent-initiated** — health check fails after deployment → agent runs rollback
3. **User-initiated** — user clicks [Rollback] in UI → agent receives rollback command

Rollback scripts go through the same broker policy checks and produce their own receipts.

---

## 9. Health Checks

### 9.1 Structure

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

### 9.2 Health Check Output Format

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

### 9.3 Periodic Health Checks

Each deployment agent runs health checks at the interval configured for its environment. If a check fails:

1. Agent files a bug ticket with diagnostic details
2. Agent reports to user via configured Bond surface
3. Deployment lock is set (no new deployments until healthy)

### 9.4 Environment Drift Detection

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

## 10. Bug Ticket Tool

### 10.1 Tool Definition

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

### 10.2 Issue Template

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

## 11. Deployment Receipts

### 11.1 Receipt Format

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

### 11.2 Context Passing

When a deployment agent picks up a script, it reads the receipt from the previous environment:

```
deploy-qa agent reads: /deploy/receipts/dev/receipt-001-dev-20260312.json
```

This gives the QA agent:
- What happened in Dev (success/failure, duration, health results)
- Any warnings or edge cases observed
- The exact SHA-256 of the script that ran (verifiable)

The agent uses this context to make better decisions — e.g., if Dev had a warning about slow query performance, the QA agent can monitor for the same issue.

---

## 12. Deployment Locks & Concurrency

### 12.1 Lock Mechanism

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

### 12.2 Queue

If a second script is promoted to an environment while a deployment is in progress:

1. The script is queued
2. The agent processes it after the current deployment completes
3. Queue order matches promotion order
4. User can reorder or cancel queued deployments from the UI

---

## 13. Deployment Windows

### 13.1 Enforcement

Deployment windows are enforced at two levels:

1. **Promotion API** — warns the user if promoting outside a window (but doesn't block — the script becomes available, just won't execute until the window opens)
2. **Agent execution** — the agent checks the window before starting a deployment. If outside the window, it reports "Deployment queued — window opens at {time}" and waits

### 13.2 Emergency Override

The user can override deployment windows via the UI with an explicit "Deploy Outside Window" button. This is logged as an escalation in the audit trail.

---

## 14. Manual Intervention Escape Hatch

### 14.1 Pause/Resume

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

### 14.2 Force Stop

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

### 14.3 Manual Takeover

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

## 15. Frontend — Promotion UI

### 15.1 Pipeline View

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

### 15.2 Environment Health Dashboard

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

### 15.3 Receipt Viewer

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

## 16. Notifications & Events

### 16.1 Event Types

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

### 16.2 Event Format

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

## 17. Script Dependencies & Ordering

### 17.1 Dependency Resolution

Scripts can declare dependencies via `depends_on` metadata:

```bash
# meta:depends_on: 001-migrate-user-table
```

Before executing a script, the agent checks:
1. All `depends_on` scripts have been successfully deployed in *this* environment
2. No `depends_on` scripts are currently being rolled back

If dependencies aren't met, the script is queued until they are.

### 17.2 Execution Order

When multiple scripts are promoted to an environment simultaneously:

1. Build a dependency graph
2. Execute in topological order (dependencies first)
3. If no dependencies between scripts, execute in promotion order (FIFO)
4. If any script fails, stop the queue (don't cascade failures)

---

## 18. File Structure

```
~/.bond/deployments/
├── config.yaml                      # Environment definitions
├── scripts/
│   ├── registry/                    # Immutable script snapshots
│   │   └── {script-id}/
│   │       └── v{n}/
│   │           ├── deploy.sh
│   │           ├── rollback.sh
│   │           ├── manifest.json
│   │           └── .sha256
│   ├── dev/                         # Symlinks to registry (per-env access)
│   ├── qa/
│   ├── staging/
│   ├── uat/
│   └── prod/
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
├── promotions/
│   └── state.json                   # Promotion state (UI-controlled)
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
├── deployments/
│   ├── router.ts                    # Promotion API + agent management
│   ├── promotion.ts                 # Promotion logic + state management
│   ├── scripts.ts                   # Script registry + snapshot management
│   ├── receipts.ts                  # Receipt generation + storage
│   ├── locks.ts                     # Deployment lock management
│   ├── events.ts                    # Event emission
│   └── __tests__/
│       ├── promotion.test.ts
│       ├── scripts.test.ts
│       └── locks.test.ts

frontend/
├── components/deployments/
│   ├── PipelineView.tsx             # Main pipeline visualization
│   ├── EnvironmentCard.tsx          # Per-environment status
│   ├── ReceiptViewer.tsx            # Deployment receipt detail
│   ├── PromoteButton.tsx            # Promotion action
│   └── HealthDashboard.tsx          # Environment health overview

backend/app/agent/
├── deploy_agent.py                  # Deployment agent behavior
├── tools/
│   └── deploy_tools.py              # file_bug_ticket + deployment-specific tools
```

---

## 19. Security Model

### 19.1 Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│ USER (trusted)                                               │
│ Can: promote scripts, pause/resume agents, override windows  │
│ Via: Frontend UI → Gateway API (user session auth)           │
└─────────────────────────┬───────────────────────────────────┘
                          │ User session token
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ GATEWAY (trusted)                                            │
│ Promotion API: only accepts user session tokens              │
│ Broker: validates agent tokens, enforces policy              │
│ State DB: only Gateway writes promotion state                │
└──────────┬──────────────────────────────────────────────────┘
           │ Agent broker token (scoped)
           ▼
┌─────────────────────────────────────────────────────────────┐
│ DEPLOYMENT AGENT (constrained)                               │
│ Can: execute promoted scripts, read code, file bugs          │
│ Cannot: promote scripts, access other env secrets,           │
│         modify code, write to promotion state                │
└─────────────────────────────────────────────────────────────┘
```

### 19.2 Key Invariants

1. **Agents cannot self-escalate.** The promotion database is outside agent-writable paths. The Promotion API rejects agent tokens.
2. **Script immutability.** Once snapshotted, a script version cannot be modified. SHA-256 verified before execution.
3. **Secret isolation.** Each environment's secrets are only loaded by the broker when executing commands for that environment's agent.
4. **Audit everything.** Every promotion, deployment, rollback, health check, and bug ticket is logged with timestamps and actor identity.

---

## 20. Build Order

### Phase 1: Core Pipeline (~1 week)

1. Deployment config schema + loader (`config.yaml`)
2. Script registry + snapshot mechanism (registry, SHA-256, manifest)
3. Promotion state management (state.json, symlink creation)
4. Promotion API in Gateway (`/api/v1/deployments/promote`)
5. Per-environment broker policies
6. Deployment agent container creation (RO workspace mounts, env scoping)
7. Basic deployment execution flow (validate → execute → receipt)
8. Receipt generation
9. `file_bug_ticket` tool (wraps `gh issue create`)

### Phase 2: Safety & Observability (~1 week)

10. Rollback scripts + automatic rollback on failure
11. Health checks (periodic + post-deployment)
12. Deployment locks + queue
13. Deployment windows
14. Pre/post hooks
15. Dry-run support
16. Script validation (syntax check, dependency resolution)
17. Event notifications through Bond channels

### Phase 3: Frontend (~1 week)

18. Pipeline view (script promotion visualization)
19. Promote button (single + promote-all)
20. Environment health dashboard
21. Receipt viewer
22. Pause/resume/abort controls
23. Deployment window override

### Phase 4: Hardening (~3-5 days)

24. Context passing between environment agents
25. Script dependency ordering
26. Environment drift detection
27. Secret encryption at rest
28. Stale lock auto-release
29. Deployment log streaming to UI

---

## 21. Open Questions

1. **Script authoring.** Who writes deployment scripts — code agents or humans? If code agents, they'd need write access to the registry (only Dev). If humans, there should be a CLI or UI for script submission.

2. **Multi-repo.** If Bond manages multiple repositories, do deployment scripts live per-repo or in a central location? The current design assumes a central `~/.bond/deployments/scripts/` but per-repo might scale better.

3. **Database migrations vs. deployment scripts.** Are DB migrations a special case of deployment scripts, or a separate system? They have the same needs (ordering, rollback, environment scoping) but different tooling (Prisma, Alembic, raw SQL).

4. **Agent model.** Should deployment agents use a cheaper/faster model (they're running scripts, not writing code) or the same model as code agents (they need to troubleshoot and write detailed bug reports)?

5. **Shared environments.** What if two teams share a Staging environment? Current design assumes one owner with full control. Multi-tenant deployment would need additional access control.

6. **Parallel deployments across environments.** If "Promote to All" is clicked, should Dev→QA→Staging→UAT→Prod run sequentially (safer) or should independent environments run in parallel (faster)?
