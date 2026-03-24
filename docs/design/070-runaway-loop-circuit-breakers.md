# Design Doc 070: Runaway Loop Circuit Breakers

**Status:** Draft  
**Date:** 2026-03-24  
**Author:** Agent (requested by Andrew)  
**Problem:** An agent loop can rack up unbounded AI spend when existing safeguards fail to terminate it.

---

## 1. Problem Statement

Bond already has several loop-control mechanisms:

| Mechanism | Location | What it does | Why it's not enough |
|---|---|---|---|
| `max_iterations` | `worker.py` L914 | Hard iteration cap (default 25) | Only counts iterations, not cost. A single iteration with massive context can cost $1+. |
| Adaptive budget | `iteration_handlers.py` | Lowers effective iteration count for simple tasks | Still advisory — the loop keeps running. Budget escalation nudges but doesn't kill. |
| Loop detection | `iteration_handlers.py` `detect_loop()` | Detects repeated/cyclical tool calls | Only injects a SYSTEM message asking the agent to stop. The agent can ignore it. |
| Empty result detection | `iteration_handlers.py` `detect_empty_result()` | Detects consecutive empty/failed results | Same — injects a message, doesn't force-stop. |
| Budget escalation | `iteration_handlers.py` `handle_budget_escalation()` | Forces tool set to `respond+say` at threshold+2 | Good, but only triggers at 80% of budget. If budget is 25, that's iteration 20. |
| Cost alerting | `cost_tracker.py` | Logs a warning when cost > $0.25 or iterations > 20 | **Alert only. Never stops the loop.** |
| Batching nudge | `iteration_handlers.py` | Tells agent to batch calls | Advisory. Reduces waste but doesn't prevent runaway. |

**The core gap:** There is no mechanism that unconditionally kills the loop based on dollar spend. Every existing safeguard is either iteration-based or advisory (injects a message the agent can ignore). A loop where the agent keeps making tool calls that return content — but never converges on a response — will run until `max_iterations` is exhausted, potentially spending $10+ on a single turn.

**Scenario that triggered this:** Normal usage was fine, but an error condition caused the agent to enter a retry loop. Each iteration re-sent growing context to an expensive model, and the loop ran to exhaustion.

---

## 2. Proposed Safeguards

### 2.1 Hard Dollar Cap (Per-Turn Kill Switch)

**The most important change.** Add a dollar ceiling that unconditionally breaks the loop.

```python
# In CostTracker.__init__:
_raw_hard_cap = os.environ.get("LLM_TURN_COST_HARD_CAP")
self.hard_cap = float(_raw_hard_cap) if _raw_hard_cap else 2.00  # default $2

# In worker.py, after cost.track_primary_call():
if cost.tracking["total_cost"] >= cost.hard_cap:
    logger.critical(
        "CIRCUIT BREAKER: Turn cost $%.2f exceeded hard cap $%.2f — killing loop",
        cost.tracking["total_cost"], cost.hard_cap,
    )
    # Inject a final response to the user
    messages.append({
        "role": "assistant",
        "content": (
            f"⚠️ This turn was automatically stopped because it exceeded the "
            f"cost safety limit (${cost.hard_cap:.2f}). "
            f"The agent used {cost.tracking['iterations_used']} iterations "
            f"and ${cost.tracking['total_cost']:.2f} before being stopped. "
            f"Please review what happened and retry with a simpler request if needed."
        ),
    })
    break  # Exit the for loop unconditionally
```

**Configuration:**
- `LLM_TURN_COST_HARD_CAP` — env var, default `2.00`
- Also settable per-agent in the agents table (new column `cost_hard_cap`)
- Agent-level setting overrides env var (allows cheap agents to have lower caps)

### 2.2 Daily Spend Ceiling

Prevent a bad day from becoming a bad month.

```python
# New: DailySpendTracker (simple file or DB-backed)
class DailySpendTracker:
    """Tracks cumulative spend across all turns for the current UTC day."""
    
    DAILY_CAP_DEFAULT = 25.00  # env: LLM_DAILY_COST_CAP
    
    def record(self, cost: float) -> None: ...
    def total_today(self) -> float: ...
    def would_exceed(self, estimated_cost: float) -> bool: ...
```

**Behavior:**
- Before starting a turn, check `daily_tracker.total_today()` against `LLM_DAILY_COST_CAP`
- If already exceeded: return immediately with a message like "Daily AI spend limit reached ($X/$Y). Resets at midnight UTC."
- After each turn, record the turn's total cost
- Storage: append-only file at `~/.bond/spend/{date}.jsonl` (one line per turn)

### 2.3 Mandatory Loop-Break on Detection

Currently, `detect_loop()` and `detect_empty_result()` return messages that get injected but can be ignored. Add a **strike counter** that force-breaks on the second intervention.

```python
# In LoopState:
LOOP_MAX_INTERVENTIONS: int = 2  # Already exists!

# But it's never checked. Add this after loop detection fires:
loop_state.loop_intervention_count += 1
if loop_state.loop_intervention_count >= loop_state.LOOP_MAX_INTERVENTIONS:
    logger.critical(
        "CIRCUIT BREAKER: %d loop interventions ignored — force-breaking",
        loop_state.loop_intervention_count,
    )
    # Force tool set to respond-only AND set a flag to break next iteration
    loop_state.force_break_next = True
```

Note: `LOOP_MAX_INTERVENTIONS = 2` already exists in `loop_state.py` (L38) but is **never read** anywhere in the codebase. This is a bug — the field was added but the check was never wired up.

### 2.4 Error-Retry Circuit Breaker

The specific failure mode that triggered this: an error caused the agent to retry the same operation. Add explicit error-retry tracking.

```python
# In LoopState, add:
consecutive_error_iterations: int = 0
ERROR_ITERATION_THRESHOLD: int = 3

# After each iteration, if ALL tool calls in that iteration returned errors:
if all_tools_errored:
    loop_state.consecutive_error_iterations += 1
    if loop_state.consecutive_error_iterations >= loop_state.ERROR_ITERATION_THRESHOLD:
        logger.critical(
            "CIRCUIT BREAKER: %d consecutive iterations where all tools errored",
            loop_state.consecutive_error_iterations,
        )
        # Force-break and tell the user
        break
else:
    loop_state.consecutive_error_iterations = 0
```

### 2.5 Context Growth Rate-Limiter

The snowball problem from the cost analysis doc: each iteration re-sends everything. If context is growing faster than it should, intervene early.

```python
# Track context size per iteration
if input_tokens > 0:
    loop_state.context_sizes.append(input_tokens)
    
    if len(loop_state.context_sizes) >= 3:
        # If context doubled in the last 3 iterations, warn
        growth = loop_state.context_sizes[-1] / loop_state.context_sizes[-3]
        if growth > 2.0 and input_tokens > 50_000:
            messages.append({
                "role": "user",
                "content": (
                    "SYSTEM: Your context has grown rapidly (2x in 3 iterations, "
                    f"now at ~{input_tokens:,} tokens). Finish your current approach "
                    "and respond. Do not read more files."
                ),
            })
```

---

## 3. Implementation Plan

### Phase 1: Hard Caps (Critical — do first)

| Task | File | Effort |
|---|---|---|
| Add `hard_cap` to `CostTracker` | `cost_tracker.py` | S |
| Check hard cap after `track_primary_call()` in loop | `worker.py` | S |
| Add `LLM_TURN_COST_HARD_CAP` env var | `.env.example`, docs | S |
| Wire up `LOOP_MAX_INTERVENTIONS` (already exists, unused) | `worker.py` | S |
| Add error-retry circuit breaker | `loop_state.py`, `worker.py` | S |

### Phase 2: Daily Ceiling

| Task | File | Effort |
|---|---|---|
| Create `DailySpendTracker` class | `backend/app/agent/daily_spend.py` | M |
| Check daily cap before starting turn | `worker.py` | S |
| Record turn cost after loop | `worker.py` | S |
| Add `LLM_DAILY_COST_CAP` env var | `.env.example`, docs | S |
| Add API endpoint to check/reset daily spend | `backend/app/api/v1/` | M |

### Phase 3: Observability & UI

| Task | File | Effort |
|---|---|---|
| Add per-agent `cost_hard_cap` column | SpacetimeDB schema | M |
| Expose cost settings in agent config UI | Frontend | M |
| Add real-time cost display during long turns | Frontend (SSE) | M |
| Context growth rate-limiter | `worker.py` | S |
| Dashboard widget: daily/weekly spend chart | Frontend | L |

---

## 4. Configuration Summary

| Env Var | Default | Description |
|---|---|---|
| `LLM_TURN_COST_HARD_CAP` | `2.00` | Max dollar spend per agent turn before force-kill |
| `LLM_DAILY_COST_CAP` | `25.00` | Max dollar spend per UTC day across all turns |
| `LLM_COST_ALERT_THRESHOLD` | `0.25` | Existing — log warning when exceeded (no action) |
| `LLM_ITERATION_ALERT_THRESHOLD` | `20` | Existing — log warning when exceeded (no action) |

---

## 5. What This Doesn't Cover

- **Sub-agent cost tracking:** When the agent spawns Claude Code or Codex, those costs are incurred by the sub-agent process, not tracked by Bond's `CostTracker`. Sub-agent spend needs its own circuit breaker (future work — the coding agent skill could enforce a `--max-cost` flag if the provider supports it).
- **Streaming cost estimation:** We can only compute cost after the LLM call completes. Mid-call cancellation would require the provider to support it (some do via SSE abort).
- **Multi-agent coordination:** If multiple agents are running concurrently, daily caps should be shared. The `DailySpendTracker` handles this naturally if it's a single shared file/DB.

---

## 6. Open Questions

1. **Should the hard cap be per-turn or per-conversation?** Per-turn is simpler and prevents the immediate problem. Per-conversation would also catch "death by a thousand turns" but is harder to get right (conversations can span days).
2. **Should circuit breakers notify the user proactively?** (e.g., send a Telegram/Signal message when a cap is hit, not just log it.) Probably yes — if the user isn't watching the web UI, they won't see the loop was killed.
3. **Should there be a "pause and ask" mode** instead of hard-kill? e.g., at 80% of the cap, pause the loop and send the user a message asking "Continue? You've spent $X so far." This is more graceful but requires async user interaction mid-loop.
