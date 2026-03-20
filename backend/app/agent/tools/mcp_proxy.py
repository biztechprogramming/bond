"""MCP Proxy Client — HTTP client for workers to access MCP tools via the Gateway broker.

Workers don't run MCP servers locally. Instead they call the Gateway's broker
which authenticates, applies policy, audit-logs, and forwards to the Backend.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from backend.app.agent.tools import ToolRegistry

logger = logging.getLogger("bond.mcp.proxy")


class MCPProxyClient:
    """HTTP proxy client for accessing MCP tools through the Gateway broker."""

    def __init__(self, gateway_url: str, agent_id: str, agent_token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.agent_token = agent_token
        self._tool_cache: list[dict] = []
        self._definition_cache: dict[str, dict] = {}
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Authorization": f"Bearer {self.agent_token}"},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def list_tools(self) -> list[dict]:
        """GET /broker/mcp/tools — list available MCP tools for this agent."""
        client = self._get_client()
        try:
            resp = await client.get(
                f"{self.gateway_url}/broker/mcp/tools",
                params={"agent_id": self.agent_id},
            )
            resp.raise_for_status()
            tools = resp.json().get("tools", [])
            self._tool_cache = tools
            # Build definition cache
            self._definition_cache.clear()
            for tool in tools:
                name = tool["name"]
                self._definition_cache[name] = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", f"MCP tool {name}"),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    }
                }
            logger.info("Fetched %d MCP tools via proxy", len(tools))
            return tools
        except Exception as e:
            logger.error("Failed to list MCP tools via proxy: %s", e)
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """POST /broker/mcp — execute an MCP tool."""
        client = self._get_client()
        try:
            resp = await client.post(
                f"{self.gateway_url}/broker/mcp",
                json={
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "agent_id": self.agent_id,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text
            logger.error("MCP proxy call failed (%d): %s", e.response.status_code, body)
            return {"error": f"MCP proxy call denied or failed: {body}"}
        except Exception as e:
            logger.error("MCP proxy call error: %s", e)
            return {"error": f"MCP proxy call failed: {str(e)}"}

    def get_tool_definitions(self, tool_names: Optional[list[str]] = None) -> list[dict]:
        """Return OpenAI-format tool definitions from cache."""
        if tool_names is None:
            return list(self._definition_cache.values())
        return [self._definition_cache[n] for n in tool_names if n in self._definition_cache]

    def get_cached_tool_names(self) -> list[str]:
        """Return cached tool names."""
        return list(self._definition_cache.keys())

    async def register_proxy_handlers(self, registry: "ToolRegistry"):
        """Fetch tools from broker and register a handler for each into the registry."""
        tools = await self.list_tools()
        for tool in tools:
            bond_name = tool["name"]
            # Parse server_name and mcp_tool_name from bond name
            # bond_tool_name = mcp_{server}_{tool}
            registry.register(bond_name, self._create_proxy_handler(bond_name))
            logger.info("Registered proxy MCP tool: %s", bond_name)
        return [t["name"] for t in tools]

    def _create_proxy_handler(self, bond_tool_name: str):
        async def handler(arguments: dict, context: dict) -> dict:
            return await self.call_tool(bond_tool_name, arguments)
        return handler
