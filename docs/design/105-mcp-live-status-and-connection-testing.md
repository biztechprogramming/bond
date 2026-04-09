# Design Doc 105: MCP Live Status & Connection Testing

## Status: Draft
## Author: Bond
## Date: 2025-04-08

---

## Problem

MCP servers in Bond have **no live feedback loop**. When a user adds or enables an MCP server through the Settings → MCP Servers tab, they have no way to know whether it actually works. The current failure mode is completely silent:

1. User adds an MCP server (e.g., solidtime) via the UI
2. SpacetimeDB stores the config — UI shows "enabled" ✅
3. Backend's `MCPManager.ensure_servers_loaded()` tries to start the process
4. **The process fails** (bad path, missing binary, wrong args, auth error)
5. Exception is caught and logged: `logger.error(f"Failed to load MCP servers from DB: {e}")`
6. Agent's MCP proxy calls `list_tools()` → gets `{"tools": []}` — no error surfaced
7. User and agent both think it's working. **It's not.**

Additionally, agent workers cache the MCP tool list at startup. Even if the server is fixed later, agents don't see the change until their container restarts.

### Real-world example

The `solidtime` MCP server was configured with command `node /mnt/c/dev/automation/solidtime/mcp-solidtime/dist/index.js -s user`. The UI showed it as enabled. But:
- The backend couldn't start the node process (path issue or missing file)
- The status was "stopped" — only visible via a direct REST API call
- No agent could see solidtime tools
- The user had no indication anything was wrong

---

## Goals

1. **Live connection status** visible in the MCP Servers settings tab — not just "enabled/disabled" but actual process health
2. **Test Connection button** — on-demand validation when adding or editing an MCP server
3. **Tool discovery feedback** — show which tools a server exposes after successful connection
4. **Status refresh** — agents can pick up MCP changes without container restart

---

## Non-Goals

- Changing the MCP transport protocol (stdio/SSE stays as-is)
- Auto-remediation of failed servers (out of scope)
- MCP server marketplace/discovery (separate feature)

---

## Design

### 1. Backend: MCP Status API

Add a new endpoint to the backend's MCP router that returns live status for all configured servers.

#### `GET /api/v1/mcp/servers/status`

Returns enriched status from `MCPManager.get_pool_status()`:

```json
{
  "servers": [
    {
      "name": "solidtime",
      "scope": "global",
      "enabled": true,
      "status": "connected",
      "healthy_connections": 2,
      "pool_size": 2,
      "tools": ["create_time_entry", "list_projects", "list_tasks"],
      "last_error": null,
      "last_checked": "2025-04-08T18:30:00Z"
    },
    {
      "name": "github",
      "scope": "global",
      "enabled": true,
      "status": "error",
      "healthy_connections": 0,
      "pool_size": 2,
      "tools": [],
      "last_error": "spawn npx ENOENT — npx not found in PATH",
      "last_checked": "2025-04-08T18:30:05Z"
    }
  ]
}
```

**Status values:**
| Status | Meaning |
|--------|---------|
| `connected` | Pool has ≥1 healthy connection, tools discovered |
| `connecting` | Pool is starting, not yet healthy |
| `error` | Pool failed to start or all connections unhealthy |
| `stopped` | Server is enabled but pool hasn't been created yet |
| `disabled` | Server exists but `enabled = false` |

#### Changes to `MCPManager`

```python
class MCPConnectionPool:
    # Add these fields:
    last_error: Optional[str] = None
    last_checked: Optional[datetime] = None
    discovered_tools: list[str] = []

    async def start(self):
        try:
            # existing start logic...
            self.discovered_tools = [t.name for t in await conn.session.list_tools()]
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)
            raise
        finally:
            self.last_checked = datetime.utcnow()
```

Update `ensure_servers_loaded()` to **not silently swallow errors** — store the error on the pool object so it can be reported:

```python
for row in filtered_rows:
    config = MCPServerConfig(...)
    key = f"{config.name}::{scope}"
    if key not in self.connection_pools:
        pool = MCPConnectionPool(config, self._pool_size)
        try:
            await pool.start()
            logger.info(f"Started connection pool: {key}")
        except Exception as e:
            logger.error(f"Failed to start MCP server {config.name}: {e}")
            pool.last_error = str(e)
            # Still store the pool so we can report the error
        self.connection_pools[key] = pool
```

### 2. Backend: Test Connection Endpoint

#### `POST /api/v1/mcp/servers/test`

Accepts a server configuration (not necessarily saved yet) and attempts a one-shot connection:

```json
// Request
{
  "name": "solidtime",
  "command": "node",
  "args": ["/path/to/mcp-solidtime/dist/index.js", "-s", "user"],
  "env": {
    "SOLIDTIME_API_TOKEN": "sk-..."
  }
}
```

```json
// Response — success
{
  "success": true,
  "status": "connected",
  "tools": [
    { "name": "create_time_entry", "description": "Create a new time entry in SolidTime" },
    { "name": "list_projects", "description": "List all projects" },
    { "name": "list_tasks", "description": "List tasks for a project" }
  ],
  "connect_time_ms": 340,
  "error": null
}
```

```json
// Response — failure
{
  "success": false,
  "status": "error",
  "tools": [],
  "connect_time_ms": 5000,
  "error": "Process exited with code 1: Error: Cannot find module '/path/to/mcp-solidtime/dist/index.js'"
}
```

**Implementation:**

```python
@router.post("/servers/test")
async def test_mcp_server(config: MCPServerTestRequest):
    """Test an MCP server connection without saving it."""
    import time
    start = time.monotonic()

    # Create a temporary single connection (not a pool)
    temp_conn = MCPConnection(MCPServerConfig(
        name=config.name,
        command=config.command,
        args=config.args,
        env=config.env or {},
        enabled=True
    ))

    try:
        await asyncio.wait_for(temp_conn.start(), timeout=10.0)
        tools_result = await temp_conn.session.list_tools()
        tools = [{"name": t.name, "description": t.description} for t in tools_result.tools]
        elapsed = int((time.monotonic() - start) * 1000)

        return {
            "success": True,
            "status": "connected",
            "tools": tools,
            "connect_time_ms": elapsed,
            "error": None
        }
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "status": "error",
            "tools": [],
            "connect_time_ms": elapsed,
            "error": "Connection timed out after 10 seconds"
        }
    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "success": False,
            "status": "error",
            "tools": [],
            "connect_time_ms": elapsed,
            "error": str(e)
        }
    finally:
        try:
            await temp_conn.stop()
        except Exception:
            pass
```

### 3. SpacetimeDB: Status Fields

Add live status fields to the `mcp_servers` table so the frontend can display them via subscription:

```rust
// In SpacetimeDB module
#[table(name = mcp_servers, public)]
pub struct McpServers {
    #[primary_key]
    pub id: String,
    pub name: String,
    pub command: String,
    pub args: String,       // JSON array
    pub env: String,        // JSON object
    pub enabled: bool,
    pub agent_id: String,

    // NEW: Live status fields (updated by backend)
    pub status: String,             // "connected" | "error" | "stopped" | "disabled"
    pub last_error: String,         // Empty string or error message
    pub tool_count: u32,            // Number of discovered tools
    pub discovered_tools: String,   // JSON array of tool names
    pub last_checked: String,       // ISO timestamp of last health check
}
```

The backend's health monitor loop (`_health_loop`) would update these fields via a reducer:

```rust
#[reducer]
pub fn update_mcp_server_status(
    ctx: &ReducerContext,
    id: String,
    status: String,
    last_error: String,
    tool_count: u32,
    discovered_tools: String,
    last_checked: String,
) -> Result<(), String> {
    // Update the row's status fields
}
```

This way the frontend gets **real-time status updates via subscription** — no polling, no REST.

### 4. Frontend: McpTab Enhancements

#### 4a. Status Indicators in List View

Each server row shows a live status badge:

```
┌─────────────────────────────────────────────────────────────┐
│ MCP Servers                                        [+ Add]  │
├─────────────────────────────────────────────────────────────┤
│ 🟢 solidtime          3 tools    Global     [Edit] [Delete] │
│ 🔴 github             Error      Global     [Edit] [Delete] │
│    "spawn npx ENOENT"                                       │
│ ⚪ my-custom-server    Disabled   Agent: F.  [Edit] [Delete] │
└─────────────────────────────────────────────────────────────┘
```

**Status badge colors** (matching existing Bond UI conventions):
| Status | Color | Icon |
|--------|-------|------|
| `connected` | Green `#22c55e` | 🟢 |
| `connecting` | Yellow `#eab308` | 🟡 (pulsing) |
| `error` | Red `#ef4444` | 🔴 |
| `stopped` | Gray `#6b7280` | ⚪ |
| `disabled` | Gray `#6b7280` | ⚪ |

**Error display:** When status is `error`, show `last_error` as a muted red subtitle below the server name. Truncate to ~80 chars with tooltip for full message.

**Tool count:** Show "N tools" badge next to connected servers. Clicking it expands to show the tool names list.

#### 4b. Test Connection Button

In the Add/Edit form, add a **"Test Connection"** button that fires before saving:

```
┌─────────────────────────────────────────────────────────────┐
│ Add MCP Server                                              │
├─────────────────────────────────────────────────────────────┤
│ Name:     [solidtime                    ]                   │
│ Command:  [node                         ]                   │
│ Args:     [/path/to/index.js] [-s] [user]  [+ Add Arg]     │
│ Env:      SOLIDTIME_API_TOKEN = [••••••••]  [+ Add Var]     │
│                                                             │
│ Scope:    ○ Global (all agents)                             │
│           ● Specific agent: [Frederica ▾]                   │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ [🔌 Test Connection]                                    │ │
│ │                                                         │ │
│ │ ✅ Connected in 340ms — 3 tools discovered:             │ │
│ │    • create_time_entry                                  │ │
│ │    • list_projects                                      │ │
│ │    • list_tasks                                         │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│                              [Cancel]  [Save]               │
└─────────────────────────────────────────────────────────────┘
```

**States of the test panel:**
1. **Idle** — just the button
2. **Testing** — spinner + "Testing connection..."
3. **Success** — green check + time + tool list
4. **Failed** — red X + error message + suggestion

**Implementation:**

```typescript
const [testResult, setTestResult] = useState<TestResult | null>(null);
const [testing, setTesting] = useState(false);

async function handleTestConnection() {
  setTesting(true);
  setTestResult(null);
  try {
    const resp = await fetch(`http://${backendHost}/api/v1/mcp/servers/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: form.name,
        command: form.command,
        args: form.args,
        env: Object.fromEntries(form.envVars.map(v => [v.key, v.value]))
      })
    });
    const data = await resp.json();
    setTestResult(data);
  } catch (err) {
    setTestResult({ success: false, error: String(err), tools: [], connect_time_ms: 0, status: 'error' });
  } finally {
    setTesting(false);
  }
}
```

> **Note:** The Test Connection button uses a REST call by necessity — it's a one-shot action against an unsaved config, not a subscription. This is appropriate for imperative actions (like "test this now").

### 5. Agent MCP Refresh (No Restart Required)

Currently, container-mode agents cache MCP tools at worker startup and never refresh. This needs to change.

#### Option A: Periodic refresh (simple)

In the worker's main loop, periodically re-fetch tools:

```python
# In worker.py, after handling each turn (or every N minutes)
MCP_REFRESH_INTERVAL = 300  # 5 minutes

if time.time() - _state.last_mcp_refresh > MCP_REFRESH_INTERVAL:
    tools = await _state.mcp_proxy.list_tools()
    _state.mcp_proxy._tool_cache = tools
    _state.last_mcp_refresh = time.time()
```

#### Option B: Event-driven refresh (better)

When the backend detects an MCP server config change (via SpacetimeDB subscription), it sends a signal to affected agent workers:

```
SpacetimeDB mcp_servers change → Backend detects change →
  POST worker_url/refresh-mcp → Worker re-fetches tools
```

Add a new endpoint to the worker:

```python
@app.post("/refresh-mcp")
async def refresh_mcp():
    """Re-fetch MCP tools from the backend."""
    if _state.mcp_proxy:
        tools = await _state.mcp_proxy.list_tools()
        _state.mcp_proxy._tool_cache = tools
        return {"status": "refreshed", "tool_count": len(tools)}
    return {"status": "no_proxy"}
```

**Recommendation:** Implement Option A first (simple, no new infrastructure), then Option B as a follow-up for instant refresh.

---

## Implementation Plan

### Phase 1: Backend status tracking (2-3 hours)
1. Add `last_error`, `last_checked`, `discovered_tools` fields to `MCPConnectionPool`
2. Update `ensure_servers_loaded()` to store failed pools (not silently discard)
3. Update `_health_loop` to write status back to SpacetimeDB via reducer
4. Add `update_mcp_server_status` reducer to SpacetimeDB module
5. Add status fields to `mcp_servers` table

### Phase 2: Test connection endpoint (1-2 hours)
1. Add `POST /api/v1/mcp/servers/test` endpoint
2. Implement temporary single-connection test with timeout
3. Return tool list on success, error details on failure

### Phase 3: Frontend enhancements (2-3 hours)
1. Update `McpServerRow` interface with status fields
2. Add status badges to list view (color-coded)
3. Add error display (subtitle with truncation)
4. Add tool count badge with expandable tool list
5. Add "Test Connection" button to add/edit form
6. Add test result display panel (success/failure states)

### Phase 4: Agent refresh (1 hour)
1. Add periodic MCP tool refresh to worker (Option A)
2. Add `/refresh-mcp` endpoint to worker for future event-driven refresh (Option B prep)

---

## Migration

The new SpacetimeDB fields (`status`, `last_error`, `tool_count`, `discovered_tools`, `last_checked`) should have sensible defaults:
- `status`: `"stopped"`
- `last_error`: `""` (empty string)
- `tool_count`: `0`
- `discovered_tools`: `"[]"` (empty JSON array)
- `last_checked`: `""` (empty string)

Existing rows will get these defaults. The backend health loop will populate them on next check.

---

## Testing

- [ ] Add MCP server with invalid command → UI shows red status + error message
- [ ] Add MCP server with valid command → UI shows green status + tool count
- [ ] Click "Test Connection" with bad path → shows failure with specific error
- [ ] Click "Test Connection" with good config → shows tools list
- [ ] Disable a running server → status changes to "disabled" in real-time
- [ ] Enable a stopped server → status transitions: stopped → connecting → connected
- [ ] Agent in container sees new tools after refresh interval (no restart)
- [ ] Error messages are actionable (e.g., "file not found" not just "process exited")

---

## Success Criteria

- [ ] Users can see at a glance which MCP servers are actually working
- [ ] "Test Connection" validates config before saving — no more silent failures
- [ ] Error messages are specific enough to diagnose the problem (bad path, missing binary, auth failure)
- [ ] Tool discovery is visible — users know exactly what capabilities each server provides
- [ ] Agents pick up MCP changes without manual container restart
- [ ] Status updates flow via SpacetimeDB subscription — no REST polling for live status

---

## References

- [017-mcp-integration.md](017-mcp-integration.md) — Original MCP client integration design
- [054-host-side-mcp-proxy.md](054-host-side-mcp-proxy.md) — Host-side MCP proxy architecture
- [053-solidtime-mcp-integration.md](053-solidtime-mcp-integration.md) — SolidTime MCP server
- [mcp-settings-tab.md](mcp-settings-tab.md) — MCP Settings Tab UI (companion doc)
