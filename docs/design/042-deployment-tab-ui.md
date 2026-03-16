# Design Doc 042: Deployment Tab UI

**Status:** Draft
**Date:** 2026-03-13
**Depends on:** 039 (Deployment Agents)

---

## 1. The Problem

Doc 039 defines the deployment agent architecture — per-environment agents, broker-mediated execution, promotion pipelines — but provides no streamlined UI for creating and managing these agents. Today, users must manually create each deployment agent one-by-one through the generic Agents tab, set the correct slug naming convention, configure read-only mounts, and hope they got it right.

This is error-prone and tedious. Deployment agents are a cohort — they share most configuration and differ only in environment-specific overrides. The UI should reflect that: create all at once, edit shared settings in one place, override per-agent only when needed.

---

## 2. Goals

1. **Zero-to-deployed in one click.** A user with no deployment agents can create all five (or however many environments exist) in a single action.
2. **Personal names.** Each agent gets a human-chosen display name ("Ace", "Nova", "Sentinel") rather than just "deploy-dev". The slug (`deploy-{env}`) is auto-generated and immutable.
3. **Shared defaults with per-agent overrides.** Model, utility model, sandbox image, and system prompt are set once and cascade to all agents. Individual agents can override any field, shown with a visual indicator.
4. **Edit one or edit all.** The UI supports both single-agent editing (click a card) and bulk editing (shared settings propagate).
5. **Pipeline visibility.** The tab shows deployment pipelines — which scripts exist, their promotion status, and which agents have access.
6. **Consistency with existing UI.** Same visual language as the Agents, Channels, and Prompts tabs.

---

## 3. Open Source Inspiration

| Project | Pattern to adopt | Link |
|---|---|---|
| **Coolify** | Environment management — clean settings cards, environment-scoped config, create-from-template flow | [github.com/coollabsio/coolify](https://github.com/coollabsio/coolify) |
| **Backstage** (Spotify) | Scaffolder — "create all from template" wizard, entity cards with status badges | [github.com/backstage/backstage](https://github.com/backstage/backstage) |
| **Argo CD** | Pipeline visualization — environment progression arrows, sync status indicators | [github.com/argoproj/argo-cd](https://github.com/argoproj/argo-cd) |
| **Woodpecker CI** | Simple pipeline UI — minimal, fast, status-at-a-glance cards | [github.com/woodpecker-ci/woodpecker](https://github.com/woodpecker-ci/woodpecker) |
| **Rundeck** | Job execution with environment scoping, approval workflows, run history | [github.com/rundeck/rundeck](https://github.com/rundeck/rundeck) |
| **Vercel** | Environment variables UI — set once / override per-env pattern, expand/collapse | vercel.com (proprietary, but the UX pattern is well-documented) |

**Primary influence:** Coolify's environment management + Backstage's scaffolder + Vercel's "shared defaults with per-env overrides" pattern.

---

## 4. UI Structure

The Deployment tab lives alongside Agents, Channels, Prompts, LLM, Embedding, and API Keys in the Settings page.

### 4.1 Tab Registration

```typescript
const TABS = [
  { id: "agents", label: "Agents" },
  { id: "deployment", label: "Deployment" },  // NEW
  { id: "channels", label: "Channels" },
  { id: "prompts", label: "Prompts" },
  { id: "llm", label: "LLM" },
  { id: "embedding", label: "Embedding" },
  { id: "api-keys", label: "API Keys" },
];
```

### 4.2 Component

```
frontend/src/app/settings/deployment/DeploymentTab.tsx
```

---

## 5. States

The tab has three visual states based on whether deployment agents exist.

### 5.1 Empty State — Setup Wizard

Shown when no agents with `deploy-*` slugs exist.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│     🚀  Set Up Deployment Agents                                       │
│                                                                         │
│     Create agents for your deployment pipeline. Each environment        │
│     gets its own agent with read-only access to code and deployment     │
│     capabilities through the broker.                                    │
│                                                                         │
│  ┌─── Shared Settings ────────────────────────────────────────────────┐ │
│  │                                                                     │ │
│  │  Model               Utility Model          Sandbox Image          │ │
│  │  [claude-sonnet-4 ▼]  [gemini-flash ▼]      [bond-deploy-agent ▼] │ │
│  │                                                                     │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ DEV      │  │ QA       │  │ STAGING  │  │ UAT      │  │ PROD     │ │
│  │          │  │          │  │          │  │          │  │          │ │
│  │ Name:    │  │ Name:    │  │ Name:    │  │ Name:    │  │ Name:    │ │
│  │ [Ace   ] │  │ [Nova  ] │  │ [Sage  ] │  │ [Echo  ] │  │ [Apex  ] │ │
│  │          │  │          │  │          │  │          │  │          │ │
│  │ slug:    │  │ slug:    │  │ slug:    │  │ slug:    │  │ slug:    │ │
│  │ deploy-  │  │ deploy-  │  │ deploy-  │  │ deploy-  │  │ deploy-  │ │
│  │ dev      │  │ qa       │  │ staging  │  │ uat      │  │ prod     │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘ │
│                                                                         │
│                      [ 🚀 Create All Agents ]                           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

1. Environments are fetched from the Gateway API (`GET /api/v1/deployments/environments`). If no environments exist yet, the default set (dev, qa, staging, uat, prod) is shown.
2. Each card has a single editable field: the personal **display name**. The slug is auto-generated and shown as read-only.
3. Shared settings (model, utility model, sandbox image) apply to all agents.
4. "Create All Agents" calls the agents API once per environment, creating each with:
   - `name`: `deploy-{env}` (immutable slug)
   - `display_name`: user-entered personal name
   - `model`, `utility_model`, `sandbox_image`: from shared settings
   - `workspace_mounts`: auto-populated from existing code agent mounts, all set to `readonly: true`
   - `channels`: webchat enabled by default
   - `system_prompt`: deployment agent default (see §6)

### 5.2 Populated State — Dashboard

Shown when deployment agents exist.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Deployment Agents                                         [Edit All]  │
│                                                                         │
│  Shared: claude-sonnet-4 · gemini-flash · bond-deploy-agent            │
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────┐│
│  │ ● DEV    │    │ ● QA     │    │ ● STAGING│    │ ● UAT    │    │●PRO││
│  │ "Ace"    │ →  │ "Nova"   │ →  │ "Sage"   │ →  │ "Echo"   │ →  │"Ap"││
│  │          │    │          │    │ ⚙ custom │    │          │    │    ││
│  │ sonnet   │    │ sonnet   │    │ opus     │    │ sonnet   │    │opus││
│  │          │    │          │    │          │    │          │    │    ││
│  │ Healthy  │    │ Healthy  │    │Deploying │    │ Healthy  │    │Heal││
│  │ [Edit]   │    │ [Edit]   │    │ [Edit]   │    │ [Edit]   │    │[Ed]││
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘    └────┘│
│                                                                         │
│  ─── Pipelines ────────────────────────────────────────────────────── ──│
│                                                                         │
│  001-migrate-user-table (v1)                                            │
│  [✅ Dev] → [✅ QA] → [⏳ Staging] → [○ UAT] → [○ Prod]               │
│                                                                         │
│  002-add-notifications (v1)                                             │
│  [❌ Dev] → [○ QA] → [○ Staging] → [○ UAT] → [○ Prod]                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

1. Agent cards show: environment label, personal name, model (if overridden from shared, show ⚙ badge), health status, edit button.
2. Arrow connectors between cards show the promotion flow direction.
3. Clicking a card enters single-edit mode (§5.3).
4. "Edit All" opens the shared settings form at the top, making shared fields editable. Changes cascade to all agents that haven't been individually overridden.
5. Pipeline section shows scripts from the promotion database with per-environment status indicators.

### 5.3 Single-Agent Edit Mode

Clicking "Edit" on a card expands it inline (or replaces the dashboard — same pattern as AgentsTab).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Back to Dashboard                    Editing: Sage (deploy-staging) │
│                                                                         │
│  ┌─── Identity ───────────────────────────────────────────────────────┐ │
│  │  Display Name        Slug (read-only)                              │ │
│  │  [Sage            ]  deploy-staging                                │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Model ──────────────────────────────────────────────────────────┐ │
│  │                                                                     │ │
│  │  ☐ Use shared model (claude-sonnet-4)                              │ │
│  │  ☑ Override: [claude-opus-4 ▼]                                    │ │
│  │                                                                     │ │
│  │  ☐ Use shared utility model (gemini-flash)                         │ │
│  │  ☑ Override: [claude-haiku ▼]                                     │ │
│  │                                                                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── System Prompt (appended to default) ────────────────────────────┐ │
│  │  [                                                                ] │ │
│  │  [  Extra instructions for this environment...                    ] │ │
│  │  [                                                                ] │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Channels ───────────────────────────────────────────────────────┐ │
│  │  ☑ webchat  ☐ signal  ☑ telegram  ☐ discord  ☐ slack              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Workspace Mounts (read-only enforced) ──────────────────────────┐ │
│  │  /home/andrew/bond  →  /workspaces/bond  (RO)                      │ │
│  │  [+ Add Mount]                                                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  [ Save ]  [ Cancel ]  [ Reset to Shared Defaults ]                    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

1. **Display name** is freely editable. Slug is read-only.
2. **Model overrides** use a checkbox pattern: "Use shared" (default) vs "Override" with a dropdown. If overridden, the dashboard card shows a ⚙ badge.
3. **System prompt** is additive — the base deployment agent prompt (§6) is always included, this field appends environment-specific instructions.
4. **Workspace mounts** are always read-only. The RO flag is enforced and cannot be unchecked. The toggle is hidden or disabled.
5. **Reset to Shared Defaults** clears all overrides, returning the agent to the shared configuration.

### 5.4 Edit All Mode

Clicking "Edit All" on the dashboard makes the shared settings section editable.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Deployment Agents                                       [Cancel Edit] │
│                                                                         │
│  ┌─── Shared Settings (editing) ──────────────────────────────────────┐ │
│  │                                                                     │ │
│  │  Model               Utility Model          Sandbox Image          │ │
│  │  [claude-sonnet-4 ▼]  [gemini-flash ▼]      [bond-deploy-agent ▼] │ │
│  │                                                                     │ │
│  │  ⚠ 2 agents have model overrides (Sage, Apex). These will NOT     │ │
│  │    be changed. Click "Reset overrides" to apply shared to all.     │ │
│  │                                                                     │ │
│  │  [ Save Shared Settings ]  [ Reset All Overrides & Save ]          │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  (agent cards below, same as dashboard, read-only while editing shared) │
└─────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**

1. Editing shared settings only affects agents that are using the shared default (no override).
2. A warning shows which agents have overrides and won't be changed.
3. "Reset All Overrides & Save" is the nuclear option — applies shared settings to every agent, clearing all individual overrides.
4. Per-agent cards are read-only during shared editing to prevent confusion.

---

## 6. Default System Prompt

All deployment agents receive this base system prompt. Per-agent overrides append to it.

```
You are a deployment agent for the {environment} environment.

Your role:
- Execute deployment scripts that have been promoted to your environment
- Run health checks and monitor environment state
- File detailed bug tickets when deployments fail
- Read code for troubleshooting (you have read-only access)

Your constraints:
- You CANNOT modify code. All workspace mounts are read-only.
- You CANNOT promote scripts. Only users can promote via the UI.
- You CANNOT access secrets directly. The broker injects them during execution.
- You CANNOT deploy scripts not promoted to your environment.

When a deployment fails:
1. Review the error output from the broker
2. Read relevant source code from your workspace mounts
3. File a detailed bug ticket with diagnosis and suggested fix
4. Report the failure to the user

Environment: {environment}
```

---

## 7. Data Model

### 7.1 Shared Settings Storage

Shared deployment agent settings are stored as Bond settings (same key-value store as other settings):

```
deployment.shared.model          = "anthropic/claude-sonnet-4-20250514"
deployment.shared.utility_model  = "google/gemini-2.5-flash"
deployment.shared.sandbox_image  = "bond-deploy-agent"
```

### 7.2 Per-Agent Override Detection

An agent is "using shared defaults" when its `model` matches `deployment.shared.model`. An override is detected by inequality. No separate override flag needed — the source of truth is the agent record itself.

### 7.3 Personal Names

The `display_name` field on the existing Agent model. No schema changes needed.

### 7.4 Deployment Agent Identification

A deployment agent is identified by the naming convention: `name` starts with `deploy-` and the suffix matches an active environment name. The tab queries:

```
GET /api/v1/agents                           → all agents
GET /api/v1/deployments/environments         → all environments
```

Match: agent where `name === "deploy-{env.name}"` for each active environment.

---

## 8. API Interactions

### 8.1 Create All Agents

The "Create All Agents" button sends one `POST /api/v1/agents` per environment:

```typescript
async function createAllDeployAgents(
  environments: Environment[],
  shared: SharedSettings,
  names: Record<string, string>,  // env → personal name
) {
  const workspaceMounts = await collectWorkspaceMounts();

  const results = await Promise.all(
    environments.map((env) =>
      fetch(`${API_BASE}/agents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `deploy-${env.name}`,
          display_name: names[env.name] || env.display_name,
          model: shared.model,
          utility_model: shared.utility_model,
          sandbox_image: shared.sandbox_image,
          system_prompt: generateDeployPrompt(env.name),
          max_iterations: 25,
          auto_rag: false,
          auto_rag_limit: 0,
          workspace_mounts: workspaceMounts.map((m) => ({
            ...m,
            readonly: true,  // always enforced
          })),
          channels: [{ channel: "webchat", enabled: true, sandbox_override: null }],
        }),
      })
    )
  );

  return results;
}
```

### 8.2 Collect Workspace Mounts

Workspace mounts are auto-populated from existing non-deployment agents (deduplicated):

```typescript
async function collectWorkspaceMounts(): Promise<WorkspaceMount[]> {
  const agents = await fetch(`${API_BASE}/agents`).then((r) => r.json());
  const seen = new Set<string>();
  const mounts: WorkspaceMount[] = [];

  for (const agent of agents) {
    if (agent.name.startsWith("deploy-")) continue;  // skip other deploy agents
    for (const mount of agent.workspace_mounts || []) {
      if (!seen.has(mount.host_path)) {
        seen.add(mount.host_path);
        mounts.push({
          host_path: mount.host_path,
          mount_name: mount.mount_name,
          container_path: `/workspaces/${mount.mount_name}`,
          readonly: true,
        });
      }
    }
  }

  return mounts;
}
```

### 8.3 Save Shared Settings

```typescript
async function saveSharedSettings(shared: SharedSettings) {
  await Promise.all([
    fetch(`${SETTINGS_API}/deployment.shared.model`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: shared.model }),
    }),
    fetch(`${SETTINGS_API}/deployment.shared.utility_model`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: shared.utility_model }),
    }),
    fetch(`${SETTINGS_API}/deployment.shared.sandbox_image`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: shared.sandbox_image }),
    }),
  ]);
}
```

### 8.4 Save with Cascade

When shared settings change, update all non-overridden agents:

```typescript
async function saveSharedAndCascade(
  shared: SharedSettings,
  agents: Agent[],
  resetAll: boolean,
) {
  await saveSharedSettings(shared);

  const updates = agents.filter((a) => {
    if (resetAll) return true;
    // Only update agents currently using shared defaults
    return a.model === previousShared.model
        && a.utility_model === previousShared.utility_model;
  });

  await Promise.all(
    updates.map((agent) =>
      fetch(`${API_BASE}/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: shared.model,
          utility_model: shared.utility_model,
          sandbox_image: shared.sandbox_image,
        }),
      })
    )
  );
}
```

---

## 9. Pipeline Section

The bottom of the populated dashboard shows deployment pipelines. This reads from the promotion API.

### 9.1 Data Source

```
GET /api/v1/deployments/promotions          → all promotion records
GET /api/v1/deployments/environments        → environment order
```

### 9.2 Pipeline Row

Each script gets a row showing its status across all environments:

```typescript
interface PipelineRow {
  script_id: string;
  script_version: string;
  environments: {
    name: string;
    status: "not_promoted" | "awaiting_approvals" | "promoted"
          | "deploying" | "success" | "failed" | "rolled_back";
    receipt_id?: string;
    approvals?: { received: number; required: number };
  }[];
}
```

### 9.3 Status Indicators

| Status | Icon | Color |
|---|---|---|
| `success` | ✅ | Green |
| `deploying` | ⏳ | Blue (animated pulse) |
| `promoted` (ready) | ▶ | Blue |
| `awaiting_approvals` | 🔒 | Amber |
| `failed` | ❌ | Red |
| `rolled_back` | ↩ | Orange |
| `not_promoted` | ○ | Gray |

### 9.4 Pipeline Actions

From the pipeline row, users can:
- **Click a status indicator** → opens receipt viewer (if receipt exists)
- **"Promote"** button appears after a successful deployment, promoting to the next environment
- **"Promote to All"** button appears when script has succeeded in the first environment

These actions call the Promotion API from Doc 039 §6.5.

---

## 10. Component Structure

```
frontend/src/app/settings/deployment/
├── DeploymentTab.tsx          # Main tab component (state machine: empty/dashboard/edit)
├── SetupWizard.tsx            # Empty state — create all agents form
├── AgentCardGrid.tsx          # Dashboard — agent cards with arrows
├── AgentCard.tsx              # Single agent card (display + edit modes)
├── SharedSettingsForm.tsx     # Shared model/utility/sandbox form
├── SingleAgentEditor.tsx      # Expanded edit form for one agent
├── PipelineSection.tsx        # Script promotion pipeline view
├── PipelineRow.tsx            # Single script across environments
└── StatusIndicator.tsx        # ✅ ⏳ ❌ ○ etc.
```

---

## 11. Interaction Flows

### 11.1 First-Time Setup

```
1. User navigates to Settings → Deployment
2. Tab detects no deploy-* agents → shows SetupWizard
3. User optionally changes shared model/utility/sandbox
4. User enters personal names for each environment card
   (or leaves defaults: environment display names)
5. User clicks "Create All Agents"
6. Tab creates agents sequentially, shows progress
7. On completion → switches to Dashboard view
8. All agents appear as cards with health status "Starting..."
```

### 11.2 Edit One Agent

```
1. User clicks [Edit] on "Sage" (deploy-staging)
2. Dashboard transitions to SingleAgentEditor
3. User changes model to opus, adds system prompt note
4. User clicks [Save]
5. Agent updated via PUT /api/v1/agents/{id}
6. Returns to Dashboard — Sage card now shows ⚙ badge
```

### 11.3 Edit All (Change Shared Model)

```
1. User clicks [Edit All]
2. SharedSettingsForm becomes editable
3. User changes model from sonnet to opus
4. Warning appears: "Sage has a model override — won't be changed"
5. User clicks [Save Shared Settings]
6. Shared settings saved, Ace/Nova/Echo updated to opus
7. Sage stays on its existing override
8. Returns to Dashboard
```

### 11.4 Reset Agent to Shared

```
1. User clicks [Edit] on "Sage"
2. User clicks [Reset to Shared Defaults]
3. Confirmation: "This will clear all overrides for Sage"
4. Agent updated with shared model/utility/sandbox
5. ⚙ badge removed from card
```

---

## 12. Workspace Mount Enforcement

Deployment agents must have read-only workspace mounts. The UI enforces this at multiple levels:

1. **Setup Wizard** — mounts are auto-collected from existing agents, `readonly` forced to `true`. No toggle shown.
2. **Single Agent Editor** — mount list shows "(RO)" badge. The readonly checkbox is present but disabled with a tooltip: "Deployment agents always have read-only access."
3. **API-level** — the Gateway should validate that `deploy-*` agents cannot have `readonly: false` mounts. This is a backend enforcement, not just UI.

---

## 13. Responsive Layout

Agent cards use CSS grid with `auto-fill`:

```css
grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
```

- **5 environments, wide screen** → 5 cards in a row with arrows
- **5 environments, narrow screen** → 2-3 per row, arrows wrap naturally
- **Arrow connectors** → CSS `::after` pseudo-elements on cards (hidden on last card and when wrapping)

---

## 14. Progressive CI Complexity: Coolify → Woodpecker → Full Pipeline

Bond's deployment system should meet users where they are. Someone deploying a side project shouldn't need to understand promotion pipelines and approval workflows. Someone running production infrastructure shouldn't be limited to push-to-deploy. The UI provides three tiers of complexity, each building on the last.

### 14.1 Tier 1: Push-to-Deploy (Coolify Pattern)

**Who it's for:** Solo developers, side projects, internal tools, "I just want this deployed."

**Coolify's core insight:** Connect a repo, pick a branch, click deploy. No YAML, no pipeline files, no CI config. The platform figures out how to build and deploy. Preview deployments on PRs come free.

**How Bond adopts this:**

The Deployment tab's Setup Wizard (§5.1) already creates all agents in one click. Tier 1 extends this with a "Quick Deploy" mode that skips the full promotion pipeline:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Quick Deploy                                              [Advanced ↓]│
│                                                                         │
│  ┌─── Connect Repository ─────────────────────────────────────────────┐ │
│  │  Repository    [github.com/yourorg/yourapp           ] [Connect]   │ │
│  │  Branch        [main ▼]                                            │ │
│  │  Deploy on     ☑ Push to main  ☐ Tags  ☐ Manual only              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Build ──────────────────────────────────────────────────────────┐ │
│  │  Strategy      ● Auto-detect  ○ Dockerfile  ○ Docker Compose      │ │
│  │                ○ Script (custom)                                    │ │
│  │                                                                     │ │
│  │  Detected: Node.js (package.json found)                            │ │
│  │  Build cmd:    [npm run build          ]                           │ │
│  │  Start cmd:    [npm start              ]                           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Target ─────────────────────────────────────────────────────────┐ │
│  │  Environment   [dev ▼]  (single environment — no promotion)        │ │
│  │  Port          [3000        ]                                      │ │
│  │  Health check  [/health     ]  (optional)                          │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─── Environment Variables ──────────────────────────────────────────┐ │
│  │  DATABASE_URL    [postgresql://...                    ] [🔒 Secret]│ │
│  │  NODE_ENV        [production                         ]             │ │
│  │  [+ Add Variable]                                                  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│                          [ 🚀 Deploy ]                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

**What happens on "Deploy":**

1. Bond auto-generates a deployment script from the form inputs (build strategy, commands, port, health check).
2. Script is registered in the script registry as an immutable snapshot (same as Doc 039 §6.3).
3. Script is auto-promoted to the selected single environment (no approval needed for Tier 1).
4. The deployment agent for that environment picks it up and runs it via the broker.
5. If "Push to main" is checked, Bond registers a GitHub webhook (via the Gateway's existing webhook infrastructure) that triggers re-deploy on push.

**What users DON'T see in Tier 1:**

- No promotion pipeline (single environment)
- No approval workflows
- No pre/post hooks
- No script versioning UI (happens silently)
- No rollback UI (but rollback scripts are auto-generated from the previous successful deploy)

**Graduating to Tier 2:** The "Advanced ↓" toggle at the top reveals the full pipeline. Or the user adds a `.bond/deploy.yml` to their repo, which Bond auto-detects and switches to Tier 2.

### 14.2 Tier 2: Pipeline-as-Code (Woodpecker Pattern)

**Who it's for:** Teams, multi-environment deployments, "I want CI/CD but I want it in my repo."

**Woodpecker's core insight:** A simple YAML file in your repo defines the entire pipeline. Steps run in containers. Plugins are just containers with a known interface. Conditions control when steps run.

**How Bond adopts this:**

Bond reads a `.bond/deploy.yml` (or `.bond/deploy.yaml`) from any connected repository. This file defines deployment steps, and Bond's deployment agents execute them through the broker.

**Pipeline file format:**

```yaml
# .bond/deploy.yml
#
# Bond Deployment Pipeline
# Woodpecker-inspired syntax adapted for Bond's broker-mediated execution.

pipeline: myapp

# When to trigger
on:
  push:
    branches: [main]
  tag:
    pattern: "v*"
  manual: true          # allow manual trigger from UI

# Build steps — run in containers via the broker
steps:
  - name: test
    image: node:22
    commands:
      - npm ci
      - npm test
    when:
      event: [push, tag]

  - name: build
    image: node:22
    commands:
      - npm ci
      - npm run build
    depends_on: [test]

  - name: migrate
    image: postgres:16
    commands:
      - psql "$DATABASE_URL" -f migrations/latest.sql
    secrets: [DATABASE_URL]
    depends_on: [build]

  - name: deploy
    image: bond-deploy-agent
    commands:
      - ./scripts/deploy.sh
    secrets: [DEPLOY_KEY, SERVER_HOST]
    depends_on: [migrate]

  - name: health-check
    image: curlimages/curl
    commands:
      - curl -f "$APP_URL/health" || exit 1
    secrets: [APP_URL]
    depends_on: [deploy]
    when:
      status: success     # only run if deploy succeeded

  - name: notify
    image: bond-deploy-agent
    commands:
      - gh issue create --title "Deploy failed" --body "$BOND_STEP_OUTPUT"
    when:
      status: failure     # only run on failure

# Environment promotion (optional — omit for single-env)
environments:
  - name: dev
    auto_promote: true    # auto-promote on successful build
  - name: qa
    auto_promote: true    # auto-promote after dev succeeds
  - name: staging
    auto_promote: false   # manual promotion required
    approval:
      required: 1
  - name: prod
    auto_promote: false
    approval:
      required: 2
      approvers: [andrew, sarah]
    window:
      days: [tue, wed, thu]
      hours: "09:00-16:00"
      timezone: America/New_York

# Secrets — references to Bond's per-environment secret store
secrets:
  DATABASE_URL:
    source: environment    # injected by broker from env-specific secrets
  DEPLOY_KEY:
    source: environment
  APP_URL:
    source: environment
  SERVER_HOST:
    source: environment
```

**How Bond processes this file:**

```
1. Webhook fires (push/tag) OR user clicks "Deploy" in UI
2. Gateway reads .bond/deploy.yml from the repo (at the triggered commit)
3. Gateway parses steps + dependency graph
4. For each step:
   a. Gateway creates an immutable script snapshot in the registry
   b. Script = the step's commands, wrapped in a bash script
   c. Container image = the step's image (broker pulls + runs it)
5. Steps execute in dependency order via the broker
   a. Each step's stdout/stderr captured as a receipt
   b. Secrets injected by the broker (agent never sees them)
   c. when: conditions evaluated before each step
6. If environments are defined:
   a. Steps run in the first auto_promote environment
   b. On success, auto-promote to next auto_promote env
   c. Stop at first manual-promotion env → user sees "Promote" in UI
7. Receipt generated per step per environment
```

**UI for Tier 2:**

The pipeline YAML replaces the "Quick Deploy" form. The Deployment tab shows a richer view:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Deployment Pipelines                                                   │
│                                                                         │
│  ┌─── myapp (github.com/yourorg/yourapp) ─────────────────────────────┐│
│  │                                                                     ││
│  │  Trigger: push to main · Last run: 3m ago · Duration: 1m 42s       ││
│  │                                                                     ││
│  │  Steps:                                                             ││
│  │  ┌──────┐   ┌──────┐   ┌──────┐   ┌──────┐   ┌──────────┐        ││
│  │  │ test │ → │build │ → │migr. │ → │deploy│ → │health-chk│        ││
│  │  │  ✅  │   │  ✅  │   │  ✅  │   │  ✅  │   │    ✅    │        ││
│  │  │ 32s  │   │ 45s  │   │  3s  │   │ 18s  │   │    4s    │        ││
│  │  └──────┘   └──────┘   └──────┘   └──────┘   └──────────┘        ││
│  │                                                                     ││
│  │  Environments:                                                      ││
│  │  [✅ Dev] → [✅ QA] → [▶ Staging: Promote] → [○ Prod]             ││
│  │                                                                     ││
│  └─────────────────────────────────────────────────────────────────────┘│
│                                                                         │
│  Recent Runs                                                            │
│  ┌──────────────────────────────────────────────────────────────────── ┐│
│  │  #14  ✅  main  abc1234  "fix user auth"      3m ago    1m 42s    ││
│  │  #13  ✅  main  def5678  "add email verify"   2h ago    2m 01s    ││
│  │  #12  ❌  main  ghi9012  "update deps"        5h ago    0m 33s    ││
│  │       └─ Step failed: test (exit 1) — [View Log] [View Ticket #48]││
│  └────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
```

**Woodpecker patterns adopted:**

| Woodpecker Pattern | Bond Adaptation |
|---|---|
| Steps run in containers | Steps run via broker — broker pulls the image and executes commands on the host in that container |
| Plugins are containers | Bond "plugins" are step images with known env var interfaces (e.g., a Slack notification plugin container) |
| Matrix builds | `matrix:` key in YAML → Bond generates step variants (e.g., test against Node 20 + 22) |
| Secrets from UI | Secrets stored in Bond's per-environment encrypted store, injected by broker |
| Conditions (`when:`) | Same syntax — branch, event, status, tag pattern |
| Services (databases for tests) | `services:` key → broker starts sidecar containers before steps run |
| Cron triggers | `on: cron:` → Bond's heartbeat system triggers the pipeline on schedule |

**What Tier 2 adds over Tier 1:**

- Multi-step pipelines with dependency ordering
- Container-per-step (not just the deploy agent image)
- Conditional execution (on success/failure, branch, tag)
- Pipeline-as-code (version controlled in the repo)
- Build history with per-step logs
- Multi-environment with optional auto-promotion

### 14.3 Tier 3: Full Bond Pipeline (Doc 039)

**Who it's for:** Regulated environments, multi-team deployments, "I need approvals, audit trails, and deployment windows."

Tier 3 is the full Doc 039 system. Everything from Tier 2 plus:

- Multi-approver workflows with threshold-based promotion
- Deployment windows with emergency override
- Drift detection between deployments
- Pre/post hooks per environment
- Manual intervention escape hatch (pause/resume/abort)
- Full audit trail in SpacetimeDB
- Context passing between environment agents (receipt chaining)

The UI doesn't change dramatically from Tier 2 — it gains additional controls:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Environments:                                                          │
│  [✅ Dev] → [✅ QA] → [🔒 Staging: 1/2 approvals] → [○ Prod]         │
│                         │                                               │
│                         ├── ✅ andrew (approved 2m ago)                 │
│                         ├── ⏳ sarah (pending)                          │
│                         └── ⏳ mike (pending)                           │
│                                                                         │
│  ⚠ Prod deployment window: Tue-Thu 09:00-16:00 ET                     │
│  ⚠ Staging: disk usage 87% (threshold 90%)                             │
│                                                                         │
│  [Promote to Staging] [Promote to All]                                  │
│  [Pause Staging Agent] [View Drift Report]                              │
└─────────────────────────────────────────────────────────────────────────┘
```

**Tier 3 is not a separate mode** — it's what you get when you configure approvals, windows, and hooks on your environments. A Tier 2 pipeline becomes Tier 3 by adding `approval:` and `window:` blocks to the YAML or by configuring them in the Environment Settings UI.

### 14.4 Tier Progression — How the UI Adapts

The UI doesn't ask users to choose a tier. It detects complexity and shows the right controls:

```
┌─────────────────────────────────────────────────────────────────────┐
│ What the user does                → What the UI shows              │
├─────────────────────────────────────────────────────────────────────┤
│ Connects a repo, clicks Deploy    → Quick Deploy form (Tier 1)     │
│ Adds .bond/deploy.yml to repo     → Pipeline view with steps       │
│                                      (Tier 2 auto-detected)        │
│ Adds environments: block to YAML  → Multi-env promotion bar        │
│ Configures approvals in UI/YAML   → Approval workflow controls     │
│                                      (Tier 3 emerges)              │
│ Adds deployment windows           → Window warnings + override btn │
│ Enables health checks             → Health dashboard section       │
└─────────────────────────────────────────────────────────────────────┘
```

No mode switches. No "upgrade to enterprise." Features appear when you need them.

### 14.5 How Bond Orchestrates Steps Differently from Woodpecker

Woodpecker runs steps in its own worker process. Bond delegates to deployment agents via the broker. The key difference:

```
Woodpecker:
  Webhook → Woodpecker Server → Woodpecker Agent → Container → Steps

Bond:
  Webhook → Gateway → Parse YAML → Register script snapshots
         → Notify deploy-{env} agent → Agent calls broker
         → Broker pulls step image → Broker runs in container on host
         → Broker returns stdout/stderr → Agent evaluates + reports
```

**Why this matters:** The deployment agent adds intelligence. It doesn't just run steps blindly — it reads error output, correlates with source code (from its RO mounts), files bug tickets with diagnosis, and reports to the user in natural language. Woodpecker gives you logs. Bond gives you analysis.

```
Woodpecker on failure:
  "Step 'migrate' failed with exit code 1"
  [View Log]

Bond on failure:
  "Migration failed — the email_verified column already exists in the
   users table (likely from a partial run of migration #12). The error
   is on line 14 of migrations/latest.sql. Rollback executed successfully.
   I've filed issue #48 with the full diagnosis and a suggested fix:
   wrap the ALTER TABLE in an IF NOT EXISTS check."
  [View Receipt] [View Issue #48] [Retry]
```

### 14.6 Coolify Patterns: What to Adopt, What to Skip

| Coolify Feature | Adopt? | Rationale |
|---|---|---|
| **Auto-detect build strategy** (Nixpacks) | ✅ Adopt | Scan repo for Dockerfile, package.json, requirements.txt, go.mod → suggest build commands |
| **Preview deployments for PRs** | ✅ Adopt | Deploy PR to ephemeral environment, comment preview URL on the PR. Ties into Bond's GitHub integration |
| **Server/destination management** | ⚠️ Adapt | Coolify manages remote servers via SSH. Bond's equivalent is the broker executing on the host or via configured SSH targets in env secrets |
| **Wildcard domains + SSL** | ❌ Skip | Infrastructure concern, not Bond's job. Users configure their own reverse proxy |
| **Database provisioning** | ❌ Skip | Too much scope. Bond deploys apps, not infrastructure |
| **S3 storage management** | ❌ Skip | Same — infrastructure, not deployment |

### 14.7 Woodpecker Patterns: What to Adopt, What to Skip

| Woodpecker Feature | Adopt? | Rationale |
|---|---|---|
| **YAML pipeline syntax** | ✅ Adopt | Clear, version-controlled, familiar to CI users. Bond's `.bond/deploy.yml` |
| **Steps in containers** | ✅ Adopt | Isolation, reproducibility, any language/tool |
| **Plugins as containers** | ✅ Adopt | Extensible — Slack notify, S3 upload, etc. as reusable step images |
| **Matrix builds** | ✅ Adopt | Test across versions/platforms in parallel |
| **Conditions (when:)** | ✅ Adopt | Branch/tag/event/status filtering |
| **Services (sidecar containers)** | ✅ Adopt | DB for tests, Redis for integration tests |
| **Cron schedules** | ✅ Adopt | Map to Bond's heartbeat system |
| **Multi-platform (ARM/x86)** | ❌ Skip | Not relevant for Bond's deployment use case |
| **Kubernetes backend** | ❌ Skip | Bond runs on a single host with broker-mediated execution |
| **Repository forking model** | ❌ Skip | Bond has its own repo model via GitHub integration |

### 14.8 Pipeline YAML Processing — Gateway Implementation

The Gateway handles YAML parsing and step registration. Deployment agents never see the raw YAML.

```typescript
// gateway/src/deployments/pipeline-parser.ts

interface PipelineStep {
  name: string;
  image: string;
  commands: string[];
  secrets: string[];
  depends_on: string[];
  when: StepCondition;
  services?: ServiceDef[];
}

interface Pipeline {
  name: string;
  on: TriggerConfig;
  steps: PipelineStep[];
  environments?: EnvironmentConfig[];
  secrets: Record<string, SecretSource>;
  matrix?: Record<string, string[]>;
}

async function processPipeline(
  repo: string,
  commit: string,
  event: TriggerEvent,
): Promise<PipelineRun> {
  // 1. Fetch .bond/deploy.yml from repo at commit
  const yaml = await fetchFileFromRepo(repo, commit, ".bond/deploy.yml");
  const pipeline = parsePipelineYaml(yaml);

  // 2. Evaluate trigger conditions
  if (!matchesTrigger(pipeline.on, event)) {
    return { status: "skipped", reason: "Trigger conditions not met" };
  }

  // 3. Expand matrix (if any)
  const expandedSteps = expandMatrix(pipeline.steps, pipeline.matrix);

  // 4. Build dependency graph
  const graph = buildDependencyGraph(expandedSteps);
  validateNoCycles(graph);

  // 5. Register each step as an immutable script snapshot
  for (const step of topologicalSort(graph)) {
    const scriptId = `${pipeline.name}-${step.name}-${commit.slice(0, 8)}`;
    await registerStepAsScript(scriptId, step);
  }

  // 6. Determine target environment
  const targetEnv = resolveTargetEnvironment(pipeline.environments, event);

  // 7. Auto-promote to target environment (if auto_promote)
  if (targetEnv.auto_promote) {
    await promoteScripts(pipeline.name, commit, targetEnv.name);
  }

  // 8. Notify the deployment agent
  await notifyAgent(`deploy-${targetEnv.name}`, {
    pipeline: pipeline.name,
    commit,
    steps: expandedSteps.map((s) => s.name),
  });

  return { status: "started", environment: targetEnv.name };
}
```

### 14.9 Step Execution via Broker

When the deployment agent receives a pipeline notification, it executes steps through the broker's `/deploy` endpoint. The broker handles container orchestration:

```typescript
// gateway/src/broker/deploy-handler.ts — step execution addition

async function executeStep(step: PipelineStep, env: string): Promise<StepResult> {
  // 1. Start sidecar services (if any)
  const services = await startServices(step.services);

  // 2. Pull step image
  await pullImage(step.image);

  // 3. Load secrets for this environment
  const secrets = await loadSecrets(env, step.secrets);

  // 4. Run commands in the step container
  //    - Secrets injected as env vars
  //    - Services linked via Docker network
  //    - Workspace mounted read-only
  //    - Timeout from environment config
  const result = await runInContainer({
    image: step.image,
    commands: step.commands,
    env: { ...secrets, BOND_DEPLOY_ENV: env },
    network: services.networkId,
    mounts: [{ src: workspacePath, dst: "/workspace", readonly: true }],
    timeout: envConfig.max_script_timeout,
  });

  // 5. Tear down services
  await stopServices(services);

  // 6. Generate receipt
  await writeReceipt(step, env, result);

  return result;
}
```

### 14.10 Quick Deploy Auto-Generated Scripts

For Tier 1 (no YAML), Bond generates a `.bond/deploy.yml` equivalent from the Quick Deploy form:

```typescript
function generateQuickDeployPipeline(config: QuickDeployConfig): string {
  const steps: any[] = [];

  // Auto-detect build step
  if (config.buildStrategy === "auto" || config.buildStrategy === "node") {
    steps.push({
      name: "build",
      image: "node:22",
      commands: [
        config.buildCmd || "npm ci && npm run build",
      ],
    });
  } else if (config.buildStrategy === "dockerfile") {
    steps.push({
      name: "build",
      image: "docker",
      commands: [
        `docker build -t ${config.appName}:latest .`,
      ],
    });
  }

  // Deploy step
  steps.push({
    name: "deploy",
    image: "bond-deploy-agent",
    commands: config.deployCmd
      ? [config.deployCmd]
      : [`docker stop ${config.appName} || true`,
         `docker run -d --name ${config.appName} -p ${config.port}:${config.port} ${config.appName}:latest`],
    depends_on: ["build"],
    secrets: Object.keys(config.envVars).filter((k) => config.envVars[k].secret),
  });

  // Health check step (if configured)
  if (config.healthCheckPath) {
    steps.push({
      name: "health-check",
      image: "curlimages/curl",
      commands: [
        `sleep 5`,
        `curl -f http://localhost:${config.port}${config.healthCheckPath} || exit 1`,
      ],
      depends_on: ["deploy"],
    });
  }

  return yaml.dump({
    pipeline: config.appName,
    on: config.triggerOnPush
      ? { push: { branches: [config.branch] }, manual: true }
      : { manual: true },
    steps,
    environments: [{ name: config.environment, auto_promote: true }],
  });
}
```

The generated YAML is stored in the script registry (not committed to the repo). But the user can click "Export as .bond/deploy.yml" to commit it and graduate to Tier 2.

### 14.11 Component Additions

```
frontend/src/app/settings/deployment/
├── ... (existing from §10)
├── QuickDeployForm.tsx          # Tier 1 — connect repo, configure, deploy
├── PipelineStepView.tsx         # Tier 2 — step cards with status/duration
├── PipelineRunHistory.tsx       # Tier 2 — recent runs list
├── PipelineYamlEditor.tsx       # Tier 2 — in-browser YAML editor (optional)
├── ApprovalWorkflow.tsx         # Tier 3 — approval status + approve button
├── BuildStrategyDetector.tsx    # Tier 1 — auto-detect from repo contents
└── TriggerConfig.tsx            # Webhook + cron + manual trigger settings
```

### 14.12 File Structure Additions

```
gateway/src/
├── deployments/
│   ├── ... (existing from Doc 039 §19)
│   ├── pipeline-parser.ts       # Parse .bond/deploy.yml
│   ├── pipeline-runner.ts       # Orchestrate step execution
│   ├── pipeline-router.ts       # API: trigger, list runs, get logs
│   ├── trigger-handler.ts       # Webhook → pipeline trigger
│   ├── step-executor.ts         # Run step in container via broker
│   ├── matrix-expander.ts       # Expand matrix configurations
│   ├── service-manager.ts       # Start/stop sidecar service containers
│   ├── quick-deploy.ts          # Auto-generate pipeline from form input
│   └── build-detector.ts        # Scan repo for build strategy
│   └── __tests__/
│       ├── pipeline-parser.test.ts
│       ├── pipeline-runner.test.ts
│       ├── matrix-expander.test.ts
│       └── quick-deploy.test.ts
```

---

## 15. Build Order

### Phase 1: Core Tab + Agent Management (~2 days)

1. `DeploymentTab.tsx` — state machine (empty/dashboard/edit)
2. `SetupWizard.tsx` — shared settings + name cards + create all button
3. `AgentCardGrid.tsx` + `AgentCard.tsx` — dashboard view
4. `SharedSettingsForm.tsx` — shared settings editor
5. Register tab in `settings/page.tsx`

### Phase 2: Edit Flows (~1.5 days)

6. `SingleAgentEditor.tsx` — full edit form with override toggles
7. Edit All mode — shared settings cascade logic
8. Reset to Shared Defaults
9. Workspace mount auto-collection + RO enforcement

### Phase 3: Tier 1 — Quick Deploy (~2 days)

10. `QuickDeployForm.tsx` — connect repo, auto-detect build, env vars, deploy
11. `BuildStrategyDetector.tsx` — scan repo for Dockerfile/package.json/etc.
12. `quick-deploy.ts` (Gateway) — auto-generate pipeline YAML from form
13. `trigger-handler.ts` (Gateway) — webhook registration for push-to-deploy
14. "Export as .bond/deploy.yml" button

### Phase 4: Tier 2 — Pipeline-as-Code (~3 days)

15. `pipeline-parser.ts` (Gateway) — parse .bond/deploy.yml
16. `pipeline-runner.ts` + `step-executor.ts` (Gateway) — orchestrate steps
17. `matrix-expander.ts` (Gateway) — expand matrix configurations
18. `service-manager.ts` (Gateway) — sidecar container lifecycle
19. `PipelineStepView.tsx` — step cards with status, duration, log links
20. `PipelineRunHistory.tsx` — recent runs list with per-step drill-down
21. `TriggerConfig.tsx` — webhook + cron + manual trigger settings

### Phase 5: Tier 3 — Promotion + Approvals (~2 days)

22. `PipelineSection.tsx` + `PipelineRow.tsx` — script status across environments
23. `StatusIndicator.tsx` — status icons with tooltips
24. `ApprovalWorkflow.tsx` — approval status, approve button, pending notifications
25. Receipt viewer link integration
26. Promote / Promote to All button integration

### Phase 6: Polish (~1.5 days)

27. Health status polling (periodic refresh of agent health)
28. Arrow connectors between cards (CSS)
29. Responsive layout testing
30. Error handling + loading states
31. Tier auto-detection (no YAML → Tier 1, YAML detected → Tier 2, approvals → Tier 3)

**Total estimate: ~12 days**

---

## 16. Open Questions

1. **Environment management in this tab?** Should the Deployment tab also allow adding/removing environments (currently API-only per Doc 039 §3.6)? Or should that stay as a separate settings section?

2. **Agent health source.** Where does agent health status come from? The deployment agent's last health check result (via broker), or the agent container's running state, or both?

3. **Partial creation.** If a user only wants Dev, QA, and Prod (no Staging/UAT), should the setup wizard let them uncheck environments? This implies environment management belongs in this tab.

4. **Pipeline actions.** Should Promote/Rollback buttons live here, or only in a dedicated Pipeline page? Having them here is convenient but might clutter the settings tab.

5. **Real-time updates.** Should the dashboard poll for status changes, or use SpacetimeDB subscriptions for instant updates? SpacetimeDB subscriptions would give real-time promotion status, health changes, and deployment progress without polling.

6. **Step container execution.** The broker currently executes scripts on the host. Tier 2 requires running steps inside arbitrary container images (e.g., `node:22`, `postgres:16`). Does the broker need a Docker-in-Docker setup, or does it use `docker run` directly on the host's Docker socket?

7. **Pipeline YAML location.** Should Bond also support `.bond/deploy.yml` in the Bond workspace (`~/.bond/pipelines/`) for pipelines that aren't tied to a specific repo? Some deployment scripts are cross-repo or infrastructure-level.

8. **Preview deployments.** Coolify's PR preview deployments are powerful. Should Bond auto-create ephemeral environments for PRs? This implies dynamic environment creation beyond the static dev/qa/staging/uat/prod set.

9. **Plugin registry.** Woodpecker has a plugin index (plugins.woodpecker-ci.org). Should Bond have a curated list of step images for common tasks (Slack notification, S3 upload, Docker push)?  Could integrate with ClawhHub's skill marketplace.

10. **YAML editing in UI.** Should the Deployment tab include an in-browser YAML editor with syntax highlighting and validation? Or is editing the file in the repo sufficient? An in-browser editor lowers the barrier for Tier 1 → Tier 2 graduation.
