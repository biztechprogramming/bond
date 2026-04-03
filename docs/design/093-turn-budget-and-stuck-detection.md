# Design Doc 093: Turn Budget & Stuck Detection

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

Bond's agent loop has no programmatic enforcement of turn limits or detection of repetitive tool calls. When the model gets confused or encounters a persistent failure, it can:

1. **Loop indefinitely** — making dozens of turns without progress, burning tokens and wall-clock time
2. **Repeat the same failing call** — calling the same tool with the same arguments 3, 5, 10+ times hoping for a different result
3. **Spiral into irrelevant exploration** — expanding scope endlessly when it can't solve the original problem

The `AGENTS.md` system prompt contains instructions like "Don't retry the same tool hoping for different results" and "Stop exploring when you have enough information to act," but these are advisory — the model doesn't always follow them, especially under pressure (low context headroom, confusing error messages).

Claude Code enforces these constraints programmatically:
- A `BudgetTracker` that monitors cumulative token usage and can stop the loop
- Command queuing with priority-based execution limits
- Stop hooks that check conditions before each turn

Bond needs the same — **prompt instructions are necessary but not sufficient; the harness must enforce the guardrails.**

---

## Changes

### 1. Define a LoopGuard Class

**File:** `backend/app/agent/loop_guard.py` (new file)

```python
from dataclasses import dataclass, field
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class LoopGuard:
    """
    Programmatic guardrails for the agent loop.
    
    Enforces turn budgets and detects stuck patterns to prevent
    infinite loops and wasted compute.
    """
    max_turns: int = 25
    warn_at_percent: float = 0.80  # inject warning at 80% of budget
    max_consecutive_repeats: int = 2  # same call N times → intervene
    
    current_turn: int = 0
    _recent_call_hashes: list[str] = field(default_factory=list)
    _warned: bool = False
    _force_stopped: bool = False
    _stop_reason: str = ""
    
    def record_turn(self) -> None:
        """Increment the turn counter."""
        self.current_turn += 1
    
    def record_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Record a tool call for stuck detection."""
        call_hash = self._hash_call(tool_name, arguments)
        self._recent_call_hashes.append(call_hash)
        # Keep only the last N+1 hashes for comparison
        max_history = self.max_consecutive_repeats + 1
        if len(self._recent_call_hashes) > max_history:
            self._recent_call_hashes = self._recent_call_hashes[-max_history:]
    
    def should_warn(self) -> bool:
        """Check if we should inject a 'wrap up' warning."""
        if self._warned:
            return False
        threshold = int(self.max_turns * self.warn_at_percent)
        if self.current_turn >= threshold:
            self._warned = True
            return True
        return False
    
    def is_stuck(self) -> bool:
        """
        Detect if the model is repeating the same tool call.
        
        Returns True if the last N calls all have the same hash.
        """
        if len(self._recent_call_hashes) < self.max_consecutive_repeats:
            return False
        recent = self._recent_call_hashes[-self.max_consecutive_repeats:]
        return len(set(recent)) == 1
    
    def is_budget_exhausted(self) -> bool:
        """Check if the turn budget is fully spent."""
        return self.current_turn >= self.max_turns
    
    def force_stop(self, reason: str) -> None:
        """Force the loop to stop."""
        self._force_stopped = True
        self._stop_reason = reason
        logger.warning("loop_force_stop", reason=reason, turn=self.current_turn)
    
    @property
    def should_stop(self) -> bool:
        """Check all stop conditions."""
        return self._force_stopped or self.is_budget_exhausted()
    
    @property
    def stop_reason(self) -> str:
        if self._force_stopped:
            return self._stop_reason
        if self.is_budget_exhausted():
            return f"Turn budget exhausted ({self.max_turns} turns)"
        return ""
    
    @property
    def remaining_turns(self) -> int:
        return max(0, self.max_turns - self.current_turn)
    
    def get_warning_message(self) -> str:
        """Message to inject when approaching budget limit."""
        return (
            f"⚠️ You have used {self.current_turn} of {self.max_turns} turns. "
            f"Only {self.remaining_turns} turns remain. "
            f"Please wrap up your current task: commit your changes, report results, "
            f"and save any remaining work to memory. If you cannot finish, summarize "
            f"what's done and what remains."
        )
    
    def get_stuck_message(self) -> str:
        """Message to inject when stuck pattern is detected."""
        return (
            "⚠️ You appear to be repeating the same tool call. This usually means "
            "the approach isn't working. Please try a different approach:\n"
            "- If a file wasn't found, check the path or search for it\n"
            "- If a command failed, read the error and adjust\n"
            "- If you're blocked, report what's blocking you to the user\n"
            "- If the task is too complex, delegate to coding_agent\n\n"
            "Do NOT retry the same call again."
        )
    
    def get_budget_exhausted_message(self) -> str:
        """Final message when budget is fully spent."""
        return (
            f"🛑 Turn budget exhausted ({self.max_turns} turns). "
            f"Stopping the agent loop. Please save your progress to memory "
            f"and report what was completed and what remains to the user."
        )
    
    @staticmethod
    def _hash_call(tool_name: str, arguments: dict) -> str:
        """Create a deterministic hash of a tool call for comparison."""
        canonical = json.dumps(
            {"tool": tool_name, "args": arguments},
            sort_keys=True,
            default=str,
        )
        return hashlib.md5(canonical.encode()).hexdigest()
```

### 2. Integrate LoopGuard into the Agent Loop

**File:** `backend/app/agent/loop.py` (modify the main agent loop)

```python
from backend.app.agent.loop_guard import LoopGuard

async def agent_loop(messages: list, config: AgentConfig, **kwargs) -> list:
    """Main agent loop with turn budget and stuck detection."""
    
    guard = LoopGuard(
        max_turns=config.max_turns or 25,
        warn_at_percent=0.80,
        max_consecutive_repeats=2,
    )
    
    while not guard.should_stop:
        guard.record_turn()
        
        # Check if we should warn the model to wrap up
        if guard.should_warn():
            messages.append({
                "role": "system",
                "content": guard.get_warning_message(),
            })
            logger.info("loop_budget_warning", turn=guard.current_turn, max=guard.max_turns)
        
        # Get model response
        response = await get_completion(messages, config)
        messages.append(response.message)
        
        # If no tool calls, the model is done
        if not response.tool_calls:
            break
        
        # Process each tool call
        for tool_call in response.tool_calls:
            guard.record_tool_call(tool_call.name, tool_call.arguments)
            
            # Check for stuck pattern BEFORE executing
            if guard.is_stuck():
                logger.warning(
                    "stuck_pattern_detected",
                    tool=tool_call.name,
                    turn=guard.current_turn,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": guard.get_stuck_message(),
                })
                # Clear the stuck history so the model gets one more chance
                guard._recent_call_hashes.clear()
                continue  # Skip execution, let the model try again
            
            # Execute the tool
            result = await execute_tool_call(tool_call.name, tool_call.arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result.to_message_content(),
            })
    
    # If budget exhausted, inject final message
    if guard.is_budget_exhausted():
        messages.append({
            "role": "system",
            "content": guard.get_budget_exhausted_message(),
        })
        logger.warning("loop_budget_exhausted", turns_used=guard.current_turn)
    
    return messages
```

### 3. Add Turn Budget to Agent Configuration

**File:** `backend/app/agent/config.py` (or wherever AgentConfig lives)

```python
@dataclass
class AgentConfig:
    # ... existing fields ...
    
    # Turn budget — maximum number of LLM round-trips per request
    max_turns: int = 25
    
    # Stuck detection — consecutive identical calls before intervention
    max_consecutive_repeats: int = 2
    
    # Budget warning — inject warning at this percentage of budget
    budget_warn_percent: float = 0.80
```

### 4. Expose Metrics for Observability

**File:** `backend/app/agent/loop_state.py`

Add loop guard metrics to the existing loop state so they're available for logging and the UI:

```python
@dataclass
class LoopMetrics:
    """Metrics from a completed agent loop run."""
    turns_used: int = 0
    max_turns: int = 25
    stuck_interventions: int = 0
    budget_warning_issued: bool = False
    force_stopped: bool = False
    stop_reason: str = ""
    
    @staticmethod
    def from_guard(guard: "LoopGuard") -> "LoopMetrics":
        return LoopMetrics(
            turns_used=guard.current_turn,
            max_turns=guard.max_turns,
            stuck_interventions=0,  # TODO: track in guard
            budget_warning_issued=guard._warned,
            force_stopped=guard._force_stopped,
            stop_reason=guard.stop_reason,
        )
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | LoopGuard class | **Foundation** — needed by everything else | 30 min |
| 2 | Agent loop integration | **Critical** — this is the actual enforcement | 45 min |
| 3 | AgentConfig extension | **Required** — makes it configurable | 10 min |
| 4 | Loop metrics | **Observability** — understand loop behavior | 15 min |

---

## Files Affected

- `backend/app/agent/loop_guard.py` — new file, LoopGuard class
- `backend/app/agent/loop.py` — integrate guard into the main loop
- `backend/app/agent/config.py` — add turn budget configuration
- `backend/app/agent/loop_state.py` — add LoopMetrics

---

## Risks

- **Max turns too low:** Complex tasks (multi-file refactors, large investigations) may legitimately need 30+ turns. Mitigation: make it configurable per request; coding_agent sub-tasks get their own budget.
- **Stuck detection false positives:** The model may legitimately retry a tool (e.g., re-reading a file after editing it). Mitigation: only trigger after N consecutive *identical* calls (same name AND same arguments). A re-read with different line ranges won't match.
- **Warning message confuses the model:** Injecting system messages mid-conversation can sometimes confuse models. Mitigation: use clear, imperative language; test with different models.
- **Hash collisions in call dedup:** MD5 is used for convenience, not security. Collision probability is negligible for this use case.

---

## Not Addressed Here

- **Token-based budgets** — this doc covers turn-based budgets only. Token budgets are in doc 090 (Token-Aware Context Management).
- **Cost-based budgets** — stopping based on dollar spend. See existing doc 081 (Cost Tracking and Budget Controls).
- **Graceful task handoff on budget exhaustion** — when the loop stops, how to hand off remaining work. See doc 096 (Progress Checkpointing).
