from __future__ import annotations
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Dict, List, Optional, Any, TYPE_CHECKING, Union, Type
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field, create_model
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.agent.tools import ToolRegistry

logger = logging.getLogger("bond.mcp")


class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: List[str] = []
    env: Dict[str, str] = {}
    enabled: bool = True


class MCPConnection:
    """Manages a single MCP server connection with auto-recovery."""
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env={**os.environ, **config.env}
        )
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._healthy = False

    async def start(self):
        async with self._lock:
            if self.session:
                return
            logger.info(f"Starting MCP server: {self.config.name}")
            try:
                read, write = await self._exit_stack.enter_async_context(stdio_client(self.params))
                self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
                await self.session.initialize()
                self._healthy = True
            except Exception as e:
                logger.error(f"Failed to start MCP server {self.config.name}: {e}")
                self._healthy = False
                await self._exit_stack.aclose()
                raise

    async def stop(self):
        async with self._lock:
            self._healthy = False
            if self.session:
                logger.info(f"Stopping MCP server: {self.config.name}")
                try:
                    await asyncio.wait_for(self._exit_stack.aclose(), timeout=2.0)
                except BaseException as e:
                    logger.debug(f"Non-critical cleanup error for {self.config.name}: {e}")
                self.session = None
                self._exit_stack = AsyncExitStack()

    async def restart(self):
        """Stop and restart the connection (auto-recovery)."""
        await self.stop()
        await self.start()

    @property
    def is_healthy(self) -> bool:
        return self._healthy and self.session is not None


class MCPConnectionPool:
    """Pool of connections to a single MCP server for concurrency."""
    def __init__(self, config: MCPServerConfig, pool_size: int = 2):
        self.config = config
        self.pool_size = pool_size
        self._connections: List[MCPConnection] = []
        self._semaphore = asyncio.Semaphore(pool_size)
        self._robin_index = 0
        self._lock = asyncio.Lock()

    async def start(self):
        """Start all connections in the pool."""
        for _ in range(self.pool_size):
            conn = MCPConnection(self.config)
            try:
                await conn.start()
                self._connections.append(conn)
            except Exception as e:
                logger.error(f"Failed to start pool connection for {self.config.name}: {e}")
                # Start at least one - if first fails, raise
                if not self._connections:
                    raise

    async def stop(self):
        """Stop all connections in the pool."""
        for conn in self._connections:
            await conn.stop()
        self._connections.clear()

    async def acquire(self) -> MCPConnection:
        """Acquire a connection from the pool (round-robin with semaphore)."""
        await self._semaphore.acquire()
        async with self._lock:
            if not self._connections:
                self._semaphore.release()
                raise RuntimeError(f"No connections available for {self.config.name}")
            conn = self._connections[self._robin_index % len(self._connections)]
            self._robin_index += 1
            # Auto-recover dead connections
            if not conn.is_healthy:
                try:
                    await conn.restart()
                except Exception:
                    pass
            return conn

    def release(self):
        """Release a connection back to the pool."""
        self._semaphore.release()

    @property
    def has_healthy_connection(self) -> bool:
        return any(c.is_healthy for c in self._connections)

    @property
    def healthy_count(self) -> int:
        return sum(1 for c in self._connections if c.is_healthy)


def _is_stdb_none(value: Any) -> bool:
    """Check if a SpacetimeDB value represents None/null.

    SpacetimeDB encodes Option<T> as tagged enums that show up as:
      - {"none": []}  (dict format)
      - [1, []]       (array-tagged format, tag 1 = None variant)
      - None           (Python None)
      - ""             (empty string)
    """
    if value is None or value == "":
        return True
    if isinstance(value, dict) and "none" in value:
        return True
    if isinstance(value, list) and len(value) == 2 and value[0] == 1:
        return True
    return False


def parse_connection_key(key: str) -> tuple[str, str]:
    """Parse a connection pool key into (server_name, scope)."""
    parts = key.split("::", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "global")


def _format_result(result) -> str:
    """Format an MCP call result into a string."""
    output = []
    for content in result.content:
        if hasattr(content, 'text') and content.text:
            output.append(content.text)
        elif hasattr(content, 'data') and content.data:
            output.append(f"[Data content: {len(content.data)} bytes]")
        elif hasattr(content, 'uri') and content.uri:
            output.append(f"[Resource: {content.uri}]")
    return "\n".join(output) if output else "Tool executed successfully (no output)."


class MCPManager:
    """Manages MCP connection pools with health monitoring."""
    def __init__(self, pool_size: int = 2, health_interval: float = 30.0):
        self.connection_pools: Dict[str, MCPConnectionPool] = {}
        self._pool_size = pool_size
        self._health_interval = health_interval
        self._health_task: Optional[asyncio.Task] = None
        self._dynamic_definitions: Dict[str, dict] = {}
        self._bond_to_class_map: Dict[str, str] = {}

    async def ensure_servers_loaded(self, agent_id: Optional[str] = None):
        """Load enabled MCP servers from SpacetimeDB if not already loaded."""
        try:
            from backend.app.core.spacetimedb import get_stdb
            stdb = get_stdb()

            sql = "SELECT * FROM mcp_servers WHERE enabled = true"
            rows = await stdb.query(sql)

            scope = agent_id or "global"

            # Filter rows
            filtered_rows = []
            for row in rows:
                row_agent_id = row.get("agent_id")
                is_global = _is_stdb_none(row_agent_id)
                if agent_id:
                    if is_global or row_agent_id == agent_id:
                        filtered_rows.append(row)
                else:
                    if is_global:
                        filtered_rows.append(row)

            for row in filtered_rows:
                config = MCPServerConfig(
                    name=row["name"],
                    command=row["command"],
                    args=json.loads(row["args"]),
                    env=json.loads(row["env"]),
                    enabled=bool(row["enabled"])
                )
                key = f"{config.name}::{scope}"
                if key not in self.connection_pools:
                    pool = MCPConnectionPool(config, self._pool_size)
                    await pool.start()
                    self.connection_pools[key] = pool
                    logger.info(f"Started connection pool: {key}")

        except Exception as e:
            logger.error(f"Failed to load MCP servers from DB: {e}")

    async def add_server(self, config: MCPServerConfig, scope: str = "global"):
        """Add a server connection pool."""
        key = f"{config.name}::{scope}"
        if key in self.connection_pools:
            await self.connection_pools[key].stop()

        pool = MCPConnectionPool(config, self._pool_size)
        self.connection_pools[key] = pool
        await pool.start()

    async def stop_all(self):
        """Stop all connection pools and the health monitor."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        for pool in self.connection_pools.values():
            await pool.stop()
        self.connection_pools.clear()

    def start_health_monitor(self):
        """Start the background health monitoring loop."""
        if self._health_task and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(self._health_loop())

    async def _health_loop(self):
        """Periodically check pool health and restart dead connections."""
        while True:
            try:
                await asyncio.sleep(self._health_interval)
                for key, pool in list(self.connection_pools.items()):
                    if not pool.has_healthy_connection:
                        logger.warning(f"Pool {key} has no healthy connections, restarting...")
                        try:
                            await pool.stop()
                            await pool.start()
                        except Exception as e:
                            logger.error(f"Failed to restart pool {key}: {e}")
                    else:
                        unhealthy = pool.pool_size - pool.healthy_count
                        if unhealthy > 0:
                            logger.info(f"Pool {key}: {pool.healthy_count}/{pool.pool_size} healthy")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitor error: {e}")

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict, scope: str = "global") -> dict:
        """Call an MCP tool through the connection pool."""
        key = f"{server_name}::{scope}"
        pool = self.connection_pools.get(key)
        # Fall back to global scope if agent-scoped pool not found
        if not pool and scope != "global":
            key = f"{server_name}::global"
            pool = self.connection_pools.get(key)

        if not pool:
            return {"error": f"MCP server '{server_name}' is not connected (scope={scope})."}

        conn = await pool.acquire()
        try:
            if not conn.session:
                return {"error": f"MCP server '{server_name}' session is not active."}
            result = await conn.session.call_tool(tool_name, arguments)
            return {"result": _format_result(result)}
        except Exception as e:
            return {"error": f"MCP tool call failed: {str(e)}"}
        finally:
            pool.release()

    async def list_tools(self, scope: str = "global") -> list[dict]:
        """List all available tools across all servers for a scope."""
        all_tools = []
        seen_servers = set()
        for key, pool in self.connection_pools.items():
            server_name, pool_scope = parse_connection_key(key)
            if pool_scope != scope and pool_scope != "global":
                continue
            if server_name in seen_servers:
                continue
            seen_servers.add(server_name)

            if not pool.has_healthy_connection:
                continue

            conn = await pool.acquire()
            try:
                if conn.session:
                    result = await conn.session.list_tools()
                    for mcp_tool in result.tools:
                        bond_tool_name = f"mcp_{server_name}_{mcp_tool.name}"
                        all_tools.append({
                            "name": bond_tool_name,
                            "server": server_name,
                            "mcp_name": mcp_tool.name,
                            "description": mcp_tool.description or f"MCP tool {mcp_tool.name}",
                            "parameters": mcp_tool.inputSchema,
                        })
            except Exception as e:
                logger.error(f"Failed to list tools for {server_name}: {e}")
            finally:
                pool.release()
        return all_tools

    async def refresh_tools(self, registry: "ToolRegistry"):
        """Fetch tools from all servers and register them (host-side only)."""
        self._dynamic_definitions.clear()
        for key, pool in self.connection_pools.items():
            server_name, _ = parse_connection_key(key)
            if not pool.has_healthy_connection:
                continue

            conn = await pool.acquire()
            try:
                if not conn.session:
                    continue

                result = await conn.session.list_tools()
                for mcp_tool in result.tools:
                    bond_tool_name = f"mcp_{server_name}_{mcp_tool.name}"

                    self._dynamic_definitions[bond_tool_name] = {
                        "type": "function",
                        "function": {
                            "name": bond_tool_name,
                            "description": mcp_tool.description or f"MCP tool {mcp_tool.name}",
                            "parameters": mcp_tool.inputSchema
                        }
                    }

                    registry.register(
                        bond_tool_name,
                        self._create_handler(server_name, mcp_tool.name)
                    )

                    class_name = "".join(x.capitalize() for x in bond_tool_name.replace("-", "_").split("_"))
                    self._bond_to_class_map[class_name] = bond_tool_name
                    self._dynamic_definitions[bond_tool_name]["class_name"] = class_name

                    logger.info(f"Registered MCP tool: {bond_tool_name}")
            except Exception as e:
                logger.error(f"Failed to refresh tools for {server_name}: {e}")
            finally:
                pool.release()

    def _create_handler(self, server_name: str, mcp_tool_name: str):
        async def handler(arguments: dict, context: dict) -> dict:
            return await self.call_tool(server_name, mcp_tool_name, arguments)
        return handler

    def get_definitions(self, tool_names: list[str]) -> list[dict]:
        return [self._dynamic_definitions[name] for name in tool_names if name in self._dynamic_definitions]

    def resolve_tool_name(self, class_name: str) -> str:
        if class_name in self._bond_to_class_map:
            return self._bond_to_class_map[class_name]
        import re
        return re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()

    def get_pool_status(self) -> dict[str, dict]:
        """Get status of all connection pools."""
        status = {}
        for key, pool in self.connection_pools.items():
            status[key] = {
                "server": pool.config.name,
                "pool_size": pool.pool_size,
                "healthy": pool.healthy_count,
                "has_healthy": pool.has_healthy_connection,
            }
        return status
