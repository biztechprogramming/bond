# Design Doc 090: Token-Aware Context Management

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

Bond does not proactively count tokens before making LLM API calls. While `context_pipeline.py` has `_estimate_tokens()` and `_estimate_messages_tokens()`, these are used reactively during compression — not as a pre-call gate. This means context can silently exceed the model's window, causing:

1. **API rejections** — the provider returns a 413 or context-length error, and Bond has no recovery path (see doc 091)
2. **Truncated responses** — the model runs out of output tokens because the input consumed most of the window
3. **Silent quality degradation** — as context approaches the limit, model attention degrades and responses become less coherent
4. **Wasted API spend** — sending oversized context that gets rejected still costs latency and sometimes partial billing

Claude Code solves this with a `BudgetTracker` that monitors cumulative token usage and proactively triggers compaction before hitting limits. Bond needs the same pattern.

---

## Changes

### 1. Define Model-Specific Context Window Limits

**File:** `backend/app/agent/llm.py`

Add a configuration mapping of model names to their context windows and a safe threshold:

```python
# Model context window limits (input tokens)
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-haiku-20240307": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "deepseek-chat": 64_000,
}

# Trigger proactive compaction at this percentage of the context window
COMPACTION_THRESHOLD = 0.80  # 80%

# Hard ceiling — never send more than this percentage
HARD_CEILING = 0.95  # 95%

def get_context_limit(model: str) -> int:
    """Return the context window size for a model, defaulting conservatively."""
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key in model:
            return limit
    return 64_000  # conservative default for unknown models
```

### 2. Add Pre-Call Token Budget Check

**File:** `backend/app/agent/loop.py` (in `agent_turn()`, before the LLM API call)

Before every call to the LLM, count the tokens in the assembled messages and act:

```python
from backend.app.agent.context_pipeline import _estimate_messages_tokens
from backend.app.agent.llm import get_context_limit, COMPACTION_THRESHOLD, HARD_CEILING

async def _check_token_budget(messages: list[dict], model: str, db) -> list[dict]:
    """
    Check token usage against model limits. Compact if needed.
    Returns the (possibly compacted) message list.
    """
    token_count = _estimate_messages_tokens(messages)
    context_limit = get_context_limit(model)
    usage_ratio = token_count / context_limit

    logger.info(
        "token_budget_check",
        token_count=token_count,
        context_limit=context_limit,
        usage_ratio=f"{usage_ratio:.1%}",
        model=model,
    )

    if usage_ratio >= HARD_CEILING:
        # Emergency: aggressively compact
        logger.warning(
            "token_budget_hard_ceiling",
            token_count=token_count,
            limit=context_limit,
        )
        messages = await _compress_history(
            messages, target_tokens=int(context_limit * 0.6), aggressive=True
        )
    elif usage_ratio >= COMPACTION_THRESHOLD:
        # Proactive: standard compaction
        logger.info(
            "token_budget_proactive_compaction",
            token_count=token_count,
            limit=context_limit,
        )
        messages = await _compress_history(
            messages, target_tokens=int(context_limit * 0.7)
        )

    return messages
```

### 3. Integrate into the Agent Loop

**File:** `backend/app/agent/loop.py` (inside `agent_turn()`)

Add the budget check right before the LLM call:

```python
# Before calling the LLM
messages = await _check_token_budget(messages, model, db)

# Now make the API call
response = await llm_call(messages, model=model, tools=tools, ...)
```

### 4. Add Token Count Logging to Every API Call

**File:** `backend/app/agent/llm.py`

Wrap the API call to log input/output token counts:

```python
async def llm_call(messages, model, tools=None, **kwargs):
    input_tokens = _estimate_messages_tokens(messages)
    
    response = await _raw_llm_call(messages, model, tools, **kwargs)
    
    output_tokens = getattr(response.usage, 'output_tokens', 0) if hasattr(response, 'usage') else 0
    
    logger.info(
        "llm_call_complete",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    
    return response
```

### 5. Track Cumulative Compacted Tokens

**File:** `backend/app/agent/loop_state.py`

Add tracking for how many tokens have been compacted away during this conversation turn:

```python
@dataclass
class LoopState:
    # ... existing fields ...
    tokens_compacted: int = 0
    compaction_events: int = 0
    peak_token_count: int = 0
    
    def record_compaction(self, tokens_before: int, tokens_after: int):
        self.tokens_compacted += (tokens_before - tokens_after)
        self.compaction_events += 1
    
    def record_token_count(self, count: int):
        self.peak_token_count = max(self.peak_token_count, count)
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | Model context limits config | **Foundation** — needed by all other changes | 15 min |
| 2 | Pre-call token budget check | **Critical** — prevents overflow | 30 min |
| 3 | Integration into agent loop | **Critical** — activates the check | 15 min |
| 4 | Token count logging | **Observability** — needed for debugging | 15 min |
| 5 | Cumulative compaction tracking | **Enhancement** — informs strategy | 20 min |

---

## Files Affected

- `backend/app/agent/llm.py` — model limits config, token logging
- `backend/app/agent/loop.py` — pre-call budget check integration
- `backend/app/agent/loop_state.py` — compaction tracking fields
- `backend/app/agent/context_pipeline.py` — `_compress_history()` may need an `aggressive` mode parameter

---

## Risks

- **Token estimation inaccuracy:** `_estimate_tokens()` uses a heuristic (chars/4). If it underestimates by >20%, we could still overflow. Mitigation: use `tiktoken` for OpenAI models and Anthropic's token counting API for Claude, falling back to the heuristic.
- **Compaction latency:** Running `_compress_history()` synchronously before every API call adds latency. Mitigation: only trigger when above threshold; most calls will skip the check quickly.
- **Over-aggressive compaction:** If the threshold is too low, we'll compact too often and lose useful context. Mitigation: start at 80% and tune based on production metrics.

---

## Not Addressed Here

- **What to do when compaction fails to bring tokens under the limit** — see doc 091 (Overflow Recovery)
- **Summarization quality during compaction** — see doc 097 (Conversation Summarization on Compaction)
- **Per-tool token budgets** — limiting how much output a single tool result can consume (future work)
