"""Tests for iteration-exhaustion handoff to coding_agent.

Covers the bug where the agent exhausts its iteration budget during
discovery/exploration (file reads, searches) without making edits,
and should still hand off to coding_agent for coding tasks.
"""

from __future__ import annotations

import json
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
    ls = LoopState.create(
        max_iterations=25,
        preturn_msg_count=0,
        cache_bp2_index=0,
    )
    ls.adaptive_budget = overrides.pop("adaptive_budget", 25)
    for k, v in overrides.items():
        setattr(ls, k, v)
    return ls


def _make_messages_with_discovery(file_read_count: int = 5, edits: bool = False):
    """Build a message list simulating discovery work."""
    messages = []
    tool_calls = []
    for i in range(file_read_count):
        tool_calls.append({
            "function": {"name": "file_read", "arguments": json.dumps({"path": f"/workspace/src/file{i}.py"})},
            "id": f"call_{i}",
        })
    if edits:
        tool_calls.append({
            "function": {"name": "file_edit", "arguments": json.dumps({"path": "/workspace/src/fix.py"})},
            "id": "call_edit",
        })
    messages.append({"role": "assistant", "tool_calls": tool_calls})
    return messages


class TestDiscoveryHandoffEscalation:
    """Budget escalation should offer coding_agent handoff for coding tasks
    that did substantial discovery even without making edits."""

    def test_coding_task_with_discovery_gets_handoff(self):
        """Coding task with 5 file reads but no edits should get coding_agent handoff."""
        ls = _make_loop_state(
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=False,
        )
        messages = _make_messages_with_discovery(file_read_count=5, edits=False)
        tool_defs: list[dict] = []
        iteration = 20  # past 80% of budget

        result = handle_budget_escalation(iteration, _make_llm_message(), ls, messages, tool_defs)

        assert result is False
        # Should inject a handoff message mentioning coding_agent
        injected = [m for m in messages if m.get("role") == "user" and "coding_agent" in m.get("content", "")]
        assert len(injected) == 1, "Should inject coding_agent handoff message for discovery-heavy coding task"

    def test_coding_task_no_discovery_no_edits_reports_back(self):
        """Coding task with <3 discovery calls and no edits = no progress, force report-back."""
        ls = _make_loop_state(
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=False,
        )
        messages = _make_messages_with_discovery(file_read_count=1, edits=False)
        tool_defs: list[dict] = []
        iteration = 20

        result = handle_budget_escalation(iteration, _make_llm_message(), ls, messages, tool_defs)

        assert result is False
        # Should inject a report-back message, NOT a coding_agent handoff
        injected = [m for m in messages if m.get("role") == "user"]
        assert any("RESPOND" in m.get("content", "") for m in injected), \
            "Should force report-back when no meaningful work done"

    def test_coding_task_with_edits_still_gets_handoff(self):
        """Existing behavior: coding task with edits gets handoff (regression guard)."""
        ls = _make_loop_state(
            adaptive_budget=25,
            is_coding_task=True,
            has_made_consequential_call=True,
        )
        messages = _make_messages_with_discovery(file_read_count=2, edits=True)
        tool_defs: list[dict] = []
        iteration = 20

        result = handle_budget_escalation(iteration, _make_llm_message(), ls, messages, tool_defs)

        assert result is False
        injected = [m for m in messages if m.get("role") == "user" and "coding_agent" in m.get("content", "")]
        assert len(injected) == 1


class TestLoopPyHandoffCondition:
    """Test the _has_meaningful_work logic extracted from loop.py's
    iteration-exhaustion fallback."""

    def _compute_meaningful_work(self, messages):
        """Replicate loop.py's logic for testing."""
        _has_edits = False
        _discovery_tool_count = 0
        _DISCOVERY_TOOLS = {"file_read", "search_memory", "web_search", "web_read",
                            "shell_find", "file_search", "git_info", "file_list",
                            "shell_ls", "shell_tree", "project_search", "code_execute"}
        for msg in messages:
            if msg.get("role") != "assistant" or not msg.get("tool_calls"):
                continue
            for tc in msg.get("tool_calls", []):
                fn_name = tc.get("function", {}).get("name", "")
                if fn_name in ("file_edit", "file_write"):
                    _has_edits = True
                elif fn_name in _DISCOVERY_TOOLS:
                    _discovery_tool_count += 1
        return _has_edits or _discovery_tool_count >= 3

    def test_edits_are_meaningful(self):
        msgs = _make_messages_with_discovery(file_read_count=0, edits=True)
        assert self._compute_meaningful_work(msgs) is True

    def test_3_file_reads_are_meaningful(self):
        msgs = _make_messages_with_discovery(file_read_count=3, edits=False)
        assert self._compute_meaningful_work(msgs) is True

    def test_5_file_reads_are_meaningful(self):
        msgs = _make_messages_with_discovery(file_read_count=5, edits=False)
        assert self._compute_meaningful_work(msgs) is True

    def test_1_file_read_not_meaningful(self):
        msgs = _make_messages_with_discovery(file_read_count=1, edits=False)
        assert self._compute_meaningful_work(msgs) is False

    def test_no_work_not_meaningful(self):
        msgs = [{"role": "assistant", "tool_calls": [
            {"function": {"name": "respond", "arguments": "{}"}, "id": "call_0"}
        ]}]
        assert self._compute_meaningful_work(msgs) is False

    def test_mixed_discovery_tools(self):
        msgs = [{"role": "assistant", "tool_calls": [
            {"function": {"name": "file_read", "arguments": "{}"}, "id": "c1"},
            {"function": {"name": "web_search", "arguments": "{}"}, "id": "c2"},
            {"function": {"name": "git_info", "arguments": "{}"}, "id": "c3"},
        ]}]
        assert self._compute_meaningful_work(msgs) is True
