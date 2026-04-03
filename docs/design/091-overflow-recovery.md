# Design Doc 091: Overflow Recovery

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

When the LLM API returns a context overflow error (HTTP 413, `context_length_exceeded`, or provider-specific equivalents), Bond has no recovery strategy. The request fails and the user sees an error. Similarly, when the model's response is truncated because it hit `max_output_tokens`, Bond does not detect this or retry.

Claude Code handles this with a multi-tier recovery system:
1. **Reactive compaction** — compress history and retry
2. **Context collapse** — aggressively drop everything except system prompt + recent messages
3. **Microcompact** — lightweight compaction for minor overflows
4. **Max output token recovery** — detect truncated responses and auto-continue (up to 3 retries)

Bond needs equivalent recovery so that context overflow becomes a recoverable event, not a fatal error.

---

## Changes

### 1. Detect Overflow Errors

**File:** `backend/app/agent/llm.py`

Wrap the LLM API call to catch and classify overflow errors:

```python
class ContextOverflowError(Exception):
    """Raised when the LLM API rejects the request due to context length."""
    def __init__(self, message: str, tokens_sent: int = 0):
        super().__init__(message)
        self.tokens_sent = tokens_sent

class OutputTruncatedError(Exception):
    """Raised when the model's response was cut off by max_output_tokens."""
    pass

async def _raw_llm_call(messages, model, tools=None, **kwargs):
    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=tools, **kwargs
        )
        # Check for output truncation
        if hasattr(response, 'choices') and response.choices:
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                raise OutputTruncatedError(
                    f"Response truncated (finish_reason='length')"
                )
        return response
    except Exception as e:
        error_str = str(e).lower()
        if any(term in error_str for term in [
            "context_length_exceeded",
            "maximum context length",
            "request too large",
            "413",
            "token limit",
            "too many tokens",
        ]):
            raise ContextOverflowError(str(e)) from e
        raise
```

### 2. Implement 3-Tier Recovery Chain

**File:** `backend/app/agent/loop.py`

Add a recovery wrapper around the LLM call in `agent_turn()`:

```python
from backend.app.agent.llm import ContextOverflowError, OutputTruncatedError

MAX_OVERFLOW_RETRIES = 3
MAX_TRUNCATION_RETRIES = 3

async def _llm_call_with_recovery(
    messages: list[dict],
    model: str,
    tools: list | None,
    loop_state: LoopState,
    **kwargs,
) -> Any:
    """
    Call the LLM with automatic overflow recovery.
    
    Recovery tiers:
      1. Standard compaction — compress old messages to 70% of limit
      2. Aggressive compaction — drop all tool results older than 3 turns
      3. Emergency collapse — keep only system prompt + last 2 exchanges
    """
    last_error = None

    for attempt in range(MAX_OVERFLOW_RETRIES):
        try:
            return await llm_call(messages, model=model, tools=tools, **kwargs)
        except ContextOverflowError as e:
            last_error = e
            logger.warning(
                "overflow_recovery_attempt",
                attempt=attempt + 1,
                tier=["standard", "aggressive", "emergency"][attempt],
                error=str(e),
            )
            
            context_limit = get_context_limit(model)
            
            if attempt == 0:
                # Tier 1: Standard compaction
                messages = await _compress_history(
                    messages, target_tokens=int(context_limit * 0.6)
                )
            elif attempt == 1:
                # Tier 2: Aggressive — drop old tool results entirely
                messages = _aggressive_compact(messages, keep_recent_turns=3)
            else:
                # Tier 3: Emergency collapse
                messages = _emergency_collapse(messages)
            
            loop_state.record_compaction(
                tokens_before=_estimate_messages_tokens(messages),
                tokens_after=_estimate_messages_tokens(messages),
            )

    # All retries exhausted
    logger.error("overflow_recovery_exhausted", attempts=MAX_OVERFLOW_RETRIES)
    raise last_error


def _aggressive_compact(messages: list[dict], keep_recent_turns: int = 3) -> list[dict]:
    """
    Drop all tool call/result messages older than keep_recent_turns.
    Preserve system messages and recent conversation.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    
    # Keep only the last N pairs of messages
    recent = non_system[-(keep_recent_turns * 2):]
    
    return system_msgs + recent


def _emergency_collapse(messages: list[dict]) -> list[dict]:
    """
    Nuclear option: keep only the system prompt and the last user message + 
    last assistant message. Everything else is dropped.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    
    # Keep just the last exchange
    last_two = non_system[-2:] if len(non_system) >= 2 else non_system
    
    collapsed = system_msgs + last_two
    logger.warning(
        "emergency_collapse",
        original_messages=len(messages),
        collapsed_messages=len(collapsed),
    )
    return collapsed
```

### 3. Handle Output Truncation (Auto-Continue)

**File:** `backend/app/agent/loop.py`

When the model's response is cut off, automatically ask it to continue:

```python
async def _handle_truncation_retry(
    messages: list[dict],
    partial_response: Any,
    model: str,
    tools: list | None,
    **kwargs,
) -> Any:
    """
    When finish_reason='length', append the partial response and ask
    the model to continue. Retry up to MAX_TRUNCATION_RETRIES times.
    """
    accumulated_content = _extract_content(partial_response)
    
    for retry in range(MAX_TRUNCATION_RETRIES):
        logger.info("truncation_retry", attempt=retry + 1)
        
        # Append partial response and continuation prompt
        messages = messages + [
            {"role": "assistant", "content": accumulated_content},
            {"role": "user", "content": "Your response was cut off. Please continue exactly where you left off."},
        ]
        
        try:
            response = await llm_call(messages, model=model, tools=tools, **kwargs)
            new_content = _extract_content(response)
            accumulated_content += new_content
            
            # Check if this response was also truncated
            if response.choices[0].finish_reason != "length":
                # Merge accumulated content into final response
                response.choices[0].message.content = accumulated_content
                return response
        except ContextOverflowError:
            # Continuing made context too long — return what we have
            logger.warning("truncation_retry_overflow", accumulated_length=len(accumulated_content))
            break
    
    # Return partial response with what we accumulated
    partial_response.choices[0].message.content = accumulated_content
    return partial_response
```

### 4. Add Overflow Metrics

**File:** `backend/app/agent/loop_state.py`

Track overflow events for observability:

```python
@dataclass
class LoopState:
    # ... existing fields ...
    overflow_events: int = 0
    overflow_recoveries: int = 0
    truncation_retries: int = 0
    recovery_tiers_used: list[str] = field(default_factory=list)
    
    def record_overflow(self, tier: str, recovered: bool):
        self.overflow_events += 1
        self.recovery_tiers_used.append(tier)
        if recovered:
            self.overflow_recoveries += 1
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | Detect overflow errors | **Foundation** — needed by all recovery | 20 min |
| 2 | 3-tier recovery chain | **Critical** — turns fatal errors into recoverable | 45 min |
| 3 | Output truncation handling | **High** — prevents incomplete responses | 30 min |
| 4 | Overflow metrics | **Observability** — track frequency | 15 min |

---

## Files Affected

- `backend/app/agent/llm.py` — error detection, exception types
- `backend/app/agent/loop.py` — recovery chain, truncation handling
- `backend/app/agent/loop_state.py` — overflow metrics
- `backend/app/agent/context_pipeline.py` — `_compress_history()` called by recovery

---

## Risks

- **Infinite retry loops:** If compaction doesn't reduce tokens enough, we could retry forever. Mitigation: hard limit of 3 attempts with increasingly aggressive strategies.
- **Context quality after emergency collapse:** Dropping almost everything means the model loses track of the task. Mitigation: doc 097 (Conversation Summarization) addresses preserving a summary when collapsing.
- **Truncation retry cost:** Each continuation retry costs another API call. Mitigation: limit to 3 retries and return partial results if we can't complete.
- **Provider-specific error formats:** Different LLM providers return overflow errors differently. Mitigation: the detection function checks multiple patterns and can be extended.

---

## Not Addressed Here

- **Proactive prevention of overflow** — see doc 090 (Token-Aware Context Management)
- **Preserving context quality during compaction** — see doc 097 (Conversation Summarization)
- **Rate limiting and backoff** — see doc 095 (Transient Error Retry)
