"""Tests for backend.app.agent.pre_gather — Plan/Gather phases."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.agent.pre_gather import (
    GatherMetrics,
    GatherResult,
    GatherTask,
    _extract_json,
    _should_skip_plan,
    _validate_plan,
    build_handoff_context,
    compute_adaptive_budget,
    gather_phase,
    partition_by_dependencies,
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
        assert r.gather_metrics is None


# ---------------------------------------------------------------------------
# GatherTask
# ---------------------------------------------------------------------------


class TestGatherTask:
    def test_defaults(self):
        t = GatherTask(name="test", task_type="file_read")
        assert t.params == {}
        assert t.depends_on == []
        assert t.priority == 0

    def test_with_values(self):
        t = GatherTask(
            name="read:foo",
            task_type="file_read",
            params={"path": "foo.py"},
            depends_on=["other"],
            priority=5,
        )
        assert t.depends_on == ["other"]
        assert t.priority == 5


# ---------------------------------------------------------------------------
# GatherMetrics
# ---------------------------------------------------------------------------


class TestGatherMetrics:
    def test_dataclass(self):
        m = GatherMetrics(
            total_tasks=10, parallel_tasks=8, sequential_tasks=2,
            wall_clock_ms=500, sequential_equivalent_ms=2000,
            speedup=4.0, tasks_timed_out=1, tasks_failed=2,
        )
        assert m.speedup == 4.0
        assert m.total_tasks == 10


# ---------------------------------------------------------------------------
# partition_by_dependencies
# ---------------------------------------------------------------------------


class TestPartitionByDependencies:
    def test_all_independent(self):
        tasks = [
            GatherTask(name="a", task_type="file_read"),
            GatherTask(name="b", task_type="grep"),
        ]
        ind, dep = partition_by_dependencies(tasks)
        assert len(ind) == 2
        assert len(dep) == 0

    def test_all_dependent(self):
        tasks = [
            GatherTask(name="a", task_type="file_read", depends_on=["x"]),
            GatherTask(name="b", task_type="grep", depends_on=["y"]),
        ]
        ind, dep = partition_by_dependencies(tasks)
        assert len(ind) == 0
        assert len(dep) == 2

    def test_mixed(self):
        tasks = [
            GatherTask(name="a", task_type="file_read"),
            GatherTask(name="b", task_type="grep", depends_on=["a"]),
            GatherTask(name="c", task_type="file_read"),
        ]
        ind, dep = partition_by_dependencies(tasks)
        assert [t.name for t in ind] == ["a", "c"]
        assert [t.name for t in dep] == ["b"]

    def test_empty(self):
        ind, dep = partition_by_dependencies([])
        assert ind == []
        assert dep == []


# ---------------------------------------------------------------------------
# Parallel gather_phase
# ---------------------------------------------------------------------------


def _make_tool_registry(file_contents: dict[str, str] | None = None):
    """Create a mock tool registry that returns file contents."""
    contents = file_contents or {}

    async def _file_read(params, ctx):
        path = params.get("path", "")
        if path in contents:
            return contents[path]
        return f"[content of {path}]"

    registry = MagicMock()
    registry.get.return_value = AsyncMock(side_effect=_file_read)
    return registry


class TestParallelGatherPhase:
    @pytest.mark.asyncio
    async def test_independent_tasks_run(self):
        plan = {
            "files_to_read": ["a.py", "b.py"],
            "grep_patterns": [],
        }
        registry = _make_tool_registry({"a.py": "aaa", "b.py": "bbb"})
        context, metrics = await gather_phase(plan, registry, {}, "/workspace")
        assert "aaa" in context
        assert "bbb" in context
        assert metrics is not None
        assert metrics.total_tasks == 2
        assert metrics.parallel_tasks == 2
        assert metrics.tasks_failed == 0

    @pytest.mark.asyncio
    async def test_empty_plan(self):
        context, metrics = await gather_phase(
            {"files_to_read": [], "grep_patterns": []},
            MagicMock(), {}, "/workspace",
        )
        assert context == ""
        assert metrics is None

    @pytest.mark.asyncio
    async def test_cancellation(self):
        cancel = asyncio.Event()
        cancel.set()  # Already cancelled
        plan = {"files_to_read": ["a.py"], "grep_patterns": []}
        registry = _make_tool_registry()
        context, metrics = await gather_phase(
            plan, registry, {}, "/workspace", cancellation_event=cancel,
        )
        assert context == ""

    @pytest.mark.asyncio
    async def test_timeout_handling(self, monkeypatch):
        monkeypatch.setattr("backend.app.agent.pre_gather.PARALLEL_TASK_TIMEOUT", 0.01)

        async def _slow_read(params, ctx):
            await asyncio.sleep(5)
            return "never"

        registry = MagicMock()
        registry.get.return_value = AsyncMock(side_effect=_slow_read)

        plan = {"files_to_read": ["slow.py"], "grep_patterns": []}
        context, metrics = await gather_phase(plan, registry, {}, "/workspace")
        assert metrics is not None
        assert metrics.tasks_timed_out == 1
        assert metrics.tasks_failed == 1

    @pytest.mark.asyncio
    async def test_sequential_fallback(self, monkeypatch):
        monkeypatch.setattr("backend.app.agent.pre_gather.PARALLEL_GATHERING_ENABLED", False)
        plan = {"files_to_read": ["a.py"], "grep_patterns": []}
        registry = _make_tool_registry({"a.py": "content_a"})
        context, metrics = await gather_phase(plan, registry, {}, "/workspace")
        assert "content_a" in context
        assert metrics is None  # No metrics in sequential mode
