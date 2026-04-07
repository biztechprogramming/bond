"""Loop state tracking for the agent tool-use loop.

Extracted from worker._run_agent_loop to consolidate all tracking variables
into a single dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from typing import ClassVar, TYPE_CHECKING

from backend.app.agent.parallel_worker import ALWAYS_CONSEQUENTIAL
from backend.app.agent.tool_selection import CODING_SIGNAL_TOOLS

if TYPE_CHECKING:
    from backend.app.agent.tools.tool_result import ToolResult


@dataclass
class ToolMetrics:
    """Track tool execution metrics across the loop (Doc 092)."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    timeout_calls: int = 0
    total_duration_ms: int = 0
    slowest_tool: str = ""
    slowest_duration_ms: int = 0

    def record(self, result: ToolResult):
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


@dataclass
class LoopState:
    """All mutable tracking state for a single agent loop execution."""

    CONSEQUENTIAL_TOOLS: ClassVar[frozenset] = ALWAYS_CONSEQUENTIAL
    CODING_TOOLS: ClassVar[frozenset] = CODING_SIGNAL_TOOLS

    # Adaptive max_tokens: start low (fast + cheap), escalate on truncation
    TOKEN_TIERS: list[int] = field(default_factory=lambda: [32768, 65536])
    current_tier: int = 0
    continuation_attempts: int = 0
    MAX_CONTINUATIONS: int = 3

    # Iteration tracking
    max_iterations: int = 100
    adaptive_budget: int = 100
    iteration: int = 0

    # Budget escalation tracking
    budget_warned_65: bool = False
    budget_warned_80: bool = False
    budget_restricted: bool = False
    restricted_tools: list[str] = field(default_factory=list)

    # Message tracking
    preturn_msg_count: int = 0
    cache_bp2_index: int = 0

    # Loop detection
    consecutive_tool_only: int = 0
    last_tool_names: list[str] = field(default_factory=list)
    last_tool_args_hash: str = ""

    # Consequential / coding-task tracking
    has_made_consequential_call: bool = False
    is_coding_task: bool = False
    _tool_density_warned: bool = False

    # Metrics (Doc 092)
    tool_metrics: ToolMetrics = field(default_factory=ToolMetrics)

    # Compaction tracking (Doc 091)
    tokens_compacted: int = 0
    compaction_events: int = 0
    peak_token_count: int = 0

    # Overflow recovery tracking (Doc 091)
    overflow_events: int = 0
    overflow_recoveries: int = 0
    recovery_tiers_used: list[str] = field(default_factory=list)

    def record_compaction(self, tokens_before: int, tokens_after: int):
        self.tokens_compacted += (tokens_before - tokens_after)
        self.compaction_events += 1

    def record_token_count(self, count: int):
        self.peak_token_count = max(self.peak_token_count, count)

    def record_overflow(self, tier: str, recovered: bool):
        """Record an overflow event and recovery attempt (Doc 091)."""
        self.overflow_events += 1
        self.recovery_tiers_used.append(tier)
        if recovered:
            self.overflow_recoveries += 1

    @classmethod
    def create(cls, max_iterations: int, preturn_msg_count: int, cache_bp2_index: int) -> LoopState:
        """Factory method to create a LoopState with computed defaults."""
        return cls(
            max_iterations=max_iterations,
            adaptive_budget=max_iterations,
            preturn_msg_count=preturn_msg_count,
            cache_bp2_index=cache_bp2_index,
        )
