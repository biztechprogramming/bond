"""Tests for the tool registry and definitions."""

from __future__ import annotations

import pytest

from backend.app.agent.tools import ToolRegistry, build_registry
from backend.app.agent.tools.definitions import TOOL_DEFINITIONS, TOOL_SUMMARIES, TOOL_MAP


def test_all_14_tools_defined():
    """Should have exactly 14 tool definitions."""
    assert len(TOOL_DEFINITIONS) == 15


def test_tool_summaries_match():
    """Each definition should have a matching summary."""
    assert len(TOOL_SUMMARIES) == 15
    for name in TOOL_SUMMARIES:
        assert name in TOOL_MAP


def test_registry_register_and_get():
    """Should register and retrieve handlers."""
    registry = ToolRegistry()

    async def dummy(args, ctx):
        return {"ok": True}

    registry.register("test_tool", dummy)
    assert registry.get("test_tool") is dummy
    assert registry.get("nonexistent") is None


@pytest.mark.asyncio
async def test_registry_execute():
    """Should execute a registered handler."""
    registry = ToolRegistry()

    async def echo(args, ctx):
        return {"echo": args.get("msg")}

    registry.register("echo", echo)
    result = await registry.execute("echo", {"msg": "hello"}, {})
    assert result == {"echo": "hello"}


@pytest.mark.asyncio
async def test_registry_execute_missing_tool():
    """Should return error for unregistered tool."""
    registry = ToolRegistry()
    result = await registry.execute("missing", {}, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_registry_execute_exception():
    """Should catch exceptions and return error."""
    registry = ToolRegistry()

    async def fail(args, ctx):
        raise ValueError("boom")

    registry.register("fail", fail)
    result = await registry.execute("fail", {}, {})
    assert "error" in result
    assert "boom" in result["error"]


def test_get_definitions_for_filters():
    """Should filter tool definitions by agent's tool list."""
    registry = build_registry()
    defs = registry.get_definitions_for(["respond", "search_memory"])
    assert len(defs) == 2
    names = {d["function"]["name"] for d in defs}
    assert names == {"respond", "search_memory"}


def test_get_definitions_for_empty():
    """Should return empty list for no tools."""
    registry = build_registry()
    defs = registry.get_definitions_for([])
    assert defs == []


def test_build_registry_has_all_tools():
    """Built registry should have handlers for all 14 tools."""
    registry = build_registry()
    assert len(registry.registered_names) == 15
    expected = {
        "respond", "search_memory", "memory_save", "memory_update",
        "code_execute", "file_read", "file_write", "call_subordinate",
        "web_search", "web_read", "browser", "email", "cron", "notify", "skills",
    }
    assert set(registry.registered_names) == expected
