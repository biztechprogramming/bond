# Design Doc 088: Agent Loop Robustness

**Status:** Proposed  
**Date:** 2026-04-01  
**Triggered by:** Production logs showing wasted iterations, type bugs, and zombie processes

---

## Problem

The agent loop has several compounding robustness issues visible in a single production trace:

1. **Agent ran `sleep 120`** — burned 2 minutes of wall-clock time and a full iteration doing nothing useful
2. **`timeout` parameter passed as string** — caused `'<=' not supported between instances of 'str' and 'int'` in `asyncio.wait_for`
3. **Zombie process on exception** — when `code_execute` hits the generic exception handler, `proc.communicate()` is never awaited and `proc.kill()` is never called
4. **Budget overrun** — agent reached iteration 13 against an adaptive budget of 8 (62.5% over budget) before hard restriction kicked in
5. **No guard against time-wasting commands** — `sleep`, infinite loops, and other no-op commands burn budget with zero value

These issues compound: a sleep wastes an iteration → budget escalation fires late → agent continues making calls with type errors → zombie processes accumulate.

---

## Changes

### 1. Type Coercion for `timeout` in `code_execute`

**File:** `backend/app/agent/tools/native.py` (~line 271)

The `timeout` parameter comes from LLM-generated JSON and can arrive as a string. The current code does:

```python
timeout = arguments.get("timeout", 30)
```

**Fix:** Coerce to int with a hard ceiling:

```python
MAX_CODE_EXEC_TIMEOUT = 60  # seconds — no single command should run longer

try:
    timeout = min(int(arguments.get("timeout", 30)), MAX_CODE_EXEC_TIMEOUT)
except (ValueError, TypeError):
    timeout = 30
```

**Why 60s?** Any shell command that legitimately needs >60s should be run asynchronously or delegated to a coding agent. The previous implicit allowance of 130s timeouts is excessive for inline code execution.

### 2. Kill Zombie Processes on Exception

**File:** `backend/app/agent/tools/native.py` (~line 305)

Current code:

```python
except Exception as e:
    return {"stdout": "", "stderr": str(e), "exit_code": -1}
```

The `proc` variable may already be assigned when the exception fires (e.g., during `wait_for`). The process is never cleaned up.

**Fix:**

```python
except asyncio.TimeoutError:
    try:
        proc.kill()
        await proc.communicate()  # reap the zombie
    except Exception:
        pass
    return {"stdout": "", "stderr": "Execution timed out", "exit_code": -1}
except Exception as e:
    # If proc was created before the exception, kill it
    try:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.communicate()
    except Exception:
        pass
    return {"stdout": "", "stderr": str(e), "exit_code": -1}
```

Also add `proc = None` before the `try` block so the variable is always defined.

### 3. Block/Penalize Time-Wasting Commands

**File:** `backend/app/agent/tools/native.py`

Add a pre-execution filter that detects and rejects obviously wasteful commands:

```python
import re

_BLOCKED_PATTERNS = [
    (re.compile(r'\bsleep\s+(\d+)'), lambda m: int(m.group(1)) > 5,
     "sleep commands >5s are not allowed in code_execute — use async workflows"),
]

def _check_blocked_command(code: str) -> str | None:
    """Return rejection reason if the command should be blocked, else None."""
    for pattern, condition, reason in _BLOCKED_PATTERNS:
        match = pattern.search(code)
        if match and condition(match):
            return reason
    return None
```

Call this before subprocess creation. Return an error result immediately instead of running the command.

**Why not just cap the timeout?** Because even a 30s sleep wastes 30s + an iteration. The agent should never intentionally idle.

### 4. Adaptive Budget Uses Configured max_iterations, Not Hardcoded Ceilings

**File:** `backend/app/agent/iteration_handlers.py` — `handle_adaptive_budget()`

**Problem:** The adaptive budget used hardcoded ceilings (2, 8, 10, 20, 25) that were `min()`-ed with `max_iterations`. If an agent's config sets `max_iterations=50`, the adaptive budget still capped at 25 for the most complex tasks. Budget escalation then used this artificially low `adaptive_budget` as the effective limit — so the agent got force-restricted at iteration ~20, nowhere near the configured 50.

**Fix (implemented):** Express adaptive budgets as fractions of the configured `max_iterations`:

| Task type | Old ceiling | New formula | Example (max=50) |
|-----------|-------------|-------------|-------------------|
| Simple Q&A | 2 | max(2, 8%) | 4 |
| File lookup | 8 | max(8, 30%) | 15 |
| Analysis | 10 | max(10, 40%) | 20 |
| Implementation | 20 | max(20, 80%) | 40 |
| Complex multi-file | 25 | 100% | 50 |

The `max()` with the old values ensures small configs (e.g., `max_iterations=10`) don't get absurdly tiny budgets.

### 5. Count Wall-Clock Time Against Budget

Currently budget is purely iteration-count based. An agent that runs `sleep 120` burns 2 minutes but only 1 iteration. 

**Proposal:** Track cumulative tool execution time. If total wall-clock time for tool execution exceeds a threshold (e.g., 5 minutes), inject a budget warning and count excess time as phantom iterations:

```python
# In the main loop, after tool execution:
loop.cumulative_tool_time += tool_elapsed
if loop.cumulative_tool_time > 300:  # 5 minutes
    phantom_iters = int(loop.cumulative_tool_time / 60)  # 1 phantom iter per minute
    effective_iteration = _iteration + phantom_iters
    # Use effective_iteration for budget checks
```

This is a softer alternative to blocking sleeps outright — it lets time-consuming legitimate commands run but penalizes the budget accordingly.

### 6. Validate Tool Arguments at Schema Level

The `timeout` type bug could have been caught earlier. Tool argument schemas should enforce types, and the worker should coerce/validate before passing to handlers:

```python
def validate_and_coerce_args(schema: dict, args: dict) -> dict:
    """Coerce argument types based on tool schema. 
    Handles common LLM mistakes like string-for-int."""
    properties = schema.get("properties", {})
    for key, value in args.items():
        if key in properties:
            expected_type = properties[key].get("type")
            if expected_type == "integer" and isinstance(value, str):
                try:
                    args[key] = int(value)
                except ValueError:
                    pass
            elif expected_type == "number" and isinstance(value, str):
                try:
                    args[key] = float(value)
                except ValueError:
                    pass
    return args
```

Add this as a pre-processing step in the tool dispatch path in `worker.py`, before calling any handler.

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | Type coercion for timeout | **Bug fix** — causes crashes | 5 min |
| 2 | Kill zombie processes | **Bug fix** — resource leak | 10 min |
| 3 | Block time-wasting commands | **Defense** — prevents waste | 15 min |
| 4 | Tighten budget thresholds | **Tuning** — reduces overruns | 15 min |
| 5 | Wall-clock time budget | **Enhancement** — smarter budgeting | 30 min |
| 6 | Schema-level arg validation | **Hardening** — prevents type bugs | 30 min |

Items 1-3 are low-risk, high-value fixes that should ship immediately. Items 4-6 need more testing to avoid breaking legitimate long-running tasks.

---

## Files Affected

- `backend/app/agent/tools/native.py` — items 1, 2, 3
- `backend/app/agent/iteration_handlers.py` — item 4
- `backend/app/worker.py` — items 5, 6

---

## Risks

- **Timeout cap too low:** Some legitimate commands (large git operations, builds) may need >60s. Mitigation: the coding_agent path doesn't go through `code_execute`, so delegated work is unaffected.
- **Sleep blocking too aggressive:** An agent might use short sleeps for legitimate polling. Mitigation: only block sleeps >5s.
- **Tighter budget thresholds:** May cause premature termination on complex tasks. Mitigation: the adaptive budget system already adjusts for task complexity; these changes only tighten the *overrun* allowance, not the budget itself.

---

## Not Addressed Here

- **Why the agent ran `sleep 120` in the first place** — this is a prompt/behavioral issue. The agent was likely waiting for an external process. The fix is to block the symptom (wasteful sleeps) and ensure the agent uses proper async patterns (coding_agent delegation) instead.
- **RuntimeWarning for unawaited coroutine** — this is a consequence of the zombie process issue (item 2) and will be resolved by that fix.
