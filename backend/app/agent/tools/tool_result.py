"""Structured tool result type (Design Doc 092).

Every tool execution returns a ToolResult with explicit success/error state,
duration tracking, and formatted output for the model.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    def from_success(output: str, tool_name: str, duration_ms: int = 0) -> ToolResult:
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
    def from_error(error: str, tool_name: str, duration_ms: int = 0) -> ToolResult:
        return ToolResult(
            success=False,
            output="",
            error=error,
            tool_name=tool_name,
            duration_ms=duration_ms,
        )

    @staticmethod
    def from_timeout(tool_name: str, timeout_seconds: int) -> ToolResult:
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
        self.start_time: float = 0
        self.duration_ms: int = 0

    def __enter__(self):
        self.start_time = time.monotonic()
        return self

    def __exit__(self, *args):
        self.duration_ms = int((time.monotonic() - self.start_time) * 1000)
