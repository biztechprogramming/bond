# Design Doc 017: MCP Client Integration

## Goal
Integrate a Model Context Protocol (MCP) client into Bond to enable the agent to discover and use any MCP-compliant tool servers (e.g., Google Drive, Slack, GitHub, local database connectors) without requiring custom Python tool code for each integration.

## Context
Bond's current tool system is "hardcoded" in Python. While robust, it requires manual implementation for every new capability. MCP is an open standard that allows external servers to expose tools to LLM agents.

By adding an MCP client, Bond unlocks:
1.  **Instant Tool Ecosystem:** Access to hundreds of community-built MCP servers.
2.  **External Resource Access:** Standardized way to browse files, search databases, or interact with APIs.
3.  **Local-First Extensibility:** Users can run their own MCP servers locally and Bond will "just see" them.

## Proposed Changes

### 1. New Dependency
- Add `mcp` (Python SDK) to `pyproject.toml`.
- Add `anyio` (if not already present) for async MCP communication.

### 2. MCP Client Manager (`backend/app/mcp/manager.py`)
- Create a manager to handle connections to multiple MCP servers.
- Support "stdio" (local process) and "sse" (remote server) transports.
- Configuration in `bond.json` to define which MCP servers to start on boot:
  ```json
  "mcp_servers": {
    "sqlite-explorer": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db", "./data/knowledge.db"]
    }
  }
  ```

### 3. Tool Registry Integration (`backend/app/agent/tools/registry.py`)
- Update `ToolRegistry` to dynamically fetch tool definitions from connected MCP servers.
- Map MCP tool calls to the internal `ToolCall` objects.
- Use **Instructor** to validate the dynamically fetched schemas.

### 4. Implementation Phases

#### Phase 1: Core Client & Stdio Transport
- Implement the basic MCP client that can launch a local server process.
- Map one test MCP server (e.g., `everything` server) into Bond's tool list.

#### Phase 2: Dynamic Registration
- Automate discovery of MCP tools during the `agent_turn` setup.
- Inject MCP tool descriptions into the system prompt.

#### Phase 3: Configuration UI
- Add a "Servers" tab in the Bond frontend to manage, start/stop, and debug MCP server connections.

## Success Criteria
- [ ] Bond can successfully list tools from a local MCP server.
- [ ] Agent can call an MCP tool and receive a formatted result.
- [ ] Instructor correctly validates arguments for dynamically discovered MCP tools.
- [ ] No performance degradation in the main agent loop.
