# Design Doc 063: CascadeFlow Model Cascading Integration

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 025 (RouteLLM Classifier-Based Routing), 049 (Closed-Loop Optimization)  
**Reference:** [lemony-ai/cascadeflow](https://github.com/lemony-ai/cascadeflow) — `pip install cascadeflow`

---

## 1. Problem

Bond uses a single model per agent turn. The model is configured statically per agent (or overridden per session). Every iteration of the agent loop — whether it's a trivial "list files" tool call or a complex multi-step reasoning chain — uses the same model at the same cost.

Prior design docs explored routing:

| Doc | Approach | Status | Why stalled |
|-----|----------|--------|-------------|
| 024 (WilmerAI) | External proxy routing | Draft | Added infra complexity, HTTP boundary only |
| 025 (RouteLLM) | Classifier-based routing | Draft | Requires training data collection pipeline first |

Both operate at the **request boundary** — they decide the model *before* execution and can't react to quality mid-flight.

CascadeFlow operates **inside the execution loop**: speculative execution with quality validation. Try fast/cheap first, validate, escalate only if quality is insufficient. Research shows 60–70% of agent steps don't need flagship models.

## 2. What Bond Gets

1. **40–85% cost reduction** on LLM calls without quality regression — cheap models handle routine steps, expensive models handle hard ones.
2. **In-process, per-step decisions** — not per-request. Each iteration of the agent loop can use a different model based on task complexity.
3. **Quality validation** — automatic escalation when cheap model output fails confidence/completeness checks.
4. **Domain-aware routing** — code tasks → code-optimized models, math → math-tuned, general → general.
5. **Budget enforcement** — per-session cost caps with `stop`/`deny_tool`/`switch_model` actions. Replaces Bond's basic `CostTracker` alerts with active enforcement.
6. **Self-improving routing** — CascadeFlow learns which models work best for which tasks over time. Complements `optimizer.py`'s A/B framework.

## 3. Integration Architecture

```
                    ┌──────────────────────────────┐
                    │         loop.py              │
                    │     (agent main loop)         │
                    │                              │
                    │  for each iteration:          │
                    │    ┌────────────────────┐     │
                    │    │  CascadeFlow       │     │
                    │    │  Harness            │     │
                    │    │                    │     │
                    │    │  1. Classify step  │     │
                    │    │  2. Select model   │     │
                    │    │  3. Execute (cheap) │     │
                    │    │  4. Validate quality│     │
                    │    │  5. Escalate?       │     │
                    │    └────────┬───────────┘     │
                    │             │                  │
                    │    ┌────────▼───────────┐     │
                    │    │    llm.py          │     │
                    │    │  (LiteLLM call)    │     │
                    │    └────────────────────┘     │
                    └──────────────────────────────┘
```

### Current Flow (simplified)
```
loop iteration → llm.chat(model=agent.model) → LiteLLM → provider
```

### New Flow
```
loop iteration → cascadeflow.harness.execute(messages, tools) →
  try cheap model → validate quality →
    if pass: return result
    if fail: escalate to strong model → return result
```

## 4. Implementation Plan

### Phase 1: Cascade Wrapper in llm.py

**Files changed:** `backend/app/agent/llm.py`

Wrap the existing `chat()` / `acompletion()` calls with CascadeFlow's cascade logic:

```python
from cascadeflow import CascadeAgent, CascadeConfig

def build_cascade_config(agent_config: dict) -> CascadeConfig:
    """Map Bond agent config to CascadeFlow config."""
    return CascadeConfig(
        models=[
            # Tier 1: Fast/cheap (handles ~65% of steps)
            {"provider": "groq", "model": "llama-3.3-70b", "tier": "draft"},
            # Tier 2: Strong (handles remaining ~35%)  
            {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "tier": "verify"},
        ],
        quality_thresholds={
            "min_confidence": 0.7,
            "min_completeness": 0.8,
        },
        budget={
            "max_cost_per_session": agent_config.get("cost_limit", 1.0),
            "actions": {"over_budget": "stop"},
        },
    )
```

**Key constraint:** CascadeFlow must respect Bond's existing provider config (`providers.yaml`) and API key resolution (`api_key_resolver.py`). We need an adapter layer.

### Phase 2: Domain-Aware Model Tiers

**Files changed:** `backend/app/agent/llm.py`, new file `backend/app/agent/cascade_config.py`

Define domain-specific model cascades:

| Domain | Draft Model | Verify Model | Rationale |
|--------|------------|--------------|-----------|
| CODE | `deepseek-coder-v2` or `qwen-coder` | `claude-sonnet` | Code-tuned SLMs excel at routine code tasks |
| MATH | `qwen-math` | `claude-sonnet` | Math-tuned models handle formulas well |
| GENERAL | `llama-3.3-70b` | `claude-sonnet` | Good general performance at low cost |
| COMPLEX_REASONING | Skip draft, go direct | `claude-opus` | Some tasks shouldn't waste time on draft |

CascadeFlow's domain classifier handles routing. Bond provides the model mapping.

### Phase 3: Integration with CostTracker

**Files changed:** `backend/app/agent/cost_tracker.py`

Replace passive cost alerts with CascadeFlow's active enforcement:

```python
# Current: alert when threshold exceeded (passive)
if self.tracking["total_cost"] > self.cost_alert_threshold:
    logger.warning("Cost alert!")

# New: CascadeFlow enforces budget (active)
# - switch_model: downgrade to cheaper model
# - deny_tool: block expensive tool calls
# - stop: halt the agent loop
```

CostTracker becomes a read-only reporter; CascadeFlow owns enforcement.

### Phase 4: Learning Loop Integration with Optimizer

**Files changed:** `backend/app/agent/optimizer.py`

Feed CascadeFlow's per-step decision traces into the optimizer's observation pipeline:

- Which model was selected for each step
- Whether escalation was needed
- Quality scores per step
- Cost per step

This data enables `optimizer.py` to tune cascade thresholds over time (quality_thresholds, domain routing weights).

### Phase 5: Per-Agent Cascade Configuration (UI)

**Files changed:** Frontend settings, SpacetimeDB schema

Allow users to configure per-agent:
- Enable/disable cascading
- Set model tiers (draft/verify per domain)
- Set budget limits and enforcement actions
- View cascade analytics (% handled by draft, escalation rate, cost savings)

## 5. Configuration

New environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CASCADE_ENABLED` | `false` | Master switch (off by default — opt-in) |
| `CASCADE_DRAFT_MODEL` | `groq/llama-3.3-70b` | Default draft tier model |
| `CASCADE_VERIFY_MODEL` | (agent's configured model) | Default verify tier model |
| `CASCADE_QUALITY_THRESHOLD` | `0.7` | Minimum confidence to accept draft |
| `CASCADE_BUDGET_ACTION` | `switch_model` | What to do when budget exceeded |

Per-agent overrides stored in SpacetimeDB `agents` table (new columns or JSON config field).

## 6. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Draft model produces subtly wrong tool calls | High | CascadeFlow validates tool call format + Bond's tool executor validates args; escalation catches misses |
| Latency increase from speculative execution + validation | Medium | Draft models are 2–10x faster; even with validation overhead, net latency is usually lower. Measure p95. |
| Quality regression on complex tasks | High | Start with `CASCADE_ENABLED=false`, opt-in per agent. Always escalate for complex reasoning (skip draft). |
| Provider API key management complexity | Medium | Adapter layer maps CascadeFlow's provider config to Bond's `api_key_resolver.py` + `providers.yaml` |
| CascadeFlow upstream instability | Medium | Pin version, wrap behind interface in `llm.py`, feature flag for instant rollback |
| Interaction with Headroom (Doc 062) | Low | Headroom compresses *input*; CascadeFlow routes *output generation*. Complementary, not conflicting. Order: compress first, then cascade. |

## 7. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Avg cost per agent session | Baseline TBD | -50% |
| Avg latency per agent turn | Baseline TBD | -30% (faster draft models) |
| Quality (eval suite) | Baseline | No regression (±2%) |
| % steps handled by draft model | 0% (no cascading) | >60% |
| Escalation rate | N/A | <40% |

## 8. Dependency

```
pip install cascadeflow
```

Requires at least two LLM providers configured (one cheap, one strong). Bond already supports 17+ via LiteLLM, so this is a config change, not infra.

## 9. Relationship to Prior Design Docs

- **Doc 024 (WilmerAI):** Superseded. CascadeFlow does what WilmerAI does (model routing) but in-process, not via external proxy.
- **Doc 025 (RouteLLM):** Complementary. RouteLLM's trained classifiers could replace CascadeFlow's rule-based domain classifier for better routing accuracy. Phase 5 opportunity.
- **Doc 049 (Optimizer):** CascadeFlow feeds data *into* the optimizer. The optimizer tunes CascadeFlow's thresholds. Symbiotic.
