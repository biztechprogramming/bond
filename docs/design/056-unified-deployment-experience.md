# Design Doc 056: Unified Deployment Experience — "Super Simple Ship"

**Status:** Draft
**Date:** 2026-03-22
**Depends on:** 045 (Guided Workflows), 052 (Simplified Deployment UX)

---

## 1. Motivation

Docs 045 and 052 each solve half the problem:

- **Doc 045** introduced guided workflows, server onboarding wizards, and zero-config monitoring — but kept the agent-centric mental model and added multiple new components (`OnboardServerWizard`, `QuickDeployForm`, `DiscoverStackWizard`) as separate entry points.
- **Doc 052** introduced the "Ship It" wizard and the app-centric dashboard — but assumed the user already has agents and servers configured.

Neither doc alone delivers the "Railway/Vercel for your own infra" promise. This doc unifies them into a single, opinionated flow: **one button, one wizard, one dashboard**.

### What changes from each doc

| From Doc 045 | Kept | Changed |
|---|---|---|
| Guided connection flow | Yes | Folded into a single modal step, not a standalone wizard |
| Auto-discovery as onboarding | Yes | Runs silently during "Magic Discovery" step |
| Zero-config monitoring | Yes | Enabled by default in the generated plan |
| `OnboardServerWizard` component | Absorbed | Merged into `OneClickShipWizard` |
| `QuickDeployForm` component | Absorbed | Merged into `OneClickShipWizard` |

| From Doc 052 | Kept | Changed |
|---|---|---|
| Top-level "Deploy" nav item | Yes | Unchanged |
| App-centric dashboard | Yes | Enhanced with health indicators and environment drilldown |
| Deployment Plan display | Yes | Generated from auto-discovery, not manual configuration |
| "Ship It" button | Yes | Now the final step of the unified wizard |

---

## 2. Design

### 2.1 Top-Level "Deploy" Navigation

Deploy moves out of Settings into the primary sidebar, sitting alongside Agents and Channels. This is the single entry point for all deployment activity. The sidebar item shows a count badge for active deployments.

```
Sidebar:
  Agents
  Channels
  Deploy  ← primary nav, not nested under Settings
  Settings
```

### 2.2 App-Centric Dashboard

The Deploy landing page shows **Apps**, not agents or pipelines. Each app is a card displaying:

- App name and linked repository
- Health status badge (Healthy / Degraded / Down / Deploying)
- Environment pills (Dev, Staging, Prod) showing per-environment status
- Last deploy timestamp and duration
- Quick actions: Redeploy, View Logs, Rollback

An empty state shows a prominent "Ship New App" button with a one-line description: *"Connect a repo or server and Bond handles the rest."*

### 2.3 One-Click Ship Wizard

A single modal wizard replaces all existing deployment entry points. Three steps, one flow.

#### Step 1: Connect

The user provides **one thing**: a repository URL or a server address.

```
┌─────────────────────────────────────────────────┐
│  Ship New App                                    │
│                                                  │
│  Repository URL                                  │
│  [ https://github.com/user/repo           ]      │
│                                                  │
│  ─── or ───                                      │
│                                                  │
│  Server Address                                  │
│  [ 10.0.1.50                              ]      │
│  SSH credentials: auto-detected from agent keys  │
│                                                  │
│              [Cancel]  [Discover →]               │
└─────────────────────────────────────────────────┘
```

No agent selection, no environment configuration, no resource forms. Bond figures it out.

#### Step 2: Magic Discovery

Bond runs auto-discovery (from Doc 045's `BuildStrategyDetector` for repos, SSH discovery scripts for servers) and presents a **Deployment Plan**. This step shows an animated scanning state, then reveals the results.

```
┌──────────────────────────────────────────────────┐
│  Magic Discovery                                  │
│                                                   │
│  ✓ Cloned repository                              │
│  ✓ Detected Next.js 14 application                │
│  ✓ Found Dockerfile (multi-stage build)           │
│  ✓ Found .env.example (3 variables)               │
│  ✓ Located target server from workspace config    │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │  Deployment Plan                             │  │
│  │                                              │  │
│  │  ✓ Create Production deployment agent        │  │
│  │  ✓ Build Docker image from Dockerfile        │  │
│  │  ✓ Deploy to prod-web-01 (10.0.1.50)        │  │
│  │  ✓ Expose on port 3000                       │  │
│  │  ✓ Enable health monitoring (30s interval)   │  │
│  │                                              │  │
│  │  [▸ Advanced Options]                        │  │
│  └─────────────────────────────────────────────┘  │
│                                                   │
│              [← Back]  [Ship It! →]               │
└──────────────────────────────────────────────────┘
```

The "Advanced Options" expander (progressive disclosure per Doc 045 principles) lets power users tweak: environment name, build args, deploy strategy (rolling/blue-green), monitoring interval, and alert channels.

#### Step 3: Ship It

A progress view with real-time status for each plan step, a live log tail, and a completion celebration.

```
┌──────────────────────────────────────────────────┐
│  🚀 Shipping your app...                         │
│                                                   │
│  ████████████████░░░░░░░░  65%                    │
│                                                   │
│  ✓ Created deployment agent                       │
│  ✓ Built Docker image (42s)                       │
│  ● Deploying to prod-web-01...                    │
│  ○ Verifying health                               │
│  ○ Enabling monitoring                            │
│                                                   │
│  ┌─ Live Logs ─────────────────────────────────┐  │
│  │ > docker push ecoinspector:latest           │  │
│  │ > Connecting to 10.0.1.50...                │  │
│  │ > Pulling image on remote...                │  │
│  └─────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

On completion, the modal transitions to a success state with a link to the new app card on the dashboard.

---

## 3. Component Architecture

### New Components

| Component | Replaces | Purpose |
|---|---|---|
| `OneClickShipWizard.tsx` | `QuickDeployForm`, `OnboardServerWizard`, `DiscoverStackWizard` | Single modal wizard for all deployment creation |
| `AppDashboard.tsx` | Agent card grid + `PipelineSection` | App-centric deployment home page |
| `AppCard.tsx` | — | Individual app status with environment pills |
| `ShipProgress.tsx` | — | Real-time deployment execution view |

### Modified Components

| Component | Change |
|---|---|
| `Sidebar.tsx` | Add "Deploy" as top-level nav item |
| `App.tsx` / Router | Add `/deploy` route at top level |
| `deploy_tools.py` | Add `generate_plan` action that returns structured JSON |

### Removed Entry Points

The following are absorbed into `OneClickShipWizard` and no longer have standalone entry points:

- `QuickDeployForm` standalone route
- `OnboardServerWizard` standalone route
- `DiscoverStackWizard` standalone route

The underlying logic is reused; only the UI entry points are consolidated.

---

## 4. Backend Changes

### 4.1 `generate_plan` API

A new action on the deployment tools endpoint that accepts a repo URL or server address and returns a structured plan:

```json
{
  "source": { "type": "repository", "url": "https://github.com/user/repo" },
  "detected": {
    "framework": "nextjs",
    "version": "14.2",
    "dockerfile": true,
    "env_vars": ["DATABASE_URL", "NEXTAUTH_SECRET", "REDIS_URL"],
    "port": 3000
  },
  "plan": {
    "steps": [
      { "action": "create_agent", "name": "deploy-prod", "environment": "production" },
      { "action": "build_image", "dockerfile": "./Dockerfile", "tag": "app:latest" },
      { "action": "deploy", "target": "prod-web-01", "port": 3000 },
      { "action": "monitor", "interval": 30, "health_endpoint": "/api/health" }
    ]
  }
}
```

### 4.2 `execute_plan` API

Accepts a plan object and executes it step by step, streaming progress events via SSE for the `ShipProgress` component.

---

## 5. Mockup

See: [`mockups/super-simple-deploy.html`](mockups/super-simple-deploy.html)

The mockup demonstrates the complete flow:
1. App-centric dashboard with health/environment status
2. "Ship New App" button opening the wizard modal
3. Magic Discovery step with animated scanning and plan generation
4. Ship It progress bar with step completion and live logs

---

## 6. Migration Path

1. **Phase 1**: Add Deploy to sidebar, ship `AppDashboard` and `AppCard` (read-only view over existing data).
2. **Phase 2**: Ship `OneClickShipWizard` behind a feature flag, keep old entry points.
3. **Phase 3**: Remove old standalone wizards, make `OneClickShipWizard` the default.

---

## 7. Success Metrics

| Metric | Current | Target |
|---|---|---|
| Steps to first deployment | 8+ | 3 (connect → review plan → ship) |
| Time to first deployment | ~15 min | < 3 min |
| User drop-off during setup | ~60% (estimated) | < 20% |
| Deployment visibility | Buried in Settings | Top-level sidebar |
