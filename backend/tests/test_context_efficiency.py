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
from backend.app.agent.context_decay import _estimate_tokens, apply_progressive_decay

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
# apply_progressive_decay (replaces _advance_cache_breakpoint and
# _decay_in_loop_tool_results which were removed from worker.py)
# ---------------------------------------------------------------------------

def test_progressive_decay_empty_messages():
    """Empty list should pass through."""
    result = apply_progressive_decay([])
    assert result == []

def test_progressive_decay_preserves_non_tool_messages():
    """Non-tool messages (user, assistant) should pass through unchanged."""
    messages = [
        {"role": "user", "content": "fix the bug"},
        {"role": "assistant", "content": "I'll look at the code."},
    ]
    result = apply_progressive_decay(messages)
    assert result == messages

def test_progressive_decay_preserves_small_tool_results():
    """Tool results under 200 tokens should be kept as-is regardless of age."""
    messages = [
        {"role": "user", "content": "read the file"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": json.dumps({"content": "x = 1", "path": "/test.py"})},
        {"role": "user", "content": "now do something else"},
        {"role": "user", "content": "and another thing"},
        {"role": "user", "content": "and yet another"},
        {"role": "user", "content": "keep going"},
    ]
    result = apply_progressive_decay(messages)
    # The tool result is small, should be unchanged
    assert result[2]["content"] == messages[2]["content"]

def test_progressive_decay_decays_large_old_tool_results():
    """Large tool results from many turns ago should be summarized/truncated."""
    large_content = json.dumps({"content": "x = 1\n" * 500, "path": "/big.py", "file_path": "/big.py"})
    messages = [
        {"role": "user", "content": "read the file"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "function": {"name": "file_read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": large_content},
        # Add several turns to age the tool result
        {"role": "user", "content": "turn 2"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "turn 3"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "turn 4"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "turn 5"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "turn 6"},
    ]
    result = apply_progressive_decay(messages)
    # The tool result should be significantly smaller than the original
    assert len(result[2]["content"]) < len(large_content)

# ---------------------------------------------------------------------------
# Fragment selection cache tests removed — fragment cache moved to
# manifest-based system (Doc 027). See test_fragment_router.py for
# current fragment selection tests.
# ---------------------------------------------------------------------------
