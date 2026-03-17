"""Tests for native file tools — line-range reads, outline mode, diff editing, say, and respond."""

from __future__ import annotations

import asyncio
import os
import tempfile
import shutil

import pytest

from backend.app.agent.tools.native import (
    handle_file_edit,
    handle_file_read,
    handle_respond,
    handle_say,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace():
    """Create a temporary workspace with test files."""
    tmpdir = tempfile.mkdtemp(prefix="bond_native_tools_")

    # 10-line text file
    with open(os.path.join(tmpdir, "ten_lines.txt"), "w") as f:
        for i in range(1, 11):
            f.write(f"Line {i}\n")

    # Python file with classes and functions
    with open(os.path.join(tmpdir, "example.py"), "w") as f:
        f.write(
            "import os\n"
            "import sys\n"
            "\n"
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "\n"
            "    def baz(self, x: int):\n"
            "        return x\n"
            "\n"
            "async def helper():\n"
            "    pass\n"
            "\n"
            "def standalone():\n"
            "    return 42\n"
        )

    # Plain text file (20 lines)
    with open(os.path.join(tmpdir, "notes.txt"), "w") as f:
        for i in range(1, 21):
            f.write(f"Note line {i}\n")

    # Empty file
    with open(os.path.join(tmpdir, "empty.txt"), "w") as f:
        pass

    # File for editing
    with open(os.path.join(tmpdir, "editable.txt"), "w") as f:
        f.write("Hello World\nfoo bar baz\nGoodbye World\n")

    # File with duplicate content
    with open(os.path.join(tmpdir, "duplicates.txt"), "w") as f:
        f.write("apple\nbanana\napple\n")

    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def _ctx():
    return {"agent_id": "test-agent"}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# file_read: line-range tests
# ---------------------------------------------------------------------------

class TestFileReadLineRange:
    def test_file_read_line_range(self, workspace):
        """Read lines 2-4 of a 10-line file."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read(
            {"path": path, "line_start": 2, "line_end": 4}, _ctx()
        ))
        assert "error" not in result
        assert result["line_start"] == 2
        assert result["line_end"] == 4
        assert result["total_lines"] == 10
        lines = result["content"].splitlines()
        assert len(lines) == 3
        assert lines[0] == "Line 2"
        assert lines[2] == "Line 4"

    def test_file_read_line_start_only(self, workspace):
        """Read from line 5 to end."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read(
            {"path": path, "line_start": 5}, _ctx()
        ))
        assert "error" not in result
        assert result["line_start"] == 5
        assert result["line_end"] == 10
        assert result["total_lines"] == 10
        lines = result["content"].splitlines()
        assert len(lines) == 6
        assert lines[0] == "Line 5"
        assert lines[-1] == "Line 10"

    def test_file_read_line_end_only(self, workspace):
        """Read from start to line 3."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read(
            {"path": path, "line_end": 3}, _ctx()
        ))
        assert "error" not in result
        assert result["line_start"] == 1
        assert result["line_end"] == 3
        assert result["total_lines"] == 10
        lines = result["content"].splitlines()
        assert len(lines) == 3
        assert lines[0] == "Line 1"
        assert lines[2] == "Line 3"

    def test_file_read_total_lines(self, workspace):
        """Verify total_lines is always returned in full read mode."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read({"path": path}, _ctx()))
        assert "error" not in result
        assert result["total_lines"] == 10

    def test_file_read_line_range_clamp(self, workspace):
        """line_end beyond file length clamps to end."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read(
            {"path": path, "line_start": 8, "line_end": 999}, _ctx()
        ))
        assert "error" not in result
        assert result["line_end"] == 10
        lines = result["content"].splitlines()
        assert len(lines) == 3
        assert lines[0] == "Line 8"
        assert lines[-1] == "Line 10"

    def test_file_read_line_start_too_large(self, workspace):
        """line_start exceeding total lines returns error."""
        path = os.path.join(workspace, "ten_lines.txt")
        result = _run(handle_file_read(
            {"path": path, "line_start": 50}, _ctx()
        ))
        assert "error" in result
        assert "exceeds total lines" in result["error"]


# ---------------------------------------------------------------------------
# file_read: outline tests
# ---------------------------------------------------------------------------

class TestFileReadOutline:
    def test_file_read_outline_python(self, workspace):
        """Outline mode on a .py file extracts class/def signatures."""
        path = os.path.join(workspace, "example.py")
        result = _run(handle_file_read(
            {"path": path, "outline": True}, _ctx()
        ))
        assert "error" not in result
        assert "outline" in result
        assert result["total_lines"] > 0
        assert result["size"] > 0

        outline = result["outline"]
        assert len(outline) >= 4

        outline_text = "\n".join(outline)
        assert "class Foo:" in outline_text
        assert "def bar(self):" in outline_text
        assert "def baz(self, x: int):" in outline_text
        assert "async def helper():" in outline_text
        assert "def standalone():" in outline_text

    def test_file_read_outline_non_code(self, workspace):
        """Outline mode on .txt returns head/tail lines."""
        path = os.path.join(workspace, "notes.txt")
        result = _run(handle_file_read(
            {"path": path, "outline": True}, _ctx()
        ))
        assert "error" not in result
        assert "outline" in result
        assert result["total_lines"] == 20

        outline = result["outline"]
        assert any("Note line 1" in line for line in outline)
        assert any("Note line 20" in line for line in outline)
        assert any("omitted" in line for line in outline)


# ---------------------------------------------------------------------------
# file_edit tests
# ---------------------------------------------------------------------------

class TestFileEdit:
    def test_file_edit_single(self, workspace):
        """Single edit replaces text correctly."""
        path = os.path.join(workspace, "editable.txt")
        result = _run(handle_file_edit(
            {"path": path, "edits": [{"old_text": "Hello World", "new_text": "Hi There"}]},
            _ctx(),
        ))
        assert result["status"] == "edited"
        assert result["edits_applied"] == 1

        with open(path) as f:
            content = f.read()
        assert "Hi There" in content
        assert "Hello World" not in content

    def test_file_edit_multiple(self, workspace):
        """Two edits applied sequentially."""
        path = os.path.join(workspace, "editable.txt")
        result = _run(handle_file_edit(
            {
                "path": path,
                "edits": [
                    {"old_text": "Hello World", "new_text": "Hi There"},
                    {"old_text": "Goodbye World", "new_text": "See Ya"},
                ],
            },
            _ctx(),
        ))
        assert result["status"] == "edited"
        assert result["edits_applied"] == 2

        with open(path) as f:
            content = f.read()
        assert "Hi There" in content
        assert "See Ya" in content
        assert "Hello World" not in content
        assert "Goodbye World" not in content

    def test_file_edit_not_found(self, workspace):
        """old_text that doesn't exist returns error."""
        path = os.path.join(workspace, "editable.txt")
        result = _run(handle_file_edit(
            {"path": path, "edits": [{"old_text": "NONEXISTENT", "new_text": "X"}]},
            _ctx(),
        ))
        assert "error" in result
        assert "not found" in result["error"]

    def test_file_edit_ambiguous(self, workspace):
        """old_text matching multiple times returns error."""
        path = os.path.join(workspace, "duplicates.txt")
        result = _run(handle_file_edit(
            {"path": path, "edits": [{"old_text": "apple", "new_text": "orange"}]},
            _ctx(),
        ))
        assert "error" in result
        assert "ambiguous" in result["error"]

    def test_file_edit_empty_file(self, workspace):
        """Editing an empty file with non-matching text returns error."""
        path = os.path.join(workspace, "empty.txt")
        result = _run(handle_file_edit(
            {"path": path, "edits": [{"old_text": "something", "new_text": "else"}]},
            _ctx(),
        ))
        assert "error" in result
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# handle_respond tests
# ---------------------------------------------------------------------------

class TestHandleRespond:
    def test_respond_is_terminal(self):
        """respond sets _terminal to True — ends the agent loop."""
        result = _run(handle_respond({"message": "All done."}, _ctx()))
        assert result.get("_terminal") is True

    def test_respond_message_preserved(self):
        """respond echoes the message verbatim."""
        result = _run(handle_respond({"message": "Task complete!"}, _ctx()))
        assert result["message"] == "Task complete!"

    def test_respond_empty_message_defaults(self):
        """respond with no message argument defaults to empty string."""
        result = _run(handle_respond({}, _ctx()))
        assert result["message"] == ""
        assert result["_terminal"] is True

    def test_respond_no_sse_event(self):
        """respond must not emit an SSE event — it is terminal, not streamed."""
        result = _run(handle_respond({"message": "Done"}, _ctx()))
        assert "_sse_event" not in result

    def test_respond_only_terminal_and_message(self):
        """respond result contains exactly 'message' and '_terminal'."""
        result = _run(handle_respond({"message": "Hi"}, _ctx()))
        assert set(result.keys()) == {"message", "_terminal"}


# ---------------------------------------------------------------------------
# handle_say tests
# ---------------------------------------------------------------------------

class TestHandleSay:
    def test_say_status_is_said(self):
        """say returns status='said'."""
        result = _run(handle_say({"message": "Processing..."}, _ctx()))
        assert result.get("status") == "said"

    def test_say_not_terminal(self):
        """say must NOT set _terminal — the turn must continue."""
        result = _run(handle_say({"message": "Working on step 1..."}, _ctx()))
        assert not result.get("_terminal")

    def test_say_sse_event_present(self):
        """say returns an _sse_event so the backend can stream the message."""
        result = _run(handle_say({"message": "Step 1 done"}, _ctx()))
        assert "_sse_event" in result

    def test_say_sse_event_is_chunk(self):
        """_sse_event uses the 'chunk' event type — consistent with text responses."""
        result = _run(handle_say({"message": "Running tests..."}, _ctx()))
        assert result["_sse_event"]["event"] == "chunk"

    def test_say_sse_data_contains_content(self):
        """_sse_event data has a 'content' key matching the message."""
        msg = "Searching 42 files for pattern..."
        result = _run(handle_say({"message": msg}, _ctx()))
        assert result["_sse_event"]["data"]["content"] == msg

    def test_say_message_verbatim_in_sse(self):
        """The full message, including special characters, is preserved in SSE data."""
        msg = "Found `foo()` in 3 files — reviewing now"
        result = _run(handle_say({"message": msg}, _ctx()))
        assert result["_sse_event"]["data"]["content"] == msg

    def test_say_empty_message_returns_error(self):
        """say with an empty string message returns an error dict."""
        result = _run(handle_say({"message": ""}, _ctx()))
        assert "error" in result
        assert result.get("status") != "said"

    def test_say_missing_message_returns_error(self):
        """say with no 'message' argument returns an error dict."""
        result = _run(handle_say({}, _ctx()))
        assert "error" in result
        assert result.get("status") != "said"

    def test_say_empty_message_not_terminal(self):
        """Even on error, say must not accidentally set _terminal."""
        result = _run(handle_say({"message": ""}, _ctx()))
        assert not result.get("_terminal")

    def test_say_sse_structure_matches_worker_expectation(self):
        """
        Verify the full _sse_event structure worker.py expects:
            result["_sse_event"]["event"]       -> event type string
            result["_sse_event"]["data"]        -> dict with at least "content"
        worker.py line ~1957: _sse_event(sse["event"], sse.get("data", {}))
        """
        result = _run(handle_say({"message": "hello"}, _ctx()))
        sse = result["_sse_event"]
        assert isinstance(sse["event"], str)
        assert isinstance(sse.get("data", None), dict)
        assert "content" in sse["data"]
