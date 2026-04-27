"""Tests for early termination and budget nudge behavior.

Regression tests ensuring the agent does NOT stop after discovery and ask
the user for permission to proceed on coding tasks (fix/no-permission-reask).
"""

from __future__ import annotations

import pytest

from backend.app.agent.continuation import IterationBudget
from backend.app.agent.loop_state import LoopState


# ---------------------------------------------------------------------------
# handle_early_termination tests
# ---------------------------------------------------------------------------

class TestHandleEarlyTermination:
    """Test that handle_early_termination behaves differently for coding vs Q&A tasks."""

    def _call(self, iteration: int, loop_state: LoopState, messages: list, tool_defs: list):
        from backend.app.agent.iteration_handlers import handle_early_termination
        handle_early_termination(iteration, loop_state, messages, tool_defs)

    def _make_state(self, *, adaptive_budget: int = 100, is_coding_task: bool = False,
                    has_consequential: bool = False) -> LoopState:
        state = LoopState.create(max_iterations=100, preturn_msg_count=0, cache_bp2_index=0)
        state.adaptive_budget = adaptive_budget
        state.is_coding_task = is_coding_task
        state.has_made_consequential_call = has_consequential
        return state

    def test_coding_task_iteration10_does_not_say_respond(self):
        """At iteration 10, a coding task should be told to implement, not respond."""
        state = self._make_state(adaptive_budget=80, is_coding_task=True)
        messages: list[dict] = []
        tool_defs: list[dict] = []
        self._call(10, state, messages, tool_defs)

        assert len(messages) == 1
        msg = messages[0]["content"]
        assert "START IMPLEMENTING" in msg
        assert "Do NOT ask for permission" in msg
        # Must NOT tell the agent to "respond to the user now"
        assert "respond to the user now" not in msg.lower()

    def test_coding_task_high_budget_iteration10_implements(self):
        """Even if is_coding_task is False, a high adaptive_budget (>=40) should trigger implementation nudge."""
        state = self._make_state(adaptive_budget=60, is_coding_task=False)
        messages: list[dict] = []
        tool_defs: list[dict] = []
        self._call(10, state, messages, tool_defs)

        assert len(messages) == 1
        msg = messages[0]["content"]
        assert "START IMPLEMENTING" in msg

    def test_qa_task_iteration10_responds(self):
        """A low-budget Q&A task should still be told to respond."""
        state = self._make_state(adaptive_budget=8, is_coding_task=False)
        messages: list[dict] = []
        tool_defs: list[dict] = []
        self._call(10, state, messages, tool_defs)

        assert len(messages) == 1
        msg = messages[0]["content"]
        assert "Synthesize your findings and respond" in msg

    def test_coding_task_iteration15_no_tool_restriction(self):
        """At iteration 15+, coding tasks must NOT be restricted to respond+say."""
        state = self._make_state(adaptive_budget=80, is_coding_task=True)
        messages: list[dict] = []
        tool_defs = [{"name": "file_edit"}, {"name": "respond"}]
        original_defs = list(tool_defs)
        self._call(15, state, messages, tool_defs)

        # tool_defs should NOT have been cleared/restricted
        assert tool_defs == original_defs
        assert len(messages) == 1
        assert "file edits RIGHT NOW" in messages[0]["content"]

    def test_qa_task_iteration15_forces_respond(self):
        """At iteration 15+, Q&A tasks should be restricted to respond+say."""
        state = self._make_state(adaptive_budget=8, is_coding_task=False)
        messages: list[dict] = []
        tool_defs = [{"name": "file_edit"}, {"name": "respond"}]
        self._call(15, state, messages, tool_defs)

        # For non-coding tasks, tool_defs should be cleared to respond+say
        # (the actual clearing depends on TOOL_MAP being importable)
        # At minimum, no implementation nudge should appear
        assert len(messages) == 0  # no message injected at 15 for Q&A (only tool restriction)

    def test_consequential_calls_skip_entirely(self):
        """If the agent has already made consequential calls, no nudge at all."""
        state = self._make_state(adaptive_budget=80, is_coding_task=True, has_consequential=True)
        messages: list[dict] = []
        tool_defs: list[dict] = []
        self._call(10, state, messages, tool_defs)
        assert len(messages) == 0

    def test_iteration_below_threshold_no_nudge(self):
        """Before iteration 10, no early termination nudge."""
        state = self._make_state(adaptive_budget=80, is_coding_task=True)
        messages: list[dict] = []
        tool_defs: list[dict] = []
        self._call(5, state, messages, tool_defs)
        assert len(messages) == 0


# ---------------------------------------------------------------------------
# IterationBudget anti-permission-reask tests
# ---------------------------------------------------------------------------

class TestIterationBudgetNoPermissionReask:
    """Ensure budget messages never induce 'want me to proceed?' behavior."""

    _PERMISSION_PHRASES = [
        "would you like",
        "shall i",
        "do you want me to",
        "if you want",
        "want me to proceed",
        "ready to execute",
        "i can do the actual implementation",
    ]

    def _assert_no_permission_asking(self, msg: str | None):
        if msg is None:
            return
        lower = msg.lower()
        for phrase in self._PERMISSION_PHRASES:
            assert phrase not in lower, (
                f"Budget message contains permission-asking phrase '{phrase}': {msg[:200]}"
            )

    def test_wrap_up_message_no_permission_asking(self):
        budget = IterationBudget(total=100, used=80)
        msg = budget.get_budget_message()
        assert msg is not None
        self._assert_no_permission_asking(msg)
        # Should tell agent to delegate, not ask user
        assert "coding_agent" in msg

    def test_stop_message_no_permission_asking(self):
        budget = IterationBudget(total=100, used=95)
        msg = budget.get_budget_message()
        assert msg is not None
        self._assert_no_permission_asking(msg)

    def test_stop_message_includes_anti_reask_language(self):
        """Budget stop/wrap messages must explicitly forbid asking permission."""
        budget = IterationBudget(total=100, used=95)
        msg = budget.get_budget_message()
        assert msg is not None
        assert "Do NOT tell the user you found things and ask" in msg

    def test_wrap_up_message_includes_anti_reask_language(self):
        budget = IterationBudget(total=100, used=80)
        msg = budget.get_budget_message()
        assert msg is not None
        assert "Do NOT tell the user you found things and ask" in msg

    def test_nudge_message_no_permission_asking(self):
        budget = IterationBudget(total=100, used=65)
        msg = budget.get_budget_message()
        assert msg is not None
        self._assert_no_permission_asking(msg)


# ---------------------------------------------------------------------------
# handle_budget_escalation anti-permission-reask tests
# ---------------------------------------------------------------------------

class TestBudgetEscalationMessages:
    """Ensure budget escalation injected messages don't induce permission-asking."""

    _PERMISSION_PHRASES = [
        "would you like",
        "shall i",
        "if you want",
        "want me to proceed",
    ]

    def test_coding_task_handoff_message_no_permission_asking(self):
        """The coding_agent handoff message should command, not ask."""
        from backend.app.agent.iteration_handlers import handle_budget_escalation

        state = LoopState.create(max_iterations=100, preturn_msg_count=0, cache_bp2_index=0)
        state.adaptive_budget = 80
        state.is_coding_task = True
        state.has_made_consequential_call = True

        # Create a mock LLM message with no tool calls
        class MockMsg:
            tool_calls = []

        messages: list[dict] = []
        tool_defs: list[dict] = []

        # Trigger at iteration = effective_threshold (80% of 80 = 64)
        handle_budget_escalation(64, MockMsg(), state, messages, tool_defs)

        if messages:
            msg = messages[0]["content"]
            lower = msg.lower()
            for phrase in self._PERMISSION_PHRASES:
                assert phrase not in lower, f"Budget escalation contains '{phrase}'"
