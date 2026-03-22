# 061 — Deployment Tab Simplification

**Status:** Draft
**Date:** 2026-03-22
**Depends on:** 052, 056
**Supersedes:** 052 (partially), 056 (partially)
**Mockups:** [Current State](mockups/061-current-state.html) | [New Flow](mockups/061-new-flow.html) | [Component Map](mockups/061-component-map.html)

---

## Problem Statement

Bond's deployment tab contains **44 components** with **22 ViewModes**, managed by a single orchestrator (`DeploymentTab.tsx`) through a giant switch statement. Deployment is buried under **Settings → Deployment**, requires users to choose between 5+ entry points before they can do anything, and forces an agent-centric mental model when users think in terms of apps.

**Result:** ~15 minutes to first deployment, ~60% drop-off during setup.

---

## Current State Audit

### Component Inventory (44 files)

#### Wizards & Setup (6 components) — CONSOLIDATION TARGET
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| OnboardServerWizard.tsx | 44KB | SSH server onboarding (legacy) | **DELETE** — merged into OneClickShipWizard |
| DiscoverStackWizard.tsx | 40KB | Auto-discovery of server stack | **DELETE** — merged into OneClickShipWizard |
| SetupWizard.tsx | 10KB | Initial agent setup | **DELETE** — merged into OneClickShipWizard |
| QuickDeployForm.tsx | 16KB | Quick deploy form | **DELETE** — merged into OneClickShipWizard |
| AddServerModal.tsx | 10KB | Add server modal | **KEEP** — used within OneClickShipWizard Step 1 |
| AddComponentForm.tsx | 14KB | Add component form | **KEEP** — used in advanced drill-down |

#### Dashboards & Navigation (5 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| DeploymentTab.tsx | 18KB | Main orchestrator, 22 ViewModes | **REPLACE** — with DeployPage.tsx (~8KB, 5 views) |
| EnvironmentDashboard.tsx | 23KB | Environment-level dashboard | **REPLACE** — with AppDashboard.tsx |
| AgentCard.tsx | 2KB | Agent card display | **REPLACE** — with AppCard.tsx |
| AgentCardGrid.tsx | 3KB | Agent card grid | **DELETE** — absorbed into AppDashboard |
| StatusIndicator.tsx | 1KB | Status dot | **KEEP** |

#### Monitoring & Alerts (4 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| MonitoringSetupWizard.tsx | 11KB | Monitoring setup wizard | **DELETE** — auto-configured during deploy |
| MonitoringConfig.tsx | 6KB | Monitoring configuration | **KEEP** — behind "Advanced" |
| MonitoringSection.tsx | 7KB | Monitoring display | **KEEP** — in AppDetail view |
| AlertRulesEditor.tsx | 12KB | Alert rules | **KEEP** — behind "Advanced" |

#### Pipelines (5 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| PipelineSection.tsx | 6KB | Pipeline list | **KEEP** — in AppDetail |
| PipelineRow.tsx | 5KB | Pipeline row | **KEEP** |
| PipelineRunHistory.tsx | 6KB | Run history | **KEEP** |
| PipelineStepView.tsx | 8KB | Step detail | **KEEP** |
| PipelineYamlEditor.tsx | 7KB | YAML editor | **KEEP** — behind "Advanced" |

#### Infrastructure Visualization (2 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| InfraMap.tsx | 13KB | Infrastructure map | **KEEP** — behind "Infrastructure" tab |
| TopologyGraph.tsx | 5KB | Topology graph | **KEEP** |

#### Deployment Execution & History (4 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| LiveLogViewer.tsx | 10KB | Real-time logs | **KEEP** — used in ShipProgress |
| DeploymentTimeline.tsx | 10KB | Deployment timeline | **KEEP** |
| ProposalViewer.tsx | 7KB | Deployment proposals | **MERGE** — into ShipProgress |
| ReceiptViewer.tsx | 8KB | Deployment receipts | **MERGE** — into deployment detail |

#### Resources (3 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| ResourceCard.tsx | 2KB | Resource card | **KEEP** |
| ResourceDetail.tsx | 8KB | Resource detail | **KEEP** |
| ResourceForm.tsx | 12KB | Resource form | **KEEP** |

#### Discovery & Detection (3 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| DiscoveryView.tsx | 7KB | Discovery results | **DELETE** — absorbed into OneClickShipWizard Step 2 |
| DiscoveryMonitoringPanel.tsx | 6KB | Discovery monitoring | **DELETE** — absorbed into OneClickShipWizard |
| BuildStrategyDetector.tsx | 3KB | Build strategy detection | **KEEP** — used internally by wizard |

#### Configuration & Management (7 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| SecretManager.tsx | 13KB | Secrets management | **KEEP** — behind "Advanced" |
| ScriptRegistration.tsx | 17KB | Script registration | **KEEP** — behind "Advanced" |
| ScriptFromDiscoveryWizard.tsx | 12KB | Scripts from discovery | **DELETE** — merged into OneClickShipWizard |
| CompareEnvironments.tsx | 10KB | Environment comparison | **KEEP** — in dashboard |
| SingleAgentEditor.tsx | 11KB | Agent editor | **KEEP** — renamed to AppConfig |
| SharedSettingsForm.tsx | 5KB | Shared settings | **KEEP** — behind "Advanced" |
| ComponentDetail.tsx | 22KB | Component management | **KEEP** — in drill-down |

#### Other (5 components)
| Component | Size | Purpose | Fate |
|-----------|------|---------|------|
| IssueTracker.tsx | 5KB | Issue tracking | **KEEP** |
| FolderBrowser.tsx | 8KB | File browser | **KEEP** — used in wizard |
| TriggerConfig.tsx | 7KB | Deploy triggers | **KEEP** — behind "Advanced" |
| ApprovalStatus.tsx | 2KB | Approval status | **KEEP** |
| EnvironmentHistory.tsx | 7KB | Environment history | **MERGE** — into DeploymentTimeline |

### Consolidation Summary

| Action | Count | KB Removed |
|--------|-------|------------|
| DELETE (remove entirely) | 10 | ~185KB |
| REPLACE (new component) | 4 | ~46KB replaced with ~20KB |
| MERGE (into other components) | 3 | ~22KB absorbed |
| KEEP (as-is or minor changes) | 27 | — |
| **Net result** | **~31 components** (from 44) | **~233KB eliminated** |

### ViewMode Reduction

| Current (22 modes) | New (6 modes) |
|---------------------|---------------|
| loading, empty, dashboard, edit-one, edit-all, quick-deploy, register-script, onboard-server, script-from-discovery, monitoring-setup, live-logs, alert-rules, secrets, compare-envs, infra-map, timeline, agent-settings, component-detail, add-server, discover, add-component | dashboard, app-detail, new-deploy, deploy-progress, infrastructure, settings |

---

## Proposed Architecture

### Navigation Change

```
BEFORE                          AFTER
Settings                        Deploy  ← top-level sidebar
  └── Deployment tab              ├── App Dashboard (default)
        ├── Environment tabs      ├── [App Name] → App Detail
        ├── Quick Deploy          ├── Infrastructure (map/topology)
        ├── Discover              └── + Deploy New App (wizard)
        ├── Onboard Server
        ├── Agent Settings
        └── ... 17 more views
```

### New Component Structure

```
DeployPage.tsx (~8KB)                    ← replaces DeploymentTab.tsx
├── AppDashboard.tsx (~12KB)             ← replaces EnvironmentDashboard + AgentCardGrid
│   ├── AppCard.tsx (~3KB)               ← replaces AgentCard
│   └── CompareEnvironments.tsx (keep)
├── AppDetail.tsx (~15KB)                ← replaces ComponentDetail + env drilldowns
│   ├── PipelineSection.tsx (keep)
│   ├── MonitoringSection.tsx (keep)
│   ├── DeploymentTimeline.tsx (keep)
│   └── LiveLogViewer.tsx (keep)
├── OneClickShipWizard.tsx (~20KB)       ← replaces 6 wizards/forms
│   ├── Step 1: ConnectStep (repo or server)
│   ├── Step 2: DiscoveryStep (auto-detect + plan)
│   └── Step 3: ShipStep (execute + progress)
├── ShipProgress.tsx (~8KB)              ← new, replaces ProposalViewer + ReceiptViewer
└── [Advanced views accessible via tabs/drawers]
    ├── InfraMap.tsx (keep)
    ├── SecretManager.tsx (keep)
    ├── AlertRulesEditor.tsx (keep)
    ├── PipelineYamlEditor.tsx (keep)
    └── ScriptRegistration.tsx (keep)
```

### The 3-Step "Ship It" Flow

**Step 1: Connect** — "What are you deploying?"
- Two cards: "Connect Repository" (URL input) or "Connect Server" (IP + SSH)
- Can also select from existing resources
- Replaces: QuickDeployForm source selection, OnboardServerWizard connect step, DiscoverStackWizard server selection

**Step 2: Discover & Plan** — "Here's what I found"
- Animated discovery phase (scanning repo/server)
- Shows structured deployment plan: detected framework, build strategy, target servers, monitoring config
- "Advanced Options" expandable for power users
- Replaces: DiscoverStackWizard discovery+review, BuildStrategyDetector UI, DiscoveryView, DiscoveryMonitoringPanel, MonitoringSetupWizard (auto-configured)

**Step 3: Ship It** — "One button, real-time progress"
- Big "Ship It" button → progress view
- Step-by-step completion with green checkmarks
- Live log tail
- Replaces: QuickDeployForm execution, deployment progress scattered across views

### App-Centric Dashboard

```
┌─────────────────────────────────────────────┐
│  Deploy                    [+ Deploy New App]│
├─────────────────────────────────────────────┤
│                                              │
│  ┌─ ecoinspector-portal ──────────────────┐ │
│  │ Next.js • prod-web-01                  │ │
│  │ Dev ●  Staging ●  Prod ●               │ │
│  │ Last deploy: 2 hours ago • Healthy     │ │
│  └────────────────────────────────────────┘ │
│                                              │
│  ┌─ bond-gateway ─────────────────────────┐ │
│  │ Go • api-server-01                     │ │
│  │ Dev ●  Staging ●  Prod ●               │ │
│  │ Last deploy: 1 day ago • Healthy       │ │
│  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

Each app card shows:
- App name and framework
- Environment status pills (green/yellow/red)
- Last deploy time and health
- Click → AppDetail with timeline, logs, monitoring, pipelines

---

## Before / After Comparison

| Metric | Before | After |
|--------|--------|-------|
| Components | 44 | ~31 |
| ViewModes | 22 | 6 |
| Entry points for new deploy | 5 (Quick Deploy, Onboard Server, Discover Stack, Setup Wizard, Add Component) | 1 ("Deploy New App" button) |
| Steps to first deployment | 8+ (navigate to Settings → Deployment → choose wizard → 5+ wizard steps) | 3 (Connect → Review Plan → Ship) |
| Time to first deployment | ~15 min | < 3 min (target) |
| Mental model | Agent-centric ("create deploy-prod agent") | App-centric ("deploy my app") |
| Navigation location | Settings → Deployment tab | Top-level "Deploy" sidebar |
| User drop-off (estimated) | ~60% | < 20% (target) |
| Total component KB | ~500KB | ~300KB |

---

## Migration Plan

### Phase 1: Foundation (Week 1)
1. Add "Deploy" to top-level sidebar routing
2. Create `DeployPage.tsx` shell with feature flag
3. Create `AppDashboard.tsx` reading existing agent data but presenting app-centric view
4. Create `AppCard.tsx`
5. Old Settings → Deployment still works, both accessible during transition

### Phase 2: Wizard (Week 2)
1. Build `OneClickShipWizard.tsx` with 3-step flow
2. Wire Step 1 (Connect) to existing server/repo connection logic
3. Wire Step 2 (Discover) to existing `BuildStrategyDetector` + discovery APIs
4. Wire Step 3 (Ship) to existing deployment execution + `LiveLogViewer`
5. Create `ShipProgress.tsx`
6. Feature-flag the new wizard alongside old wizards

### Phase 3: Cutover (Week 3)
1. Make new Deploy page the default
2. Remove Settings → Deployment tab (redirect to new page)
3. Delete deprecated components (OnboardServerWizard, DiscoverStackWizard, SetupWizard, QuickDeployForm, etc.)
4. Remove old ViewMode switch statement
5. Update all internal links/references

### Phase 4: Polish (Week 4)
1. Add transitions and animations
2. Add empty states and onboarding hints
3. Performance audit of remaining components
4. User testing and iteration

---

## Backend Requirements

Two new API endpoints (from Doc 056):

1. **`POST /api/deploy/generate-plan`** — Takes `{ repoUrl?: string, serverAddress?: string, sshKeyId?: string }`, returns structured deployment plan JSON
2. **`POST /api/deploy/execute-plan`** — Takes plan JSON, returns SSE stream of execution progress

These compose existing internal APIs (agent creation, docker build, server connection) into a unified flow.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Power users lose quick access to advanced features | Medium | Progressive disclosure — all features still accessible via tabs/drawers in AppDetail |
| Existing deployments break during migration | High | Feature flag phased rollout; old UI stays until Phase 3 |
| 3-step flow too simple for complex deployments | Medium | "Advanced Options" expandable in Step 2; full pipeline/YAML editor in AppDetail |
| Agent model still required internally | Low | UI translates app-centric actions to agent operations behind the scenes |

---

## Success Criteria

- [ ] First deployment achievable in < 3 minutes by new user
- [ ] Single entry point for all deployment creation
- [ ] Deploy accessible from top-level sidebar
- [ ] Component count reduced by 30%+
- [ ] ViewMode count reduced from 22 to ≤ 6
- [ ] User drop-off during setup < 20%
