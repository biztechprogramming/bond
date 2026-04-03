"""Tests for iteration-aware file result compression."""

import json
import pytest
from backend.app.agent.context_compression import (
    compress_file_results,
    COMPRESSION_ITERATION_THRESHOLD,
    _is_file_read_result,
    _is_error_result,
    _is_already_compressed,
    _extract_file_path,
    _build_file_summary,
)


def _tool_msg(content: str, tool_call_id: str = "tc1") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _user_msg(content: str = "do something") -> dict:
    return {"role": "user", "content": content}


def _assistant_msg(content: str = "ok", tool_calls: list | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _file_read_content(path: str = "src/foo.py", lines: int = 200) -> str:
    code = "\n".join(f"line {i}" for i in range(lines))
    return json.dumps({"path": path, "content": code})


def _file_read_python(path: str = "src/foo.py") -> str:
    code = (
        "import os\n\n"
        "class FooService:\n    pass\n\n"
        "class FooRepository:\n    pass\n\n"
        "def process_order():\n    pass\n\n"
        "def validate_input():\n    pass\n\n"
        + "# filler\n" * 200
    )
    return json.dumps({"path": path, "content": code})


def _code_execute_content() -> str:
    return json.dumps({"exit_code": 0, "stdout": "ok"})


def _file_search_content() -> str:
    return json.dumps({
        "results": [
            {"path": "src/a.py", "line": 10, "content": "match"},
            {"path": "src/b.py", "line": 20, "content": "match"},
        ]
    })


class TestNoCompressionBelowThreshold:
    def test_below_threshold(self):
        msgs = [
            _user_msg(),
            _assistant_msg(),
            _tool_msg(_file_read_content()),
        ]
        result = compress_file_results(msgs, current_iteration=5)
        assert result == msgs

    def test_at_threshold_minus_one(self):
        msgs = [_tool_msg(_file_read_content())]
        result = compress_file_results(msgs, current_iteration=COMPRESSION_ITERATION_THRESHOLD - 1)
        assert result == msgs


class TestCompressesOldFileReads:
    def test_old_read_compressed(self):
        """File read from early in conversation, re-read later, old one compressed."""
        old_read = _tool_msg(_file_read_content("src/foo.py", 300), "tc_old")
        new_read = _tool_msg(_file_read_content("src/foo.py", 300), "tc_new")
        msgs = [
            _user_msg(),
            _assistant_msg(),
            old_read,
            # many messages in between
            *[_user_msg(f"q{i}") for i in range(20)],
            _assistant_msg(),
            new_read,
        ]
        result = compress_file_results(msgs, current_iteration=15)
        # The old read should be compressed
        compressed = json.loads(result[2]["content"])
        assert compressed.get("compressed") is True
        assert compressed.get("path") == "src/foo.py"

    def test_returns_new_dicts(self):
        """Compressed messages should be new dicts, not mutated originals."""
        old_read = _tool_msg(_file_read_content("src/old.py", 300), "tc_old")
        new_read = _tool_msg(_file_read_content("src/new.py", 50), "tc_new")
        msgs = [_user_msg(), _assistant_msg(), old_read, _user_msg(), _assistant_msg(), new_read]
        result = compress_file_results(msgs, current_iteration=15)
        # Original should be unchanged
        assert "compressed" not in json.loads(old_read["content"])


class TestPreservesLatestReadPerFile:
    def test_only_older_read_compressed(self):
        """If same file read twice, only the older one gets compressed."""
        read1 = _tool_msg(_file_read_content("src/foo.py", 300), "tc1")
        read2 = _tool_msg(_file_read_content("src/foo.py", 300), "tc2")
        msgs = [
            _user_msg(), _assistant_msg(), read1,
            *[_user_msg(f"q{i}") for i in range(10)],
            _user_msg(), _assistant_msg(), read2,
        ]
        result = compress_file_results(msgs, current_iteration=15)
        # First read: compressed
        parsed1 = json.loads(result[2]["content"])
        assert parsed1.get("compressed") is True
        # Second read (latest): not compressed
        parsed2 = json.loads(result[-1]["content"])
        assert parsed2.get("compressed") is not True

    def test_single_read_never_compressed(self):
        """A file read only once is the latest read and should not be compressed."""
        read = _tool_msg(_file_read_content("src/only.py", 300), "tc1")
        msgs = [_user_msg(), _assistant_msg(), read]
        result = compress_file_results(msgs, current_iteration=15)
        parsed = json.loads(result[2]["content"])
        assert parsed.get("compressed") is not True


class TestPreservesCurrentBatch:
    def test_current_batch_not_compressed(self):
        """Results in current_batch_start..end are never compressed."""
        read1 = _tool_msg(_file_read_content("src/a.py", 300), "tc1")
        read2 = _tool_msg(_file_read_content("src/b.py", 300), "tc2")
        msgs = [
            _user_msg(), _assistant_msg(), read1,
            _user_msg(), _assistant_msg(), read2,
        ]
        # Mark read2 as part of current batch
        result = compress_file_results(msgs, current_iteration=15, current_batch_start=5)
        parsed2 = json.loads(result[5]["content"])
        assert parsed2.get("compressed") is not True


class TestPreservesErrorResults:
    def test_error_not_compressed(self):
        error_content = json.dumps({"path": "src/missing.py", "content": "", "error": "File not found"})
        msgs = [
            _user_msg(), _assistant_msg(),
            _tool_msg(error_content, "tc1"),
            _user_msg(), _assistant_msg(),
            _tool_msg(_file_read_content("src/other.py", 300), "tc2"),
        ]
        result = compress_file_results(msgs, current_iteration=15)
        parsed = json.loads(result[2]["content"])
        assert "error" in parsed
        assert parsed.get("compressed") is not True


class TestAlreadyCompressedPassthrough:
    def test_already_compressed(self):
        compressed_content = json.dumps({
            "path": "src/foo.py", "summary": "Already compressed.", "compressed": True,
        })
        msgs = [
            _user_msg(), _assistant_msg(),
            _tool_msg(compressed_content, "tc1"),
            _user_msg(), _assistant_msg(),
            _tool_msg(_file_read_content("src/other.py", 300), "tc2"),
        ]
        result = compress_file_results(msgs, current_iteration=15)
        assert result[2]["content"] == compressed_content


class TestNonFileResultsUnchanged:
    def test_code_execute_unchanged(self):
        exec_content = _code_execute_content()
        msgs = [_user_msg(), _assistant_msg(), _tool_msg(exec_content, "tc1")]
        result = compress_file_results(msgs, current_iteration=15)
        assert result[2]["content"] == exec_content

    def test_plain_text_unchanged(self):
        msgs = [_user_msg(), _assistant_msg(), _tool_msg("plain text", "tc1")]
        result = compress_file_results(msgs, current_iteration=15)
        assert result[2]["content"] == "plain text"


class TestSummaryContent:
    def test_summary_includes_key_info(self):
        """Verify path, line count, language, classes/functions in summary."""
        content = _file_read_python("src/service.py")
        parsed = json.loads(content)
        summary_json = _build_file_summary(content, parsed, iteration=3)
        summary = json.loads(summary_json)

        assert summary["path"] == "src/service.py"
        assert summary["compressed"] is True
        s = summary["summary"]
        assert "Python" in s
        assert "FooService" in s
        assert "FooRepository" in s
        assert "process_order" in s
        assert "validate_input" in s

    def test_summary_includes_iteration(self):
        content = _file_read_content("test.py", 100)
        parsed = json.loads(content)
        summary_json = _build_file_summary(content, parsed, iteration=7)
        summary = json.loads(summary_json)
        assert "Read at iteration 7" in summary["summary"]

    def test_summary_line_count(self):
        content = _file_read_content("test.py", 150)
        parsed = json.loads(content)
        summary_json = _build_file_summary(content, parsed, iteration=1)
        summary = json.loads(summary_json)
        assert "150 lines" in summary["summary"]


class TestFileSearchResultsCompressed:
    def test_search_results_compressed(self):
        """file_search results should also get compressed."""
        search = _file_search_content()
        # Need another file read so the search isn't the "latest read"
        # Actually file_search doesn't have a single path, so it won't be in latest_reads
        # We need to make it large enough to compress
        big_search = json.dumps({
            "results": [
                {"path": f"src/file{i}.py", "line": i * 10, "content": "x" * 200}
                for i in range(20)
            ]
        })
        msgs = [
            _user_msg(), _assistant_msg(),
            _tool_msg(big_search, "tc1"),
            # Add more messages and a different tool result so tc1 isn't latest
            *[_user_msg(f"q{i}") for i in range(5)],
            _assistant_msg(),
            _tool_msg(_file_read_content("src/other.py", 300), "tc2"),
        ]
        result = compress_file_results(msgs, current_iteration=15)
        parsed = json.loads(result[2]["content"])
        assert parsed.get("compressed") is True
        assert "file_search" in parsed.get("summary", "")


class TestSmallResultsNotCompressed:
    def test_small_file_not_compressed(self):
        """Results under ~200 tokens should pass through."""
        small = json.dumps({"path": "src/tiny.py", "content": "x = 1\n"})
        # Need another read of a different file so this isn't preserved as "latest"
        # Actually it IS the latest for tiny.py, so it's preserved anyway.
        # Let's make two reads of same file, the first being small.
        msgs = [
            _user_msg(), _assistant_msg(),
            _tool_msg(small, "tc1"),
            _user_msg(), _assistant_msg(),
            _tool_msg(_file_read_content("src/tiny.py", 300), "tc2"),
        ]
        result = compress_file_results(msgs, current_iteration=15)
        # The small first read: even though it's not latest, it's too small to compress
        parsed = json.loads(result[2]["content"])
        assert parsed.get("compressed") is not True


class TestHelperFunctions:
    def test_is_file_read_result(self):
        assert _is_file_read_result(_tool_msg(_file_read_content())) is True
        assert _is_file_read_result(_tool_msg(_code_execute_content())) is False
        assert _is_file_read_result(_tool_msg("not json")) is False
        assert _is_file_read_result({"role": "user", "content": "hi"}) is False

    def test_is_error_result(self):
        assert _is_error_result(_tool_msg(json.dumps({"error": "fail"}))) is True
        assert _is_error_result(_tool_msg(json.dumps({"path": "x"}))) is False

    def test_is_already_compressed(self):
        assert _is_already_compressed(_tool_msg(json.dumps({"compressed": True}))) is True
        assert _is_already_compressed(_tool_msg(json.dumps({"compressed": False}))) is False
        assert _is_already_compressed(_tool_msg(json.dumps({"path": "x"}))) is False

    def test_extract_file_path(self):
        assert _extract_file_path(_tool_msg(json.dumps({"path": "a.py"}))) == "a.py"
        assert _extract_file_path(_tool_msg(json.dumps({"file_path": "b.py"}))) == "b.py"
        assert _extract_file_path(_tool_msg("nope")) is None
