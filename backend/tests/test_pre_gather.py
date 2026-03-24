"""Tests for backend.app.agent.pre_gather — Plan/Gather phases."""

from __future__ import annotations

import json

import pytest

from backend.app.agent.pre_gather import (
    _extract_json,
    _should_skip_plan,
    _validate_plan,
    build_handoff_context,
    compute_adaptive_budget,
    WORKSPACE_PLAN_SYSTEM_PROMPT,
    DEEP_MAP_FILE_SELECT_PROMPT,
)
from backend.app.agent.pre_gather_integration import PreGatherResult


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json(self):
        result = _extract_json('{"complexity": "simple"}')
        assert result == {"complexity": "simple"}

    def test_json_in_code_block(self):
        text = '```json\n{"complexity": "moderate"}\n```'
        result = _extract_json(text)
        assert result == {"complexity": "moderate"}

    def test_json_in_plain_block(self):
        text = '```\n{"complexity": "complex"}\n```'
        result = _extract_json(text)
        assert result == {"complexity": "complex"}

    def test_invalid_text_returns_none(self):
        assert _extract_json("not json at all") is None

    def test_empty_returns_none(self):
        assert _extract_json("") is None
        assert _extract_json(None) is None


# ---------------------------------------------------------------------------
# _validate_plan
# ---------------------------------------------------------------------------


class TestValidatePlan:
    def test_valid_plan(self):
        plan = {
            "complexity": "moderate",
            "approach": "read files",
            "files_to_read": ["a.py", "b.py"],
            "grep_patterns": [{"pattern": "foo", "directory": "src/"}],
            "delegate_to_coding_agent": False,
            "estimated_iterations": 5,
        }
        result = _validate_plan(plan)
        assert result is not None
        assert result["complexity"] == "moderate"
        assert result["files_to_read"] == ["a.py", "b.py"]

    def test_missing_fields_uses_defaults(self):
        result = _validate_plan({})
        assert result is not None
        assert result["complexity"] == "moderate"
        assert result["files_to_read"] == []
        assert result["grep_patterns"] == []
        assert result["delegate_to_coding_agent"] is False
        assert result["estimated_iterations"] == 5

    def test_invalid_complexity_defaults(self):
        result = _validate_plan({"complexity": "extreme"})
        assert result["complexity"] == "moderate"

    def test_files_capped_at_15(self):
        files = [f"file{i}.py" for i in range(20)]
        result = _validate_plan({"files_to_read": files})
        assert len(result["files_to_read"]) == 15

    def test_repos_to_map_field(self):
        plan = {"repos_to_map": ["bond", "openclaw"]}
        result = _validate_plan(plan)
        assert result["repos_to_map"] == ["bond", "openclaw"]

    def test_repos_to_map_defaults_empty(self):
        result = _validate_plan({})
        assert result["repos_to_map"] == []

    def test_repos_to_map_capped_at_3(self):
        repos = [f"repo{i}" for i in range(5)]
        result = _validate_plan({"repos_to_map": repos})
        assert len(result["repos_to_map"]) == 3

    def test_repos_to_map_non_list(self):
        result = _validate_plan({"repos_to_map": "not-a-list"})
        assert result["repos_to_map"] == []

    def test_repos_to_map_strips_whitespace(self):
        result = _validate_plan({"repos_to_map": ["  bond  ", "openclaw"]})
        assert result["repos_to_map"] == ["bond", "openclaw"]

    def test_non_dict_returns_none(self):
        assert _validate_plan("not a dict") is None
        assert _validate_plan([1, 2]) is None


# ---------------------------------------------------------------------------
# _should_skip_plan
# ---------------------------------------------------------------------------


class TestShouldSkipPlan:
    def test_short_message(self):
        assert _should_skip_plan("hi") is True
        assert _should_skip_plan("ok") is True

    def test_greeting(self):
        assert _should_skip_plan("hello!") is True
        assert _should_skip_plan("thanks") is True

    def test_normal_message(self):
        assert _should_skip_plan("Please fix the bug in worker.py") is False

    def test_borderline_length(self):
        assert _should_skip_plan("short msg") is True  # < 20 chars
        assert _should_skip_plan("this is a longer message now") is False  # >= 20 chars


# ---------------------------------------------------------------------------
# compute_adaptive_budget
# ---------------------------------------------------------------------------


class TestComputeAdaptiveBudget:
    def test_simple(self):
        assert compute_adaptive_budget({"complexity": "simple"}, 25) == 3

    def test_moderate(self):
        assert compute_adaptive_budget({"complexity": "moderate"}, 25) == 12

    def test_complex(self):
        assert compute_adaptive_budget({"complexity": "complex"}, 25) == 20

    def test_delegate(self):
        assert compute_adaptive_budget({"delegate_to_coding_agent": True}, 25) == 5

    def test_respects_max_iterations(self):
        assert compute_adaptive_budget({"complexity": "complex"}, 10) == 10

    def test_unknown_complexity(self):
        assert compute_adaptive_budget({"complexity": "unknown"}, 25) is None


# ---------------------------------------------------------------------------
# build_handoff_context
# ---------------------------------------------------------------------------


class TestBuildHandoffContext:
    def test_empty_messages(self):
        ctx = build_handoff_context([])
        assert ctx["files_read"] == "None"
        assert ctx["edits_made"] == "None"

    def test_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "file_read",
                            "arguments": json.dumps({"path": "src/main.py"}),
                        }
                    },
                    {
                        "function": {
                            "name": "file_edit",
                            "arguments": json.dumps({"path": "src/utils.py"}),
                        }
                    },
                ],
            }
        ]
        ctx = build_handoff_context(messages)
        assert "src/main.py" in ctx["files_read"]
        assert "src/utils.py" in ctx["edits_made"]

    def test_deduplicates(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "file_read", "arguments": json.dumps({"path": "a.py"})}},
                    {"function": {"name": "file_read", "arguments": json.dumps({"path": "a.py"})}},
                ],
            }
        ]
        ctx = build_handoff_context(messages)
        assert ctx["files_read"].count("a.py") == 1

    def test_ignores_non_assistant(self):
        messages = [
            {
                "role": "user",
                "tool_calls": [
                    {"function": {"name": "file_read", "arguments": json.dumps({"path": "a.py"})}},
                ],
            }
        ]
        ctx = build_handoff_context(messages)
        assert ctx["files_read"] == "None"


# ---------------------------------------------------------------------------
# PreGatherResult
# ---------------------------------------------------------------------------


class TestPreGatherResult:
    def test_defaults(self):
        r = PreGatherResult()
        assert r.plan is None
        assert r.context_bundle == ""
        assert r.adaptive_budget is None
        assert r.delegate_to_coding_agent is False


# ---------------------------------------------------------------------------
# Prompt templates (Design Doc 069)
# ---------------------------------------------------------------------------


class TestWorkspacePromptTemplates:
    def test_workspace_plan_prompt_has_placeholder(self):
        assert "{workspace_overview}" in WORKSPACE_PLAN_SYSTEM_PROMPT

    def test_workspace_plan_prompt_mentions_repos_to_map(self):
        assert "repos_to_map" in WORKSPACE_PLAN_SYSTEM_PROMPT

    def test_workspace_plan_prompt_renders(self):
        rendered = WORKSPACE_PLAN_SYSTEM_PROMPT.format(workspace_overview="=== bond/ (git) ===\n  src/")
        assert "=== bond/" in rendered
        assert "repos_to_map" in rendered

    def test_deep_map_file_select_prompt_has_placeholders(self):
        assert "{approach}" in DEEP_MAP_FILE_SELECT_PROMPT
        assert "{deep_map}" in DEEP_MAP_FILE_SELECT_PROMPT
        assert "{initial_files}" in DEEP_MAP_FILE_SELECT_PROMPT

    def test_deep_map_file_select_prompt_renders(self):
        rendered = DEEP_MAP_FILE_SELECT_PROMPT.format(
            approach="fix the bug",
            deep_map="backend/app/worker.py\n│ async def run_turn(...)",
            initial_files='["bond/backend/app/worker.py"]',
        )
        assert "fix the bug" in rendered
        assert "worker.py" in rendered
