import json
import logging
import os
import asyncio
from typing import Dict, List, Optional
from pydantic import BaseModel

logger = logging.getLogger("bond.mcp.manager")

class MCPServerConfig(BaseModel):
    name: string
    command: string
    args: list[string] = []
    env: dict[string, string] = {}
    enabled: bool = True

class MCPManager:
    def __init__(self):
        self.servers: Dict[string, MCPServerConfig] = {}
        self._load_servers()

    def _load_servers(self):
        # Placeholder for loading from SpacetimeDB or config
        pass

    async def add_server(self, config: MCPServerConfig):
        self.servers[config.name] = config
        logger.info(f"Added MCP server: {config.name}")

    async def sync_from_claude(self):
        """Import MCP servers from Claude config."""
        config_path = "/.claude/mcp-config.json"
        if not os.path.exists(config_path):
            logger.warning(f"Claude config not found at {config_path}")
            return

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            
            mcp_servers = config.get("mcpServers", {})
            for name, server_info in mcp_servers.items():
                bond_config = MCPServerConfig(
                    name=name,
                    command=server_info.get("command", ""),
                    args=server_info.get("args", []),
                    env=server_info.get("env", {}),
                    enabled=True
                )
                await self.add_server(bond_config)
                logger.info(f"Imported MCP server from Claude: {name}")
                
        except Exception as e:
            logger.error(f"Failed to sync from Claude: {e}")

    async def get_servers(self) -> List[MCPServerConfig]:
        return list(self.servers.values())
