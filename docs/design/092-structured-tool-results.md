# Design Doc 092: Structured Tool Results with Error Feedback

**Status:** Implemented
**Date:** 2026-04-02  
**Triggered by:** Comparison of Bond agent loop vs Claude Code source — stability improvements

---

## Problem

Tool execution in `execute_tool_call()` (iteration_handlers.py) and the handlers in `native.py` doesn't consistently return structured error information to the model. When a tool fails:

1. **Silent failures** — some exceptions are caught and logged but the model receives no feedback, leaving it confused about what happened
2. **Raw exceptions** — sometimes a Python traceback leaks through instead of an actionable error message
3. **No duration tracking** — we can't identify slow tools that are burning wall-clock time
4. **No timeout per tool** — a hanging tool (e.g., a shell command that blocks on stdin) can block the entire agent loop indefinitely

Claude Code solves this with a `ToolResult<T>` type and `ValidationResult` — every tool returns structured data with explicit success/error states, and the model always gets actionable feedback.

---

## Changes

### 1. Define a ToolResult Dataclass

**File:** `backend/app/agent/tools/tool_result.py` (new file)

```python
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class ToolResult:
    """Structured result from tool execution."""
    success: bool
    output: str
    error: str | None = None
    duration_ms: int = 0
    tool_name: str = ""
    truncated: bool = False
    
    def to_message_content(self) -> str:
        """Format for inclusion in the assistant message to the model."""
        if self.success:
            content = self.output
            if self.truncated:
                content += "\n\n[Output truncated — showing first 50,000 characters]"
            return content
        else:
            return (
                f"Error executing {self.tool_name}: {self.error}\n"
                f"The tool failed after {self.duration_ms}ms. "
                f"Please check your parameters and try a different approach."
            )
    
    @staticmethod
    def from_success(output: str, tool_name: str, duration_ms: int = 0) -> "ToolResult":
        # Truncate very large outputs to prevent context bloat
        truncated = False
        if len(output) > 50_000:
            output = output[:50_000]
            truncated = True
        return ToolResult(
            success=True,
            output=output,
            tool_name=tool_name,
            duration_ms=duration_ms,
            truncated=truncated,
        )
    
    @staticmethod
    def from_error(error: str, tool_name: str, duration_ms: int = 0) -> "ToolResult":
        return ToolResult(
            success=False,
            output="",
            error=error,
            tool_name=tool_name,
            duration_ms=duration_ms,
        )
    
    @staticmethod
    def from_timeout(tool_name: str, timeout_seconds: int) -> "ToolResult":
        return ToolResult(
            success=False,
            output="",
            error=f"Tool '{tool_name}' timed out after {timeout_seconds}s. "
                  f"The operation took too long. Try a simpler operation or delegate to coding_agent.",
            tool_name=tool_name,
            duration_ms=timeout_seconds * 1000,
        )


class ToolTimer:
    """Context manager to time tool execution."""
    def __init__(self):
        self.start_time = 0
        self.duration_ms = 0
    
    def __enter__(self):
        self.start_time = time.monotonic()
        return self
    
    def __exit__(self, *args):
        self.duration_ms = int((time.monotonic() - self.start_time) * 1000)
```

### 2. Wrap Tool Handlers to Return ToolResult

**File:** `backend/app/agent/iteration_handlers.py` (modify `execute_tool_call()`)

Wrap every tool execution in structured error handling:

```python
from backend.app.agent.tools.tool_result import ToolResult, ToolTimer

# Per-tool timeout limits (seconds)
TOOL_TIMEOUTS: dict[str, int] = {
    "code_execute": 60,
    "shell_grep": 30,
    "shell_find": 30,
    "file_read": 10,
    "file_write": 10,
    "file_edit": 10,
    "project_search": 30,
    "coding_agent": 900,  # 15 minutes
    "respond": 5,
    "say": 5,
}
DEFAULT_TOOL_TIMEOUT = 30

async def execute_tool_call(tool_name: str, arguments: dict, **kwargs) -> ToolResult:
    """
    Execute a tool call with structured result handling.
    
    - Always returns a ToolResult (never throws to the caller)
    - Applies per-tool timeouts
    - Logs duration and success/failure
    """
    timeout = TOOL_TIMEOUTS.get(tool_name, DEFAULT_TOOL_TIMEOUT)
    
    with ToolTimer() as timer:
        try:
            raw_result = await asyncio.wait_for(
                _dispatch_tool(tool_name, arguments, **kwargs),
                timeout=timeout,
            )
            result = ToolResult.from_success(
                output=str(raw_result) if raw_result else "(no output)",
                tool_name=tool_name,
                duration_ms=timer.duration_ms,
            )
        except asyncio.TimeoutError:
            result = ToolResult.from_timeout(tool_name, timeout)
            logger.warning(
                "tool_timeout",
                tool=tool_name,
                timeout=timeout,
                args_summary=_summarize_args(arguments),
            )
        except Exception as e:
            result = ToolResult.from_error(
                error=f"{type(e).__name__}: {str(e)}",
                tool_name=tool_name,
                duration_ms=timer.duration_ms,
            )
            logger.error(
                "tool_execution_error",
                tool=tool_name,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=timer.duration_ms,
            )
    
    logger.info(
        "tool_executed",
        tool=tool_name,
        success=result.success,
        duration_ms=result.duration_ms,
        output_length=len(result.output),
        truncated=result.truncated,
    )
    
    return result


def _summarize_args(args: dict) -> str:
    """Summarize tool arguments for logging (truncate long values)."""
    summary = {}
    for k, v in args.items():
        s = str(v)
        summary[k] = s[:100] + "..." if len(s) > 100 else s
    return str(summary)
```

### 3. Feed Structured Errors Back to the Model

**File:** `backend/app/agent/loop.py` (in the tool result handling section of `agent_turn()`)

Ensure the model always receives the formatted tool result:

```python
# After executing a tool call
result: ToolResult = await execute_tool_call(tool_name, arguments, **kwargs)

# Build the tool result message for the model
tool_result_message = {
    "role": "tool",
    "tool_call_id": tool_call.id,
    "content": result.to_message_content(),
}
messages.append(tool_result_message)

# If the tool failed, optionally inject a hint
if not result.success:
    logger.info("tool_failure_feedback", tool=tool_name, error=result.error)
```

### 4. Add Tool Execution Metrics

**File:** `backend/app/agent/loop_state.py`

```python
@dataclass
class ToolMetrics:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    timeout_calls: int = 0
    total_duration_ms: int = 0
    slowest_tool: str = ""
    slowest_duration_ms: int = 0
    
    def record(self, result: "ToolResult"):
        self.total_calls += 1
        self.total_duration_ms += result.duration_ms
        if result.success:
            self.successful_calls += 1
        elif result.error and "timed out" in result.error:
            self.timeout_calls += 1
        else:
            self.failed_calls += 1
        if result.duration_ms > self.slowest_duration_ms:
            self.slowest_duration_ms = result.duration_ms
            self.slowest_tool = result.tool_name
```

---

## Priority & Ordering

| # | Change | Severity | Effort |
|---|--------|----------|--------|
| 1 | ToolResult dataclass | **Foundation** — needed by everything else | 20 min |
| 2 | Wrap execute_tool_call | **Critical** — prevents silent failures | 45 min |
| 3 | Feed errors back to model | **Critical** — model must always know what happened | 20 min |
| 4 | Tool execution metrics | **Observability** — identify slow/failing tools | 20 min |

---

## Files Affected

- `backend/app/agent/tools/tool_result.py` — new file, ToolResult dataclass
- `backend/app/agent/iteration_handlers.py` — `execute_tool_call()` refactored
- `backend/app/agent/loop.py` — tool result message construction
- `backend/app/agent/loop_state.py` — ToolMetrics tracking

---

## Risks

- **Output truncation at 50K chars:** Some legitimate tool outputs (e.g., large file reads) may need more. Mitigation: the truncation message tells the model what happened; it can request a smaller range.
- **Per-tool timeouts too aggressive:** Some tools legitimately take longer (large repos, slow networks). Mitigation: timeouts are configurable per tool, and `coding_agent` gets 15 minutes.
- **Changing execute_tool_call signature:** Callers currently expect a raw string. Mitigation: the `to_message_content()` method provides backward compatibility for message construction.

---

## Not Addressed Here

- **Tool input validation** — see doc 094 (Tool Input Validation)
- **Tool result caching** — see existing doc 065 (Tool Result Caching)
- **Retry logic for transient tool failures** — future work; currently tools fail once and report
