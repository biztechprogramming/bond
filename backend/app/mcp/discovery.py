"""Shared MCP tool discovery helper (Design Doc 111).

Provides a single source of truth for enumerating live MCP tools,
used by both the ``/agents/tools`` API and the context builder.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.agent.tools.mcp_proxy import MCPProxyClient
    from backend.app.mcp.manager import MCPManager

logger = logging.getLogger("bond.mcp.discovery")


async def discover_mcp_tools(
    mcp_manager: Optional["MCPManager"] = None,
    mcp_proxy: Optional["MCPProxyClient"] = None,
    scope: str = "global",
) -> list[dict[str, Any]]:
    """Return a unified list of live MCP tools from all connected servers.

    Each entry has: ``name``, ``server``, ``mcp_name``, ``description``,
    ``parameters``, ``source`` (``"proxy"`` or ``"host"``).

    Prefers the proxy client cache when available (worker context);
    falls back to the host-side MCPManager.
    """
    tools: list[dict[str, Any]] = []

    # Worker-side: proxy client has a cached tool list
    if mcp_proxy is not None and mcp_proxy._tool_cache:
        for t in mcp_proxy._tool_cache:
            tools.append({
                "name": t["name"],
                "server": t.get("server", ""),
                "mcp_name": t.get("mcp_name", t["name"]),
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
                "source": "proxy",
            })
        return tools

    # Host-side: query the MCPManager directly
    manager = mcp_manager
    if manager is None:
        try:
            from backend.app.mcp import mcp_manager as _global_manager
            manager = _global_manager
        except Exception:
            return []

    try:
        raw_tools = await manager.list_tools(scope=scope)
        for t in raw_tools:
            tools.append({
                "name": t["name"],
                "server": t.get("server", ""),
                "mcp_name": t.get("mcp_name", t["name"]),
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
                "source": "host",
            })
    except Exception as e:
        logger.warning("MCP tool discovery failed: %s", e)

    return tools


def format_mcp_tools_for_context(tools: list[dict[str, Any]]) -> str:
    """Format discovered MCP tools into a prompt section for agent context.

    Groups tools by server and lists each tool with its description,
    replacing the old server-names-only summary.
    """
    if not tools:
        return (
            "## MCP Integrations\n"
            "No MCP servers are connected. If asked about external service "
            "integrations (e.g. time tracking, CRM), say you don't currently "
            "have access rather than searching the filesystem."
        )

    by_server: dict[str, list[dict]] = {}
    for t in tools:
        server = t.get("server", "unknown")
        by_server.setdefault(server, []).append(t)

    lines = ["## MCP Integrations"]
    lines.append(
        "Tools from MCP servers are prefixed `mcp_<server>_`. "
        "If asked about a service you don't have an MCP connection for, "
        "say so directly — don't search the filesystem for it."
    )

    for server, server_tools in sorted(by_server.items()):
        lines.append(f"\n### Server: {server}")
        for t in sorted(server_tools, key=lambda x: x["name"]):
            desc = t.get("description", "")
            if desc:
                lines.append(f"- **{t['name']}**: {desc}")
            else:
                lines.append(f"- **{t['name']}**")

    return "\n".join(lines)
