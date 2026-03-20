# Design Doc 054: Host-Side MCP with Proxy API

## Status
Proposed

## Problem

The current MCP integration (Doc 017) runs MCP servers **inside** each Docker worker container. This has several problems:

1. **MCP servers must be installed in the container image.** Every MCP server binary (e.g., `npx @modelcontextprotocol/server-sqlite`) must exist inside the Docker image. This bloats images, requires rebuilds for new servers, and means every agent container redundantly ships the same MCP dependencies.

2. **MCP servers can't access host resources.** A SQLite MCP server inside the container can only see databases mounted into that container. A GitHub MCP server can't use the host's `gh` CLI auth. Filesystem MCP servers can't browse the host's filesystem. The whole point of MCP — connecting to local tools and data — is undermined by containerization.

3. **Per-container MCP process overhead.** Each container spawns its own set of MCP server subprocesses. If 5 agents are running, you get 5 copies of each MCP server, each holding its own connections and state.

4. **Startup latency.** The container must fetch MCP configs from Gateway, then `npx install` or spawn each server before the first tool call. This adds seconds to cold-start turns.

5. **No shared state between agents.** Each container's MCP sessions are isolated. If an MCP server has state (e.g., an open database connection), it can't be shared.

## Proposed Design

Move all MCP server lifecycle management to the **Backend** (host process). Worker containers access MCP tools via an HTTP proxy API on the Backend — the same way they already access persistence, settings, and API keys.

### Architecture

```
┌──────────────┐     ┌────────────────────────────┐     ┌──────────────────────────────┐
│  Worker      │     │  Gateway :18789             │     │  Backend (Host) :18790       │
│  Container   │     │                             │     │                              │
│  :18791      │     │  Permission Broker          │     │  MCPManager                  │
│              │     │  ┌────────────────────────┐ │     │  ┌────────────────────────┐  │
│  mcp_proxy   │────▶│  │ 1. Validate agent token│ │     │  │ ConnectionPool         │  │
│  tool        │     │  │ 2. Evaluate MCP policy │ │────▶│  │  ["sqlite::global"]    │  │
│              │     │  │ 3. Audit log           │ │     │  │   ├─ conn[0] ──stdio──▶│──│──▶ MCP Server
│              │◀────│  │ 4. Forward to Backend  │ │◀────│  │   ├─ conn[1] ──stdio──▶│──│──▶ MCP Server
│              │     │  └────────────────────────┘ │     │  │   └─ conn[2] ──stdio──▶│──│──▶ MCP Server
│              │     │                             │     │  │ ConnectionPool         │  │
│              │     │  POST /broker/mcp           │     │  │  ["github::agent-A"]   │  │
│              │     │                             │     │  │   └─ conn[0] ──stdio──▶│──│──▶ MCP Server
│              │     │                             │     │  └────────────────────────┘  │
│              │     │                             │     │  health monitor (30s loop)   │
└──────────────┘     └────────────────────────────┘     └──────────────────────────────┘
                                   │                                    │
                                   ▼                                    ▼
                     ┌──────────────────────────┐         ┌──────────────────────┐
                     │  Audit Log (JSONL)        │         │  SpacetimeDB :3000   │
                     │  broker-audit.jsonl        │         │  - mcp_servers       │
                     └──────────────────────────┘         │  - mcp_tool_acls     │
                                                          └──────────────────────┘
```

**Key change:** Worker containers never spawn MCP subprocesses. MCP tool calls route through the Gateway's **Permission Broker** — the same security layer that gates `exec` commands. The broker authenticates the agent, evaluates MCP-specific policy rules, audit-logs the call, and forwards to the Backend's MCPManager for execution.

**Why route through the Broker (not direct to Backend)?** MCP tools run on the host with full access to the filesystem, network, and credentials. This is the same trust boundary as shell execution. The Permission Broker already handles this for `exec` — agent tokens, policy evaluation, audit logging. MCP calls need the same treatment. Without broker gating, any container could call any MCP tool with no authentication, no policy check, and no audit trail.

```
Worker → POST Gateway:18789/broker/mcp            → broker: auth + policy + audit
Gateway broker → POST Backend:18790/api/v1/mcp/proxy/call  → MCPManager → stdio
Worker → GET  Gateway:18789/broker/mcp/tools       → broker: auth + list tools
Gateway broker → GET  Backend:18790/api/v1/mcp/proxy/tools  → MCPManager → list_tools
```

## Detailed Design

### 1. Permission Broker: MCP Gating

The Gateway's Permission Broker (`gateway/src/broker/`) already provides:
- **Agent tokens** — HMAC-signed JWTs with `sub` (agent ID) and `sid` (session ID)
- **Policy engine** — glob-pattern rules with allow/deny/prompt decisions
- **Audit logging** — append-only JSONL with timestamps, agent, decision, and context

MCP calls reuse this entire infrastructure. The broker gets two new endpoints and an MCP-specific policy layer.

#### Broker Endpoints

Add to `gateway/src/broker/router.ts`:

```typescript
// POST /broker/mcp — execute an MCP tool (auth + policy + audit)
router.post("/mcp", async (req: Request, res: Response) => {
  const { tool_name, arguments: args } = req.body || {};
  const agent = req.agentToken!;  // set by authMiddleware

  if (!tool_name || typeof tool_name !== "string") {
    res.status(400).json({ error: "tool_name is required" });
    return;
  }

  // Evaluate MCP policy
  const decision = mcpPolicy.evaluate(tool_name, args, agent.sub, agent.sid);

  // Phase 1: treat "prompt" as deny (same as exec)
  if (decision.decision === "prompt") {
    decision.decision = "deny";
    decision.reason = "Requires user approval (not yet implemented)";
  }

  // Audit log — always, regardless of decision
  audit.log({
    timestamp: new Date().toISOString(),
    agent_id: agent.sub,
    session_id: agent.sid,
    command: `mcp:${tool_name}`,
    decision: decision.decision,
    policy_rule: decision.source,
  });

  if (decision.decision === "deny") {
    res.json({
      status: "denied",
      decision: "deny",
      reason: decision.reason,
      policy_rule: decision.source,
    });
    return;
  }

  // Forward to Backend MCPManager
  try {
    const backendRes = await fetch(`${backendUrl}/api/v1/mcp/proxy/call`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool_name, arguments: args, agent_id: agent.sub }),
    });
    const result = await backendRes.json();

    audit.log({
      timestamp: new Date().toISOString(),
      agent_id: agent.sub,
      session_id: agent.sid,
      command: `mcp:${tool_name}`,
      decision: "allow",
      policy_rule: decision.source,
      exit_code: result.is_error ? 1 : 0,
    });

    res.json({ status: "ok", decision: "allow", ...result });
  } catch (err: any) {
    res.status(502).json({ status: "error", reason: err.message });
  }
});

// GET /broker/mcp/tools — list available tools (auth required, no policy check)
router.get("/mcp/tools", async (req: Request, res: Response) => {
  const agent = req.agentToken!;

  try {
    const backendRes = await fetch(
      `${backendUrl}/api/v1/mcp/proxy/tools?agent_id=${agent.sub}`,
    );
    const tools = await backendRes.json();

    // Filter out tools this agent is denied access to
    const filtered = tools.filter((tool: any) => {
      const decision = mcpPolicy.evaluate(tool.name, {}, agent.sub, agent.sid);
      return decision.decision !== "deny";
    });

    res.json(filtered);
  } catch (err: any) {
    res.status(502).json({ error: err.message });
  }
});
```

The `GET /broker/mcp/tools` endpoint filters the tool list through the policy engine — agents only see tools they're allowed to call. This prevents the LLM from even knowing about denied tools.

#### MCP Policy Engine

New file `gateway/src/broker/mcp-policy.ts`:

```typescript
/**
 * MCP Policy Engine — controls which agents can call which MCP tools.
 *
 * Evaluates rules in order, first match wins. Default: allow (backward compatible).
 * Rules can match on:
 *   - tool_name patterns (glob): "mcp_github_*", "mcp_sqlite-explorer_query"
 *   - server patterns (glob): "mcp_dangerous-server_*"
 *   - agent_id patterns: exact match or glob
 */

import type { PolicyDecision } from "./types.js";

export interface MCPPolicyRule {
  tools: string[];           // glob patterns for bond tool names
  agents?: string[];         // glob patterns for agent IDs (omit = all agents)
  decision: "allow" | "deny" | "prompt";
  reason?: string;
}

const DEFAULT_MCP_RULES: MCPPolicyRule[] = [
  // Example: deny all agents from calling dangerous-server tools
  // { tools: ["mcp_dangerous-server_*"], decision: "deny", reason: "Server is restricted" },

  // Example: only deploy agents can use the deployment MCP server
  // { tools: ["mcp_deploy-tools_*"], agents: ["deploy-*"], decision: "allow" },
  // { tools: ["mcp_deploy-tools_*"], decision: "deny", reason: "Deploy tools restricted to deploy agents" },

  // Default: allow all MCP tools (backward compatible)
  { tools: ["*"], decision: "allow" },
];

export class MCPPolicyEngine {
  private rules: Array<{
    toolPatterns: RegExp[];
    agentPatterns?: RegExp[];
    decision: "allow" | "deny" | "prompt";
    reason?: string;
    index: number;
  }>;

  constructor(rules?: MCPPolicyRule[]) {
    const effectiveRules = rules || DEFAULT_MCP_RULES;
    this.rules = effectiveRules.map((rule, index) => ({
      toolPatterns: rule.tools.map(globToRegex),
      agentPatterns: rule.agents?.map(globToRegex),
      decision: rule.decision,
      reason: rule.reason,
      index,
    }));
  }

  evaluate(
    toolName: string,
    _args: Record<string, any>,
    agentId: string,
    _sessionId: string,
  ): PolicyDecision {
    for (const rule of this.rules) {
      const toolMatches = rule.toolPatterns.some((p) => p.test(toolName));
      if (!toolMatches) continue;

      // If rule specifies agents, check agent match
      if (rule.agentPatterns) {
        const agentMatches = rule.agentPatterns.some((p) => p.test(agentId));
        if (!agentMatches) continue;
      }

      return {
        decision: rule.decision,
        reason: rule.reason,
        source: `mcp-policy#rule-${rule.index}`,
      };
    }

    // Default allow (no matching rule)
    return {
      decision: "allow",
      source: "mcp-policy#default-allow",
    };
  }

  /**
   * Load rules from SpacetimeDB mcp_policy_rules table.
   * Called on startup and on /reload.
   */
  async loadFromDB(spacetimedbUrl: string, moduleName: string, token?: string): Promise<void> {
    // Future: load rules from SpacetimeDB instead of hardcoded defaults
    // This allows the admin to manage MCP policies via the frontend
  }
}

function globToRegex(glob: string): RegExp {
  let regex = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*") regex += ".*";
    else if (ch === "?") regex += ".";
    else if ("[{()+^$|\\.]".includes(ch)) regex += "\\" + ch;
    else regex += ch;
  }
  return new RegExp("^" + regex + "$");
}
```

#### Policy Rule Examples

```typescript
// Only the "research" agent can use the web-scraper MCP server
{ tools: ["mcp_web-scraper_*"], agents: ["research-*"], decision: "allow" },
{ tools: ["mcp_web-scraper_*"], decision: "deny", reason: "Web scraper restricted to research agents" },

// Deny all agents from using the filesystem MCP server's delete tool
{ tools: ["mcp_filesystem_delete_file"], decision: "deny", reason: "File deletion via MCP is blocked" },

// Deploy agents can only use deploy-related MCP servers
{ tools: ["mcp_deploy-*"], agents: ["deploy-*"], decision: "allow" },
{ tools: ["mcp_deploy-*"], decision: "deny", reason: "Deploy MCP tools restricted" },

// All other MCP tools are allowed by default
{ tools: ["*"], decision: "allow" },
```

#### Audit Log Format

MCP calls produce the same JSONL audit entries as `exec` calls, with `command` prefixed by `mcp:`:

```jsonl
{"timestamp":"2026-03-20T17:30:00Z","agent_id":"01HABC...","session_id":"01HXYZ...","command":"mcp:mcp_sqlite-explorer_query","decision":"allow","policy_rule":"mcp-policy#rule-3","exit_code":0}
{"timestamp":"2026-03-20T17:30:05Z","agent_id":"01HDEF...","session_id":"01HXYZ...","command":"mcp:mcp_web-scraper_fetch","decision":"deny","policy_rule":"mcp-policy#rule-1"}
```

This means existing audit analysis tools, dashboards, and grep-based queries work on MCP calls with no changes.

### 2. Backend: MCP Proxy Endpoints

Add to `backend/app/api/v1/mcp.py`:

```python
class MCPToolCallRequest(BaseModel):
    tool_name: str          # e.g. "mcp_sqlite-explorer_query"
    arguments: dict = {}
    agent_id: str           # for ACL check + connection routing


@router.get("/proxy/tools")
async def list_mcp_tools(agent_id: Optional[str] = None):
    """Return all available MCP tools for an agent (global + agent-specific)."""
    from backend.app.mcp import mcp_manager

    # Ensure servers for this agent are loaded
    await mcp_manager.ensure_servers_loaded(agent_id=agent_id)

    # Collect tool definitions from all reachable connections
    tools = []
    for key, pool in mcp_manager.connection_pools.items():
        server_name, scope = mcp_manager.parse_connection_key(key)

        # Only return global pools + pools scoped to this agent
        if scope != "global" and scope != agent_id:
            continue

        for tool in await pool.list_tools():
            bond_name = f"mcp_{server_name}_{tool.name}"
            tools.append({
                "name": bond_name,
                "server": server_name,
                "mcp_name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema,
            })
    return tools


@router.post("/proxy/call")
async def call_mcp_tool(req: MCPToolCallRequest):
    """Execute an MCP tool via the host-side MCPManager.

    Checks agent ACLs before execution. Routes to the correct
    connection pool based on agent_id scoping.
    """
    from backend.app.mcp import mcp_manager

    # ACL check
    allowed = await check_mcp_acl(req.agent_id, req.tool_name)
    if not allowed:
        return {"error": f"Agent {req.agent_id} not authorized for tool {req.tool_name}"}

    result = await mcp_manager.call_tool(
        bond_tool_name=req.tool_name,
        arguments=req.arguments,
        agent_id=req.agent_id,
    )
    return result
```

### 2. MCPManager: Connection Pooling, Auto-Recovery, Agent Isolation

Refactor `backend/app/mcp/manager.py` to support concurrent access, agent-scoped connections, and self-healing:

```python
class MCPConnectionPool:
    """Maintains N instances of the same MCP server for concurrent access.

    stdio is inherently serial per connection. A pool of connections allows
    multiple agents (or parallel tool calls within one agent) to execute
    concurrently without blocking each other.
    """

    def __init__(self, config: MCPServerConfig, pool_size: int = 1):
        self.config = config
        self.pool_size = pool_size
        self._connections: list[MCPConnection] = []
        self._semaphore = asyncio.Semaphore(pool_size)
        self._round_robin = 0

    async def start(self):
        """Spawn pool_size instances of the MCP server subprocess."""
        for _ in range(self.pool_size):
            conn = MCPConnection(self.config)
            await conn.start()
            self._connections.append(conn)

    async def stop(self):
        """Stop all connections in the pool."""
        for conn in self._connections:
            await conn.stop()
        self._connections.clear()

    async def list_tools(self):
        """List tools from one healthy connection (tools are the same across all)."""
        for conn in self._connections:
            if conn.session:
                return (await conn.session.list_tools()).tools
        return []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Acquire a connection from the pool, execute, release."""
        async with self._semaphore:
            conn = self._pick_connection()
            return await conn.call_tool_safe(tool_name, arguments)

    def _pick_connection(self) -> MCPConnection:
        """Round-robin connection selection."""
        idx = self._round_robin % len(self._connections)
        self._round_robin += 1
        return self._connections[idx]


class MCPConnection:
    """Manages a single MCP server connection with auto-recovery."""

    async def call_tool_safe(self, tool_name: str, arguments: dict) -> dict:
        """Call tool with auto-reconnect on subprocess death."""
        try:
            result = await self.session.call_tool(tool_name, arguments)
            return self._format_result(result)
        except Exception as e:
            logger.warning(
                "MCP server %s call failed (%s), attempting restart...",
                self.config.name, e,
            )
            await self.stop()
            await self.start()
            try:
                result = await self.session.call_tool(tool_name, arguments)
                return self._format_result(result)
            except Exception as e2:
                return {"error": f"MCP tool call failed after restart: {e2}"}

    def _format_result(self, result) -> dict:
        """Convert MCP result content list to serializable dict."""
        output = []
        for content in result.content:
            if hasattr(content, 'text') and content.text:
                output.append(content.text)
            elif hasattr(content, 'data') and content.data:
                output.append(f"[Data content: {len(content.data)} bytes]")
            elif hasattr(content, 'uri') and content.uri:
                output.append(f"[Resource: {content.uri}]")
        return {
            "result": "\n".join(output) if output else "Tool executed successfully (no output).",
            "is_error": result.isError if hasattr(result, 'isError') else False,
        }


class MCPManager:
    """Manages MCP server pools with agent-scoped isolation."""

    def __init__(self):
        self.connection_pools: dict[str, MCPConnectionPool] = {}
        self._health_task: asyncio.Task | None = None

    def _connection_key(self, server_name: str, agent_id: str | None) -> str:
        """Unique key for a connection pool.

        Global servers (agent_id=None) use 'global' scope.
        Agent-specific servers use the agent_id as scope.
        """
        scope = agent_id if agent_id else "global"
        return f"{server_name}::{scope}"

    def parse_connection_key(self, key: str) -> tuple[str, str]:
        """Parse a connection key back into (server_name, scope)."""
        parts = key.rsplit("::", 1)
        return parts[0], parts[1] if len(parts) > 1 else "global"

    async def ensure_servers_loaded(self, agent_id: str | None = None):
        """Load and start MCP servers for an agent if not already running.

        Loads global servers plus agent-specific servers. Idempotent — skips
        servers that are already connected.
        """
        from backend.app.core.spacetimedb import get_stdb
        stdb = get_stdb()

        rows = await stdb.query("SELECT * FROM mcp_servers WHERE enabled = true")

        for row in rows:
            row_agent_id = row.get("agent_id")
            is_global = (
                row_agent_id is None
                or row_agent_id == ""
                or (isinstance(row_agent_id, dict) and "none" in row_agent_id)
            )
            is_for_agent = (not is_global and row_agent_id == agent_id)

            if not is_global and not is_for_agent:
                continue

            scope_agent_id = None if is_global else agent_id
            key = self._connection_key(row["name"], scope_agent_id)

            if key in self.connection_pools:
                continue  # Already running

            config = MCPServerConfig(
                name=row["name"],
                command=row["command"],
                args=json.loads(row["args"]),
                env=json.loads(row["env"]),
                enabled=True,
            )
            pool_size = row.get("pool_size", 1) or 1

            pool = MCPConnectionPool(config, pool_size=pool_size)
            try:
                await pool.start()
                self.connection_pools[key] = pool
                logger.info("Started MCP pool: %s (size=%d)", key, pool_size)
            except Exception as e:
                logger.error("Failed to start MCP pool %s: %s", key, e)

    async def call_tool(
        self,
        bond_tool_name: str,
        arguments: dict,
        agent_id: str,
    ) -> dict:
        """Route a tool call to the correct connection pool.

        Tries agent-scoped pool first, falls back to global pool.
        """
        server_name, mcp_tool_name = self._parse_bond_tool_name(bond_tool_name)

        # Try agent-specific pool first, fall back to global
        key = self._connection_key(server_name, agent_id)
        if key not in self.connection_pools:
            key = self._connection_key(server_name, None)

        pool = self.connection_pools.get(key)
        if not pool:
            return {"error": f"MCP server '{server_name}' not connected"}

        return await pool.call_tool(mcp_tool_name, arguments)

    def _parse_bond_tool_name(self, bond_tool_name: str) -> tuple[str, str]:
        """Parse 'mcp_server-name_tool-name' into (server_name, tool_name).

        Matches against known connection pool keys to handle server names
        containing underscores.
        """
        if not bond_tool_name.startswith("mcp_"):
            raise ValueError(f"Not an MCP tool name: {bond_tool_name}")

        remainder = bond_tool_name[4:]  # strip 'mcp_'

        # Find longest matching server name across all pools
        for key in self.connection_pools:
            server_name, _ = self.parse_connection_key(key)
            if remainder.startswith(f"{server_name}_"):
                mcp_tool_name = remainder[len(server_name) + 1:]
                return server_name, mcp_tool_name

        raise ValueError(f"No matching MCP server for tool: {bond_tool_name}")

    # ── Health monitoring ──

    async def start_health_monitor(self):
        """Start background health loop. Call once at Backend startup."""
        self._health_task = asyncio.create_task(self._health_loop())

    async def _health_loop(self):
        """Periodically check MCP server health, restart dead connections."""
        while True:
            await asyncio.sleep(30)
            for key, pool in list(self.connection_pools.items()):
                for i, conn in enumerate(pool._connections):
                    if conn.session is None:
                        logger.warning(
                            "MCP connection %s[%d] dead, restarting...", key, i,
                        )
                        try:
                            await conn.start()
                        except Exception as e:
                            logger.error(
                                "Failed to restart MCP connection %s[%d]: %s",
                                key, i, e,
                            )

    async def stop_all(self):
        """Stop all pools and the health monitor."""
        if self._health_task:
            self._health_task.cancel()
            self._health_task = None
        for pool in self.connection_pools.values():
            await pool.stop()
        self.connection_pools.clear()
```

### 3. Worker: Replace MCPManager with HTTP Proxy Client

Currently the worker imports `mcp_manager`, spawns MCP servers locally, and registers tool handlers into the registry. **All of that is removed.**

New file `backend/app/agent/tools/mcp_proxy.py`:

```python
"""MCP Proxy Client for container-side workers.

Routes all MCP tool calls through the Gateway's Permission Broker,
which authenticates the agent, evaluates MCP policy, audit-logs the
call, and forwards to the Backend's MCPManager for execution.

Flow: Worker → Gateway Broker (/broker/mcp) → Backend (/api/v1/mcp/proxy/call) → MCP stdio
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.tools.mcp_proxy")


class MCPProxyClient:
    """Proxy MCP tool calls through the Gateway's Permission Broker.

    The broker validates the agent token, evaluates MCP policy rules,
    and audit-logs every call before forwarding to the Backend.
    """

    def __init__(self, gateway_url: str, agent_id: str, agent_token: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self._tools_cache: list[dict] | None = None
        self._client = httpx.AsyncClient(
            base_url=f"{self.gateway_url}/broker",
            headers={"Authorization": f"Bearer {agent_token}"},
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0),
        )

    async def close(self):
        """Close the persistent HTTP client."""
        await self._client.aclose()

    async def list_tools(self) -> list[dict]:
        """Fetch available MCP tools through the broker.

        The broker filters out tools this agent is denied access to,
        so the LLM never sees tools it can't call.
        """
        resp = await self._client.get("/mcp/tools")
        resp.raise_for_status()
        self._tools_cache = resp.json()
        logger.info("Loaded %d MCP tools via broker (agent=%s)", len(self._tools_cache), self.agent_id)
        return self._tools_cache

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Execute an MCP tool via the broker.

        The broker will:
        1. Validate the agent token
        2. Evaluate MCP policy rules for this agent + tool
        3. Audit-log the call (allow or deny)
        4. If allowed, forward to Backend MCPManager
        """
        resp = await self._client.post(
            "/mcp",
            json={"tool_name": tool_name, "arguments": arguments},
        )
        resp.raise_for_status()
        result = resp.json()

        # Broker returns { status: "denied", reason: "..." } if policy blocks it
        if result.get("status") == "denied":
            logger.warning(
                "MCP call denied by broker: tool=%s agent=%s reason=%s",
                tool_name, self.agent_id, result.get("reason"),
            )
            return {
                "error": f"Permission denied: {result.get('reason', 'blocked by policy')}",
                "is_error": True,
            }

        return result

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI-format tool definitions from cached tool list."""
        if not self._tools_cache:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in self._tools_cache
        ]

    def register_proxy_handlers(self, registry):
        """Register a handler for each MCP tool into the native registry.

        Each handler routes through the broker — the agent never calls
        the Backend directly for MCP.
        """
        if not self._tools_cache:
            return

        for tool in self._tools_cache:
            name = tool["name"]

            async def handler(arguments: dict, context: dict, _name=name) -> dict:
                return await self.call_tool(_name, arguments)

            registry.register(name, handler)
            logger.debug("Registered MCP proxy handler: %s", name)
```

**Worker startup changes** (`backend/app/worker.py`):

```python
# BEFORE (remove all of this):
from backend.app.mcp import mcp_manager
await _worker_load_mcp_servers(mcp_manager)

# AFTER:
from backend.app.agent.tools.mcp_proxy import MCPProxyClient

gateway_url = _state.persistence.gateway_url  # already configured
agent_token = os.environ.get("BOND_AGENT_TOKEN", "")  # issued by broker at container start
_state.mcp_proxy = MCPProxyClient(
    gateway_url=gateway_url,
    agent_id=_state.agent_id,
    agent_token=agent_token,
)
await _state.mcp_proxy.list_tools()
```

**Worker agent loop changes** (`_run_agent_loop`):

```python
# BEFORE:
from backend.app.mcp import mcp_manager
await mcp_manager.refresh_tools(registry)
# ... mcp_manager.get_definitions() scattered throughout

# AFTER:
if _state.mcp_proxy:
    _state.mcp_proxy.register_proxy_handlers(registry)
    for name in [t["name"] for t in (_state.mcp_proxy._tools_cache or [])]:
        if name not in agent_tools:
            agent_tools.append(name)
    # Tool definitions come from proxy cache
    tool_defs.extend(_state.mcp_proxy.get_tool_definitions())
```

**Worker shutdown:**

```python
# In lifespan shutdown:
if _state.mcp_proxy:
    await _state.mcp_proxy.close()
```

### 4. Agent-Scoped Tool ACLs

New SpacetimeDB table for per-agent MCP tool permissions:

```sql
CREATE TABLE mcp_tool_acls (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,     -- which agent this ACL applies to
    server_name TEXT NOT NULL,  -- MCP server name (e.g. "sqlite-explorer")
    tool_name TEXT,             -- specific tool name, or NULL for all tools on server
    permission TEXT NOT NULL    -- 'allow' | 'deny'
        CHECK(permission IN ('allow', 'deny'))
);
```

**Default policy:** If no ACLs exist for an agent+server pair, access is **allowed** (backward compatible). Explicit `deny` entries block specific tools or entire servers.

ACL check function on the Backend:

```python
async def check_mcp_acl(agent_id: str, bond_tool_name: str) -> bool:
    """Check if an agent is authorized to call this MCP tool.

    Default: allowed (no ACLs = open access).
    Explicit 'deny' on server or tool overrides.
    """
    from backend.app.core.spacetimedb import get_stdb
    stdb = get_stdb()

    server_name, tool_name = mcp_manager._parse_bond_tool_name(bond_tool_name)

    rows = await stdb.query(
        f"SELECT permission, tool_name FROM mcp_tool_acls "
        f"WHERE agent_id = '{agent_id}' AND server_name = '{server_name}'"
    )

    for row in rows:
        acl_tool = row.get("tool_name")
        perm = row.get("permission")
        # Server-level deny (tool_name is NULL → applies to all tools)
        if acl_tool is None and perm == "deny":
            return False
        # Tool-level deny
        if acl_tool == tool_name and perm == "deny":
            return False

    return True  # Default: allowed
```

### 5. MCP Server Config Schema Update

Add `pool_size` to the `mcp_servers` table for per-server connection pool configuration:

```sql
ALTER TABLE mcp_servers ADD COLUMN pool_size INTEGER NOT NULL DEFAULT 1;
```

Config example stored in SpacetimeDB:

```json
{
  "id": "01HXYZ...",
  "name": "sqlite-explorer",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-sqlite", "--db", "/data/knowledge.db"],
  "env": {},
  "enabled": true,
  "agent_id": null,
  "pool_size": 3
}
```

- `pool_size: 1` — single connection, calls serialized (default, fine for most servers)
- `pool_size: 3` — three subprocess instances, three concurrent calls

### 6. Tool Schema Flow (Unchanged to LLM)

The LLM sees identical tool names and schemas. Only the execution path changes:

```
BEFORE (current — inside container):
  LLM response: tool_calls=[mcp_sqlite-explorer_query({sql: "SELECT ..."})]
  Worker MCPManager → stdio → local MCP subprocess → result

AFTER (proposed — broker-gated host proxy):
  LLM response: tool_calls=[mcp_sqlite-explorer_query({sql: "SELECT ..."})]
  Worker proxy handler → POST Gateway:18789/broker/mcp     (auth + policy + audit)
  Gateway broker → POST Backend:18790/api/v1/mcp/proxy/call (ACL check + execution)
  Backend MCPManager → ConnectionPool → stdio → host MCP subprocess
  Backend → Gateway → Worker → appended to messages
```

## What Gets Removed from Worker/Container

| Removed | Location |
|---------|----------|
| `from backend.app.mcp import mcp_manager` | `worker.py` |
| `_worker_load_mcp_servers()` function | `worker.py` |
| `mcp_manager.refresh_tools(registry)` calls | `worker.py` (~line 869) |
| `mcp_manager.get_definitions()` calls | `worker.py` (~line 909) |
| `mcp_manager.get_pydantic_models()` call | `tools/definitions.py` |
| `mcp_manager.load_servers_from_db()` | `agent/loop.py` |
| `mcp_manager.resolve_tool_name()` | `agent/loop.py` |
| MCP server subprocess spawning inside containers | All of the above |
| `mcp` Python SDK in container image | `pyproject.toml` (optional) |

## What Stays / Changes on the Backend (Host)

| File | Status |
|------|--------|
| `backend/app/mcp/manager.py` | **Refactored** — MCPConnectionPool, agent scoping, health monitor, `call_tool()` API |
| `backend/app/mcp/__init__.py` | Unchanged — exports `mcp_manager` singleton |
| `backend/app/main.py` | Updated — calls `mcp_manager.start_health_monitor()` on startup |
| `backend/app/api/v1/mcp.py` | **Extended** — new `/proxy/tools` and `/proxy/call` endpoints |

## What Stays / Changes on the Gateway

| File | Status |
|------|--------|
| `gateway/src/broker/router.ts` | **Extended** — new `POST /broker/mcp` and `GET /broker/mcp/tools` endpoints |
| `gateway/src/broker/mcp-policy.ts` | **New** — MCP-specific policy engine (glob rules per agent/tool) |
| `gateway/src/broker/types.ts` | **Extended** — new `MCPPolicyRule` type |
| `gateway/src/broker/audit.ts` | Unchanged — MCP calls use existing `AuditLogger` with `mcp:` prefix |
| `gateway/src/persistence/router.ts` | Unchanged — existing `GET /mcp` for config listing stays |

## What Gets Added

| File | Description |
|------|-------------|
| `gateway/src/broker/mcp-policy.ts` | **New** — MCP policy engine (glob matching on tool names + agent IDs) |
| `backend/app/agent/tools/mcp_proxy.py` | **New** — HTTP proxy client routing through broker |
| SpacetimeDB `mcp_tool_acls` table | **New** — per-agent tool permissions (checked by Backend) |
| SpacetimeDB `mcp_servers.pool_size` column | **New** — connection pool sizing |

## Migration Plan

### Phase 1: Broker + Backend Proxy Endpoints (Non-Breaking)
1. Create `gateway/src/broker/mcp-policy.ts` with default-allow rules
2. Add `POST /broker/mcp` and `GET /broker/mcp/tools` to broker router
3. Refactor `MCPManager` with `MCPConnectionPool`, agent-scoped keys, health monitor
4. Add `GET /api/v1/mcp/proxy/tools` and `POST /api/v1/mcp/proxy/call` to Backend
5. Add `mcp_tool_acls` table and `pool_size` column to SpacetimeDB schema
6. Existing container-side MCP continues to work (old + new paths coexist)

### Phase 2: Worker-Side Proxy Client
1. Create `backend/app/agent/tools/mcp_proxy.py` (routes through broker)
2. Ensure agent token (`BOND_AGENT_TOKEN`) is issued to containers at startup
3. Wire into worker startup and agent loop alongside old `mcp_manager` path
4. Feature-flag: `BOND_MCP_MODE=proxy|local` (default `local` initially)
5. Test with existing MCP servers to verify identical behavior

### Phase 3: Switch Default & Remove Old Path
1. Set default to `BOND_MCP_MODE=proxy`
2. Remove `_worker_load_mcp_servers()`, all `mcp_manager` imports from `worker.py`
3. Remove `mcp_manager` calls from `loop.py` and `definitions.py`
4. Remove `mcp` SDK from container image dependencies (optional)
5. Delete the feature flag, proxy is the only path

### Phase 4: Policy & Optimization
1. Add MCP policy management to the frontend (admin UI for rules)
2. Add SpacetimeDB-backed policy rules (load on startup + /reload)
3. Tune pool sizes based on observed concurrency
4. Add tool-list caching with TTL at the broker layer
5. Add batch `POST /broker/mcp/batch` for parallel tool calls
6. Consider MCP server subprocess sandboxing (namespaces, seccomp)

## Success Criteria

- [ ] MCP tools work identically from the LLM's perspective (same names, same schemas)
- [ ] No MCP server subprocesses run inside Docker containers
- [ ] MCP servers on the host can access host filesystem, credentials, and network
- [ ] Multiple agents can call the same MCP server concurrently (connection pooling)
- [ ] Dead MCP server subprocesses are auto-restarted within 30 seconds
- [ ] Agent-specific MCP servers are isolated (no cross-agent access)
- [ ] Permission Broker gates all MCP calls (auth + policy + audit)
- [ ] MCP policy rules can deny specific agents access to specific tools or servers
- [ ] Denied tools are filtered from the tool list (LLM never sees them)
- [ ] All MCP calls are audit-logged in broker-audit.jsonl
- [ ] Per-agent tool ACLs provide a second layer of access control on the Backend
- [ ] Cold-start turn latency is reduced (no MCP server startup in container)
- [ ] Container image size is reduced (no MCP server binaries)
- [ ] Existing MCP CRUD API and frontend continue to work unchanged
