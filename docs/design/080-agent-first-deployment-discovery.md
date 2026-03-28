# Design Doc 080: Agent-First Deployment Discovery

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-28  
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
UI → "Start Discovery" → Gateway → discovery-agent.ts → llm-discovery.ts → BackendClient.llmComplete() → /api/v1/llm/complete → chat_completion() [BROKEN PATH]
```

We route through the existing, working agent infrastructure:

```
UI → Select Agent → Select/Add Repo → "Discover" → Gateway → Agent Turn → agent loop → ApiKeyResolver → LiteLLM [WORKING PATH]
```

The discovery becomes a **skill invocation on an existing agent**, not a standalone LLM call. The agent already has:
- Working key resolution (`ApiKeyResolver`)
- Model normalization
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
   c. Calls runLLMDiscovery() → BackendClient.llmComplete() → FAILS
   d. Merges results
5. UI shows progressive discovery results
```

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

The `POST /deployments/discover` endpoint changes from spawning `runAgentDiscovery()` to sending a message to an existing agent:

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

await sendAgentMessage(agentId, {
  content: buildDiscoveryPrompt(repoId, resourceId),
  skill: "appdeploy",               // Routes to deployment skill context
  structured_output: DISCOVERY_SCHEMA, // Agent returns structured JSON
});
```

#### Backend: No changes to `llm.py` or `chat_completion()`

The entire broken `/api/v1/llm/complete` path is **no longer used for discovery**. It can remain for other uses or be deprecated separately. Zero changes to the backend LLM code.

#### Backend: Agent skill — `appdeploy`

The existing `skills/appdeploy/SKILL.md` already describes deployment analysis. The agent's system prompt, augmented by the skill context, tells it to:

1. Read project files (using existing `file_read` / `shell` tools)
2. Analyze the project type, framework, build strategy
3. If a target server resource is provided, SSH to probe it
4. Return a structured JSON object matching `DiscoveryState.findings`

The agent **is** the LLM call. There's no separate `llm-discovery.ts` → `BackendClient.llmComplete()` hop.

#### Gateway: `discovery-agent.ts` — Simplified to probe orchestrator

The file probes (`detect_framework`, `detect_ports`, `detect_services`, etc.) are still valuable as **pre-gathering** — they run fast, don't need an LLM, and provide structured signals. But instead of being orchestrated by `discovery-agent.ts` alongside a broken LLM call, they become:

**Option A: Pre-gather probes injected into agent context**
- Run probes before the agent turn
- Inject results into the agent's context as a system message: "Here are preliminary findings from automated probes: ..."
- Agent uses these to confirm/override/augment with its own analysis

**Option B: Agent tools**
- Expose `detect_framework`, `detect_ports`, etc. as agent tools
- Agent decides which probes to run based on what it sees in the repo
- More flexible but uses more agent turns

**Recommendation: Option A** for v1. It's simpler, faster (probes run in parallel before the agent turn), and the agent gets the benefit of probe data without spending tool-call budget on it.

#### Frontend: New selection step

The Discovery UI (072) adds a pre-step before the discovery stream:

```
┌─────────────────────────────────────┐
│  Deploy a Project                   │
│                                     │
│  1. Select Agent                    │
│     [▾ deployment-agent        ]    │
│                                     │
│  2. Select Repository               │
│     [▾ my-app (github.com/...)  ]   │
│     [ + Add new repo ]              │
│                                     │
│  3. Target Server (optional)        │
│     [▾ prod-server-1            ]   │
│                                     │
│  [ Start Discovery ]                │
└─────────────────────────────────────┘
```

After clicking "Start Discovery", the existing discovery progress UI from 072 renders, but fed by the agent's SSE stream instead of the custom discovery SSE.

---

## 5. Trade-off Analysis: Better or Worse?

### What Gets Better

| Aspect | Why |
|---|---|
| **LLM calls just work** | Zero new code for key resolution, OAuth, model normalization. The agent path is battle-tested. This is the entire motivation. |
| **Single code path** | Every LLM call in the system goes through `agent loop → ApiKeyResolver → LiteLLM`. No second path to maintain. |
| **Cost tracking for free** | Agent turns are already tracked by `cost_tracker.py` and Langfuse. Discovery LLM costs become visible automatically. |
| **Tool reuse** | The agent can read files, run shell commands, SSH to servers — all using existing, tested tools. No need for `llm-discovery.ts` to re-implement file reading. |
| **Context management** | The agent's context pipeline handles token budgets, truncation, and priority. The standalone `chat_completion()` had none of this. |
| **Iterative reasoning** | If the agent needs multiple turns to analyze a complex project (monorepo, multi-service), it can. The standalone LLM call was one-shot. |
| **Skill ecosystem** | The `appdeploy` skill is already defined. Other skills (monitoring, rollback) can follow the same pattern. |
| **Eliminates Doc 028 entirely** | No need to build `create_standalone_resolver()`. The problem it solves doesn't exist if we don't use the standalone path. |

### What Gets Worse

| Aspect | Why | Mitigation |
|---|---|---|
| **Extra UX step** | User must select an agent before discovering. Current flow just needs a resource. | Default to a "deployment" agent if only one exists. Auto-create one on first use. |
| **Slower startup** | Agent turn has overhead: context building, skill loading, pre-gather. The standalone LLM call was a single HTTP request. | Pre-run file probes in parallel while agent context loads. Net latency similar. |
| **Agent must exist** | Requires a configured agent with working LLM credentials. Current flow attempted to use system-level credentials. | Those system-level credentials were broken anyway. This makes the requirement explicit rather than silently failing. |
| **Heavier for simple projects** | A simple static site doesn't need a full agent turn. | The agent will still return quickly for simple projects. The overhead is ~2-3 seconds of context building. |
| **SSE stream format change** | Frontend currently expects custom discovery events. Agent turns emit different SSE events. | Map agent SSE events to the existing discovery event format in the Gateway. Adapter layer, not a rewrite. |
| **Agent turn cost** | Each discovery costs an LLM call against the user's token budget / OAuth quota. | Discovery was always going to cost an LLM call. Now it's just tracked properly. |

### Net Assessment: **Better**

The "worse" column is entirely about minor UX friction and startup latency. The "better" column eliminates an entire class of bugs (dual LLM paths), removes the need for Design Doc 028, and gets cost tracking, tracing, and iterative reasoning for free.

The fundamental insight is: **discovery IS an agent task**. Treating it as a standalone LLM call was an optimization that created a maintenance burden and a broken code path. Routing it through the agent is the architecturally correct solution.

---

## 6. What Happens to Existing Code

| File | Fate |
|---|---|
| `gateway/src/deployments/llm-discovery.ts` | **Deleted.** The agent replaces this entirely. |
| `gateway/src/deployments/discovery-agent.ts` | **Simplified.** Keep probe orchestration (`detect_framework`, `detect_ports`, etc.) as a pre-gather step. Remove `runLLMDiscovery()` call and the completeness loop. The agent handles completeness. |
| `gateway/src/backend/client.ts` — `llmComplete()` | **Unused by discovery.** Can be deprecated or kept for other uses. |
| `backend/app/api/v1/llm.py` — `chat_completion()` | **Unchanged.** Not used by discovery anymore. Can be fixed separately (or not). |
| `backend/app/agent/api_key_resolver.py` | **Unchanged.** Already works. |
| `skills/appdeploy/SKILL.md` | **Enhanced.** Add structured output schema for discovery findings. |
| `gateway/src/deployments/router.ts` | **Modified.** Discovery endpoint sends agent message instead of spawning custom pipeline. |
| `frontend/` Discovery UI components | **Modified.** Add agent/repo selection step. Adapt SSE event mapping. |
| **Design Doc 028** | **Superseded.** No longer needed if discovery doesn't use the standalone LLM path. |

---

## 7. Implementation Plan

### Phase 1: Agent-Routed Discovery (eliminates the bug)
1. Add agent/repo selection to the discovery UI
2. Modify `POST /deployments/discover` to send an agent message instead of spawning `runAgentDiscovery()`
3. Enhance `appdeploy` skill with structured discovery output schema
4. Map agent SSE events to discovery UI events (adapter in Gateway)
5. Keep file probes as pre-gather context injected into the agent turn

### Phase 2: Cleanup
1. Delete `llm-discovery.ts`
2. Simplify `discovery-agent.ts` to just the probe runner (no LLM, no completeness loop)
3. Mark `BackendClient.llmComplete()` as deprecated
4. Update Design Doc 028 status to "Superseded by 080"

### Phase 3: Polish
1. Auto-create a default "deployment" agent on first use
2. Remember last-used agent/repo per resource for one-click re-discovery
3. Allow the agent to ask clarifying questions mid-discovery (leveraging the existing agent Q&A UX)

---

## 8. Open Questions

1. **Default agent creation.** Should Bond auto-create a "deployment-agent" with the `appdeploy` skill on first use, or require the user to configure one? Auto-creation is smoother UX but hides what's happening.

2. **Structured output enforcement.** The agent needs to return a specific JSON schema for discovery findings. Should we use `structured_output` (forcing JSON mode) or let the agent return freeform text and parse it? Structured output is more reliable but limits the agent's ability to explain its reasoning.

3. **Probe results as context vs tools.** Option A (pre-gather) is recommended, but Option B (agent tools) allows the agent to be smarter about which probes to run. Worth revisiting after v1.

4. **Multi-repo discovery.** If the user wants to discover deployment config for a monorepo with multiple services, should each service be a separate agent turn, or should the agent handle all services in one turn?

5. **Does `/api/v1/llm/complete` still need to exist?** If discovery was the primary consumer, and other callers (like `deployment-planner.ts`) also switch to agent turns, the endpoint could be removed entirely. This would eliminate the dual-path problem at the root.

---

## 9. Relationship to Design Doc 028

Doc 028 proposed: *"Make `chat_completion()` use `ApiKeyResolver` by building a standalone resolver."*

This doc proposes: *"Don't call `chat_completion()` for discovery at all — route through the agent."*

**These are complementary, not contradictory.** Doc 028 fixes the standalone endpoint for any remaining consumers. Doc 080 removes the largest consumer (discovery) from needing the standalone endpoint. Together, they mean:

- Discovery: routed through agent (Doc 080) — no standalone LLM call needed
- Other standalone callers (e.g., `deployment-planner.ts`): fixed by Doc 028 if they remain, or also migrated to agent turns

The recommended order is:
1. **Implement Doc 080 first** — it solves the immediate bug (discovery LLM calls fail) without touching `llm.py`
2. **Evaluate Doc 028 after** — if `deployment-planner.ts` and any other callers are also migrated to agent turns, Doc 028 becomes unnecessary. If standalone callers remain, implement 028 for them.
