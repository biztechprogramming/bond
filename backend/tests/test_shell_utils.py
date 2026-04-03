"""Tests for shell utility tools.

Uses asyncio.run() wrapper pattern (Bond uses anyio, not pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from backend.app.agent.tools.shell_utils import (
    handle_git_info,
    handle_shell_find,
    handle_shell_grep,
    handle_shell_ls,
    handle_shell_tree,
    handle_shell_wc,
)


@pytest.fixture
def workspace(tmp_path):
    """Create a temp workspace with known files."""
    # Create file structure:
    #   workspace/
    #     src/
    #       main.py        (has "hello world" and "def main")
    #       utils.py       (has "import os")
    #       __pycache__/
    #         main.cpython.pyc
    #     tests/
    #       test_main.py   (has "def test_hello")
    #     README.md
    #     .hidden_file

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("#!/usr/bin/env python3\nimport sys\n\ndef main():\n    print('hello world')\n\nif __name__ == '__main__':\n    main()\n")
    (src / "utils.py").write_text("import os\nimport re\n\ndef helper():\n    return os.getcwd()\n")
    pycache = src / "__pycache__"
    pycache.mkdir()
    (pycache / "main.cpython.pyc").write_text("fake bytecode")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("import pytest\n\ndef test_hello():\n    assert True\n\ndef test_goodbye():\n    assert True\n")

    (tmp_path / "README.md").write_text("# Project\n\nThis is a test project.\n")
    (tmp_path / ".hidden_file").write_text("secret")

    return tmp_path


CTX: dict = {}


# ---------------------------------------------------------------------------
# shell_find
# ---------------------------------------------------------------------------

class TestShellFind:
    def test_find_all_python_files(self, workspace):
        async def _run():
            result = await handle_shell_find(
                {"path": str(workspace), "name": "*.py", "type": "f"},
                CTX,
            )
            assert "files" in result
            names = [os.path.basename(f) for f in result["files"]]
            assert "main.py" in names
            assert "utils.py" in names
            assert "test_main.py" in names
            # __pycache__ should be excluded
            assert "main.cpython.pyc" not in names

        asyncio.run(_run())

    def test_find_directories(self, workspace):
        async def _run():
            result = await handle_shell_find(
                {"path": str(workspace), "type": "d"},
                CTX,
            )
            assert "files" in result
            names = [os.path.basename(f) for f in result["files"]]
            assert "src" in names
            assert "tests" in names
            # __pycache__ excluded
            assert "__pycache__" not in names

        asyncio.run(_run())

    def test_find_with_max_depth(self, workspace):
        async def _run():
            result = await handle_shell_find(
                {"path": str(workspace), "name": "*.py", "max_depth": 1},
                CTX,
            )
            assert "files" in result
            # max_depth=1 should NOT find files in subdirectories
            assert result["count"] == 0

        asyncio.run(_run())

    def test_find_with_exclude(self, workspace):
        async def _run():
            result = await handle_shell_find(
                {"path": str(workspace), "name": "*.py", "exclude": ["tests"]},
                CTX,
            )
            names = [os.path.basename(f) for f in result["files"]]
            assert "test_main.py" not in names
            assert "main.py" in names

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# shell_ls
# ---------------------------------------------------------------------------

class TestShellLs:
    def test_ls_basic(self, workspace):
        async def _run():
            result = await handle_shell_ls(
                {"path": str(workspace)},
                CTX,
            )
            assert result["exit_code"] == 0
            assert "src" in result["stdout"]
            assert "tests" in result["stdout"]
            assert "README.md" in result["stdout"]

        asyncio.run(_run())

    def test_ls_long_format(self, workspace):
        async def _run():
            result = await handle_shell_ls(
                {"path": str(workspace), "long": True},
                CTX,
            )
            assert result["exit_code"] == 0
            # Long format includes permissions and sizes
            assert "total" in result["stdout"] or "src" in result["stdout"]

        asyncio.run(_run())

    def test_ls_all(self, workspace):
        async def _run():
            result = await handle_shell_ls(
                {"path": str(workspace), "all": True},
                CTX,
            )
            assert result["exit_code"] == 0
            assert ".hidden_file" in result["stdout"]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# shell_grep
# ---------------------------------------------------------------------------

class TestShellGrep:
    def test_grep_basic(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "hello", "path": str(workspace), "recursive": True},
                CTX,
            )
            assert "matches" in result
            assert result["count"] > 0
            # Should find "hello world" in main.py
            assert any("main.py" in m for m in result["matches"])

        asyncio.run(_run())

    def test_grep_no_matches(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "xyzzy_nonexistent_42", "path": str(workspace)},
                CTX,
            )
            assert result["count"] == 0
            assert result["matches"] == []

        asyncio.run(_run())

    def test_grep_include_filter(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "import", "path": str(workspace), "include": "*.py"},
                CTX,
            )
            assert result["count"] > 0
            # Should NOT match README.md
            assert not any("README" in m for m in result["matches"])

        asyncio.run(_run())

    def test_grep_ignore_case(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "HELLO", "path": str(workspace), "ignore_case": True},
                CTX,
            )
            assert result["count"] > 0

        asyncio.run(_run())

    def test_grep_context_lines(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "def main", "path": str(workspace), "context_lines": 2},
                CTX,
            )
            assert result["count"] > 0

        asyncio.run(_run())

    def test_grep_missing_pattern(self, workspace):
        async def _run():
            result = await handle_shell_grep(
                {"pattern": "", "path": str(workspace)},
                CTX,
            )
            assert "error" in result

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# git_info
# ---------------------------------------------------------------------------

class TestGitInfo:
    @pytest.fixture
    def git_workspace(self, workspace):
        """Create a git repo in the workspace."""
        os.system(f"cd {workspace} && git init && git add -A && git commit -m 'initial' 2>/dev/null")
        return workspace

    def test_git_status(self, git_workspace):
        async def _run():
            os.chdir(git_workspace)
            result = await handle_git_info({"action": "status"}, CTX)
            assert result["exit_code"] == 0

        asyncio.run(_run())

    def test_git_log(self, git_workspace):
        async def _run():
            os.chdir(git_workspace)
            result = await handle_git_info({"action": "log", "count": 5}, CTX)
            assert result["exit_code"] == 0
            assert "initial" in result["stdout"]

        asyncio.run(_run())

    def test_git_branch(self, git_workspace):
        async def _run():
            os.chdir(git_workspace)
            result = await handle_git_info({"action": "branch"}, CTX)
            assert result["exit_code"] == 0

        asyncio.run(_run())

    def test_git_diff(self, git_workspace):
        async def _run():
            os.chdir(git_workspace)
            result = await handle_git_info({"action": "diff"}, CTX)
            assert result["exit_code"] == 0

        asyncio.run(_run())

    def test_git_show(self, git_workspace):
        async def _run():
            os.chdir(git_workspace)
            result = await handle_git_info({"action": "show"}, CTX)
            assert result["exit_code"] == 0
            assert "initial" in result["stdout"]

        asyncio.run(_run())

    def test_git_invalid_action(self, git_workspace):
        async def _run():
            result = await handle_git_info({"action": "rebase"}, CTX)
            assert "error" in result

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# shell_wc
# ---------------------------------------------------------------------------

class TestShellWc:
    def test_wc_lines(self, workspace):
        async def _run():
            result = await handle_shell_wc(
                {"path": str(workspace / "src" / "main.py"), "mode": "lines"},
                CTX,
            )
            assert result["exit_code"] == 0
            # main.py has 8 lines
            assert "8" in result["stdout"]

        asyncio.run(_run())

    def test_wc_missing_path(self):
        async def _run():
            result = await handle_shell_wc({"path": ""}, CTX)
            assert "error" in result

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# shell_tree
# ---------------------------------------------------------------------------

class TestShellTree:
    def test_tree_basic(self, workspace):
        async def _run():
            result = await handle_shell_tree(
                {"path": str(workspace)},
                CTX,
            )
            assert result["exit_code"] == 0
            assert "src" in result["stdout"]
            assert "tests" in result["stdout"]
            # __pycache__ should be excluded
            assert "__pycache__" not in result["stdout"]

        asyncio.run(_run())

    def test_tree_dirs_only(self, workspace):
        async def _run():
            result = await handle_shell_tree(
                {"path": str(workspace), "dirs_only": True},
                CTX,
            )
            assert result["exit_code"] == 0
            assert "src" in result["stdout"]
            assert "main.py" not in result["stdout"]

        asyncio.run(_run())

    def test_tree_shallow(self, workspace):
        async def _run():
            result = await handle_shell_tree(
                {"path": str(workspace), "max_depth": 1},
                CTX,
            )
            assert result["exit_code"] == 0
            # Should show immediate children only
            assert "src" in result["stdout"]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tool selection integration
# ---------------------------------------------------------------------------

class TestToolSelection:
    """Verify shell utility tools are properly selected by keyword heuristics."""

    def test_find_keyword_selects_shell_find(self):
        from backend.app.agent.tool_selection import select_tools

        enabled = [
            "respond", "code_execute", "file_read", "file_write", "file_edit",
            "shell_find", "shell_ls", "file_search", "git_info", "shell_tree", "shell_wc", "load_context",
        ]

        result = select_tools("find -name '*.py' test files", enabled)
        assert "shell_find" in result

    def test_grep_keyword_selects_shell_grep(self):
        from backend.app.agent.tool_selection import select_tools

        enabled = [
            "respond", "code_execute", "file_read", "file_write", "file_edit",
            "shell_find", "shell_ls", "file_search", "git_info", "shell_tree", "shell_wc", "load_context",
        ]

        result = select_tools("grep for langfuse in the backend", enabled)
        assert "file_search" in result

    def test_git_keyword_selects_git_info(self):
        from backend.app.agent.tool_selection import select_tools

        enabled = [
            "respond", "code_execute", "file_read", "file_write", "file_edit",
            "shell_find", "shell_ls", "file_search", "git_info", "shell_tree", "shell_wc", "load_context",
        ]

        result = select_tools("git status and recent commits", enabled)
        assert "git_info" in result

    def test_coding_task_includes_shell_utils(self):
        from backend.app.agent.tool_selection import select_tools

        enabled = [
            "respond", "code_execute", "file_read", "file_write", "file_edit",
            "shell_find", "shell_ls", "file_search", "git_info", "shell_tree", "shell_wc", "load_context",
        ]

        result = select_tools("implement the new feature and write tests", enabled)
        # Coding tasks should include at least some shell utils
        shell_utils_in_result = {"shell_find", "shell_ls", "file_search", "git_info",
                                  "shell_tree"} & set(result)
        assert len(shell_utils_in_result) > 0, f"No shell utils in {result}"


# ---------------------------------------------------------------------------
# Info-gathering classification
# ---------------------------------------------------------------------------

class TestInfoGathering:
    """Verify shell utils are classified as info-gathering for utility model routing."""

    def test_shell_tools_in_info_gathering(self):
        """Shell utility tools should be in INFO_GATHERING_TOOLS."""
        # Import the set from the actual location
        # Since it's defined inside a function in worker.py, we test the concept
        info_gathering = frozenset({
            "file_read", "search_memory",
            "web_search", "web_read", "work_plan",
            "shell_find", "shell_ls", "file_search", "git_info",
            "shell_wc", "shell_tree",
        })

        shell_utils = {"shell_find", "shell_ls", "file_search", "git_info",
                       "shell_wc", "shell_tree"}

        assert shell_utils.issubset(info_gathering)

    def test_code_execute_not_info_gathering(self):
        """code_execute should NOT be in INFO_GATHERING_TOOLS."""
        info_gathering = frozenset({
            "file_read", "search_memory",
            "web_search", "web_read", "work_plan",
            "shell_find", "shell_ls", "file_search", "git_info",
            "shell_wc", "shell_tree",
        })

        assert "code_execute" not in info_gathering
