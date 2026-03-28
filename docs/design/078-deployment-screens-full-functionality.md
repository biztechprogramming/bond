# Design Doc 078: Deployment Screens — Full Functionality

**Status:** Draft
**Date:** 2026-03-27
**Depends on:** 039 (Deployment Simplification), 061 (Pipeline-as-Code), 071 (Discovery), 077 (Port & Directory Allocation)

---

## TL;DR

The deployment UI and backend have a working skeleton — environment dashboards, quick deploy forms, deployment timelines, infra maps, and a promotion pipeline — but several user-facing actions are stubs, backend endpoints return incomplete data, and critical operational workflows (rollback execution, live log streaming, environment promotion with gates, real remote execution) are not wired end-to-end. This doc catalogs every gap and proposes the implementation plan to make the deployment screens fully functional.

**Key gaps identified:**

1. **Quick action buttons are fire-and-forget navigations to nonexistent views** — "View Logs", "Check Health", "Agent Settings", "Deploy Script", "+ Component" in `EnvironmentDashboard` call `onNavigate()` but several target views do not exist.
2. **Quick Deploy `POST /deployments/quick-deploy` generates scripts but does not execute them** — it registers scripts and auto-promotes, but never SSHes into the target server to run anything.
3. **Rollback is modeled in receipts but has no trigger UI or backend workflow** — receipts track `rolled_back` status and rollback scripts are generated, but there is no "Rollback" button or API to initiate one.
4. **Pipeline executor runs commands locally, not on remote servers** — `pipeline-executor.ts` calls `executeCommand` which runs on the gateway host, not on deployment targets.
5. **DeploymentTimeline fetches receipts but has no WebSocket/SSE for live updates** — polling is the only mechanism; in-progress deployments have no streaming output.
6. **Health check results are not surfaced in the dashboard** — `health-scheduler.ts` stores results in-memory (`Map<string, HealthStatus>`) but `EnvironmentDashboard` does not fetch or display them.
7. **Component health status is never populated** — `ComponentNode.health_status` and `last_deploy` are declared in the interface but never filled from any data source.
8. **Server metrics (CPU/RAM/Disk) rely on probe data that is rarely populated** — the probe endpoint exists but only works for SSH-type resources; the gauges show 0% for most servers.
9. **Pipeline-as-code YAML parser exists but has no UI to manage pipelines** — `pipeline-parser.ts` and `pipeline-executor.ts` are implemented but there is no frontend to create, edit, or view pipeline runs.
10. **Trigger handler stores triggers but cron triggers are never scheduled** — `cron_schedule` field exists on `DeploymentTrigger` but no cron scheduler evaluates it.

---

## 1. Problem Statement

Bond's deployment screens were built UI-first: rich React components exist for environment dashboards, quick deploy, deployment timelines, infrastructure maps, and deployment plan panels. The backend has corresponding routers, script management, a promotion workflow, and a receipt system. However, a user attempting an end-to-end deployment workflow hits dead ends:

- Clicking "Deploy" in QuickDeployForm generates scripts on disk but nothing runs on the target server. The UI shows "Deployment started successfully!" based on a 200 response, but the server remains untouched.
- The "View Logs" quick action navigates to `live-logs` view which does not exist as a component. Log data exists on disk (`~/.bond/deployments/logs/`) and the `readLog` / `collectLogs` functions work, but there is no streaming UI.
- Rollback scripts are generated alongside deploy scripts (in `quick-deploy.ts`) but there is no API endpoint or UI to trigger a rollback.
- The promotion pipeline view (`/deployments/pipeline`) returns data but the frontend does not render a promotion gate UI where users can approve/reject promotions.
- Component health status fields (`health_status`, `last_deploy`) in `EnvironmentDashboard` are always `undefined` because no backend populates them.
- Server CPU/RAM/Disk gauges show 0% because probe results only populate `stateJson` for SSH resources, and most resources have never been probed.

### Impact

- Users cannot complete a deployment from the UI without manual SSH intervention
- Operational visibility (health, logs, metrics) is placeholders showing empty/zero states
- The promotion workflow exists in the backend but is invisible in the frontend
- Rollback — arguably the most critical operational action — has no user-facing path

---

## 2. Proposed Solution

Close all gaps across four workstreams:

### 2.1 Workstream A: Remote Execution Pipeline

**Goal:** When a user clicks "Deploy", the script actually runs on the target server.

**Changes:**
- **Gateway:** Add `executeRemoteDeployment(resourceId, scriptId, version, env)` that resolves the resource's SSH connection from `connectionJson`, uploads the script via SCP/SFTP, executes it over SSH, streams stdout/stderr back, and writes a receipt.
- **QuickDeployForm backend (`quick-deploy.ts`):** After script registration and promotion, call `executeRemoteDeployment` instead of returning immediately. Return a `run_id` for the frontend to track.
- **Pipeline executor (`pipeline-executor.ts`):** Replace `executeCommand` (local) with `executeRemoteCommand` that routes to the correct resource based on the pipeline job's `runs-on` field.
- **Frontend:** QuickDeployForm should transition to a "deployment in progress" view showing streaming output (see Workstream C).

### 2.2 Workstream B: Missing Views & Actions

**Goal:** Every quick action button and navigation target has a functioning view.

| Navigation target | Status | Action required |
|---|---|---|
| `add-component` | No component creation form exists | Build `AddComponentForm` that calls SpacetimeDB `addComponent` reducer |
| `add-server` | Exists (`AddServerPanel`) | Already functional, no changes |
| `deploy-script` | Navigates to script list | Wire to `ScriptRegistration` view with environment pre-selected |
| `live-logs` | No view exists | Build `LiveLogsPanel` — fetches from `GET /deployments/logs/{env}`, supports tailing via byte offset, optional WebSocket for real-time streaming |
| `check-health` | No view exists | Build `HealthCheckPanel` — calls `POST /deployments/health/{env}/check` to trigger immediate health check, displays results from `GET /deployments/health/{env}` |
| `agent-settings` | No view exists | Build `AgentSettingsPanel` — shows agent status, pause/resume controls using existing `POST /deployments/agents/{id}/pause` endpoint |
| `component-detail` | No view exists | Build `ComponentDetailPanel` — shows component config, associated resources, deploy history, health status |
| `receipts` (full list) | No dedicated view | Build `ReceiptListPanel` — paginated receipt browser with filters by script, status, date range |
| Promotion gates UI | Backend exists, no frontend | Build `PromotionGatePanel` — shows pending promotions, approve/reject buttons, approval counts |

### 2.3 Workstream C: Live Streaming & Real-Time Updates

**Goal:** Deployment output streams to the UI in real time; dashboards update without polling.

**Changes:**
- **Gateway:** Add SSE endpoint `GET /deployments/runs/{runId}/stream` that tails the deployment log file and sends lines as SSE events. Heartbeat every 15s.
- **Gateway:** Add SSE endpoint `GET /deployments/logs/{env}/stream` for live log tailing across all deployments in an environment.
- **Frontend:** `DeploymentTimeline` subscribes to an SSE stream for in-progress deployments instead of polling every 60 seconds.
- **Frontend:** `EnvironmentDashboard` already uses SpacetimeDB subscriptions for resources/components/alerts (no polling needed for those), but receipts still poll. Move receipts to SSE or SpacetimeDB table.

### 2.4 Workstream D: Rollback & Operational Actions

**Goal:** Users can trigger rollback from the UI; health check failures can auto-trigger rollback.

**Changes:**
- **Gateway:** Add `POST /deployments/rollback` endpoint accepting `{ receipt_id, environment }`. Looks up the rollback script from the receipt's associated script version, executes it on the target resource, writes a new receipt with `type: "rollback"`.
- **Frontend:** Add "Rollback" button on each receipt row in the deployment timeline and receipt list. Confirmation dialog showing what will be rolled back.
- **Gateway:** Add `POST /deployments/promote/{scriptId}/{version}` for manual environment promotion (already partially exists in `promotion.ts`, needs UI wiring).
- **Frontend:** Build promotion gate UI showing the pipeline progression (dev -> staging -> prod) with approve/reject at each gate.
- **Health-triggered rollback:** When `health-scheduler.ts` detects an unhealthy status after a deployment, optionally auto-trigger rollback if the environment has `auto_rollback: true` configured.

---

## 3. Data Model Changes

### 3.1 New Table: `deployment_run`

Tracks active and historical deployment executions (distinct from receipts, which are the final audit record).

```
deployment_run {
  id: String (ULID)
  script_id: String
  script_version: String
  environment: String
  resource_id: String
  status: "queued" | "running" | "success" | "failed" | "cancelled"
  started_at: u64
  finished_at: u64
  triggered_by: String          // user ID or "trigger:{trigger_id}" or "auto-rollback"
  log_file: String              // path to log file for streaming
  receipt_id: String            // FK to receipt once completed
  run_type: String              // "deploy" | "rollback" | "health-check"
}
```

### 3.2 New Table: `component_status`

Persists component health and last deploy info (currently only in-memory/untracked).

```
component_status {
  id: String (ULID)
  component_id: String          // FK to component
  environment_name: String
  health_status: String         // "healthy" | "degraded" | "offline" | "unknown"
  last_deploy_script: String
  last_deploy_version: String
  last_deploy_status: String
  last_deploy_at: u64
  last_health_check_at: u64
  updated_at: u64
}
```

### 3.3 Extension: `deployment_environment`

Add fields to existing environment table:

```
auto_rollback: bool             // default false
rollback_on_health_failure: bool // default false
max_rollback_window_seconds: u32 // 0 = no limit
```

---

## 4. API Endpoints (New or Modified)

| Method | Path | Description |
|---|---|---|
| `POST` | `/deployments/runs` | Start a deployment run (replaces the implicit quick-deploy execution) |
| `GET` | `/deployments/runs/{id}` | Get run status |
| `GET` | `/deployments/runs/{id}/stream` | SSE stream of run output |
| `POST` | `/deployments/runs/{id}/cancel` | Cancel a running deployment |
| `POST` | `/deployments/rollback` | Initiate rollback for a receipt |
| `GET` | `/deployments/health/{env}` | Get latest health status (already exists, needs component-level detail) |
| `POST` | `/deployments/health/{env}/check` | Trigger immediate health check |
| `GET` | `/deployments/logs/{env}/stream` | SSE stream for environment logs |
| `GET` | `/deployments/components/{id}/status` | Get component health + deploy status |
| `GET` | `/deployments/pipeline/gates` | List pending promotion gates |
| `POST` | `/deployments/pipeline/gates/{id}/approve` | Approve a promotion gate |
| `POST` | `/deployments/pipeline/gates/{id}/reject` | Reject a promotion gate |

---

## 5. Implementation Plan

### Phase 1: Remote Execution (1 week)
1. Implement `executeRemoteDeployment` in gateway using SSH2 library (already used by `resource-probe.ts`)
2. Wire `quick-deploy.ts` to call remote execution after script registration
3. Add `deployment_run` table and `POST /deployments/runs` endpoint
4. Add SSE streaming endpoint for run output
5. Update `QuickDeployForm` to show streaming deployment output

### Phase 2: Missing Views (1 week)
1. Build `LiveLogsPanel` with SSE tailing
2. Build `HealthCheckPanel` displaying `HealthStatus` data
3. Build `ComponentDetailPanel` with status from new `component_status` table
4. Build `AddComponentForm` using existing SpacetimeDB reducers
5. Wire all quick action buttons to their respective views

### Phase 3: Rollback & Promotion UI (1 week)
1. Add `POST /deployments/rollback` endpoint
2. Add rollback button to receipt rows in `DeploymentTimeline`
3. Build `PromotionGatePanel` showing pipeline progression
4. Wire approve/reject to existing promotion API
5. Add `ReceiptListPanel` with full pagination and filtering

### Phase 4: Operational Polish (1 week)
1. Populate `component_status` from health checks and deploy receipts
2. Wire component health/deploy data into `EnvironmentDashboard` component cards
3. Implement cron trigger scheduling (evaluate `cron_schedule` field on `DeploymentTrigger`)
4. Add auto-rollback on health failure (configurable per environment)
5. Add `AgentSettingsPanel` view

---

## 6. Open Questions

1. **Should `deployment_run` live in SpacetimeDB or remain file-based like receipts?** SpacetimeDB enables real-time subscriptions (no SSE needed), but runs can generate large log output that may not suit SpacetimeDB's storage model. Hybrid approach: metadata in SpacetimeDB, log content on disk, SSE for streaming.

2. **Rollback scope:** Should rollback re-execute the previous version's deploy script, or execute the current version's rollback script? The current `quick-deploy.ts` generates a dedicated `rollback.sh` that undoes the specific deploy, which is more precise but version-coupled.

3. **Pipeline executor parallelism:** `pipeline-executor.ts` currently executes jobs sequentially. Should Phase 1 add DAG-based parallel execution (respecting `needs` dependencies), or defer this to a later doc?

4. **Promotion gate notifications:** When a deployment is pending approval, should the system send notifications (email, Slack webhook)? This is out of scope for this doc but should be addressed.

5. **Multi-server deployments:** QuickDeployForm targets a single environment but an environment can have multiple servers (via `resource_environment` join table). Should `executeRemoteDeployment` fan out to all servers in the environment, or require the user to select a specific server?

---

## 7. Appendix: Current Gap Inventory

### Frontend Gaps

| File | Gap | Severity |
|---|---|---|
| `EnvironmentDashboard.tsx` | Quick actions navigate to 4 nonexistent views (`live-logs`, `check-health`, `agent-settings`, `component-detail`) | High |
| `EnvironmentDashboard.tsx` | `ComponentNode.health_status` and `last_deploy` never populated — always shows "unknown" | Medium |
| `EnvironmentDashboard.tsx` | Server CPU/RAM/Disk gauges show 0% — `stateJson` rarely has metrics | Medium |
| `QuickDeployForm.tsx` | Deploy button creates scripts but nothing runs remotely; gracefully handles 404 with "not available yet" message | Critical |
| `QuickDeployForm.tsx` | No deployment progress or streaming output after clicking Deploy | High |
| `DeploymentTimeline.tsx` | No live updates for in-progress deployments; 60s receipt polling only | Medium |
| `DeploymentTimeline.tsx` | No rollback action on receipt dots | High |
| `InfraMap.tsx` | Probe only works for SSH resources; no feedback on probe failure reason | Low |
| `DeploymentPlanPanel.tsx` | "Ship It" button calls `onShipIt` callback but the parent must wire actual deployment — currently a pass-through | High |
| No file | No promotion gate UI exists despite backend support | High |
| No file | No pipeline run viewer despite `pipeline-executor.ts` existing | Medium |

### Backend Gaps

| File | Gap | Severity |
|---|---|---|
| `quick-deploy.ts` | Generates scripts and promotes but never executes remotely | Critical |
| `pipeline-executor.ts` | `executeCommand` runs locally on gateway host, not on target servers | Critical |
| `pipeline-parser.ts` / tests | Pipeline tests are placeholder — verify JS objects, not actual YAML parsing | Low |
| `resource-probe.ts:409` | Probe returns "not implemented" for non-SSH resource types | Medium |
| `health-scheduler.ts` | Health results stored in-memory `Map`, lost on restart | Medium |
| `trigger-handler.ts` | `cron_schedule` field stored but never evaluated by any scheduler | Medium |
| `promotion.ts` | Promotion workflow complete but no auto-deploy after approval (promotion just changes status) | High |
| `receipts.ts` | Rollback script referenced but no endpoint to trigger rollback execution | High |
| `log-stream.ts` | File-based log append/read works but no SSE/WebSocket streaming | Medium |
| `proposal-generator.ts` | All improvement scripts are placeholder `echo` statements | Low |
