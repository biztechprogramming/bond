# Design Doc 067: Adaptive Token Budgeting

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 012 (Context Distillation Pipeline), 029 (LLM Call Efficiency)

---

## 1. Problem

Bond's agent has no awareness of its own token consumption. It reads files, gathers context, and makes tool calls without any feedback on how much of its context window or cost budget it's using. The result:

- The agent reads 15 files "for context" when 3 would suffice
- Long tool outputs consume window space that forces earlier conversation history to be summarized (losing detail)
- The agent hits cost limits reactively (CostTracker alerts after the fact) rather than planning around them
- Pre-gathering (`pre_gather.py`) collects information without considering whether the agent has room for it

This is like giving someone a credit card with no balance display and expecting them to budget.

## 2. What Bond Gets

1. **Budget awareness in the system prompt** — the agent knows how many tokens it has left (context window) and how much budget remains (cost)
2. **Gathering budget hints** — `pre_gather.py` respects a token budget, gathering the most relevant information first and stopping when the budget is consumed
3. **Agent self-regulation** — the agent can make tradeoffs: "I've used 60% of my context window, I'll summarize what I have rather than reading more files"
4. **Proactive cost management** — replaces reactive CostTracker alerts with agent-visible budget that influences behavior naturally

## 3. Design

### Budget State

Every turn, the agent receives a compact budget summary in its system prompt (or as a tool-accessible state):

```
📊 Budget: 73% context remaining (58k of 80k tokens) | $0.42 of $1.00 session budget remaining
   This turn: ~12k tokens used (system: 8k, history: 22k, tools: 38k)
   Largest consumers: file_read(worker.py): 4.1k, file_read(loop.py): 3.8k, conversation history: 22k
```

This is not a tool call — it's injected into the context by the framework, similar to how conversation summaries work.

### Gathering Budget

Before pre-gathering, calculate a token budget for the gathering phase:

```python
def calculate_gathering_budget(
    context_window: int,
    system_prompt_tokens: int,
    history_tokens: int,
    reserved_for_response: int = 4096,
    gathering_cap: float = 0.3,  # max 30% of remaining window for gathering
) -> int:
    """Calculate how many tokens pre-gathering can consume."""
    available = context_window - system_prompt_tokens - history_tokens - reserved_for_response
    return int(available * gathering_cap)
```

Pre-gathering tasks are prioritized by expected relevance, and executed in order until the budget is consumed:

```python
def pre_gather_with_budget(tasks: list[GatherTask], budget: int) -> list[GatherResult]:
    """Execute gathering tasks in priority order, stopping at budget."""
    results = []
    remaining = budget
    
    for task in sorted(tasks, key=lambda t: t.priority, reverse=True):
        estimated_tokens = task.estimated_tokens
        if estimated_tokens > remaining:
            # Skip this task — not enough budget
            logger.info("Skipping gather task %s: needs ~%d tokens, only %d remaining",
                       task.name, estimated_tokens, remaining)
            continue
        
        result = execute_gather_task(task)
        actual_tokens = count_tokens(result.content)
        
        results.append(result)
        remaining -= actual_tokens
        
        if remaining <= 0:
            break
    
    return results
```

### Agent Decision Support

The agent can also query its budget explicitly via a lightweight built-in:

```
Agent: [calls get_budget()]
Response: {
  "context_window": 80000,
  "used": 22000,
  "available": 58000,
  "cost_remaining": 0.42,
  "cost_limit": 1.00,
  "largest_items": [
    {"type": "history", "tokens": 12000},
    {"type": "tool_result:file_read(worker.py)", "tokens": 4100},
    {"type": "system_prompt", "tokens": 5900}
  ]
}
```

This lets the agent reason about whether to read another file or work with what it has.

## 4. Implementation

### Phase 1: Budget Injection in System Prompt (~1 day)

**Files changed:** `backend/app/agent/context_builder.py`

After assembling the system prompt, conversation history, and any pre-gathered context, calculate and inject a budget summary:

```python
def build_budget_summary(
    context_window: int,
    system_tokens: int,
    history_tokens: int,
    tool_result_tokens: int,
    cost_spent: float,
    cost_limit: float,
) -> str:
    """Build a compact budget summary for the agent."""
    total_used = system_tokens + history_tokens + tool_result_tokens
    pct_remaining = (context_window - total_used) / context_window * 100
    
    summary = (
        f"📊 Budget: {pct_remaining:.0f}% context remaining "
        f"({context_window - total_used:,} of {context_window:,} tokens)"
    )
    
    if cost_limit > 0:
        cost_remaining = cost_limit - cost_spent
        summary += f" | ${cost_remaining:.2f} of ${cost_limit:.2f} session budget remaining"
    
    return summary
```

Inject at the end of the system prompt (after all fragments, before conversation history).

### Phase 2: Gathering Budget in pre_gather.py (~1 day)

**Files changed:** `backend/app/agent/pre_gather.py`

Add budget-aware gathering:

```python
class PreGatherConfig:
    gathering_budget_pct: float = 0.3  # max % of available context for gathering
    min_gathering_budget: int = 2000   # always allow at least this many tokens
    

def pre_gather(agent_config: dict, context: dict, budget: int) -> list[GatherResult]:
    """Pre-gather information for the agent, respecting token budget."""
    tasks = plan_gathering_tasks(agent_config, context)
    
    # Estimate token cost per task
    for task in tasks:
        task.estimated_tokens = estimate_task_tokens(task)
    
    # Execute in priority order with budget
    return pre_gather_with_budget(tasks, budget)
```

Token estimation per task type:

| Task Type | Estimation Method |
|-----------|------------------|
| File read | `file_size_bytes / 4` (rough chars-to-tokens) |
| Git status | Fixed estimate: 500 tokens |
| Grep results | `estimated_matches * avg_line_length / 4` |
| Web fetch | Fixed estimate: 3000 tokens (capped) |

Estimates don't need to be precise — they're budget guardrails, not accounting.

### Phase 3: Cost Tracker Integration (~0.5 days)

**Files changed:** `backend/app/agent/cost_tracker.py`

Replace passive alerts with budget data that feeds into the budget summary:

```python
class CostTracker:
    def get_budget_state(self) -> dict:
        """Return current cost state for budget summary."""
        return {
            "spent": self.tracking["total_cost"],
            "limit": self.cost_alert_threshold,
            "remaining": self.cost_alert_threshold - self.tracking["total_cost"],
            "per_turn_avg": self.tracking["total_cost"] / max(self.tracking["turns"], 1),
            "estimated_turns_remaining": self._estimate_remaining_turns(),
        }
    
    def _estimate_remaining_turns(self) -> int:
        """Estimate how many more turns the budget allows."""
        if self.tracking["turns"] == 0:
            return -1  # unknown
        avg_cost = self.tracking["total_cost"] / self.tracking["turns"]
        if avg_cost <= 0:
            return -1
        remaining = self.cost_alert_threshold - self.tracking["total_cost"]
        return int(remaining / avg_cost)
```

### Phase 4: Prompt Fragment for Budget-Aware Behavior (~0.5 days)

**New file:** `prompts/universal/budget-awareness.md`

```markdown
## Context Budget

You have a budget summary showing your remaining context window and cost budget.
Use it to make smart decisions:

- If context is >70% remaining: gather freely, read files as needed
- If context is 30-70% remaining: be selective about what you read; prefer targeted reads over full files
- If context is <30% remaining: work with what you have; summarize rather than gather more
- If cost budget is <20% remaining: prefer cheaper operations; avoid speculative tool calls
```

## 5. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BUDGET_INJECTION_ENABLED` | `true` | Show budget summary to agent |
| `GATHERING_BUDGET_PCT` | `0.3` | Max % of available context for pre-gathering |
| `GATHERING_MIN_BUDGET` | `2000` | Minimum gathering budget (tokens) |
| `BUDGET_COST_DISPLAY` | `true` | Include cost info in budget summary |

## 6. What This Intentionally Does NOT Do

- **Enforce hard limits on tool calls.** The agent can still read files that exceed its gathering budget — the budget is for *pre-gathering*, not for interactive tool use. The agent is trusted to make good decisions with budget information.
- **Replace CostTracker enforcement.** CostTracker's hard stop still applies. Budget awareness is about *planning*, not *policing*.
- **Optimize the agent's response length.** Response token limits are a separate concern (model `max_tokens` parameter).

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Agent becomes too conservative (under-gathers) | Medium | Budget awareness prompt encourages using budget, not hoarding it. Monitor gathering task completion rates. |
| Budget summary adds tokens to every turn | Low | ~50 tokens. Negligible. |
| Token estimates for gathering are inaccurate | Low | Estimates are guardrails. Over-estimating is safe (skips low-priority tasks). Under-estimating wastes some budget (agent still works, just with less headroom). |
| Agent ignores budget information | Low | That's fine — it behaves exactly like today. Budget awareness is additive. |

## 8. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| Context utilization | % of context window used at end of turn | More even distribution (less 95%+ overflow) |
| Gathering efficiency | Tokens gathered vs tokens actually referenced by agent | >60% of gathered content referenced |
| Cost predictability | Variance in per-session cost | -30% variance |
| Context overflow rate | How often history summarization triggers mid-session | -25% reduction |
| Agent quality | Eval suite | No regression |

## 9. Relationship to Prior Docs

- **Doc 012 (Context Distillation):** Budget awareness reduces pressure on context distillation by preventing over-gathering. Less content in → less to summarize.
- **Doc 029 (LLM Call Efficiency):** Direct implementation of token efficiency — the agent makes informed decisions about token allocation.
- **Doc 062 (Headroom):** Complementary. Headroom compresses what's already in context; budgeting prevents unnecessary content from entering context.
- **Doc 063 (CascadeFlow):** Budget awareness handles the *cost* side of the problem. Combined with step classification (doc 066), covers both cost and model routing without CascadeFlow's complexity.
