"""Tests for the _find_line helper and whitespace-flexible smart edit search."""

import asyncio
import os
import tempfile

import pytest

from backend.app.agent.tools.file_buffer import (
    FileBuffer,
    _find_line,
    _normalize_ws,
    _sanitize_content,
    handle_file_smart_edit,
    _manager,
)


# ---------------------------------------------------------------------------
# _normalize_ws
# ---------------------------------------------------------------------------

class TestNormalizeWs:
    def test_collapses_spaces(self):
        assert _normalize_ws("  hello   world  ") == "hello world"

    def test_collapses_tabs(self):
        assert _normalize_ws("\t\thello\tworld\t") == "hello world"

    def test_mixed_whitespace(self):
        assert _normalize_ws("  \t  hello \t world  \t") == "hello world"

    def test_empty(self):
        assert _normalize_ws("") == ""

    def test_single_word(self):
        assert _normalize_ws("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# _find_line — Strategy 1: regex match
# ---------------------------------------------------------------------------

class TestFindLineRegex:
    """Strategy 1: exact regex matching (existing behavior)."""

    def test_simple_match(self):
        lines = ["    <div>", "    <label>Name</label>", "    </div>"]
        result, count = _find_line(lines, "<label>Name")
        assert result == 2  # 1-indexed
        assert count == 1

    def test_occurrence(self):
        lines = ["foo", "bar", "foo", "baz", "foo"]
        result, count = _find_line(lines, "foo", occurrence=2)
        assert result == 3
        assert count == 2

    def test_occurrence_3(self):
        lines = ["foo", "bar", "foo", "baz", "foo"]
        result, count = _find_line(lines, "foo", occurrence=3)
        assert result == 5
        assert count == 3

    def test_not_found(self):
        lines = ["foo", "bar", "baz"]
        result, count = _find_line(lines, "qux")
        assert result is None
        assert count == 0

    def test_case_insensitive(self):
        lines = ["    <Label>Qty</Label>"]
        result, _ = _find_line(lines, "<label>qty")
        assert result == 1

    def test_regex_special_chars_escaped(self):
        """Patterns with invalid regex should be auto-escaped."""
        lines = ["value = foo(bar"]
        result, _ = _find_line(lines, "foo(bar")
        assert result == 1

    def test_start_from(self):
        lines = ["foo", "bar", "foo"]
        result, _ = _find_line(lines, "foo", start_from=1)
        assert result == 3  # skips index 0

    def test_skip_first(self):
        lines = ["foo", "bar", "foo"]
        result, _ = _find_line(lines, "foo", start_from=0, skip_first=True)
        assert result == 3

    def test_max_lines(self):
        lines = ["aaa", "bbb", "ccc", "target", "ddd"]
        # Only search first 3 lines — should NOT find "target" at index 3
        result, _ = _find_line(lines, "target", max_lines=3)
        assert result is None

    def test_max_lines_includes_target(self):
        lines = ["aaa", "bbb", "ccc", "target", "ddd"]
        result, _ = _find_line(lines, "target", max_lines=4)
        assert result == 4


# ---------------------------------------------------------------------------
# _find_line — Strategy 2: whitespace-normalized fallback
# ---------------------------------------------------------------------------

class TestFindLineWhitespaceFallback:
    """Strategy 2: when regex finds 0 matches, retry with normalized whitespace."""

    def test_wrong_indentation(self):
        """Agent sends 8 spaces but file has 4 — should still match."""
        lines = ["    <label>Qty</label>"]
        result, _ = _find_line(lines, "        <label>Qty</label>")
        assert result == 1

    def test_tabs_vs_spaces(self):
        """Agent sends spaces but file has tabs."""
        lines = ["\t\t<label>Qty</label>"]
        result, _ = _find_line(lines, "    <label>Qty</label>")
        assert result == 1

    def test_no_indentation_in_pattern(self):
        """Agent sends no indentation but file has some."""
        lines = ["        <label>Qty</label>"]
        result, _ = _find_line(lines, "<label>Qty</label>")
        # Regex strategy 1 already handles this (partial match), so this works
        assert result == 1

    def test_extra_internal_spaces(self):
        """Agent sends extra spaces between tokens."""
        lines = ['    <div class="info-row">']
        result, _ = _find_line(lines, '<div  class="info-row">')
        assert result == 1

    def test_occurrence_with_fallback(self):
        lines = [
            "        <label>Name</label>",
            "        <label>Qty</label>",
        ]
        # Both match the normalized pattern "<label>" but we want the 2nd
        result, _ = _find_line(lines, "        <label>Qty</label>")
        assert result == 2


# ---------------------------------------------------------------------------
# _find_line — Strategy 3: multi-line search
# ---------------------------------------------------------------------------

class TestFindLineMultiLine:
    """Strategy 3: patterns containing newlines."""

    def test_two_line_match(self):
        lines = [
            '<div class="info-row">',
            "    <label>Qty</label>",
            "    <input />",
            "</div>",
        ]
        result, _ = _find_line(lines, '<div class="info-row">\n<label>Qty</label>')
        assert result == 1  # first line of the block

    def test_multiline_wrong_indentation(self):
        """Multi-line with wrong indentation should still match via normalization."""
        lines = [
            '    <div class="info-row">',
            "        <label>Qty</label>",
            "        <input />",
            "    </div>",
        ]
        result, _ = _find_line(
            lines, '<div class="info-row">\n<label>Qty</label>\n<input />'
        )
        assert result == 1

    def test_multiline_not_found(self):
        lines = ["foo", "bar", "baz"]
        result, count = _find_line(lines, "foo\nqux")
        assert result is None
        assert count == 0

    def test_trailing_newline_stripped(self):
        """A trailing newline in the pattern should not cause issues."""
        lines = ["foo", "bar", "baz"]
        result, _ = _find_line(lines, "foo\nbar\n")
        assert result == 1

    def test_multiline_occurrence(self):
        lines = ["a", "b", "c", "a", "b", "d"]
        result, _ = _find_line(lines, "a\nb", occurrence=2)
        assert result == 4  # second "a\nb" block starts at line 4

    def test_multiline_with_skip_first(self):
        lines = ["a", "b", "c", "a", "b"]
        result, _ = _find_line(lines, "a\nb", start_from=0, skip_first=True)
        assert result == 4


# ---------------------------------------------------------------------------
# _find_line — Literal-first ordering
# ---------------------------------------------------------------------------

class TestFindLineLiteralFirst:
    """Verify literal match is preferred over regex interpretation."""

    def test_parentheses_in_pattern(self):
        """Search for `getModel()` should match literally, not as regex group."""
        lines = ["const x = getModel()", "const y = getmode", "const z = getModel()"]
        result, count = _find_line(lines, "getModel()")
        assert result == 1
        assert count == 1

    def test_brackets_in_pattern(self):
        """Search for `items[0]` should match literally, not as char class."""
        lines = ["val = items[0]", "val = items0"]
        result, _ = _find_line(lines, "items[0]")
        assert result == 1

    def test_dot_in_pattern(self):
        """Search for `config.name` should match literally, not `.` as any-char."""
        lines = ["x = configXname", "x = config.name"]
        result, _ = _find_line(lines, "config.name")
        assert result == 2

    def test_regex_still_works_as_fallback(self):
        """Actual regex like `def \\w+\\(` should still work as Strategy 3."""
        lines = ["def hello(x):", "class Foo:"]
        result, _ = _find_line(lines, r"def \w+\(")
        assert result == 1

    def test_literal_beats_regex_false_match(self):
        """Regex would match wrong line but literal matches the right one."""
        # Pattern: "a.b" — regex matches "axb" (line 1), literal matches "a.b" (line 2)
        lines = ["axb", "a.b"]
        result, _ = _find_line(lines, "a.b")
        # Literal strategy finds "a.b" at line 2 but also "axb" won't match literally
        # Wait — "a.b" is literally in "a.b" (line 2) but NOT in "axb"
        assert result == 2


# ---------------------------------------------------------------------------
# Integration: handle_file_smart_edit with whitespace flexibility
# ---------------------------------------------------------------------------

class TestSmartEditWhitespaceIntegration:
    """End-to-end tests through handle_file_smart_edit."""

    def _make_temp_file(self, content: str) -> str:
        """Write content to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".razor")
        os.write(fd, content.encode())
        os.close(fd)
        return path

    def _cleanup(self, path: str):
        _manager.close(path)
        if os.path.exists(path):
            os.unlink(path)

    def test_preview_with_wrong_indentation(self):
        content = '    <div class="info-row">\n        <label>Qty</label>\n        <input />\n    </div>\n'
        path = self._make_temp_file(content)
        try:
            result = asyncio.run(
                handle_file_smart_edit(
                    {"path": path, "search": '<div class="info-row">'},
                    {},
                )
            )
            assert result.get("mode") == "preview"
            assert "info-row" in result["content"]
        finally:
            self._cleanup(path)

    def test_edit_with_mismatched_whitespace(self):
        content = '    <div class="info-row">\n        <label>Qty</label>\n    </div>\n    <div class="other">\n'
        path = self._make_temp_file(content)
        try:
            # Search with wrong indentation (no leading spaces)
            result = asyncio.run(
                handle_file_smart_edit(
                    {
                        "path": path,
                        "search": '<div class="info-row">',
                        "end_search": "</div>",
                        "new_content": '    <div class="info-row">\n        <label>Name</label>\n    </div>\n',
                    },
                    {},
                )
            )
            assert result.get("mode") == "edited"
            assert "Name" in result.get("after_edit", "")
        finally:
            self._cleanup(path)

# ---------------------------------------------------------------------------
# _sanitize_content
# ---------------------------------------------------------------------------

class TestSanitizeContent:
    """Tests for invisible Unicode character stripping."""

    def test_clean_content_unchanged(self):
        text = "def hello():\n    return 42\n"
        assert _sanitize_content(text) == text

    def test_strips_zero_width_space(self):
        text = "def\u200b hello():\n    return 42\n"
        assert _sanitize_content(text) == "def hello():\n    return 42\n"

    def test_strips_zero_width_non_joiner(self):
        text = "import\u200c os\n"
        assert _sanitize_content(text) == "import os\n"

    def test_strips_zero_width_joiner(self):
        text = "x\u200d = 1\n"
        assert _sanitize_content(text) == "x = 1\n"

    def test_strips_bom(self):
        text = "\ufeffimport sys\n"
        assert _sanitize_content(text) == "import sys\n"

    def test_strips_word_joiner(self):
        text = "hello\u2060world\n"
        assert _sanitize_content(text) == "helloworld\n"

    def test_strips_soft_hyphen(self):
        text = "some\u00adthing\n"
        assert _sanitize_content(text) == "something\n"

    def test_strips_multiple_different_chars(self):
        text = "\ufeffdef\u200b foo\u200c():\u200d\n"
        assert _sanitize_content(text) == "def foo():\n"

    def test_empty_string(self):
        assert _sanitize_content("") == ""

    def test_preserves_normal_unicode(self):
        text = "# Comment with émojis 🎉 and ñ\n"
        assert _sanitize_content(text) == text

    def test_preserves_tabs_and_whitespace(self):
        text = "\tdef foo():\n\t\treturn True\n"
        assert _sanitize_content(text) == text


    def test_search_not_found_returns_error(self):
        content = "line1\nline2\nline3\n"
        path = self._make_temp_file(content)
        try:
            result = asyncio.run(
                handle_file_smart_edit(
                    {"path": path, "search": "nonexistent_pattern_xyz"},
                    {},
                )
            )
            assert "error" in result
            assert "not found" in result["error"].lower()
        finally:
            self._cleanup(path)
