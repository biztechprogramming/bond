# Bond Submodule / Integration Evaluation

Evaluated against Bond's current stack: Python/FastAPI backend, LiteLLM for LLM calls, custom memory system (SQLite + sqlite-vec), custom tool framework, browser tool stubbed, no observability.

---

## Structured Output from LLMs

### Instructor
**What it does:** Wraps LLM calls to guarantee Pydantic model responses with automatic retries/validation.

| | |
|---|---|
| **Fit** | ⭐⭐⭐⭐ High — Bond already uses Pydantic and LiteLLM. Instructor plugs directly into litellm.completion() |
| **Effort** | Low — `pip install instructor`, wrap existing LLM calls |
| **Benefit** | Eliminates manual JSON parsing/validation in tool calls, entity extraction, structured agent outputs. Reduces retry logic you'd write yourself |
| **Drawback** | Extra dependency for something you could hand-roll with Pydantic + retry. Adds latency on retries (but less than crashing on bad output) |
| **Overlap** | None — Bond currently does free-form text parsing |
| **Verdict** | **Yes, add it.** Small, focused, high ROI. Not a submodule — just a pip dependency. |

### Outlines
**What it does:** Token-level constrained generation for local/open-source models.

| | |
|---|---|
| **Fit** | ⭐⭐ Low-Medium — Bond uses hosted APIs (Anthropic, OpenAI, etc.) via LiteLLM, not local models |
| **Benefit** | Guaranteed valid output with zero retries when running local models |
| **Drawback** | Only works with local models (vLLM, transformers). Useless for API-based providers. Heavy dependency (torch) |
| **Overlap** | Competes with Instructor for the same problem, different approach |
| **Verdict** | **Skip for now.** Only relevant if Bond adds local model support. Revisit then. |

---

## Tool Integration

### MCP (Model Context Protocol)
**What it does:** Standard protocol for exposing tools to LLM agents. Hundreds of pre-built servers for Postgres, GitHub, Slack, filesystem, etc.

| | |
|---|---|
| **Fit** | ⭐⭐⭐⭐⭐ Excellent — Bond has a custom tool framework. MCP compatibility would let it use the entire MCP ecosystem without writing custom tool code |
| **Effort** | Medium — Need an MCP client in the agent loop that discovers and calls MCP servers. Bond's tool definitions system needs to dynamically register MCP tools |
| **Benefit** | Instant access to hundreds of integrations. Future-proof — MCP is becoming the standard. Users can install MCP servers without modifying Bond code |
| **Drawback** | Protocol overhead for simple tools. MCP servers are separate processes to manage. Quality varies across community servers |
| **Overlap** | Bond's existing tools (search, files, memory, email) would coexist — MCP adds new ones |
| **Verdict** | **Yes, high priority.** This is the single highest-leverage integration. Don't submodule it — add an MCP client library (e.g., `mcp` Python package) and wire it into the tool registry. |

### Composio
**What it does:** 500+ pre-built, auth-managed integrations as callable tools. Handles OAuth, token refresh.

| | |
|---|---|
| **Fit** | ⭐⭐⭐ Medium — solves the "talk to SaaS" problem Bond doesn't address yet |
| **Effort** | Low — SDK install + API key |
| **Benefit** | OAuth/auth management is genuinely painful to build. Composio handles it. Fast path to Slack/Jira/Gmail/etc. |
| **Drawback** | **Hosted service** — not local-first (contradicts Bond's philosophy). API key dependency. Cost. Vendor lock-in. Overlaps heavily with MCP if you adopt MCP |
| **Overlap** | Direct overlap with MCP. MCP is open/self-hosted, Composio is managed/hosted |
| **Verdict** | **Skip if you adopt MCP.** MCP gives you the same integrations without the hosted dependency. Composio is the "I don't want to manage anything" option — fine for prototyping, wrong for Bond's local-first ethos. |

---

## Memory / State

### Mem0
**What it does:** Persistent memory layer with add/search/get_all API. Remembers users and context across sessions.

| | |
|---|---|
| **Fit** | ⭐⭐ Low — Bond already has a full memory system: MemoryRepository with versioning, soft-delete, hybrid search (sqlite-vec + FTS), entity graph, knowledge store |
| **Benefit** | Simpler API. Cross-agent memory sharing out of the box |
| **Drawback** | **Bond's memory system is more sophisticated than Mem0.** You'd be downgrading. Mem0 uses its own storage — you'd lose SQLite locality and the entity graph |
| **Overlap** | Near-total overlap with existing features/memory/ |
| **Verdict** | **Skip.** Bond's memory is already ahead of what Mem0 offers. Would be a regression. |

### Letta (MemGPT)
**What it does:** Tiered memory management (core/archival/recall) for agents managing large knowledge over time.

| | |
|---|---|
| **Fit** | ⭐⭐ Low — Letta is a full agent framework, not a library. It wants to own the agent loop |
| **Benefit** | Sophisticated memory tiering. Good ideas about what to keep in context vs. archive |
| **Drawback** | **Framework, not a library.** Integrating it means either replacing Bond's agent loop or running Letta as a separate service. Massive architectural mismatch |
| **Overlap** | Overlaps with Bond's memory, context pipeline, and agent loop |
| **Verdict** | **Skip.** Steal the tiered memory ideas (Bond's context_decay.py and context_pipeline.py are already heading this direction), but don't integrate the framework. |

---

## Observability / Tracing

### Langfuse
**What it does:** Open-source tracing, cost tracking, latency breakdowns, eval scores for LLM apps. Self-hostable.

| | |
|---|---|
| **Fit** | ⭐⭐⭐⭐⭐ Excellent — Bond has zero observability today. This is a blind spot |
| **Effort** | Low-Medium — Langfuse has a LiteLLM integration (callback). Add `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` and set `litellm.success_callback = ["langfuse"]`. That's it for basic tracing |
| **Benefit** | See every LLM call: cost, latency, token usage, traces. Debug agent loops. Track spend. Eval quality over time. Self-hostable = local-first compatible |
| **Drawback** | Another service to run (Postgres + Langfuse server) if self-hosting. Or use their cloud (free tier exists). Adds ~2ms per call for the callback |
| **Overlap** | None — Bond has nothing here |
| **Verdict** | **Yes, add it.** The LiteLLM integration makes this nearly free to wire up. Start with Langfuse cloud, self-host later. You're flying blind without observability. |

### Braintrust
**What it does:** LLM eval framework with tracing. Stronger on systematic evaluation.

| | |
|---|---|
| **Fit** | ⭐⭐⭐ Medium — good for "is the agent improving" but Bond isn't at the eval stage yet |
| **Benefit** | Rigorous eval framework. Dataset management. A/B testing prompts |
| **Drawback** | Hosted service (not self-hostable). Overkill before you have basic tracing. More relevant for teams tuning production agents |
| **Overlap** | Partial overlap with Langfuse |
| **Verdict** | **Skip for now.** Get Langfuse first for visibility. Add evals later when you have data to evaluate. |

---

## The LLM Call Itself

### LiteLLM
**What it does:** Unified API across 100+ LLM providers with fallbacks, load balancing, spend tracking.

| | |
|---|---|
| **Fit** | ✅ Already integrated — `litellm>=1.40.0` is in pyproject.toml |
| **Verdict** | **Already have it.** No action needed. |

---

## Browser Automation

### Playwright MCP
**What it does:** MCP server wrapping Playwright. Agent gets browser actions as tools: navigate, click, extract, screenshot.

| | |
|---|---|
| **Fit** | ⭐⭐⭐⭐⭐ Excellent — Bond has `browser.py` stubbed as "coming in Phase 2." This IS Phase 2 |
| **Effort** | Low if you adopt MCP (it's just another MCP server). Medium if standalone |
| **Benefit** | Full browser automation without writing the integration yourself. Navigate, fill forms, extract content, take screenshots. Works inside OpenSandbox containers |
| **Drawback** | Needs a browser runtime (Chromium). Heavy in containers. The MCP approach means you need MCP client support first |
| **Overlap** | Replaces the browser.py stub entirely |
| **Verdict** | **Yes, after MCP client is added.** This becomes trivial once Bond speaks MCP. Perfect match for the browser tool stub. |

### Browserbase
**What it does:** Hosted headless browser infrastructure. API-based browser sessions.

| | |
|---|---|
| **Fit** | ⭐⭐ Low — hosted service, not local-first |
| **Benefit** | No Chrome management. Clean API. Scales without local resources |
| **Drawback** | Hosted dependency. Cost per session. Not local-first. Latency for remote browser |
| **Overlap** | Competes with Playwright MCP |
| **Verdict** | **Skip.** Playwright MCP in an OpenSandbox container is the local-first answer. Browserbase is for SaaS products at scale. |

---

## Priority Ranking

| Priority | Integration | Type | Why |
|---|---|---|---|
| 🥇 1 | **MCP Client** | pip dependency | Unlocks entire tool ecosystem. Enables Playwright, database, and hundreds of other integrations |
| 🥈 2 | **Langfuse** | pip dependency + config | Zero observability today = flying blind. LiteLLM callback makes it nearly free |
| 🥉 3 | **Instructor** | pip dependency | Structured outputs with zero parsing code. Small, focused, works with existing LiteLLM |
| 4 | **Playwright MCP** | MCP server | Browser automation, but needs MCP client first |
| — | Skip | Outlines, Composio, Mem0, Letta, Braintrust, Browserbase | Either overlap with existing features, contradict local-first philosophy, or aren't needed yet |

**Note:** None of these should be git submodules. They're all pip/npm dependencies or MCP servers. Submodules are for when you need to vendor and modify source code (like OpenSandbox). These are stable libraries you consume as-is.
