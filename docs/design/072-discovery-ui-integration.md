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

---

## 2. Design Principles

1. **Keep it simple** — The discovery step should feel like watching Bond think, not like operating a control panel. Minimal chrome, clear status.
2. **Progressive disclosure** — Show a clean summary by default. Let users expand details or override values only when they want to.
3. **Stay in flow** — Agent questions appear inline, not as modals. The user answers and discovery continues without navigation.
4. **Confidence, not complexity** — Use simple icons (✓ detected, ~ inferred, ? needs review) instead of numeric scores. Users don't need to see "0.73".
5. **Graceful degradation** — If the agent discovery flag is off or the endpoint fails, fall back to the existing shell-script discovery seamlessly.

---

## 3. Scope

### In Scope (This Doc)

- New `AgentDiscoveryView` component replacing Step 2 content in the wizard
- New `DeploymentPlanPanel` component showing the plan as it builds
- Inline question UI for `ask_user` events
- SSE event consumption via `EventSource`
- Feature flag check (`BOND_AGENT_DISCOVERY`) to toggle between old and new paths
- Override capability for any discovered field

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
    server: "192.168.1.50"
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
    completeness: { ready: false, required_coverage: 0.4, missing_required: ["build_strategy", "app_port"] }
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
    default: "8080"
  }
}

// discovery_agent_completed
{
  event: "discovery_agent_completed",
  summary: "Discovery complete — plan ready",
  details: {
    state: { /* full DiscoveryState */ },
    completeness: { ready: true, required_coverage: 1.0, missing_required: [] }
  }
}
```

### 4.3 New Gateway Endpoint

The existing `POST /broker/deploy` with `action: "discover"` needs a small addition:

```typescript
// When agent discovery is enabled, the request includes:
{ action: "discover", resource_id: "server-123", agent: true }

// The response initiates SSE streaming on:
GET /deployments/discovery/stream/{session_id}

// User answers are posted to:
POST /deployments/discovery/answer/{session_id}
{ field: "app_port", value: "8080" }
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
| `discovering` | Live activity feed + progress bar + plan panel building up |
| `question` | Activity feed paused, inline question card highlighted at bottom |
| `complete` | Full plan panel, "Continue" button enabled |
| `error` | Error message with "Retry" button |

**Layout:**

```
┌──────────────────────────────────────────────────┐
│  Discovering your application...                 │
│                                                  │
│  ✅ Cloned repository                            │
│  ✅ Detected Express framework (v4.18)           │
│  ✅ Found Dockerfile (multi-stage build)         │
│  🔍 Checking application port...                 │
│                                                  │
│  ┌─ Question ──────────────────────────────────┐ │
│  │ I found ports 3000 and 8080 in your         │ │
│  │ codebase. Which is your main app port?      │ │
│  │                                             │ │
│  │  ○ 3000 (server.js:42)                      │ │
│  │  ● 8080 (Dockerfile EXPOSE)  ← suggested    │ │
│  │                                             │ │
│  │           [Continue]                        │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ━━━━━━━━━━━━━━━━━━━━░░░░░░░░  3/5 fields  60%  │
└──────────────────────────────────────────────────┘
```

**Key behaviors:**

- Activity items appear one at a time as `discovery_agent_progress` events arrive
- Each item shows an icon: ✅ (detected, score ≥ 0.9), ✓ (inferred, 0.6–0.89), ⚠️ (low confidence, flagged)
- The progress bar shows `required_coverage` from the completeness report
- When a `discovery_user_question` event arrives, the question card slides in at the bottom of the activity feed
- The user answers via radio buttons (if `options` provided) or a text input (if free-form)
- Clicking "Continue" on the question posts the answer and discovery resumes
- Auto-scroll keeps the latest activity item visible

**Props:**

```typescript
interface AgentDiscoveryViewProps {
  resourceId: string;
  onComplete: (state: DiscoveryState) => void;
  onError: (error: string) => void;
}
```

### 5.2 `DeploymentPlanPanel.tsx`

Shown alongside or below the activity feed. Builds up as fields are discovered.

**Layout:**

```
┌─ Deployment Plan ────────────────────────────────┐
│                                                  │
│  Source       github.com/acme/api          ✓     │
│  Framework    Express 4.18                 ✓     │
│  Build        Dockerfile (multi-stage)     ✓     │
│  Target       192.168.1.50:22              ✓     │
│  App Port     8080                     [edit] 👤  │
│  Health       GET /healthz                 ~     │
│  Env Vars     3 detected               [edit]    │
│                                                  │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  5/5  Ready  │
│                                                  │
│              [ Ship It 🚀 ]                      │
└──────────────────────────────────────────────────┘
```

**Key behaviors:**

- Fields appear as they're discovered (animated fade-in)
- Confidence icons: ✓ = detected, ~ = inferred, 👤 = user-provided, ⚠️ = low confidence
- Every field row has a subtle `[edit]` button on hover — clicking it opens an inline text input to override the value
- When a user overrides a field, its confidence source changes to `user-provided` (score 1.0)
- The bottom progress bar shows required field coverage
- "Ship It" button is enabled only when `completeness.ready === true`
- Fields in `low_confidence` array have an amber dot — hovering shows "This was inferred, you may want to verify"

**Props:**

```typescript
interface DeploymentPlanPanelProps {
  state: DiscoveryState;
  onFieldEdit: (field: string, value: string) => void;
  onShipIt: () => void;
  ready: boolean;
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
  onAnswer: (field: string, value: string) => void;
}
```

**Behavior:**

- If `options` is provided: render radio buttons, pre-select `defaultValue`
- If no `options`: render a single text input with `defaultValue` as placeholder
- "Continue" button submits the answer
- After submission, the question card collapses into a single line in the activity feed: "✓ App port: 8080 (you confirmed)"
- The component auto-focuses on mount so the user can answer immediately

---

## 6. Hook: `useAgentDiscovery`

A custom React hook that encapsulates all SSE and state management:

```typescript
interface UseAgentDiscoveryReturn {
  // State
  status: "idle" | "discovering" | "question" | "complete" | "error";
  activityLog: ActivityItem[];
  currentQuestion: UserQuestion | null;
  discoveryState: DiscoveryState | null;
  completeness: CompletenessReport | null;
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
}
```

**Implementation details:**

1. `startDiscovery` — POSTs to `/broker/deploy` with `{ action: "discover", agent: true }`, receives `session_id`, opens `EventSource` to `/deployments/discovery/stream/{session_id}`
2. SSE `onmessage` handler parses events and updates `activityLog`, `discoveryState`, `completeness`, and `currentQuestion`
3. `answerQuestion` — POSTs to `/deployments/discovery/answer/{session_id}`, clears `currentQuestion`, resumes SSE listening
4. `editField` — Local state update: modifies `discoveryState.findings[field]` and sets confidence to `{ source: "user-provided", score: 1.0 }`, recalculates completeness locally
5. `cancel` — Closes the EventSource, POSTs cancel to backend
6. Cleanup: EventSource is closed on unmount via `useEffect` cleanup

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
      />
    : <LegacyDiscoveryView /* existing code */ />
)}
```

### 7.2 Review Step Changes

Step 3 ("review") currently shows raw discovery layers. When agent discovery is used, it instead shows the `DeploymentPlanPanel` in a read/edit mode where every field is editable. This replaces the accordion of raw layer data with a clean summary.

### 7.3 Ship It Integration

The "Ship It" button calls `generate_plan` (Doc 056 §2.4) with the `DiscoveryState.findings` converted to the plan format via `convertToManifest()` (already implemented in `discovery-agent.ts`). The frontend calls:

```typescript
POST /broker/deploy {
  action: "generate_plan",
  manifest: convertedManifest
}
```

---

## 8. Fallback & Error Handling

| Scenario | Behavior |
|----------|----------|
| Feature flag off | Use existing shell-script discovery (no changes) |
| SSE connection drops | Show "Connection lost" banner with "Reconnect" button; auto-retry once after 2s |
| Agent times out (60s) | Show partial results in plan panel; missing required fields become editable inputs with placeholder text "Enter value" |
| Agent hits max iterations | Same as timeout — partial results + editable fields for gaps |
| User closes wizard mid-discovery | EventSource closed, backend session abandoned |
| All required fields missing after agent | Render a simple form with just the missing fields (structured interview fallback from Doc 071 §8.1) |

---

## 9. File Changes

| File | Action | Description |
|------|--------|-------------|
| `frontend/src/app/settings/deployment/AgentDiscoveryView.tsx` | **New** | Main discovery step component with activity feed |
| `frontend/src/app/settings/deployment/DeploymentPlanPanel.tsx` | **New** | Real-time plan assembly with confidence icons and inline edit |
| `frontend/src/app/settings/deployment/InlineQuestion.tsx` | **New** | Inline question card for `ask_user` events |
| `frontend/src/hooks/useAgentDiscovery.ts` | **New** | Custom hook for SSE consumption and discovery state management |
| `frontend/src/app/settings/deployment/DiscoverStackWizard.tsx` | **Modify** | Add feature flag gate, import `AgentDiscoveryView`, pass discovery state to review step |
| `gateway/src/deployments/discovery.ts` | **Modify** | Add SSE streaming endpoint and answer endpoint |

---

## 10. Testing Strategy

### Unit Tests

- `useAgentDiscovery` hook: mock EventSource, verify state transitions for each event type
- `InlineQuestion`: render with options vs free-form, verify answer callback
- `DeploymentPlanPanel`: render with partial state, verify field edit callback, verify Ship It disabled when not ready

### Integration Tests

- Full wizard flow: start discovery → receive progress events → answer question → complete → Ship It
- Fallback: agent discovery flag off → old discovery path used
- Error: SSE drops → reconnect banner shown
- Timeout: partial results → editable fields for missing values

### Manual QA Checklist

- [ ] Discovery starts automatically when entering Step 2
- [ ] Activity items appear in real-time (not batched)
- [ ] Question card is answerable and discovery resumes
- [ ] Plan panel fields are editable on hover/click
- [ ] Overriding a field updates confidence to "user-provided"
- [ ] Ship It is disabled until all required fields are filled
- [ ] Low-confidence fields show amber indicator
- [ ] Works when `BOND_AGENT_DISCOVERY` is unset (falls back to old path)

---

## 11. Open Questions

1. **Plan panel position** — Should it be a right-side panel (side-by-side with activity feed) or below the activity feed? Side-by-side is better for wide screens but may not work well on narrow viewports. **Recommendation:** Below on mobile, side-by-side on desktop (breakpoint at 1024px).

2. **Edit persistence** — When a user edits a field in the plan panel, should that override persist across re-discoveries? **Recommendation:** Yes, store in `user_answers` and pre-populate on re-run (aligns with Doc 071 §11 cache behavior).

3. **Animation budget** — Activity items fade in, question cards slide in, plan fields appear. How much animation is too much? **Recommendation:** Keep transitions under 200ms, use `prefers-reduced-motion` to disable.
