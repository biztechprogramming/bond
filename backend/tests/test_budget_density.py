"""Tests for tool-call density budget warning in handle_budget_escalation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.app.agent.iteration_handlers import handle_budget_escalation
from backend.app.agent.loop_state import LoopState


def _make_llm_message(tool_names: list[str] | None = None):
    msg = MagicMock()
    if tool_names:
        msg.tool_calls = [
            MagicMock(function=MagicMock(name=n)) for n in tool_names
        ]
    else:
        msg.tool_calls = []
    return msg


def _make_loop_state(**overrides) -> LoopState:
    defaults = dict(
        max_iterations=25,
        adaptive_budget=25,
        preturn_msg_count=0,
        cache_bp2_index=0,
    )
    ls = LoopState.create(**{k: defaults[k] for k in ("max_iterations", "preturn_msg_count", "cache_bp2_index")})
    ls.adaptive_budget = overrides.pop("adaptive_budget", 25)
    for k, v in overrides.items():
        setattr(ls, k, v)
    return ls


class TestToolDensityWarning:
    """Tests for the tool-call density soft warning."""

    def test_density_warning_fires_when_threshold_met(self):
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=True,
        )
        messages: list[dict] = []
        tool_defs: list[dict] = []
        result = handle_budget_escalation(3, _make_llm_message(), ls, messages, tool_defs)

        assert result is False
        assert ls._tool_density_warned is True
        assert len(messages) == 1
        assert "tool calls across" in messages[0]["content"]

    def test_density_warning_skipped_for_non_coding(self):
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=False,
            has_made_consequential_call=True,
        )
        messages: list[dict] = []
        result = handle_budget_escalation(3, _make_llm_message(), ls, messages, [])

        assert ls._tool_density_warned is False
        # No density message injected
        assert not any("tool calls across" in m.get("content", "") for m in messages)

    def test_density_warning_skipped_without_consequential_calls(self):
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=False,
        )
        messages: list[dict] = []
        result = handle_budget_escalation(3, _make_llm_message(), ls, messages, [])

        assert ls._tool_density_warned is False

    def test_density_warning_fires_only_once(self):
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=True,
        )
        messages: list[dict] = []

        handle_budget_escalation(3, _make_llm_message(), ls, messages, [])
        assert ls._tool_density_warned is True
        assert len(messages) == 1

        # Second call should not add another message
        handle_budget_escalation(4, _make_llm_message(), ls, messages, [])
        assert len(messages) == 1

    def test_density_warning_returns_false(self):
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=True,
        )
        result = handle_budget_escalation(3, _make_llm_message(), ls, [], [])
        assert result is False

    def test_iteration_escalation_still_works_after_density_warning(self):
        """The existing iteration-based escalation should still fire after density warning."""
        ls = _make_loop_state(
            tool_calls_made=17,
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=True,
        )
        messages: list[dict] = []

        # Fire density warning at iteration 3
        handle_budget_escalation(3, _make_llm_message(), ls, messages, [])
        assert ls._tool_density_warned is True

        # Now at iteration 20 (80% of 25), iteration-based escalation should fire
        messages2: list[dict] = []
        result = handle_budget_escalation(20, _make_llm_message(), ls, messages2, [])
        # Should have injected an escalation message
        assert len(messages2) >= 1
