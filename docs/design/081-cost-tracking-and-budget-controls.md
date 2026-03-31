# Design Doc 081: Cost Tracking & Budget Controls

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-29  
**Depends on:** 070 (Runaway Loop Circuit Breakers), 064 (Prompt Fragment Cost Accounting)  
**Inspired by:** Paperclip's per-agent budget enforcement and spend visibility

---

## 1. Problem Statement

Bond currently has basic cost alerting in `cost_tracker.py` — it logs warnings when a single turn exceeds $0.25 and Design Doc 070 added a hard dollar cap per turn. However, there is **no persistent, cross-conversation visibility** into how much Bond is costing the user. Users cannot:

- See total spend per conversation, per day, per week, or per agent.
- Set a monthly or weekly budget ceiling that pauses all agent work when exceeded.
- Compare cost across different models or tasks to make informed routing decisions.
- Get proactive alerts ("You've used 80% of your weekly budget").

Paperclip solves this with per-agent budget tracking, hard limits that auto-pause agents, and a dashboard showing spend over time. Bond needs similar capabilities to give users confidence in running agents autonomously.

---

## 2. Goals

1. **Persistent cost ledger** — Every LLM call's token counts and dollar cost are written to SpacetimeDB, attributed to a conversation, agent, and model.
2. **Budget controls** — Users can set spending limits at multiple scopes (per-turn, per-conversation, daily, weekly, monthly) with configurable actions (warn, pause, kill).
3. **Cost dashboard** — The frontend displays spend breakdowns by time period, agent, model, and conversation.
4. **Proactive alerts** — Notifications when approaching budget thresholds (50%, 80%, 100%).
5. **Cost-aware routing** — Expose cost data to the model routing layer so cheaper models can be preferred when budget is tight.

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = llm_cost_event, public)]
pub struct LlmCostEvent {
    #[primary_key]
    pub id: String,
    pub conversation_id: String,
    pub agent_id: String,
    pub model: String,
    pub provider: String,
    pub input_tokens: u32,
    pub output_tokens: u32,
    pub cost_usd: f64,
    pub turn_index: u32,
    pub created_at: Timestamp,
}

#[table(name = budget_config, public)]
pub struct BudgetConfig {
    #[primary_key]
    pub id: String,
    pub scope: String,          // "global" | "agent:{id}" | "conversation:{id}"
    pub period: String,         // "turn" | "conversation" | "daily" | "weekly" | "monthly"
    pub limit_usd: f64,
    pub action: String,         // "warn" | "pause" | "kill"
    pub enabled: bool,
    pub created_at: Timestamp,
    pub updated_at: Timestamp,
}

#[table(name = budget_alert, public)]
pub struct BudgetAlert {
    #[primary_key]
    pub id: String,
    pub budget_config_id: String,
    pub threshold_pct: u8,      // 50, 80, 100
    pub current_spend_usd: f64,
    pub limit_usd: f64,
    pub acknowledged: bool,
    pub created_at: Timestamp,
}
```

### 3.2 Reducers

- `record_cost_event {id, conversationId, agentId, model, provider, inputTokens, outputTokens, costUsd, turnIndex}` — Called by the worker after every LLM call.
- `set_budget {id, scope, period, limitUsd, action, enabled}` — Create or update a budget rule.
- `acknowledge_alert {id}` — User dismisses a budget alert.

---

## 4. Architecture

### 4.1 Cost Recording (Worker Side)

After each `litellm.acompletion()` call, the existing `CostTracker` already computes token counts and cost. We add a single call to persist this:

```python
# In cost_tracker.py, after computing cost:
await spacetimedb_client.call_reducer("record_cost_event", [
    str(uuid4()), conversation_id, agent_id,
    model, provider, input_tokens, output_tokens,
    cost_usd, turn_index
])
```

### 4.2 Budget Enforcement (Worker Side)

Before each LLM call, the worker queries the accumulated spend for all applicable budget scopes:

```python
async def check_budgets(conversation_id: str, agent_id: str) -> BudgetDecision:
    """Returns ALLOW, WARN, or BLOCK with reason."""
    configs = await get_active_budgets(conversation_id, agent_id)
    for config in configs:
        current_spend = await get_spend_for_period(config.scope, config.period)
        pct = current_spend / config.limit_usd
        if pct >= 1.0 and config.action in ("pause", "kill"):
            return BudgetDecision.BLOCK, f"Budget exceeded: ${current_spend:.2f}/${config.limit_usd:.2f}"
        if pct >= 0.8:
            emit_alert(config, pct, current_spend)
    return BudgetDecision.ALLOW, None
```

### 4.3 Frontend Dashboard

A new "Costs" section in the sidebar showing:

- **Summary cards**: Today's spend, this week, this month, all-time.
- **Chart**: Daily spend over the last 30 days, stacked by model.
- **Table**: Per-conversation cost breakdown with drill-down to individual turns.
- **Budget manager**: CRUD interface for budget rules with scope/period/limit/action.
- **Alert feed**: Active budget alerts with acknowledge buttons.

---

## 5. Interaction with Existing Systems

| System | Integration |
|--------|------------|
| `CostTracker` (070) | Extended to persist events, not just track in-memory |
| Circuit breakers (070) | Per-turn hard cap remains as the fastest kill switch; budget controls add slower, broader limits |
| Cost accounting (064) | Fragment-level cost attribution feeds into the same ledger |
| Model routing (025) | Cost data exposed as a signal for route selection |

---

## 6. Migration Path

1. **Phase 1**: Add `LlmCostEvent` table + `record_cost_event` reducer. Worker starts persisting costs. No budget enforcement yet.
2. **Phase 2**: Add `BudgetConfig` + `BudgetAlert` tables. Worker checks budgets before LLM calls. Default global monthly budget of $50 (warn only).
3. **Phase 3**: Frontend dashboard with charts and budget management UI.

---

## 7. Open Questions

- Should budget enforcement happen in the Gateway (central) or Worker (distributed)? Gateway is simpler but adds latency; Worker is faster but harder to keep consistent.
- How do we handle cost for tool calls that invoke external APIs (web search, browser agent)? Those have their own costs not captured by LiteLLM.
- Should there be an "emergency override" for the user to bypass a budget block for a single turn?
