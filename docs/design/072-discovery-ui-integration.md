# Design Doc 072: Agent Discovery UI Integration

**Status:** Draft
**Date:** 2026-03-24
**Depends on:** 071 (Agent-Driven Deployment Discovery), 061 (Deployment Tab Simplification), 056 (Unified Deployment Experience)

---

## 1. Problem Statement

Design Doc 071 implemented the backend for agent-driven deployment discovery — 9 discovery tools, an orchestrator with a completeness model, and 4 new SSE event types (`discovery_agent_started`, `discovery_agent_progress`, `discovery_user_question`, `discovery_agent_completed`). However, the frontend has no awareness of this new system. The existing `DiscoverStackWizard.tsx` still calls the old `POST /broker/deploy { action: "discover" }` endpoint and renders a fake progress animation that ticks through 5 hardcoded layers on an 800ms timer.

**The result:** Users see zero difference. The agent discovery backend is unreachable from the UI.

### What's Missing

| Gap | Impact |
|-----|--------|
| No SSE listener for agent discovery events | Real-time progress is invisible |
| No inline question UI for `ask_user` | Agent can't ask targeted questions — falls back to timeout |
| No confidence indicators on discovered fields | Users can't distinguish detected vs inferred values |
| No way to override individual discovered fields | Users must accept everything or nothing |
| Old discovery endpoint still called | Agent path is never triggered |
| No UI for discovered services/dependencies | `detect_services` findings (databases, caches, queues) are silently discarded |
| No UI for optional deployment settings | `deploy_strategy`, `rollback_plan`, `alert_channels` discovered by agent have nowhere to render |
| No monitoring config display | `monitoring_config` (recommended field) is invisible |
| No degraded-mode indicators | When SSH or repo clone fails, user doesn't know discovery is running in a limited mode |
| No cancel button for in-progress discovery | `cancel()` exists in the hook but no UI affordance |

---

## 2. Design Principles

1. **Keep it simple** — The discovery step should feel like watching Bond think, not like operating a control panel. Minimal chrome, clear status.
2. **Progressive disclosure** — Show a clean summary by default. Let users expand details or override values only when they want to. Optional/recommended fields are collapsed by default.
3. **Stay in flow** — Agent questions appear inline, not as modals. The user answers and discovery continues without navigation.
4. **Confidence, not complexity** — Use simple icons (✓ detected, ~ inferred, ? needs review) instead of numeric scores. Users don't need to see "0.73".
5. **Graceful degradation** — If the agent discovery flag is off or the endpoint fails, fall back to the existing shell-script discovery seamlessly.
6. **Full coverage** — Every field the backend can discover (required, recommended, and optional) must have a place in the UI, even if optional fields are collapsed by default.
7. **Accessible by default** — All interactive elements are keyboard-navigable, screen-reader-friendly, and respect `prefers-reduced-motion`.

---

## 3. Scope

### In Scope (This Doc)

- New `AgentDiscoveryView` component replacing Step 2 content in the wizard
- New `DeploymentPlanPanel` component showing the plan as it builds — including required, recommended, AND optional field sections
- Inline question UI for `ask_user` events (respecting the 2–3 question-per-session limit from Doc 071 §6)
- SSE event consumption via `EventSource`
- Feature flag check (`BOND_AGENT_DISCOVERY`) to toggle between old and new paths
- Override capability for any discovered field
- Degraded-mode banners (repo-only, SSH-only, structured-interview fallback)
- Cancel discovery button
- Accessibility (keyboard nav, ARIA, reduced-motion)

### Out of Scope

- Changes to the backend agent orchestrator (Doc 071)
- Changes to wizard Steps 1, 3, 4 (server selection, environment, scripts)
- Discovery caching UI (future — Doc 071 §11 mentions cache but no UI yet)
- Mobile/responsive layout changes

---

## 4. Architecture

### 4.1 Data Flow

```
┌─────────────┐     POST /broker/deploy        ┌──────────────────┐
│  Wizard      │  ──── { action: "discover",  ──▶│  Gateway         │
│  (frontend)  │       agent: true }            │  (backend)       │
│              │                                │                  │
│              │◀── SSE stream ─────────────────│  EventBus emits: │
│              │    discovery_agent_started      │  - started       │
│              │    discovery_agent_progress     │  - progress      │
│              │    discovery_user_question      │  - question      │
│              │    discovery_agent_completed    │  - completed     │
│              │                                │                  │
│              │  POST /broker/deploy            │                  │
│              │  ──── { action: "answer",  ────▶│  Agent receives  │
│              │       field, value }            │  user answer     │
│              │                                │                  │
│              │  POST /broker/deploy            │                  │
│              │  ──── { action: "cancel" } ────▶│  Agent session   │
│              │                                │  terminated      │
└─────────────┘                                └──────────────────┘
```

### 4.2 SSE Event Payloads

These events are already emitted by the backend (Doc 071 §3.4, `events.ts`). The frontend needs to consume them:

```typescript
// discovery_agent_started
{
  event: "discovery_agent_started",
  summary: "Agent discovery started for production",
  details: {
    source: "https://github.com/acme/api",
    server: "192.168.1.50",
    mode: "full" | "repo-only" | "server-only" | "interview"  // degraded mode indicator
  }
}

// discovery_agent_progress
{
  event: "discovery_agent_progress",
  summary: "Detected Express framework (v4.18)",
  details: {
    field: "framework",
    value: { name: "express", version: "4.18", confidence: 0.95 },
    confidence: { source: "detected", detail: "Found in package.json", score: 0.95 },
    completeness: {
      ready: false,
      required_coverage: 0.4,
      recommended_coverage: 0.33,
      missing_required: ["build_strategy", "app_port"],
      low_confidence: []
    },
    probe: "detect_framework"  // which tool produced this finding
  }
}

// discovery_user_question
{
  event: "discovery_user_question",
  summary: "Which port does your application listen on?",
  details: {
    field: "app_port",
    question: "I found ports 3000 and 8080 in your codebase. Which is your main application port?",
    context: "Port 3000 in server.js line 42, port 8080 in Dockerfile EXPOSE",
    options: ["3000", "8080"],
    default: "8080",
    questions_remaining: 2  // from the 2-3 question budget (Doc 071 §6)
  }
}

// discovery_agent_completed
{
  event: "discovery_agent_completed",
  summary: "Discovery complete — plan ready",
  details: {
    state: { /* full DiscoveryState including probes_run, user_answers */ },
    completeness: {
      ready: true,
      required_coverage: 1.0,
      recommended_coverage: 0.67,
      missing_required: [],
      low_confidence: ["health_endpoint"]
    }
  }
}
```

### 4.3 New Gateway Endpoints

The existing `POST /broker/deploy` with `action: "discover"` needs a small addition:

```typescript
// When agent discovery is enabled, the request includes:
{ action: "discover", resource_id: "server-123", agent: true }

// The response initiates SSE streaming on:
GET /deployments/discovery/stream/{session_id}

// User answers are posted to:
POST /deployments/discovery/answer/{session_id}
{ field: "app_port", value: "8080" }

// Cancel an in-progress discovery:
POST /deployments/discovery/cancel/{session_id}
```

The `session_id` is returned in the initial POST response so the frontend can subscribe to the correct SSE stream.

---

## 5. Component Design

### 5.1 `AgentDiscoveryView.tsx`

Replaces the content of Step 2 ("discovery") in the wizard. This is the main component.

**States:**

| State | What the User Sees |
|-------|-------------------|
| `idle` | "Ready to discover" with a Start button (auto-starts if coming from Step 1) |
| `connecting` | Skeleton pulse animation — shown in the 1–2s between POST and first SSE event |
| `discovering` | Live activity feed + progress bar + plan panel building up |
| `degraded` | Same as `discovering` but with a banner: "⚠️ Could not connect to server — discovering from repository only" (or vice versa) |
| `question` | Activity feed paused, inline question card highlighted at bottom |
| `complete` | Full plan panel, "Continue" button enabled |
| `error` | Error message with "Retry" button |

**Layout:**

```
┌──────────────────────────────────────────────────┐
│  Discovering your application...       [Cancel]  │
│                                                  │
│  ⚠️ SSH connection failed — discovering from     │  ← degraded mode banner (only when applicable)
│     repository only                              │
│                                                  │
│  ✅ Cloned repository                            │
│  ✅ Detected Express framework (v4.18)           │
│  ✅ Found Dockerfile (multi-stage build)         │
│  ✅ Found 2 services: PostgreSQL, Redis          │  ← NEW: services discovery
│  🔍 Checking application port...                 │
│                                                  │
│  ┌─ Question (2 remaining) ──────────────────────┐ │  ← shows question budget
│  │ I found ports 3000 and 8080 in your         │ │
│  │ codebase. Which is your main app port?      │ │
│  │                                             │ │
│  │  ○ 3000 (server.js:42)                      │ │
│  │  ● 8080 (Dockerfile EXPOSE)  ← suggested    │ │
│  │                                             │ │
│  │           [Continue]                        │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ━━━━━━━━━━━━━━━━━━━━━░░░░░░░░  3/5 fields  60%  │
└──────────────────────────────────────────────────┘
```

**Key behaviors:**

- Activity items appear one at a time as `discovery_agent_progress` events arrive
- Each item shows an icon: ✅ (detected, score ≥ 0.9), ✓ (inferred, 0.6–0.89), ⚠️ (low confidence, flagged)
- The progress bar shows `required_coverage` from the completeness report; a secondary subtle bar shows `recommended_coverage`
- When a `discovery_user_question` event arrives, the question card slides in at the bottom of the activity feed
- The question card header shows how many questions remain in the session budget (Doc 071 §6: max 2–3)
- The user answers via radio buttons (if `options` provided) or a text input (if free-form)
- Clicking "Continue" on the question posts the answer and discovery resumes
- Auto-scroll keeps the latest activity item visible
- **Cancel button** in the top-right corner closes the EventSource and posts cancel to backend
- **Connecting state** shows a skeleton pulse animation (3 placeholder rows) before the first SSE event arrives
- **Degraded mode banner** appears when `discovery_agent_started` reports `mode !== "full"`, explaining what's limited and why
- Activity items include the probe name (e.g., "via detect_framework") as a subtle secondary label for debugging

**Accessibility:**

- Activity feed is an ARIA live region (`aria-live="polite"`) so screen readers announce new items
- Question card auto-focuses the first option or text input on mount
- All interactive elements (radio buttons, edit buttons, Cancel, Continue, Ship It) are keyboard-accessible with visible focus indicators
- Animations respect `prefers-reduced-motion: reduce` — items appear instantly instead of fading/sliding

**Props:**

```typescript
interface AgentDiscoveryViewProps {
  resourceId: string;
  onComplete: (state: DiscoveryState) => void;
  onError: (error: string) => void;
  onCancel: () => void;
}
```

### 5.2 `DeploymentPlanPanel.tsx`

Shown alongside or below the activity feed. Builds up as fields are discovered. Organized into three collapsible sections matching the backend's field categories (Doc 071 §5.1).

**Layout:**

```
┌─ Deployment Plan ────────────────────────────────┐
│                                                  │
│  ── Required ──────────────────────────────────  │
│  Source       github.com/acme/api          ✓     │
│  Framework    Express 4.18                 ✓     │
│  Build        Dockerfile (multi-stage)     ✓     │
│  Target       192.168.1.50:22              ✓     │
│  App Port     8080                     [edit] 👤  │
│                                                  │
│  ── Recommended ───────────────────────────────  │
│  Health       GET /healthz             [edit] ~   │
│  Env Vars     3 detected               [edit]    │
│  Monitoring   Auto-configured              ✓     │  ← NEW: monitoring_config
│                                                  │
│  ▶ Services (2 detected)                         │  ← NEW: expandable services section
│    PostgreSQL  5432  (detected in docker-compose) │
│    Redis       6379  (detected in .env.example)   │
│                                                  │
│  ▶ Optional Settings                             │  ← NEW: optional fields section (collapsed)
│    Strategy    Rolling (2 replicas)    [edit] ~   │
│    Rollback    Auto-configured             ✓     │
│    Alerts      Not configured          [edit]    │
│                                                  │
│  Required  ━━━━━━━━━━━━━━━━━━━━━━━━━━━  5/5 ✓   │
│  Recommended  ━━━━━━━━━━━━━━━━━━░░░░░░  2/3      │  ← NEW: recommended coverage bar
│                                                  │
│              [ Ship It 🚀 ]                      │
└──────────────────────────────────────────────────┘
```

**Key behaviors:**

- Fields appear as they're discovered (animated fade-in)
- Confidence icons: ✓ = detected, ~ = inferred, 👤 = user-provided, ⚠️ = low confidence
- **Three sections:** Required (always expanded), Recommended (expanded), Optional (collapsed by default with a disclosure triangle)
- **Services section** renders `detect_services` findings (Doc 071 §4.5) — shows service name, port, and how it was detected. Expandable.
- **Monitoring row** shows `monitoring_config` status (Doc 071 §1 Recommended)
- **Optional settings** section shows `deploy_strategy`, `rollback_plan`, `alert_channels` (Doc 071 §1 Optional) — collapsed by default since they have sensible defaults
- Every field row has a subtle `[edit]` button on hover — clicking it opens an inline text input to override the value
- When a user overrides a field, its confidence source changes to `user-provided` (score 1.0)
- **Two progress bars:** one for required field coverage, one for recommended field coverage (both from `CompletenessReport`)
- "Ship It" button is enabled only when `completeness.ready === true`
- Fields in `low_confidence` array have an amber dot — hovering shows "This was inferred, you may want to verify"

**Accessibility:**

- Collapsible sections use `<details>`/`<summary>` or equivalent with `aria-expanded`
- Edit buttons have `aria-label="Edit {field name}"`
- Low-confidence tooltip uses `aria-describedby` for screen readers
- Progress bars use `role="progressbar"` with `aria-valuenow`/`aria-valuemax`

**Props:**

```typescript
interface DeploymentPlanPanelProps {
  state: DiscoveryState;
  onFieldEdit: (field: string, value: string) => void;
  onShipIt: () => void;
  ready: boolean;
  completeness: CompletenessReport;  // includes both required and recommended coverage
}
```

### 5.3 `InlineQuestion.tsx`

A small, focused component for rendering agent questions inline.

```typescript
interface InlineQuestionProps {
  question: string;
  context: string;
  field: string;
  options?: string[];
  defaultValue?: string;
  questionsRemaining: number;  // from the 2-3 question budget
  onAnswer: (field: string, value: string) => void;
}
```

**Behavior:**

- If `options` is provided: render radio buttons, pre-select `defaultValue`
- If no `options`: render a single text input with `defaultValue` as placeholder
- Header shows "Question (N remaining)" to set expectations per Doc 071 §6 budget
- "Continue" button submits the answer
- After submission, the question card collapses into a single line in the activity feed: "✓ App port: 8080 (you confirmed)"
- The component auto-focuses on mount so the user can answer immediately

**Accessibility:**

- Radio group uses `role="radiogroup"` with `aria-labelledby` pointing to the question text
- Text input has `aria-label` matching the question
- "Continue" button is disabled until a selection/value is provided

### 5.4 `DegradedModeBanner.tsx`

A banner component shown when discovery is running in a limited mode (Doc 071 §8.1).

```typescript
interface DegradedModeBannerProps {
  mode: "repo-only" | "server-only" | "interview";
  reason?: string;  // e.g., "SSH connection timed out"
}
```

**Renders:**

| Mode | Banner Text |
|------|-------------|
| `repo-only` | "⚠️ Could not connect to server — discovering from repository only. You'll be asked for server-side details." |
| `server-only` | "⚠️ Could not clone repository — discovering from server only. You'll be asked about build strategy." |
| `interview` | "⚠️ Could not access repository or server — Bond will ask you a few questions to build the deployment plan." |

Uses `role="alert"` so screen readers announce it immediately.

---

## 6. Hook: `useAgentDiscovery`

A custom React hook that encapsulates all SSE and state management:

```typescript
interface UseAgentDiscoveryReturn {
  // State
  status: "idle" | "connecting" | "discovering" | "degraded" | "question" | "complete" | "error";
  discoveryMode: "full" | "repo-only" | "server-only" | "interview";
  activityLog: ActivityItem[];
  currentQuestion: UserQuestion | null;
  questionsRemaining: number;
  discoveryState: DiscoveryState | null;
  completeness: CompletenessReport | null;
  probesRun: ProbeRecord[];  // from DiscoveryState.probes_run — what the agent tried
  error: string | null;

  // Actions
  startDiscovery: (resourceId: string) => Promise<void>;
  answerQuestion: (field: string, value: string) => Promise<void>;
  editField: (field: string, value: string) => void;
  retry: () => void;
  cancel: () => void;
}

interface ActivityItem {
  id: string;
  timestamp: string;
  icon: "success" | "inferred" | "searching" | "warning" | "error" | "question";
  message: string;
  field?: string;
  probe?: string;  // which discovery tool produced this (e.g., "detect_framework")
}
```

**Implementation details:**

1. `startDiscovery` — POSTs to `/broker/deploy` with `{ action: "discover", agent: true }`, receives `session_id`, sets status to `connecting`, opens `EventSource` to `/deployments/discovery/stream/{session_id}`
2. On `discovery_agent_started` — transitions from `connecting` to `discovering` (or `degraded` if `mode !== "full"`), sets `discoveryMode`
3. SSE `onmessage` handler parses events and updates `activityLog`, `discoveryState`, `completeness` (including `recommended_coverage`), `probesRun`, and `currentQuestion`
4. On `discovery_user_question` — sets `questionsRemaining` from `details.questions_remaining`
5. `answerQuestion` — POSTs to `/deployments/discovery/answer/{session_id}`, clears `currentQuestion`, resumes SSE listening
6. `editField` — Local state update: modifies `discoveryState.findings[field]` and sets confidence to `{ source: "user-provided", score: 1.0 }`, recalculates completeness locally (both required AND recommended coverage)
7. `cancel` — Closes the EventSource, POSTs cancel to `/deployments/discovery/cancel/{session_id}`, resets status to `idle`
8. Cleanup: EventSource is closed on unmount via `useEffect` cleanup
9. **Timeout handling** — If no events received for 60s, transitions to `complete` with partial results and sets any missing required fields as editable (Doc 071 §8.1)

---

## 7. Integration into Existing Wizard

### 7.1 Feature Flag Gate

In `DiscoverStackWizard.tsx`, the Step 2 content is conditionally rendered:

```typescript
// At the top of the component
const agentDiscoveryEnabled = process.env.NEXT_PUBLIC_BOND_AGENT_DISCOVERY === "true";

// In the step 2 render:
{step === "discovery" && (
  agentDiscoveryEnabled
    ? <AgentDiscoveryView
        resourceId={selectedServerId}
        onComplete={(state) => {
          setDiscoveryState(state);
          setStep("review");
        }}
        onError={setDiscoveryError}
        onCancel={() => setStep("server-selection")}
      />
    : <LegacyDiscoveryView /* existing code */ />
)}
```

### 7.2 Review Step Changes

Step 3 ("review") currently shows raw discovery layers. When agent discovery is used, it instead shows the `DeploymentPlanPanel` in a read/edit mode where every field is editable — including recommended and optional fields. This replaces the accordion of raw layer data with a clean summary organized by field category.

### 7.3 Ship It Integration

The "Ship It" button calls `generate_plan` (Doc 056 §2.4) with the `DiscoveryState.findings` converted to the plan format via `convertToManifest()` (implemented in `discovery-agent.ts`).

**Manifest mapping** (fields from Doc 071 → plan format):

| DiscoveryState field | Manifest field | Notes |
|---------------------|----------------|-------|
| `findings.source` | `manifest.source` | Direct |
| `findings.framework` | `manifest.framework` | Direct |
| `findings.build_strategy` | `manifest.build` | Direct |
| `findings.target_server` | `manifest.target` | Direct |
| `findings.app_port` | `manifest.port` | Direct |
| `findings.env_vars` | `manifest.environment` | Direct |
| `findings.health_endpoint` | `manifest.healthCheck` | Direct |
| `findings.monitoring_config` | `manifest.monitoring` | Direct |
| `findings.services` | `manifest.dependencies` | Maps detected services to dependency declarations |
| `findings.deploy_strategy` | `manifest.strategy` | Falls back to "rolling" default |
| `findings.rollback_plan` | `manifest.rollback` | Falls back to auto-configured default |
| `findings.alert_channels` | `manifest.alerts` | Optional, omitted if not configured |

The frontend calls:

```typescript
POST /broker/deploy {
  action: "generate_plan",
  manifest: convertedManifest
}
```

---

## 8. Fallback & Error Handling

### 8.1 Feature Flag Off

Use existing shell-script discovery (no changes).

### 8.2 Degraded Discovery Modes (from Doc 071 §8.1)

| Scenario | Backend Behavior | UI Behavior |
|----------|-----------------|-------------|
| SSH connection fails | Discovers from repo only; asks user for server-side info | `DegradedModeBanner` with `mode="repo-only"`. Agent will use `ask_user` for port, running services. |
| Repo clone fails | Discovers from server only via SSH; asks user about build strategy | `DegradedModeBanner` with `mode="server-only"`. Agent will use `ask_user` for framework, build. |
| Both fail | Falls back to structured interview for all required fields | `DegradedModeBanner` with `mode="interview"`. All required fields rendered as an editable form. |

### 8.3 Connection & Timeout Errors

| Scenario | Behavior |
|----------|----------|
| SSE connection drops | Show "Connection lost" banner with "Reconnect" button; auto-retry once after 2s |
| Agent times out (60s no events) | Show partial results in plan panel; missing required fields become editable inputs with placeholder text "Enter value" |
| Agent hits max iterations | Same as timeout — partial results + editable fields for gaps |
| User closes wizard mid-discovery | EventSource closed via cleanup, cancel POST sent to backend |
| All required fields missing after agent | Render a simple form with just the missing fields (structured interview fallback from Doc 071 §8.1) |

### 8.4 Debug Support

The backend writes all probe attempts and results to `~/.bond/deployments/discovery/agent-log.jsonl` (Doc 071 §8.3). The activity feed exposes probe names on each item. In the `complete` or `error` state, a "Show debug log" link at the bottom of the activity feed opens a collapsible section showing `probes_run` from `DiscoveryState` — what the agent tried, what succeeded, and what failed. This helps users self-diagnose "why didn't it find X?" without contacting support.

---

## 9. File Changes

| File | Action | Description |
|------|--------|-------------|
| `frontend/src/app/settings/deployment/AgentDiscoveryView.tsx` | **New** | Main discovery step component with activity feed, cancel button, degraded-mode banner |
| `frontend/src/app/settings/deployment/DeploymentPlanPanel.tsx` | **New** | Real-time plan assembly with confidence icons, inline edit, three-section layout (required/recommended/optional), services section |
| `frontend/src/app/settings/deployment/InlineQuestion.tsx` | **New** | Inline question card for `ask_user` events with question budget display |
| `frontend/src/app/settings/deployment/DegradedModeBanner.tsx` | **New** | Banner for repo-only, server-only, and interview degraded modes |
| `frontend/src/hooks/useAgentDiscovery.ts` | **New** | Custom hook for SSE consumption, discovery state management, degraded mode tracking, cancel support |
| `frontend/src/app/settings/deployment/DiscoverStackWizard.tsx` | **Modify** | Add feature flag gate, import `AgentDiscoveryView`, pass discovery state to review step |
| `gateway/src/deployments/discovery.ts` | **Modify** | Add SSE streaming endpoint, answer endpoint, and cancel endpoint |

---

## 10. Testing Strategy

### Unit Tests

- `useAgentDiscovery` hook: mock EventSource, verify state transitions for each event type including `connecting` and `degraded` states
- `useAgentDiscovery` hook: verify `cancel()` closes EventSource and posts cancel
- `useAgentDiscovery` hook: verify timeout handling after 60s of no events
- `InlineQuestion`: render with options vs free-form, verify answer callback, verify `questionsRemaining` display
- `DeploymentPlanPanel`: render with partial state, verify field edit callback, verify Ship It disabled when not ready
- `DeploymentPlanPanel`: verify services section renders `detect_services` findings
- `DeploymentPlanPanel`: verify optional section is collapsed by default
- `DeploymentPlanPanel`: verify both required and recommended progress bars
- `DegradedModeBanner`: render each mode, verify correct message and `role="alert"`

### Integration Tests

- Full wizard flow: start discovery → receive progress events → answer question → complete → Ship It
- Degraded mode: SSH fails → banner shown → agent asks user questions → plan completes
- Degraded mode: repo clone fails → banner shown → server-only discovery proceeds
- Degraded mode: both fail → interview mode → user fills all required fields → Ship It enabled
- Fallback: agent discovery flag off → old discovery path used
- Error: SSE drops → reconnect banner shown
- Timeout: partial results → editable fields for missing values
- Cancel: user clicks Cancel → EventSource closed → wizard returns to Step 1
- Services: `detect_services` finds PostgreSQL + Redis → services section shows both with ports
- Optional fields: `deploy_strategy` discovered → appears in collapsed Optional section

### Accessibility Tests

- Activity feed announces new items to screen readers (ARIA live region)
- All interactive elements reachable via Tab key
- Question card focus management on mount
- Reduced-motion: animations disabled when `prefers-reduced-motion: reduce`
- Edit buttons have descriptive `aria-label`
- Progress bars have correct ARIA attributes

### Manual QA Checklist

- [ ] Discovery starts automatically when entering Step 2
- [ ] Skeleton/connecting state shown before first SSE event
- [ ] Activity items appear in real-time (not batched)
- [ ] Activity items show probe name as secondary label
- [ ] Question card is answerable and discovery resumes
- [ ] Question card shows remaining question count
- [ ] Plan panel fields are editable on hover/click
- [ ] Overriding a field updates confidence to "user-provided"
- [ ] Ship It is disabled until all required fields are filled
- [ ] Low-confidence fields show amber indicator
- [ ] Services section shows detected databases/caches/queues
- [ ] Monitoring config row appears when discovered
- [ ] Optional settings section is collapsed by default and expandable
- [ ] Both required and recommended progress bars update correctly
- [ ] Degraded mode banner appears when SSH or repo fails
- [ ] Cancel button stops discovery and returns to Step 1
- [ ] Debug log section available after completion/error
- [ ] Works when `BOND_AGENT_DISCOVERY` is unset (falls back to old path)
- [ ] Keyboard navigation works for all interactive elements
- [ ] Screen reader announces activity items and degraded-mode banners

---

## 11. Open Questions

1. **Plan panel position** — Should it be a right-side panel (side-by-side with activity feed) or below the activity feed? Side-by-side is better for wide screens but may not work well on narrow viewports. **Recommendation:** Below on mobile, side-by-side on desktop (breakpoint at 1024px).

2. **Edit persistence** — When a user edits a field in the plan panel, should that override persist across re-discoveries? **Recommendation:** Yes, store in `user_answers` and pre-populate on re-run (aligns with Doc 071 §11 cache behavior).

3. **Animation budget** — Activity items fade in, question cards slide in, plan fields appear. How much animation is too much? **Recommendation:** Keep transitions under 200ms, use `prefers-reduced-motion` to disable.

4. **Debug log depth** — How much of `probes_run` should be shown in the debug section? Full SSH command output could be very long. **Recommendation:** Show probe name, status (success/fail), and a one-line summary. Full output available via the `agent-log.jsonl` file.

5. **Services editing** — Should users be able to add/remove/edit detected services, or just view them? **Recommendation:** View-only for v1, with an "Add service" button in a future iteration.
