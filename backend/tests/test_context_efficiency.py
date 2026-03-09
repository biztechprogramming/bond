"""Tests for context efficiency features: rule-based pruning, file re-read dedup,
assistant message compression, cache breakpoint skipping, and token budget injection.
"""

from __future__ import annotations

import json

from backend.app.agent.tool_result_filter import (
    rule_based_prune,
    _strip_ansi,
    _collapse_blank_lines,
)
from backend.app.agent.context_decay import _estimate_tokens
from backend.app.worker import (
    _advance_cache_breakpoint,
    _decay_in_loop_tool_results,
)


# ---------------------------------------------------------------------------
# Rule-based pruning: ANSI stripping
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_color_codes():
    text = "\x1b[32mSuccess\x1b[0m: all tests passed"
    assert _strip_ansi(text) == "Success: all tests passed"


def test_strip_ansi_noop_on_clean_text():
    text = "No escape codes here"
    assert _strip_ansi(text) == text


def test_strip_ansi_multiple_codes():
    text = "\x1b[1m\x1b[31mERROR\x1b[0m: \x1b[33mwarning\x1b[0m"
    assert _strip_ansi(text) == "ERROR: warning"


# ---------------------------------------------------------------------------
# Rule-based pruning: blank line collapsing
# ---------------------------------------------------------------------------


def test_collapse_blank_lines_reduces_excess():
    text = "line1\n\n\n\n\nline2"
    assert _collapse_blank_lines(text) == "line1\n\nline2"


def test_collapse_blank_lines_preserves_double():
    text = "line1\n\nline2"
    assert _collapse_blank_lines(text) == text


# ---------------------------------------------------------------------------
# rule_based_prune: code_execute ANSI stripping
# ---------------------------------------------------------------------------


def test_prune_code_execute_strips_ansi():
    result = {
        "stdout": "\x1b[32mPASS\x1b[0m test_foo.py\n\x1b[31mFAIL\x1b[0m test_bar.py",
        "stderr": "",
        "exit_code": 1,
    }
    pruned = rule_based_prune("code_execute", {}, result)
    assert pruned is not None
    assert "\x1b" not in pruned["stdout"]
    assert "PASS test_foo.py" in pruned["stdout"]
    assert pruned["exit_code"] == 1


def test_prune_code_execute_no_ansi_returns_none():
    result = {"stdout": "clean output", "stderr": "", "exit_code": 0}
    pruned = rule_based_prune("code_execute", {}, result)
    assert pruned is None


# ---------------------------------------------------------------------------
# rule_based_prune: search_memory with 0 results
# ---------------------------------------------------------------------------


def test_prune_search_memory_empty_results():
    result = {"results": [], "count": 0}
    pruned = rule_based_prune("search_memory", {"query": "test"}, result)
    assert pruned is not None
    assert pruned == result  # returned as-is


def test_prune_search_memory_with_results_returns_none():
    result = {"results": [{"content": "found"}], "count": 1}
    pruned = rule_based_prune("search_memory", {"query": "test"}, result)
    assert pruned is None


# ---------------------------------------------------------------------------
# rule_based_prune: file_read/file_write small results skip filter
# ---------------------------------------------------------------------------


def test_prune_file_read_small_skips_filter():
    result = {"path": "/test.py", "content": "x = 1\n", "size": 6}
    pruned = rule_based_prune("file_read", {"path": "/test.py"}, result)
    assert pruned is not None
    assert pruned == result


def test_prune_file_read_large_returns_none():
    result = {"path": "/big.py", "content": "x" * 3000, "size": 3000}
    pruned = rule_based_prune("file_read", {"path": "/big.py"}, result)
    assert pruned is None


def test_prune_file_write_small_skips_filter():
    result = {"path": "/test.py", "status": "written", "size": 100}
    pruned = rule_based_prune("file_write", {"path": "/test.py"}, result)
    assert pruned is not None


# ---------------------------------------------------------------------------
# rule_based_prune: generic blank line collapsing
# ---------------------------------------------------------------------------


def test_prune_generic_collapses_blank_lines():
    result = {"output": "line1\n\n\n\n\nline2", "status": "ok"}
    pruned = rule_based_prune("some_tool", {}, result)
    assert pruned is not None
    assert pruned["output"] == "line1\n\nline2"


# ---------------------------------------------------------------------------
# Cache breakpoint: skip on small conversations
# ---------------------------------------------------------------------------


def test_advance_cache_breakpoint_skips_small_conversations():
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "How are you?"},
    ]
    # With < 12 messages, should return old_bp_index unchanged
    result = _advance_cache_breakpoint(messages, 1)
    assert result == 1


def test_advance_cache_breakpoint_skips_very_small():
    messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hi"},
    ]
    result = _advance_cache_breakpoint(messages, 0)
    assert result == 0


# ---------------------------------------------------------------------------
# Assistant message compression in _decay_in_loop_tool_results
# ---------------------------------------------------------------------------


def test_decay_compresses_old_assistant_with_tool_calls():
    """Old assistant messages with tool_calls get their content truncated."""
    long_reasoning = "I need to analyze the code carefully. " * 20  # ~700 chars
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": long_reasoning, "tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": '{"content": "file data"}'},
        {"role": "assistant", "content": "Another long reasoning message. " * 20, "tool_calls": [{"id": "2", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "2", "content": '{"content": "more data"}'},
        # Last 4 messages (kept verbatim)
        {"role": "assistant", "content": "recent reasoning", "tool_calls": [{"id": "3", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "3", "content": '{"content": "latest"}'},
        {"role": "assistant", "content": "final reasoning", "tool_calls": [{"id": "4", "function": {"name": "respond", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "4", "content": '{"message": "done"}'},
    ]
    preturn_count = 2  # system + user
    result = _decay_in_loop_tool_results(messages, preturn_count)

    # The first assistant message (index 2) should be compressed
    assert len(result[2]["content"]) <= 103  # 100 chars + "..."
    assert result[2]["content"].endswith("...")
    # tool_calls should still be present
    assert result[2].get("tool_calls") is not None


def test_decay_compresses_old_assistant_content_only():
    """Old assistant messages with only content get truncated to 200 chars."""
    long_content = "Here is a very detailed explanation. " * 30  # ~1000+ chars
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "explain"},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": "now do something"},
        # Last 4 messages (kept verbatim)
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": '{"content": "data"}'},
        {"role": "assistant", "content": "done", "tool_calls": [{"id": "2", "function": {"name": "respond", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "2", "content": '{"message": "ok"}'},
    ]
    preturn_count = 2
    result = _decay_in_loop_tool_results(messages, preturn_count)

    # The assistant content-only message (index 2) should be compressed to ~200 chars
    assert len(result[2]["content"]) <= 203  # 200 + "..."
    assert result[2]["content"].endswith("...")


def test_decay_preserves_recent_assistant_messages():
    """Last 4 messages should never be compressed."""
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hi"},
        # Last 4
        {"role": "assistant", "content": "A very long recent message " * 50, "tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": '{"content": "data"}'},
        {"role": "assistant", "content": "Another long recent one " * 50},
        {"role": "tool", "tool_call_id": "2", "content": '{"content": "more"}'},
    ]
    preturn_count = 2
    result = _decay_in_loop_tool_results(messages, preturn_count)

    # These are the last 4, should be unchanged
    assert result[2]["content"] == messages[2]["content"]
    assert result[4]["content"] == messages[4]["content"]


# ---------------------------------------------------------------------------
# Fragment selection cache tests removed — fragment cache moved to
# manifest-based system (Doc 027). See test_fragment_router.py for
# current fragment selection tests.
# ---------------------------------------------------------------------------
