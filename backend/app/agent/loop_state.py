"""Loop state tracking for the agent tool-use loop.

Extracted from worker._run_agent_loop to consolidate all tracking variables
into a single dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopState:
    """All mutable tracking state for a single agent loop execution."""

    # Adaptive max_tokens: start low (fast + cheap), escalate on truncation
    TOKEN_TIERS: list[int] = field(default_factory=lambda: [32768, 65536])
    current_tier: int = 0
    continuation_attempts: int = 0
    MAX_CONTINUATIONS: int = 3

    # Repetition detection
    REPETITION_THRESHOLD: int = 2
    recent_tool_calls: list[tuple[str, str]] = field(default_factory=list)

    # Name-only repetition detection (catches varied args for same tool)
    NAME_ONLY_THRESHOLD: int = 3
    recent_tool_names: list[str] = field(default_factory=list)

    # Cyclical loop detection
    CYCLE_WINDOW: int = 30
    CYCLE_MIN_PERIOD: int = 2
    CYCLE_MAX_PERIOD: int = 8
    CYCLE_REPEATS: int = 2
    loop_intervention_count: int = 0
    LOOP_MAX_INTERVENTIONS: int = 2

    # Empty/failed result tracking
    consecutive_empty_results: int = 0
    EMPTY_RESULT_THRESHOLD: int = 2

    # Pre-turn message tracking
    preturn_msg_count: int = 0
    cache_bp2_index: int = 0

    # Tool classification sets
    INFO_GATHERING_TOOLS: frozenset[str] = field(default_factory=lambda: frozenset({
        "file_read", "search_memory",
        "web_search", "web_read", "work_plan",
        "shell_find", "shell_ls", "shell_grep", "git_info",
        "shell_wc", "shell_head", "shell_tree", "project_search",
    }))
    CONSEQUENTIAL_TOOLS: frozenset[str] = field(default_factory=lambda: frozenset({
        "file_write", "file_edit", "code_execute", "respond", "memory_save",
    }))

    # Phase 1B: Batching nudge tracking
    consecutive_single_info_iterations: int = 0

    # Coding-task detection
    CODING_TOOLS: frozenset[str] = field(default_factory=lambda: frozenset({
        "file_edit", "file_write", "work_plan", "coding_agent",
    }))
    is_coding_task: bool = False

    # Phase 2A: Adaptive iteration budget
    adaptive_budget_set: bool = False
    adaptive_budget: int = 25

    # Phase 2B: Early termination for read-only tasks
    has_made_consequential_call: bool = False

    # General counters
    tool_calls_made: int = 0
    max_iterations: int = 25

    # Tool-call density warning
    _tool_density_warned: bool = False

    # Lifecycle phase tracking (Doc 024)
    lifecycle_turn_number: int = 0
    lifecycle_injected: bool = False

    @classmethod
    def create(cls, max_iterations: int, preturn_msg_count: int, cache_bp2_index: int) -> LoopState:
        """Factory method to create a LoopState with computed defaults."""
        return cls(
            max_iterations=max_iterations,
            adaptive_budget=max_iterations,
            preturn_msg_count=preturn_msg_count,
            cache_bp2_index=cache_bp2_index,
        )
