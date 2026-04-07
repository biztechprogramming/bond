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
    _execute_single_task,
    _extract_json,
    _plan_to_tasks,
    _should_skip_plan,
    _strip_html_tags,
    _validate_plan,
    build_handoff_context,
    compute_adaptive_budget,
    WORKSPACE_PLAN_SYSTEM_PROMPT,
    DEEP_MAP_FILE_SELECT_PROMPT,
    gather_phase,
    partition_by_dependencies,
    workspace_plan_phase,
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
        assert r.gather_metrics is None


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


# ---------------------------------------------------------------------------
# web_fetch task execution
# ---------------------------------------------------------------------------


class TestWebFetchExecution:
    @pytest.mark.asyncio
    async def test_web_fetch_task(self, monkeypatch):
        """web_fetch task fetches URL and returns body text."""
        import httpx

        mock_response = MagicMock()
        mock_response.text = "Hello world content"
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client)

        task = GatherTask(name="web:http://example.com", task_type="web_fetch",
                          params={"url": "http://example.com"})
        result = await _execute_single_task(task, MagicMock(), {}, "/workspace")
        assert not result.error
        assert "Hello world content" in result.content

    @pytest.mark.asyncio
    async def test_web_fetch_strips_html(self, monkeypatch):
        """web_fetch strips HTML tags when content-type is html."""
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Hello</p><p>World</p></body></html>"
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client)

        task = GatherTask(name="web:http://example.com", task_type="web_fetch",
                          params={"url": "http://example.com"})
        result = await _execute_single_task(task, MagicMock(), {}, "/workspace")
        assert not result.error
        assert "<html>" not in result.content
        assert "Hello" in result.content

    @pytest.mark.asyncio
    async def test_web_fetch_error(self, monkeypatch):
        """web_fetch returns error result on HTTP failure."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: mock_client)

        task = GatherTask(name="web:http://bad.example", task_type="web_fetch",
                          params={"url": "http://bad.example"})
        result = await _execute_single_task(task, MagicMock(), {}, "/workspace")
        assert result.error


class TestStripHtmlTags:
    def test_strips_tags(self):
        assert _strip_html_tags("<p>hello</p>") == "hello"

    def test_collapses_whitespace(self):
        result = _strip_html_tags("<div>  hello  \n  world  </div>")
        assert result == "hello world"

    def test_plain_text_unchanged(self):
        assert _strip_html_tags("no tags here") == "no tags here"


# ---------------------------------------------------------------------------
# _plan_to_tasks with urls_to_fetch
# ---------------------------------------------------------------------------


class TestPlanToTasksWebFetch:
    def test_urls_to_fetch_creates_tasks(self):
        plan = {
            "files_to_read": [],
            "grep_patterns": [],
            "urls_to_fetch": ["http://example.com/a", "http://example.com/b"],
        }
        tasks = _plan_to_tasks(plan)
        web_tasks = [t for t in tasks if t.task_type == "web_fetch"]
        assert len(web_tasks) == 2
        assert web_tasks[0].params["url"] == "http://example.com/a"
        assert web_tasks[1].params["url"] == "http://example.com/b"

    def test_no_urls_to_fetch(self):
        plan = {"files_to_read": ["a.py"], "grep_patterns": []}
        tasks = _plan_to_tasks(plan)
        web_tasks = [t for t in tasks if t.task_type == "web_fetch"]
        assert len(web_tasks) == 0

    def test_mixed_plan(self):
        plan = {
            "files_to_read": ["a.py"],
            "grep_patterns": [{"pattern": "foo", "directory": "."}],
            "urls_to_fetch": ["http://example.com"],
        }
        tasks = _plan_to_tasks(plan)
        assert len(tasks) == 3
        types = {t.task_type for t in tasks}
        assert types == {"file_read", "grep", "web_fetch"}


# ---------------------------------------------------------------------------
# Per-domain concurrency limiting
# ---------------------------------------------------------------------------


class TestDomainConcurrencyLimiting:
    @pytest.mark.asyncio
    async def test_same_domain_serialized(self, monkeypatch):
        """Two web fetches to same domain should run sequentially."""
        call_times = []

        original_execute = _execute_single_task

        async def _tracking_execute(task, reg, ctx, root):
            if task.task_type == "web_fetch":
                call_times.append(("start", task.name, asyncio.get_event_loop().time()))
                await asyncio.sleep(0.05)
                call_times.append(("end", task.name, asyncio.get_event_loop().time()))
                return GatherResult(task_name=task.name, content="ok", tokens=2)
            return await original_execute(task, reg, ctx, root)

        monkeypatch.setattr(
            "backend.app.agent.pre_gather._execute_single_task", _tracking_execute
        )

        plan = {
            "files_to_read": [],
            "grep_patterns": [],
            "urls_to_fetch": [
                "http://same.example.com/a",
                "http://same.example.com/b",
            ],
        }
        context, metrics = await gather_phase(plan, MagicMock(), {}, "/workspace")

        # Second task should start after first ends (serialized)
        starts = [(n, t) for ev, n, t in call_times if ev == "start"]
        ends = [(n, t) for ev, n, t in call_times if ev == "end"]
        assert len(starts) == 2
        # The second start should be >= first end (serialized by domain semaphore)
        first_end = ends[0][1]
        second_start = starts[1][1]
        assert second_start >= first_end - 0.001  # small tolerance

    @pytest.mark.asyncio
    async def test_different_domains_parallel(self, monkeypatch):
        """Web fetches to different domains can run in parallel."""
        active_count = []
        active = 0

        original_execute = _execute_single_task

        async def _tracking_execute(task, reg, ctx, root):
            nonlocal active
            if task.task_type == "web_fetch":
                active += 1
                active_count.append(active)
                await asyncio.sleep(0.05)
                active -= 1
                return GatherResult(task_name=task.name, content="ok", tokens=2)
            return await original_execute(task, reg, ctx, root)

        monkeypatch.setattr(
            "backend.app.agent.pre_gather._execute_single_task", _tracking_execute
        )

        plan = {
            "files_to_read": [],
            "grep_patterns": [],
            "urls_to_fetch": [
                "http://alpha.example.com/a",
                "http://beta.example.com/b",
            ],
        }
        context, metrics = await gather_phase(plan, MagicMock(), {}, "/workspace")

        # Both should have been active simultaneously at some point
        assert max(active_count) == 2


# ---------------------------------------------------------------------------
# workspace_plan_phase — LLM call kwargs inspection
# ---------------------------------------------------------------------------


class TestWorkspacePlanPhaseCallKwargs:
    """Verify the exact kwargs sent to litellm.acompletion by workspace_plan_phase.

    These tests capture the call_kwargs to ensure no unexpected keys (like
    `tools` or `tool_choice`) leak into the plan phase LLM call, which could
    cause Anthropic to reject the request (potentially misclassified as
    rate_limit_error).
    """

    @pytest.mark.asyncio
    async def test_call_kwargs_no_tools_or_tool_choice(self, monkeypatch):
        """The plan phase must NOT send tools or tool_choice to litellm."""
        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "simple"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        # Disable OAuth prefix (no OAuth token)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        await workspace_plan_phase(
            user_message="Fix the bug in worker.py",
            history=[],
            workspace_overview="=== bond/ (git) ===\n  src/",
            model="claude-sonnet-4-20250514",
        )

        # KEY ASSERTION: no tools or tool_choice in the call
        assert "tools" not in captured, (
            "tools should NOT be in plan phase call_kwargs — "
            "this would cause Anthropic to reject the request"
        )
        assert "tool_choice" not in captured, (
            "tool_choice should NOT be in plan phase call_kwargs"
        )

    @pytest.mark.asyncio
    async def test_call_kwargs_basic_structure(self, monkeypatch):
        """Verify the expected keys are present in the call."""
        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "moderate"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        await workspace_plan_phase(
            user_message="Refactor the settings page",
            history=[],
            workspace_overview="=== bond/ (git) ===",
            model="claude-sonnet-4-20250514",
            api_key="sk-test-key",
        )

        assert captured["model"] == "claude-sonnet-4-20250514"
        assert captured["temperature"] == 0.3
        assert captured["max_tokens"] == 2000
        assert captured["api_key"] == "sk-test-key"
        assert len(captured["messages"]) == 2  # system + user
        assert captured["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_llm_kwargs_spread_only_adds_filtered_keys(self, monkeypatch):
        """Extra kwargs (like api_base) should pass through, but not override existing keys."""
        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "simple"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        # Simulate plan_llm_kwargs from pre_gather_integration (filtered keys only)
        await workspace_plan_phase(
            user_message="Fix the bug in the worker module please",
            history=[],
            workspace_overview="=== bond/ ===",
            model="claude-sonnet-4-20250514",
            api_base="https://custom.api.example.com",
            extra_headers={"X-Custom": "value"},
        )

        assert captured.get("api_base") == "https://custom.api.example.com"
        assert captured.get("extra_headers") == {"X-Custom": "value"}
        # Still no tools
        assert "tools" not in captured
        assert "tool_choice" not in captured

    @pytest.mark.asyncio
    async def test_llm_kwargs_cannot_override_model_or_messages(self, monkeypatch):
        """The spread should NOT override keys already in call_kwargs."""
        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "simple"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        # Try to inject 'model' via llm_kwargs — should be ignored
        await workspace_plan_phase(
            user_message="Fix the bug in the worker module please",
            history=[],
            workspace_overview="=== bond/ ===",
            model="claude-sonnet-4-20250514",
            temperature=999,  # should NOT override
        )

        assert captured["model"] == "claude-sonnet-4-20250514"
        assert captured["temperature"] == 0.3  # original, not overridden

    @pytest.mark.asyncio
    async def test_rate_limit_error_handling(self, monkeypatch):
        """RateLimitError should be caught and return None."""
        import litellm as litellm_mod

        async def fake_acompletion(**kwargs):
            raise litellm_mod.RateLimitError(
                message="AnthropicException - rate_limit_error",
                model="claude-sonnet-4-20250514",
                llm_provider="anthropic",
            )

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        result = await workspace_plan_phase(
            user_message="Fix the bug in worker.py",
            history=[],
            workspace_overview="=== bond/ ===",
            model="claude-sonnet-4-20250514",
        )

        # Should gracefully return None, not raise
        assert result is None

    @pytest.mark.asyncio
    async def test_oauth_prefix_applied_for_oauth_token(self, monkeypatch):
        """When api_key is an OAuth token, ensure_oauth_system_prefix should be called."""
        captured_messages = {}

        async def fake_acompletion(**kwargs):
            captured_messages["messages"] = kwargs["messages"]
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "simple"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        # Use the REAL ensure_oauth_system_prefix — don't mock it

        result = await workspace_plan_phase(
            user_message="Fix the bug in the worker module please",
            history=[],
            workspace_overview="=== bond/ ===",
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-oat-fake-oauth-token",
            extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        )

        # OAuth prefix should have been prepended to system message
        from backend.app.core.oauth import OAUTH_SYSTEM_PROMPT_PREFIX
        system_content = captured_messages["messages"][0]["content"]
        assert OAUTH_SYSTEM_PROMPT_PREFIX in system_content

    @pytest.mark.asyncio
    async def test_history_truncation(self, monkeypatch):
        """History messages should be truncated to last 6 and content capped at 500 chars."""
        captured = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [
                MagicMock(message=MagicMock(content='{"complexity": "simple"}'))
            ]
            return mock_resp

        monkeypatch.setattr("backend.app.agent.pre_gather.litellm.acompletion", fake_acompletion)
        monkeypatch.setattr(
            "backend.app.core.oauth.ensure_oauth_system_prefix",
            lambda msgs, **kw: msgs,
        )

        long_history = [
            {"role": "user", "content": "x" * 1000}
            for _ in range(10)
        ]

        await workspace_plan_phase(
            user_message="Fix the bug in the worker module please",
            history=long_history,
            workspace_overview="=== bond/ ===",
            model="claude-sonnet-4-20250514",
        )

        messages = captured["messages"]
        # system + up to 6 history + user = at most 8
        assert len(messages) <= 8
        # History content should be truncated to 500 chars
        for msg in messages[1:-1]:  # skip system and final user
            assert len(msg["content"]) <= 500
