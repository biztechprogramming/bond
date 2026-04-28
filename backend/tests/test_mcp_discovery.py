"""Regression tests for dynamic MCP tool discovery (Design Doc 111)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.mcp.discovery import discover_mcp_tools, format_mcp_tools_for_context


# ── Fixtures ──────────────────────────────────────────────────


FAKE_MCP_TOOLS = [
    {
        "name": "mcp_solidtime_get_projects",
        "server": "solidtime",
        "mcp_name": "get_projects",
        "description": "List all projects",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "mcp_solidtime_create_entry",
        "server": "solidtime",
        "mcp_name": "create_entry",
        "description": "Create a time entry",
        "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}},
    },
    {
        "name": "mcp_github_list_repos",
        "server": "github",
        "mcp_name": "list_repos",
        "description": "List repositories",
        "parameters": {"type": "object", "properties": {}},
    },
]


def _make_mock_manager(tools: list[dict] | None = None):
    """Create a mock MCPManager that returns the given tools."""
    manager = MagicMock()
    manager.list_tools = AsyncMock(return_value=FAKE_MCP_TOOLS if tools is None else tools)
    manager.connection_pools = {"solidtime::global": MagicMock(), "github::global": MagicMock()}
    return manager


def _make_mock_proxy(tools: list[dict] | None = None):
    """Create a mock MCPProxyClient with cached tools."""
    proxy = MagicMock()
    proxy._tool_cache = FAKE_MCP_TOOLS if tools is None else tools
    return proxy


# ── discover_mcp_tools tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_discover_from_host_manager():
    manager = _make_mock_manager()
    tools = await discover_mcp_tools(mcp_manager=manager)

    assert len(tools) == 3
    assert all(t["source"] == "host" for t in tools)
    assert {t["server"] for t in tools} == {"solidtime", "github"}


@pytest.mark.asyncio
async def test_discover_from_proxy_client():
    proxy = _make_mock_proxy()
    tools = await discover_mcp_tools(mcp_proxy=proxy)

    assert len(tools) == 3
    assert all(t["source"] == "proxy" for t in tools)


@pytest.mark.asyncio
async def test_proxy_takes_precedence_over_manager():
    """When both proxy and manager are provided, proxy wins."""
    manager = _make_mock_manager([])
    proxy = _make_mock_proxy()
    tools = await discover_mcp_tools(mcp_manager=manager, mcp_proxy=proxy)

    assert len(tools) == 3
    assert all(t["source"] == "proxy" for t in tools)
    manager.list_tools.assert_not_called()


@pytest.mark.asyncio
async def test_discover_empty_when_no_servers():
    manager = _make_mock_manager([])
    tools = await discover_mcp_tools(mcp_manager=manager)
    assert tools == []


@pytest.mark.asyncio
async def test_discover_handles_manager_exception():
    manager = MagicMock()
    manager.list_tools = AsyncMock(side_effect=RuntimeError("connection lost"))
    tools = await discover_mcp_tools(mcp_manager=manager)
    assert tools == []


# ── format_mcp_tools_for_context tests ───────────────────────


def test_format_groups_by_server():
    result = format_mcp_tools_for_context(FAKE_MCP_TOOLS)

    assert "## MCP Integrations" in result
    assert "### Server: solidtime" in result
    assert "### Server: github" in result
    assert "**mcp_solidtime_get_projects**" in result
    assert "**mcp_github_list_repos**" in result
    assert "List all projects" in result


def test_format_empty_tools():
    result = format_mcp_tools_for_context([])
    assert "No MCP servers are connected" in result


def test_format_tool_without_description():
    tools = [{"name": "mcp_x_y", "server": "x", "description": ""}]
    result = format_mcp_tools_for_context(tools)
    assert "**mcp_x_y**" in result
    # No trailing colon when description is empty
    assert "**mcp_x_y**:" not in result


# ── /agents/tools endpoint test ──────────────────────────────


@pytest.mark.asyncio
async def test_agents_tools_endpoint_includes_mcp():
    """The /agents/tools endpoint should return both native and MCP tools."""
    with patch(
        "backend.app.mcp.discovery.discover_mcp_tools",
        new_callable=AsyncMock,
        return_value=FAKE_MCP_TOOLS,
    ):
        from backend.app.api.v1.agents import list_tools
        result = await list_tools()

    assert "native" in result
    assert "mcp" in result
    assert "all" in result
    assert len(result["native"]) > 0
    assert len(result["mcp"]) == 3
    assert all(e["source"] == "mcp" for e in result["mcp"])
    assert result["mcp"][0]["server"] in ("solidtime", "github")
    # all = native + mcp
    assert len(result["all"]) == len(result["native"]) + len(result["mcp"])


# ── Context builder integration test ─────────────────────────


@pytest.mark.asyncio
async def test_context_builder_enumerates_mcp_tools():
    """Context builder should include per-tool MCP enumeration, not just server names."""
    tools = FAKE_MCP_TOOLS
    result = format_mcp_tools_for_context(tools)

    # Should list individual tools, not just "solidtime, github"
    assert "mcp_solidtime_get_projects" in result
    assert "mcp_solidtime_create_entry" in result
    assert "mcp_github_list_repos" in result
    # Should NOT be the old format (just server names)
    assert "Connected external services" not in result
