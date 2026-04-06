"""Stuck detection for the agent loop (Design Doc 093).

Detects when the agent is making identical tool calls repeatedly
and intervenes to break the pattern. Budget enforcement is handled
by the existing iteration budget system in loop.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class StuckDetector:
    """Detects repeated identical tool calls in the agent loop.

    Tracks tool call hashes and flags when the same call appears
    N times consecutively, indicating the agent is stuck.
    """

    max_consecutive_repeats: int = 2
    stuck_interventions: int = 0
    _recent_call_hashes: list[str] = field(default_factory=list)

    def record_tool_call(self, tool_name: str, arguments: dict) -> None:
        """Record a tool call for stuck detection."""
        call_hash = self._hash_call(tool_name, arguments)
        self._recent_call_hashes.append(call_hash)
        # Only keep the window we need for comparison
        if len(self._recent_call_hashes) > self.max_consecutive_repeats * 2:
            self._recent_call_hashes = self._recent_call_hashes[-self.max_consecutive_repeats * 2 :]

    def is_stuck(self) -> bool:
        """Check if the last N tool calls are identical."""
        n = self.max_consecutive_repeats
        if len(self._recent_call_hashes) < n:
            return False
        recent = self._recent_call_hashes[-n:]
        return len(set(recent)) == 1

    def clear(self) -> None:
        """Reset the call history after an intervention."""
        self._recent_call_hashes.clear()

    def get_stuck_message(self) -> str:
        """Message injected when stuck pattern is detected."""
        return (
            "\U0001f501 Stuck pattern detected: you have made the same tool call "
            f"{self.max_consecutive_repeats} times in a row. "
            "Try a different approach:\n"
            "- Use a different tool or different arguments\n"
            "- Ask the user for clarification\n"
            "- If a command failed, read the error and adjust\n"
            "- If you're blocked, report what's blocking you to the user\n"
            "- If the task is too complex, delegate to coding_agent\n\n"
            "Do NOT retry the same call again."
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
