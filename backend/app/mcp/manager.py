from __future__ import annotations
import asyncio
import logging
import os
from contextlib import AsyncExitStack
from typing import Dict, List, Optional, Any, TYPE_CHECKING, Union, Type
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field, create_model

if TYPE_CHECKING:
    from app.agent.tools import ToolRegistry

logger = logging.getLogger("bond.mcp")

class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: List[str] = []
    env: Dict[str, str] = {}

class MCPConnection:
    """Manages a single MCP server connection."""
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

    async def start(self):
        async with self._lock:
            if self.session:
                return
            logger.info(f"Starting MCP server: {self.config.name}")
            try:
                read, write = await self._exit_stack.enter_async_context(stdio_client(self.params))
                self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
                await self.session.initialize()
            except Exception as e:
                logger.error(f"Failed to start MCP server {self.config.name}: {e}")
                await self._exit_stack.aclose()
                raise

    async def stop(self):
        async with self._lock:
            if self.session:
                logger.info(f"Stopping MCP server: {self.config.name}")
                # mcp sessions/stdio clients can be very picky about cleanup
                # especially with anyio's cancel scopes. 
                # We try a clean close but don't let it crash the manager.
                try:
                    await asyncio.wait_for(self._exit_stack.aclose(), timeout=2.0)
                except (asyncio.TimeoutError, Exception) as e:
                    logger.debug(f"Non-critical cleanup error for {self.config.name}: {e}")
                
                self.session = None
                self._exit_stack = AsyncExitStack()

class MCPManager:
    """Manages multiple MCP connections and tool integration."""
    def __init__(self):
        self.connections: Dict[str, MCPConnection] = {}
        self._dynamic_definitions: Dict[str, dict] = {} # tool_name -> json_schema
        self._bond_to_class_map: Dict[str, str] = {} # ClassName -> bond_tool_name

    async def add_server(self, config: MCPServerConfig):
        conn = MCPConnection(config)
        self.connections[config.name] = conn
        await conn.start()

    async def stop_all(self):
        for conn in self.connections.values():
            await conn.stop()

    async def refresh_tools(self, registry: ToolRegistry):
        """Fetch tools from all servers and register them."""
        self._dynamic_definitions.clear()
        for server_name, conn in self.connections.items():
            if not conn.session:
                continue
            
            try:
                result = await conn.session.list_tools()
                for mcp_tool in result.tools:
                    # Prefix tool name to avoid collisions
                    bond_tool_name = f"mcp_{server_name}_{mcp_tool.name}"
                    
                    # Store definition for LLM
                    self._dynamic_definitions[bond_tool_name] = {
                        "type": "function",
                        "function": {
                            "name": bond_tool_name,
                            "description": mcp_tool.description or f"MCP tool {mcp_tool.name}",
                            "parameters": mcp_tool.inputSchema
                        }
                    }

                    # Register handler
                    registry.register(
                        bond_tool_name, 
                        self._create_handler(server_name, mcp_tool.name)
                    )
                    # Cache the mapping for easy retrieval during Instructor response processing
                    class_name = "".join(x.capitalize() for x in bond_tool_name.replace("-", "_").split("_"))
                    self._bond_to_class_map[class_name] = bond_tool_name
                    self._dynamic_definitions[bond_tool_name]["class_name"] = class_name
                    
                    logger.info(f"Registered MCP tool: {bond_tool_name}")
            except Exception as e:
                logger.error(f"Failed to refresh tools for {server_name}: {e}")

    def _create_handler(self, server_name: str, mcp_tool_name: str):
        async def handler(arguments: dict, context: dict) -> dict:
            conn = self.connections.get(server_name)
            if not conn or not conn.session:
                return {"error": f"MCP server '{server_name}' is not connected."}
            
            try:
                result = await conn.session.call_tool(mcp_tool_name, arguments)
                # MCP results can be complex (content list), we'll simplify for now
                # Result can contain text, image, or resource content
                output = []
                for content in result.content:
                    if hasattr(content, 'text') and content.text:
                        output.append(content.text)
                    elif hasattr(content, 'data') and content.data:
                        output.append(f"[Data content: {len(content.data)} bytes]")
                    elif hasattr(content, 'uri') and content.uri:
                        output.append(f"[Resource: {content.uri}]")
                
                return {"result": "\n".join(output) if output else "Tool executed successfully (no output)."}
            except Exception as e:
                return {"error": f"MCP tool call failed: {str(e)}"}
        
        return handler

    def get_definitions(self, tool_names: list[str]) -> list[dict]:
        return [self._dynamic_definitions[name] for name in tool_names if name in self._dynamic_definitions]

    def resolve_tool_name(self, class_name: str) -> str:
        """Resolve a Pydantic class name back to its bond_tool_name."""
        # Check the bond_to_class_map first (contains PascalCase class name -> snake_case bond name)
        if class_name in self._bond_to_class_map:
            return self._bond_to_class_map[class_name]
        
        # Static tools like 'Respond' or 'FileRead' are in the map too if they use this manager
        # But they don't. So we fall back to regex for native tools.
        import re
        return re.sub(r'(?<!^)(?=[A-Z])', '_', class_name).lower()

    def get_pydantic_models(self, tool_names: list[str]) -> list[Type[BaseModel]]:
        """Generate Pydantic models for requested MCP tools."""
        models: list[Type[BaseModel]] = []
        for name in tool_names:
            if name not in self._dynamic_definitions:
                continue
            
            # If we already have a Pydantic model in our INSTRUCTOR_TOOL_MAP, don't recreate
            # Actually MCP tools are always prefixed so they won't be in the map.
            
            schema = self._dynamic_definitions[name]["function"]["parameters"]
            
            # Simple dynamic model generation using pydantic.create_model
            # Note: This is a basic implementation. Complex nested schemas might need recursion.
            fields = {}
            for prop_name, prop_info in schema.get("properties", {}).items():
                prop_type = prop_info.get("type")
                description = prop_info.get("description", "")
                
                field_type: Any = Any
                if prop_type == "string":
                    field_type = str
                elif prop_type == "integer":
                    field_type = int
                elif prop_type == "number":
                    field_type = float
                elif prop_type == "boolean":
                    field_type = bool
                elif prop_type == "array":
                    field_type = list
                elif prop_type == "object":
                    field_type = dict

                if prop_name in schema.get("required", []):
                    fields[prop_name] = (field_type, Field(..., description=description))
                else:
                    fields[prop_name] = (Optional[field_type], Field(None, description=description))

            # Create the model class
            class_name = self._dynamic_definitions[name]["class_name"]
            from pydantic import BaseModel
            try:
                model = create_model(class_name, __base__=BaseModel, **fields)
                model.__doc__ = self._dynamic_definitions[name]["function"]["description"]
                models.append(model)
            except Exception as e:
                logger.error(f"Failed to create Pydantic model for {name}: {e}")
                continue
            
        return models
