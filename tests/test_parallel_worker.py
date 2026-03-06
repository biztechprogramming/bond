"""Tests for the parallel worker pool and tool classification."""

from __future__ import annotations

import asyncio
import pytest

from backend.app.agent.parallel_worker import (
    classify_tool_call,
    _is_read_only_command,
    ParallelWorkerPool,
    format_parallel_summary,
    ALWAYS_PARALLEL_SAFE,
    ALWAYS_CONSEQUENTIAL,
)


# ── Tool Classification Tests ─────────────────────────────────────────────────


class TestClassifyToolCall:
    """Tests for classify_tool_call()."""

    def test_file_read_is_parallel(self):
        assert classify_tool_call("file_read", {"path": "foo.py"}) == "parallel"

    def test_web_search_is_parallel(self):
        assert classify_tool_call("web_search", {"query": "test"}) == "parallel"

    def test_web_read_is_parallel(self):
        assert classify_tool_call("web_read", {"url": "https://example.com"}) == "parallel"

    def test_search_memory_is_parallel(self):
        assert classify_tool_call("search_memory", {"query": "test"}) == "parallel"

    def test_load_context_is_parallel(self):
        assert classify_tool_call("load_context", {}) == "parallel"

    def test_file_write_is_consequential(self):
        assert classify_tool_call("file_write", {"path": "foo.py", "content": "x"}) == "consequential"

    def test_file_edit_is_consequential(self):
        assert classify_tool_call("file_edit", {"path": "foo.py", "edits": []}) == "consequential"

    def test_respond_is_consequential(self):
        assert classify_tool_call("respond", {"message": "hi"}) == "consequential"

    def test_memory_save_is_consequential(self):
        assert classify_tool_call("memory_save", {"content": "x"}) == "consequential"

    def test_work_plan_is_consequential(self):
        assert classify_tool_call("work_plan", {"action": "create"}) == "consequential"

    def test_unknown_tool_is_consequential(self):
        assert classify_tool_call("unknown_tool", {}) == "consequential"

    def test_code_execute_grep_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "grep -rn 'pattern' src/",
        }) == "parallel"

    def test_code_execute_ls_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "ls -la /workspace",
        }) == "parallel"

    def test_code_execute_cat_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "cat README.md",
        }) == "parallel"

    def test_code_execute_git_log_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "git log --oneline -5",
        }) == "parallel"

    def test_code_execute_find_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "find . -name '*.py' -type f",
        }) == "parallel"

    def test_code_execute_pip_install_is_consequential(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "pip install requests",
        }) == "consequential"

    def test_code_execute_git_commit_is_consequential(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "git commit -m 'test'",
        }) == "consequential"

    def test_code_execute_rm_is_consequential(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "rm -rf /tmp/test",
        }) == "consequential"

    def test_code_execute_redirect_is_consequential(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "echo 'hello' > output.txt",
        }) == "consequential"

    def test_code_execute_python_is_conservative(self):
        """Python code_execute defaults to consequential for safety."""
        assert classify_tool_call("code_execute", {
            "language": "python",
            "code": "import os\nos.listdir('.')",
        }) == "consequential"

    def test_code_execute_python_short_print_is_parallel(self):
        """Short python scripts with print() can be parallel."""
        assert classify_tool_call("code_execute", {
            "language": "python",
            "code": "print(2 + 2)",
        }) == "parallel"

    def test_code_execute_chained_reads_is_parallel(self):
        assert classify_tool_call("code_execute", {
            "language": "shell",
            "code": "cat foo.py && grep -n 'def' bar.py",
        }) == "parallel"


# ── Read-only Command Detection Tests ─────────────────────────────────────────


class TestIsReadOnlyCommand:
    """Tests for _is_read_only_command()."""

    def test_empty_string(self):
        assert _is_read_only_command("") is False

    def test_cat(self):
        assert _is_read_only_command("cat foo.txt") is True

    def test_head(self):
        assert _is_read_only_command("head -20 foo.txt") is True

    def test_grep(self):
        assert _is_read_only_command("grep -rn 'pattern' src/") is True

    def test_ls(self):
        assert _is_read_only_command("ls -la") is True

    def test_find(self):
        assert _is_read_only_command("find . -name '*.py'") is True

    def test_git_log(self):
        assert _is_read_only_command("git log --oneline") is True

    def test_git_status(self):
        assert _is_read_only_command("git status") is True

    def test_git_diff(self):
        assert _is_read_only_command("git diff HEAD") is True

    def test_wc(self):
        assert _is_read_only_command("wc -l foo.txt") is True

    def test_rm(self):
        assert _is_read_only_command("rm foo.txt") is False

    def test_pip_install(self):
        assert _is_read_only_command("pip install foo") is False

    def test_git_commit(self):
        assert _is_read_only_command("git commit -m 'x'") is False

    def test_git_push(self):
        assert _is_read_only_command("git push origin main") is False

    def test_redirect(self):
        assert _is_read_only_command("echo hello > file.txt") is False

    def test_sed_inplace(self):
        assert _is_read_only_command("sed -i 's/foo/bar/' file.txt") is False

    def test_sed_print_only(self):
        assert _is_read_only_command("sed -n '1,10p' file.txt") is True

    def test_multiline_all_reads(self):
        assert _is_read_only_command("cat foo.txt\ngrep bar baz.txt") is True

    def test_multiline_with_mutation(self):
        assert _is_read_only_command("cat foo.txt\nrm baz.txt") is False

    def test_chained_reads(self):
        assert _is_read_only_command("cat foo.txt && grep bar baz.txt") is True

    def test_mkdir(self):
        assert _is_read_only_command("mkdir -p /tmp/test") is False

    def test_docker(self):
        assert _is_read_only_command("docker run ubuntu") is False

    def test_tree(self):
        assert _is_read_only_command("tree src/") is True

    def test_jq(self):
        assert _is_read_only_command("jq '.name' package.json") is True


# ── Parallel Worker Pool Tests ────────────────────────────────────────────────


class MockRegistry:
    """Mock ToolRegistry for testing."""

    def __init__(self, results: dict[str, Any] | None = None, delay: float = 0.0):
        self.results = results or {}
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, arguments: dict, context: dict) -> dict:
        self.calls.append((name, arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        if name in self.results:
            return self.results[name]
        return {"content": f"mock result for {name}", "path": arguments.get("path", "")}


from typing import Any


class TestParallelWorkerPool:
    """Tests for ParallelWorkerPool."""

    @pytest.mark.asyncio
    async def test_all_parallel_calls_execute_concurrently(self):
        """Multiple parallel-safe calls should execute concurrently."""
        registry = MockRegistry(delay=0.1)
        pool = ParallelWorkerPool(
            registry=registry,
            utility_model="test-model",
            utility_kwargs={},
            context={},
        )
        tool_calls = [
            {"tool_call_id": "tc1", "tool_name": "file_read", "arguments": {"path": "a.py"}},
            {"tool_call_id": "tc2", "tool_name": "file_read", "arguments": {"path": "b.py"}},
            {"tool_call_id": "tc3", "tool_name": "file_read", "arguments": {"path": "c.py"}},
        ]
        parallel_results, consequential = await pool.execute(tool_calls)

        assert len(parallel_results) == 3
        assert len(consequential) == 0
        assert len(registry.calls) == 3

    @pytest.mark.asyncio
    async def test_consequential_calls_not_executed(self):
        """Consequential calls should be returned for sequential execution."""
        registry = MockRegistry()
        pool = ParallelWorkerPool(
            registry=registry,
            utility_model="test-model",
            utility_kwargs={},
            context={},
        )
        tool_calls = [
            {"tool_call_id": "tc1", "tool_name": "file_read", "arguments": {"path": "a.py"}},
            {"tool_call_id": "tc2", "tool_name": "file_write", "arguments": {"path": "b.py", "content": "x"}},
        ]
        parallel_results, consequential = await pool.execute(tool_calls)

        assert len(parallel_results) == 1
        assert len(consequential) == 1
        assert consequential[0]["tool_name"] == "file_write"

    @pytest.mark.asyncio
    async def test_mixed_calls_classification(self):
        """Mix of parallel and consequential calls should be properly split."""
        registry = MockRegistry()
        pool = ParallelWorkerPool(
            registry=registry,
            utility_model="test-model",
            utility_kwargs={},
            context={},
        )
        tool_calls = [
            {"tool_call_id": "tc1", "tool_name": "file_read", "arguments": {"path": "a.py"}},
            {"tool_call_id": "tc2", "tool_name": "web_search", "arguments": {"query": "test"}},
            {"tool_call_id": "tc3", "tool_name": "file_edit", "arguments": {"path": "b.py", "edits": []}},
            {"tool_call_id": "tc4", "tool_name": "search_memory", "arguments": {"query": "test"}},
        ]
        parallel_results, consequential = await pool.execute(tool_calls)

        assert len(parallel_results) == 3  # file_read, web_search, search_memory
        assert len(consequential) == 1  # file_edit

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Workers that time out should return error results."""
        async def slow_execute(name, args, ctx):
            await asyncio.sleep(5)
            return {"content": "should not reach"}

        registry = MockRegistry(delay=5.0)
        pool = ParallelWorkerPool(
            registry=registry,
            utility_model="test-model",
            utility_kwargs={},
            context={},
            timeout_per_worker=0.1,
        )
        tool_calls = [
            {"tool_call_id": "tc1", "tool_name": "file_read", "arguments": {"path": "a.py"}},
        ]
        parallel_results, _ = await pool.execute(tool_calls)

        assert len(parallel_results) == 1
        assert parallel_results[0]["status"] == "timeout"
        assert "error" in parallel_results[0]["result"]

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Semaphore should limit concurrent workers."""
        max_concurrent = 0
        current_concurrent = 0

        original_registry = MockRegistry()

        class TrackingRegistry:
            async def execute(self, name, args, ctx):
                nonlocal max_concurrent, current_concurrent
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
                await asyncio.sleep(0.05)
                current_concurrent -= 1
                return {"content": "ok"}

        pool = ParallelWorkerPool(
            registry=TrackingRegistry(),
            utility_model="test-model",
            utility_kwargs={},
            context={},
            max_workers=2,
        )
        tool_calls = [
            {"tool_call_id": f"tc{i}", "tool_name": "file_read", "arguments": {"path": f"{i}.py"}}
            for i in range(5)
        ]
        await pool.execute(tool_calls)

        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_results_have_tool_call_ids(self):
        """Each result should have the correct tool_call_id."""
        registry = MockRegistry()
        pool = ParallelWorkerPool(
            registry=registry,
            utility_model="test-model",
            utility_kwargs={},
            context={},
        )
        tool_calls = [
            {"tool_call_id": "abc123", "tool_name": "file_read", "arguments": {"path": "a.py"}},
            {"tool_call_id": "def456", "tool_name": "web_search", "arguments": {"query": "test"}},
        ]
        parallel_results, _ = await pool.execute(tool_calls)

        ids = {r["tool_call_id"] for r in parallel_results}
        assert "abc123" in ids
        assert "def456" in ids


# ── Summary Formatting Tests ──────────────────────────────────────────────────


class TestFormatParallelSummary:
    def test_empty(self):
        assert "No parallel results" in format_parallel_summary([])

    def test_single_result(self):
        summary = format_parallel_summary([{
            "tool_name": "file_read",
            "elapsed": 0.5,
            "status": "success",
        }])
        assert "file_read" in summary
        assert "0.50s" in summary

    def test_multiple_results_shows_savings(self):
        summary = format_parallel_summary([
            {"tool_name": "file_read", "elapsed": 0.5, "status": "success"},
            {"tool_name": "web_search", "elapsed": 0.8, "status": "success"},
            {"tool_name": "search_memory", "elapsed": 0.3, "status": "success"},
        ])
        assert "saved" in summary
        assert "3 workers" in summary
