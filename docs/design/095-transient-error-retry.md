# Design Doc 095: Transient Error Retry with Exponential Backoff

**Status:** Proposed  
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

Bond doesn't have systematic retry logic for transient API errors. When the LLM API returns a temporary failure:

1. **429 (Rate Limited)** — the request is rejected but would succeed after a short wait. Bond currently treats this as a hard failure and the agent turn ends.
2. **500 / 503 (Server Error)** — the API had a transient issue. A retry after a few seconds would likely succeed.
3. **413 / Context Overflow** — the request is too large. This needs a different recovery strategy (context compaction, not retry). Bond currently doesn't distinguish this from other errors.
4. **Network timeouts** — transient connectivity issues that resolve on retry.

Claude Code has sophisticated retry logic with:
- Exponential backoff with jitter to avoid thundering herd
- Different strategies for different error types (retry vs. compact vs. fail)
- Up to 3 retries for max output token errors (where the model was cut off)
- Logging of every retry attempt for observability

A single transient error should not kill an entire agent session. The harness should retry transparently.

---

## Changes

### 1. Define a RetryPolicy

**File:** `backend/app/agent/retry.py` (new file)

```python
import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Any

logger = logging.getLogger(__name__)


# Error categories
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
CONTEXT_OVERFLOW_CODES = {413}
NON_RETRYABLE_CODES = {400, 401, 403, 404}


@dataclass
class RetryPolicy:
    """Configuration for retry behavior."""
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_factor: float = 0.5  # random jitter up to 50% of delay
    retryable_status_codes: set[int] = field(default_factory=lambda: RETRYABLE_STATUS_CODES.copy())
    
    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay with exponential backoff and jitter.
        
        attempt 0 → base_delay * 1   (e.g., 1s)
        attempt 1 → base_delay * 2   (e.g., 2s)
        attempt 2 → base_delay * 4   (e.g., 4s)
        Plus random jitter to avoid thundering herd.
        """
        delay = min(
            self.base_delay_seconds * (2 ** attempt),
            self.max_delay_seconds,
        )
        jitter = delay * self.jitter_factor * random.random()
        return delay + jitter


@dataclass
class RetryResult:
    """Result of a retried operation."""
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    attempts: int = 0
    total_delay_seconds: float = 0.0
    final_status_code: Optional[int] = None
    recovery_action: Optional[str] = None  # "compacted_context", "gave_up", etc.


class APIError(Exception):
    """Wrapper for API errors with status code."""
    def __init__(self, message: str, status_code: int, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def classify_error(error: Exception) -> tuple[str, int | None]:
    """
    Classify an error into a category for retry decisions.
    
    Returns: (category, status_code)
    Categories: "retryable", "context_overflow", "non_retryable", "network", "unknown"
    """
    if isinstance(error, APIError):
        code = error.status_code
        if code in RETRYABLE_STATUS_CODES:
            return ("retryable", code)
        elif code in CONTEXT_OVERFLOW_CODES:
            return ("context_overflow", code)
        elif code in NON_RETRYABLE_CODES:
            return ("non_retryable", code)
        else:
            return ("unknown", code)
    
    if isinstance(error, asyncio.TimeoutError):
        return ("network", None)
    
    if isinstance(error, (ConnectionError, OSError)):
        return ("network", None)
    
    return ("unknown", None)


async def retry_with_backoff(
    operation: Callable,
    policy: RetryPolicy,
    on_context_overflow: Optional[Callable] = None,
    operation_name: str = "api_call",
) -> RetryResult:
    """
    Execute an async operation with retry logic.
    
    Args:
        operation: Async callable to execute
        policy: Retry configuration
        on_context_overflow: Callback for 413 errors (should compact context and return True if recovered)
        operation_name: Name for logging
    
    Returns:
        RetryResult with success/failure details
    """
    total_delay = 0.0
    last_error = None
    
    for attempt in range(policy.max_retries + 1):
        try:
            result = await operation()
            
            if attempt > 0:
                logger.info(
                    "retry_succeeded",
                    operation=operation_name,
                    attempt=attempt + 1,
                    total_delay_seconds=round(total_delay, 2),
                )
            
            return RetryResult(
                success=True,
                result=result,
                attempts=attempt + 1,
                total_delay_seconds=total_delay,
            )
        
        except Exception as e:
            last_error = e
            category, status_code = classify_error(e)
            
            logger.warning(
                "api_call_failed",
                operation=operation_name,
                attempt=attempt + 1,
                max_retries=policy.max_retries,
                error_category=category,
                status_code=status_code,
                error=str(e),
            )
            
            # Non-retryable errors — fail immediately
            if category == "non_retryable":
                return RetryResult(
                    success=False,
                    error=e,
                    attempts=attempt + 1,
                    total_delay_seconds=total_delay,
                    final_status_code=status_code,
                    recovery_action="gave_up_non_retryable",
                )
            
            # Context overflow — try compaction instead of retry
            if category == "context_overflow":
                if on_context_overflow:
                    logger.info("attempting_context_compaction", operation=operation_name)
                    recovered = await on_context_overflow()
                    if recovered:
                        # Don't count this as a retry — the operation was modified
                        logger.info("context_compaction_succeeded", operation=operation_name)
                        continue  # Retry with compacted context
                    else:
                        return RetryResult(
                            success=False,
                            error=e,
                            attempts=attempt + 1,
                            total_delay_seconds=total_delay,
                            final_status_code=status_code,
                            recovery_action="compaction_failed",
                        )
                else:
                    return RetryResult(
                        success=False,
                        error=e,
                        attempts=attempt + 1,
                        total_delay_seconds=total_delay,
                        final_status_code=status_code,
                        recovery_action="no_compaction_handler",
                    )
            
            # Retryable or network errors — backoff and retry
            if attempt < policy.max_retries:
                delay = policy.get_delay(attempt)
                
                # Honor Retry-After header for 429s
                if status_code == 429 and isinstance(e, APIError):
                    retry_after = _parse_retry_after(e.response_body)
                    if retry_after:
                        delay = max(delay, retry_after)
                
                logger.info(
                    "retrying_after_delay",
                    operation=operation_name,
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                    next_attempt=attempt + 2,
                )
                await asyncio.sleep(delay)
                total_delay += delay
    
    # All retries exhausted
    return RetryResult(
        success=False,
        error=last_error,
        attempts=policy.max_retries + 1,
        total_delay_seconds=total_delay,
        final_status_code=getattr(last_error, "status_code", None) if last_error else None,
        recovery_action="retries_exhausted",
    )


def _parse_retry_after(response_body: str) -> float | None:
    """Try to extract a Retry-After value from the response."""
    try:
        import json
        data = json.loads(response_body)
        if "retry_after" in data:
            return float(data["retry_after"])
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None
```

### 2. Integrate Retry into the LLM API Call

**File:** `backend/app/agent/loop.py` (modify the completion call)

```python
from backend.app.agent.retry import retry_with_backoff, RetryPolicy, APIError

# Default retry policy for LLM API calls
LLM_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    base_delay_seconds=1.0,
    max_delay_seconds=30.0,
)

async def get_completion_with_retry(
    messages: list,
    config: AgentConfig,
    context_compactor: Optional[Callable] = None,
) -> CompletionResponse:
    """Get LLM completion with automatic retry for transient errors."""
    
    async def _attempt():
        return await get_completion(messages, config)
    
    async def _on_overflow():
        """Called when context is too large (413). Try to compact."""
        if context_compactor:
            compacted = await context_compactor(messages)
            if compacted:
                messages.clear()
                messages.extend(compacted)
                return True
        return False
    
    result = await retry_with_backoff(
        operation=_attempt,
        policy=LLM_RETRY_POLICY,
        on_context_overflow=_on_overflow if context_compactor else None,
        operation_name="llm_completion",
    )
    
    if result.success:
        return result.result
    
    # All retries failed — raise with context
    raise AgentLoopError(
        f"LLM API call failed after {result.attempts} attempts "
        f"(total delay: {result.total_delay_seconds:.1f}s). "
        f"Last error: {result.error}. "
        f"Recovery action: {result.recovery_action}",
        cause=result.error,
    )
```

### 3. Handle Max Output Token Truncation

**File:** `backend/app/agent/loop.py`

When the model's response is cut off by the output token limit, retry with a hint:

```python
MAX_OUTPUT_RETRIES = 3

async def handle_truncated_response(
    response: CompletionResponse,
    messages: list,
    config: AgentConfig,
) -> CompletionResponse:
    """
    If the model was cut off by output token limits, retry with a continuation hint.
    """
    for retry in range(MAX_OUTPUT_RETRIES):
        if response.finish_reason != "length":
            return response
        
        logger.warning(
            "output_truncated",
            retry=retry + 1,
            max_retries=MAX_OUTPUT_RETRIES,
        )
        
        # Add the truncated response and a continuation hint
        messages.append(response.message)
        messages.append({
            "role": "system",
            "content": (
                "Your previous response was cut off by the output token limit. "
                "Please continue from where you left off. Be more concise if needed."
            ),
        })
        
        response = await get_completion_with_retry(messages, config)
    
    return response
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | RetryPolicy + retry_with_backoff | **Foundation** — reusable retry infrastructure | 45 min |
| 2 | LLM API integration | **Critical** — prevents transient failures from killing sessions | 30 min |
| 3 | Context overflow → compaction bridge | **Critical** — connects to doc 091 | 20 min |
| 4 | Output truncation handling | **Important** — recovers from model cut-offs | 20 min |

---

## Files Affected

- `backend/app/agent/retry.py` — new file, RetryPolicy and retry_with_backoff
- `backend/app/agent/loop.py` — integrate retry into completion calls
- `backend/app/agent/llm/` — wrap raw API calls with retry (provider-specific)

---

## Risks

- **Retry amplifies costs:** Each retry is another API call billed at full token cost. Mitigation: cap at 3 retries; log total retry cost for monitoring.
- **Retry hides persistent failures:** If the API is down for an extended period, retrying just adds latency. Mitigation: max delay cap (30s) and total retry limit; surface the final error to the user.
- **Context compaction during retry changes message state:** The `on_context_overflow` callback mutates the message list. Mitigation: this is intentional — the compacted messages are what we want to retry with.
- **Retry-After headers vary by provider:** Anthropic, OpenAI, and other providers use different formats. Mitigation: best-effort parsing with fallback to exponential backoff.

---

## Not Addressed Here

- **Context compaction strategy** — what to compact and how. See doc 091 (Overflow Recovery).
- **Token-aware pre-flight checks** — avoiding overflow before it happens. See doc 090 (Token-Aware Context Management).
- **Circuit breakers for persistent outages** — if the API is down for 5+ minutes, stop retrying entirely. Future work.
- **Per-provider retry configuration** — different providers may need different policies. Future work.
