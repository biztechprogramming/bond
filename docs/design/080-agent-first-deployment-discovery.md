# Design Doc 080: Agent-First Deployment Discovery

**Status:** Draft
**Author:** Bond
**Date:** 2026-03-28
**Updated:** 2026-03-28
**Depends on:** 028 (Unify LLM Key Resolution), 071 (Agent-Driven Deployment Discovery), 072 (Discovery UI Integration)
**Supersedes:** Partially supersedes the LLM call path in 071

---

## 1. Problem Statement

Deployment discovery currently uses **two completely different LLM call paths**:

| | Agent Turn (works) | Discovery (`/llm/complete`) (broken) |
|---|---|---|
| Key resolution | `ApiKeyResolver` — 4-tier with injected OAuth keys | `get_provider_api_key()` + `_resolve_api_key()` fallback |
| Model normalization | `normalize_model_for_litellm()` using DB `litellm_prefixes` | `f"{provider}/{model}"` hardcoded |
| OAuth headers | Injected by resolver from Gateway token | `get_oauth_extra_headers()` — same logic, different code path |
| Provider mapping | `provider_aliases` from DB | `settings.llm_provider` raw |

Design Doc 028 proposed fixing this by creating a `create_standalone_resolver()` that constructs an `ApiKeyResolver` without container context. That works, but it's **still maintaining two paths** — just making the second path use the same resolver. Every future LLM feature (rate limiting, model routing, cost tracking, Langfuse tracing) would need to be wired into both paths.

**This document proposes a fundamentally different approach:** eliminate the second path entirely by routing all LLM calls through the agent.

---

## 2. Core Idea

**What if discovery doesn't call the LLM directly at all?**

Instead of:

```
UI → "Start Discovery" → Gateway → discovery-agent.ts → BackendClient.llmComplete() → /api/v1/llm/complete → chat_completion() [BROKEN PATH]
```

We route through the existing, working agent infrastructure:

```
UI → Select Agent → Select/Add Repo → "Discover" → Gateway → Agent Turn → agent loop → ApiKeyResolver → LiteLLM [WORKING PATH]
```

The discovery becomes a **skill invocation on an existing agent**, not a standalone LLM call. The agent already has:
- Working key resolution (`ApiKeyResolver` — 4-tier: injected keys → SpacetimeDB → Vault → env var)
- Model normalization (`normalize_model_for_litellm()`)
- OAuth headers
- Cost tracking
- Langfuse tracing
- Context management
- Tool execution (shell, file read, SSH)

---

## 3. Proposed UX Flow

### Current Flow (071/072)
```
1. User goes to Deployments tab
2. Selects a resource (server)
3. Clicks "Discover"
4. Gateway runs discovery-agent.ts which:
   a. Clones repo
   b. Runs file probes (detect_framework, detect_ports, etc.)
   c. Calls BackendClient.llmComplete() → FAILS
   d. Merges results
5. UI shows progressive discovery results
```

> **Note:** `llm-discovery.ts` referenced in earlier drafts no longer exists. The LLM call path was `discovery-agent.ts` → `BackendClient.llmComplete()` in `gateway/src/backend/client.ts`.

### Proposed Flow
```
1. User goes to Deployments tab
2. Selects an Agent (or uses a default "deployment" agent)
3. Selects a Repo (from agent's existing repos, or adds a new one)
4. Optionally selects a target server/resource
5. Clicks "Discover"
6. Gateway sends a message to the agent: "Analyze this repo for deployment"
7. Agent turn runs using the EXISTING agent loop:
   a. Agent reads project files using its file tools
   b. Agent reasons about deployment config (this IS the LLM call — through the working path)
   c. Agent can use shell tools to run detect_framework, detect_ports, etc.
   d. Agent returns structured deployment findings
8. UI shows progressive results via the existing agent SSE stream
```

### Key UX Difference

The user picks an **agent** first, then a **repo**. This is actually more natural because:
- The agent already has SSH keys, environment access, and tool permissions configured
- The agent already has a working LLM connection (the whole point)
- Repos are already a first-class concept on agents
- The user doesn't need to re-enter connection details — the agent's resource bindings provide them

---

## 4. Architecture

### 4.1 What Changes

#### Gateway: `router.ts` — Discovery endpoint

The `POST /deployments/agent-discover` endpoint (already implemented in `router.ts`) changes from spawning `runAgentDiscovery()` to sending a message to an existing agent:

```typescript
// BEFORE: Custom discovery pipeline
const agentParams = {
  repoPath: conn.repo_path,
  serverHost: conn.host,
  // ... build custom params
};
runAgentDiscovery(agentParams);  // Custom pipeline with broken LLM path

// AFTER: Agent turn with deployment skill
const agentId = body.agent_id;       // Required — user selected an agent
const repoId = body.repo_id;         // Required — user selected a repo
const resourceId = body.resource_id; // Optional — target server

// Run pre-gather probes (already implemented as runProbes() in discovery-agent.ts)
const probeResults = await runProbes(repoPath, sshParams);

await sendAgentMessage(agentId, {
  content: buildDiscoveryPrompt(repoId, probeResults, resourceId),
  skill: "appdeploy",               // Routes to deployment skill context
  structured_output: DISCOVERY_SCHEMA, // Agent returns structured JSON
});
```

#### Backend: No changes to `llm.py` or `chat_completion()`

The entire broken `/api/v1/llm/complete` path is **no longer used for discovery**. **Recommendation: deprecate `BackendClient.llmComplete()`** — if `deployment-planner.ts` is also migrated to agent turns, the method can be removed entirely.

#### Backend: Agent skill — `appdeploy`

The existing `skills/appdeploy/SKILL.md` describes deployment analysis. The agent's system prompt, augmented by the skill context, tells it to:

1. Read project files (using existing `file_read` / `shell` tools)
2. Analyze the project type, framework, build strategy
3. If a target server resource is provided, SSH to probe it
4. Return a structured JSON block wrapped in `` ```discovery-result ... ``` `` markers matching the `DiscoveryState.findings` schema

The agent **is** the LLM call. There's no separate `BackendClient.llmComplete()` hop.

#### Gateway: `discovery-agent.ts` — Probe orchestrator + legacy loop

The file probes (`detect_framework`, `detect_ports`, `detect_services`, etc.) are still valuable as **pre-gathering**. The file already contains two code paths:

1. **`runProbes()`** (lines 167–269) — Standalone probe runner that returns `ProbeResults` without any LLM calls. This is the function used by the agent-first path.
2. **`runAgentDiscovery()`** (lines 281–551) — Legacy adaptive loop with iteration limits (`MAX_ITERATIONS=5`, `MAX_TOOL_CALLS=20`, `TIMEOUT_MS=60000`). Kept for backward compatibility during migration.

**Probe strategy decision: Option A (pre-gather) is implemented.** `buildDiscoveryPrompt()` in `discovery-sse-adapter.ts` injects probe results into the agent's context as JSON. The agent uses these to confirm/override/augment with its own analysis.

#### Gateway: `discovery-sse-adapter.ts` — SSE event mapping

Already implemented. Maps agent SSE events to discovery events:

| Agent SSE Event | Discovery SSE Event | Behavior |
|---|---|---|
| `message` (type: `text`/`content`) | `discovery_agent_progress` | Accumulates text; emits progress with `field: "agent_analysis"` |
| `message` (type: `status`) | `discovery_agent_progress` | Status update forwarded |
| `done` | `discovery_agent_completed` | Parses accumulated text for `` ```discovery-result``` `` JSON block via `parseDiscoveryResult()` |
| `error` | `discovery_agent_completed` | Forwards error message |

If the agent's `done` event contains no parseable `` ```discovery-result``` `` block, the adapter returns `ready: false` with the raw agent text for debugging.

#### Frontend: `AgentDiscoveryView.tsx` + `useAgentDiscovery.ts`

Already implemented with the following capabilities:

- **SSE streaming** via `POST /deployments/agent-discover` → `GET /deployments/discovery/stream/{sessionId}`
- **Activity log** showing real-time probe results and agent reasoning
- **Inline questions** via `InlineQuestion` component (auto-skips questions for fields already discovered at ≥80% confidence)
- **30-second client-side timeout** — auto-completes with accumulated state if no SSE events received
- **Force complete** — user can accept partial results at any time
- **Field editing** — user can override any discovered value
- **`startDiscovery()`** already accepts optional `agentId` and `repoId` parameters

**Remaining frontend work:** Add the agent/repo selection pre-step UI (the wireframe in §3). Currently the view takes a `resourceId` directly.

---

## 5. Trade-off Analysis: Better or Worse?

### What Gets Better

| Aspect | Why |
|---|---|
| **LLM calls just work** | Zero new code for key resolution, OAuth, model normalization. The agent path is battle-tested. This is the entire motivation. |
| **Single code path** | Every LLM call in the system goes through `agent loop → ApiKeyResolver → LiteLLM`. No second path to maintain. |
| **Cost tracking for free** | Agent turns are already tracked by `cost_tracker.py` and Langfuse. Discovery LLM costs become visible automatically. |
| **Tool reuse** | The agent can read files, run shell commands, SSH to servers — all using existing, tested tools. |
| **Context management** | The agent's context pipeline handles token budgets, truncation, and priority. The standalone `chat_completion()` had none of this. |
| **Iterative reasoning** | If the agent needs multiple turns to analyze a complex project (monorepo, multi-service), it can. The standalone LLM call was one-shot. |
| **Skill ecosystem** | The `appdeploy` skill is already defined in `skills/appdeploy/SKILL.md`. Other skills (monitoring, rollback) can follow the same pattern. |
| **Eliminates Doc 028 entirely** | No need to build `create_standalone_resolver()`. The problem it solves doesn't exist if we don't use the standalone path. |

### What Gets Worse

| Aspect | Why | Mitigation |
|---|---|---|
| **Extra UX step** | User must select an agent before discovering. Current flow just needs a resource. | Default to a "deployment" agent if only one exists. Auto-create one on first use. |
| **Slower startup** | Agent turn has overhead: context building, skill loading, pre-gather. The standalone LLM call was a single HTTP request. | Pre-run file probes in parallel while agent context loads. Net latency similar. |
| **Agent must exist** | Requires a configured agent with working LLM credentials. Current flow attempted to use system-level credentials. | Those system-level credentials were broken anyway. This makes the requirement explicit rather than silently failing. |
| **Heavier for simple projects** | A simple static site doesn't need a full agent turn. | The agent will still return quickly for simple projects. The overhead is ~2-3 seconds of context building. |
| **SSE stream format change** | Frontend currently expects custom discovery events. Agent turns emit different SSE events. | `discovery-sse-adapter.ts` already handles this mapping (see §4.1). |
| **Agent turn cost** | Each discovery costs an LLM call against the user's token budget / OAuth quota. | Discovery was always going to cost an LLM call. Now it's just tracked properly. |

### Net Assessment: **Better**

The "worse" column is entirely about minor UX friction and startup latency. The "better" column eliminates an entire class of bugs (dual LLM paths), removes the need for Design Doc 028, and gets cost tracking, tracing, and iterative reasoning for free.

The fundamental insight is: **discovery IS an agent task**. Treating it as a standalone LLM call was an optimization that created a maintenance burden and a broken code path. Routing it through the agent is the architecturally correct solution.

---

## 6. What Happens to Existing Code

| File | Fate |
|---|---|
| `gateway/src/deployments/discovery-agent.ts` | **Simplified.** `runProbes()` is kept as the pre-gather step. `runAgentDiscovery()` (the legacy loop at line 281) is deprecated and removed once migration is complete. `evaluateCompleteness()` and `convertToManifest()` remain — used by both paths. |
| `gateway/src/deployments/discovery-sse-adapter.ts` | **Already implemented.** `buildDiscoveryPrompt()`, `parseDiscoveryResult()`, `mapAgentEventToDiscovery()` handle the agent-first path. |
| `gateway/src/backend/client.ts` — `llmComplete()` | **Deprecated.** Not used by discovery. Remove once `deployment-planner.ts` is also migrated. |
| `backend/app/api/v1/llm.py` — `chat_completion()` | **Unchanged.** Not used by discovery anymore. Can be fixed separately (or not). |
| `backend/app/agent/api_key_resolver.py` | **Unchanged.** Already works — 4-tier resolution (injected → SpacetimeDB → Vault → env). |
| `skills/appdeploy/SKILL.md` | **Enhanced.** Add structured output schema for discovery findings. |
| `gateway/src/deployments/router.ts` | **Modified.** The `agent-discover` endpoint already exists; needs to wire in the agent turn instead of the legacy `runAgentDiscovery()`. |
| `frontend/src/components/discovery/AgentDiscoveryView.tsx` | **Modified.** Add agent/repo selection pre-step. Already handles SSE streaming, activity log, inline questions. |
| `frontend/src/hooks/useAgentDiscovery.ts` | **Minor change.** `startDiscovery()` already accepts `agentId`/`repoId` params — just needs to pass them to the API call. |
| **Design Doc 028** | **Superseded.** No longer needed if discovery doesn't use the standalone LLM path. |

> **Note:** `llm-discovery.ts` referenced in earlier drafts of this doc does not exist in the codebase. The LLM call was made directly via `BackendClient.llmComplete()` from within `discovery-agent.ts`.

---

## 7. Error Handling

### 7.1 Failure Modes and Responses

| Failure | Detection | Response | User-Facing Message |
|---|---|---|---|
| **Agent not found** | `sendAgentMessage()` returns 404 | Fail fast, show error | "No deployment agent configured. Create an agent with LLM credentials first." |
| **Agent LLM quota exceeded** | Agent turn returns quota error | Fail with clear error | "LLM quota exceeded. Check your API key limits." |
| **Agent timeout** | No response within 60s (server) or 30s idle (client) | Client auto-completes with accumulated probe data | "Discovery timed out — showing results from automated probes." |
| **Probe failures** | Individual `runProbe()` catches errors (line 592–601 in `discovery-agent.ts`) | Log error, continue with remaining probes | Probe error shown in activity log; discovery continues. |
| **SSH connection fails** | `sshExec()` returns non-zero exit | Degrade to repo-only discovery | "Could not connect to server — discovering from repo only." |
| **Repo clone fails** | `git clone` throws in `runAgentDiscovery()` | Degrade to server-only or interview mode | "Could not clone repo — please check the URL and permissions." |
| **No structured output from agent** | `parseDiscoveryResult()` returns null on `done` event | Return `ready: false` with raw agent text | "Agent completed but didn't return structured results. Raw response shown for debugging." |
| **Agent loops without progress** | Agent hits `MAX_ITERATIONS=5` or `MAX_TOOL_CALLS=20` | Present partial results, ask user for remaining | "Discovery made partial progress. Please fill in the remaining fields." |
| **Concurrent discovery for same resource** | Check for existing session with same resource ID | Reject with clear message | "Another discovery is already running for this resource. Wait or cancel it first." |

### 7.2 Idempotency

If the user clicks "Start Discovery" while a discovery is already running for the same session:
- The frontend `useAgentDiscovery` hook resets state and creates a new `AbortController`, aborting any in-flight SSE stream.
- The gateway should check for an existing active session for the same `(agent_id, repo_id, resource_id)` tuple and either reuse it or cancel-then-restart.
- Each session gets a unique ID via `ulid()` — no risk of data interleaving.

---

## 8. Security Considerations

### 8.1 Discovery Credential Scoping

- **Environment isolation:** The existing `runDiscovery()` in `discovery.ts` (line 57) enforces that agents can only discover resources in their own environment. This check must be preserved in the agent-first path.
- **SSH command allowlist:** `discovery-tools.ts` defines `ALLOWED_SSH_COMMANDS` (line 110) and validates all commands via `validateSshCommand()` before execution. Shell operators (`;`, `&`, `|`, `` ` ``, `$`, `(`, `)`) in the command prefix are rejected.
- **Agent tool permissions:** The discovery agent should use a restricted tool set — only `file_read`, `shell` (read-only), and `ssh_exec` (with the existing allowlist). No write operations.

### 8.2 Who Can Trigger Discovery

Discovery is triggered via `POST /deployments/agent-discover`, which requires:
1. A valid user session (enforced by gateway auth middleware)
2. An `agent_id` that the user has access to
3. A `resource_id` that belongs to the agent's environment

No additional RBAC is needed for v1 — access control is inherited from agent and resource ownership.

### 8.3 Sensitive Data

- Probe results may contain environment variable names (but not values) from `.env.example` files.
- SSH command output is logged to `~/.bond/deployments/discovery/agent-log.jsonl`. This log should not be exposed via API without auth.
- The agent's accumulated text (which may contain file contents) is stored only in-memory during the session and in the SSE stream. It is not persisted after the session ends.

---

## 9. Observability

### 9.1 Metrics

| Metric | Source | Purpose |
|---|---|---|
| `discovery.started` | `emitDeploymentEvent("discovery_agent_started", ...)` | Track discovery volume |
| `discovery.completed` | `emitDeploymentEvent("discovery_agent_completed", ...)` | Track completion rate |
| `discovery.probe.duration_ms` | `ProbeRecord.duration_ms` in each probe result | Identify slow probes |
| `discovery.probe.failure` | `ProbeRecord.success === false` | Track probe reliability |
| `discovery.agent_turn.tokens` | Agent cost tracking (Langfuse / `cost_tracker.py`) | Monitor LLM cost per discovery |
| `discovery.user_questions` | Count of `discovery_user_question` events per session | Measure automation quality |
| `discovery.timeout` | Client-side 30s timeout or server-side 60s timeout hit | Track timeout frequency |

### 9.2 Debugging a Failed Discovery

1. **Client-side:** The `AgentDiscoveryView` renders a "Raw SSE Events" debug panel showing every event received with timestamps.
2. **Server-side:** All discovery events are emitted via `emitDeploymentEvent()` and written to the deployment event log.
3. **Probe-level:** Each `ProbeRecord` captures `tool`, `timestamp`, `duration_ms`, `success`, `fields_discovered`, and `error` (if failed).
4. **Agent-level:** The agent turn is tracked in Langfuse with the full prompt, tool calls, and response — standard agent observability.

---

## 10. Implementation Plan

### Phase 1: Agent-Routed Discovery (eliminates the bug)
1. Add agent/repo selection UI pre-step to `AgentDiscoveryView.tsx`
2. Modify `POST /deployments/agent-discover` in `router.ts` to send an agent message (via `sendAgentMessage()`) instead of calling `runAgentDiscovery()`
3. Enhance `skills/appdeploy/SKILL.md` with structured discovery output schema (`` ```discovery-result``` `` block format)
4. Wire `runProbes()` → `buildDiscoveryPrompt()` → agent turn → `mapAgentEventToDiscovery()` → SSE stream (adapter already exists)
5. Preserve environment isolation check from `discovery.ts` line 57

**Acceptance criteria:**
- Discovery completes end-to-end using the agent path with working LLM calls
- Probe results are injected into agent context and visible in the activity log
- SSE events render correctly in the existing discovery UI

### Phase 2: Cleanup
1. Remove the `runAgentDiscovery()` legacy loop from `discovery-agent.ts` (keep `runProbes()`, `evaluateCompleteness()`, `convertToManifest()`)
2. Mark `BackendClient.llmComplete()` as `@deprecated`
3. Update Design Doc 028 status to "Superseded by 080"
4. Remove the `runAgentDiscovery` import from `router.ts`

### Phase 3: Polish
1. Auto-create a default "deployment" agent on first use
   - **Acceptance:** If no agents exist, clicking "Discover" creates one with the `appdeploy` skill and the user's default LLM provider
2. Remember last-used agent/repo per resource for one-click re-discovery
   - **Acceptance:** Second discovery for the same resource pre-fills the agent/repo selection
3. Allow the agent to ask clarifying questions mid-discovery (leveraging the existing agent Q&A UX)
   - **Acceptance:** Agent can emit `discovery_user_question` events that render as `InlineQuestion` in the UI

### Migration/Rollout Strategy

- **Feature flag:** `agent_first_discovery` (default: off during Phase 1, on during Phase 2)
- **When flag is off:** The existing `runAgentDiscovery()` loop is used (probe-only, no working LLM)
- **When flag is on:** The agent-first path is used
- **In-flight discoveries during rollout:** Flag is checked at session start. A discovery that started with the old path continues on the old path. No mid-session switching.
- **Rollback:** Toggle the flag off. The legacy loop still exists until Phase 2 cleanup.

---

## 11. Open Questions (with Recommended Defaults)

1. **Default agent creation.** Should Bond auto-create a "deployment-agent" with the `appdeploy` skill on first use, or require the user to configure one?
   - **Recommendation: Auto-create.** The UX cost of requiring manual agent setup before first discovery is too high. Show a one-time toast: "Created a deployment agent using your default LLM provider." The agent is visible in the Agents tab for customization.

2. **Structured output enforcement.** Should we use `structured_output` (forcing JSON mode) or let the agent return freeform text and parse it?
   - **Recommendation: Freeform text with `` ```discovery-result``` `` markers.** This is already implemented in `parseDiscoveryResult()`. The agent can explain its reasoning in prose AND return structured data. If parsing fails, the raw text is available for debugging. Structured output mode can be revisited if parse failures become common.

3. **Probe results as context vs tools.** Option A (pre-gather) or Option B (agent tools)?
   - **Recommendation: Option A (pre-gather) for v1.** Already implemented. Probes run fast (~1-5s total), inject results into the agent's first message. The agent doesn't spend tool-call budget on probes. Revisit Option B if agents need to selectively probe (e.g., skipping SSH probes when no server is provided — though `runProbes()` already handles this via its `sshParams` parameter).

4. **Multi-repo discovery.** One agent turn per service, or all services in one turn?
   - **Recommendation: One turn per repo/service.** The agent's context window and structured output format are designed for single-service discovery. For monorepos, the UI should allow the user to select a subdirectory or the agent should identify services and suggest splitting. Multi-service-per-turn is a Phase 3+ optimization.

5. **Does `/api/v1/llm/complete` still need to exist?**
   - **Recommendation: Deprecate after Phase 2.** `deployment-planner.ts` is the other known consumer. Migrate it to an agent turn as well (separate design doc if needed), then remove the endpoint entirely.

---

## 12. Relationship to Design Doc 028

Doc 028 proposed: *"Make `chat_completion()` use `ApiKeyResolver` by building a standalone resolver."*

This doc proposes: *"Don't call `chat_completion()` for discovery at all — route through the agent."*

**These are complementary, not contradictory.** Doc 028 fixes the standalone endpoint for any remaining consumers. Doc 080 removes the largest consumer (discovery) from needing the standalone endpoint. Together, they mean:

- Discovery: routed through agent (Doc 080) — no standalone LLM call needed
- Other standalone callers (e.g., `deployment-planner.ts`): fixed by Doc 028 if they remain, or also migrated to agent turns

The recommended order is:
1. **Implement Doc 080 first** — it solves the immediate bug (discovery LLM calls fail) without touching `llm.py`
2. **Evaluate Doc 028 after** — if `deployment-planner.ts` and any other callers are also migrated to agent turns, Doc 028 becomes unnecessary. If standalone callers remain, implement 028 for them.
