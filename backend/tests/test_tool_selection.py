"""Tests for heuristic tool selection."""

import pytest
from backend.app.agent.tool_selection import select_tools, compact_tool_schema


ALL_TOOLS = [
    "respond", "search_memory", "memory_save", "memory_update", "memory_delete",
    "code_execute", "file_read", "file_write", "call_subordinate",
    "web_search", "web_read", "browser", "email", "cron", "notify", "skills",
]


class TestSelectTools:
    def test_respond_always_included(self):
        result = select_tools("hello", ALL_TOOLS)
        assert "respond" in result

    def test_simple_greeting_only_respond(self):
        result = select_tools("hi there, how are you?", ALL_TOOLS)
        assert result == ["respond"]

    def test_file_keywords_select_file_tools(self):
        result = select_tools("can you read the file src/main.py?", ALL_TOOLS)
        assert "file_read" in result

    def test_code_keywords_select_code_tools(self):
        result = select_tools("run the test suite", ALL_TOOLS)
        assert "code_execute" in result

    def test_coding_tools_grouped(self):
        """If any coding tool matches, all coding tools should be included."""
        result = select_tools("read the file and fix the bug", ALL_TOOLS)
        assert "file_read" in result
        assert "file_write" in result
        assert "code_execute" in result

    def test_memory_keywords(self):
        result = select_tools("do you remember what we discussed?", ALL_TOOLS)
        assert "search_memory" in result

    def test_web_keywords(self):
        result = select_tools("search the web for Python 3.14 release date", ALL_TOOLS)
        assert "web_search" in result

    def test_url_detection(self):
        result = select_tools("can you read https://example.com/api", ALL_TOOLS)
        assert "web_read" in result

    def test_momentum_from_recent_tools(self):
        result = select_tools(
            "ok now do the next one",
            ALL_TOOLS,
            recent_tools_used=["file_read", "file_write", "code_execute"],
        )
        assert "file_read" in result
        assert "file_write" in result

    def test_max_tools_cap(self):
        # Message with many keywords shouldn't exceed MAX
        result = select_tools(
            "read the file, run the tests, search memory, search the web, send email, schedule a cron",
            ALL_TOOLS,
        )
        assert len(result) <= 8

    def test_only_enabled_tools_returned(self):
        enabled = ["respond", "file_read", "code_execute"]
        result = select_tools("read the file", enabled)
        assert all(t in enabled for t in result)

    def test_file_extension_triggers(self):
        result = select_tools("what's in config.yaml?", ALL_TOOLS)
        assert "file_read" in result


class TestCompactToolSchema:
    def test_strips_long_description(self):
        tool = {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Read the contents of a file. Supports text files and binary. Returns content as string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "The absolute or relative path to the file to read.",
                        },
                    },
                    "required": ["path"],
                },
            },
        }
        compact = compact_tool_schema(tool)
        assert compact["function"]["name"] == "file_read"
        # Description should be first sentence only
        assert compact["function"]["description"] == "Read the contents of a file."
        # Parameter descriptions stripped
        assert "description" not in compact["function"]["parameters"]["properties"]["path"]

    def test_preserves_enums(self):
        tool = {
            "type": "function",
            "function": {
                "name": "test",
                "description": "A test tool.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["fast", "slow"],
                            "description": "The mode to use.",
                        },
                    },
                    "required": ["mode"],
                },
            },
        }
        compact = compact_tool_schema(tool)
        assert compact["function"]["parameters"]["properties"]["mode"]["enum"] == ["fast", "slow"]

    def test_no_params(self):
        tool = {
            "type": "function",
            "function": {
                "name": "simple",
                "description": "A simple tool with no params.",
            },
        }
        compact = compact_tool_schema(tool)
        assert "parameters" not in compact["function"]
