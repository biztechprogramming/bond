# Design Doc: MCP Servers Settings Tab

## Status: Draft
## Author: Bond
## Date: 2025-01-20

---

## Problem

MCP servers are fully supported in the Bond backend (SpacetimeDB schema, reducers, MCPManager) but there is **no UI to manage them**. The directory `frontend/src/app/settings/mcp/` exists but is empty, and the `TABS` array in `page.tsx` has no MCP entry.

This means:
- Users must add MCP servers through direct SpacetimeDB calls or API
- Users can't assign MCP servers to specific agents (or make them global)
- Frederica can't see the solidtime MCP server because there's no way to manage agent assignments through the UI

## What Exists (Backend — Complete)

### SpacetimeDB Table: `mcp_servers`

From `mcp_servers_table.ts` (auto-generated):
```typescript
{
  id: string          // primary key
  name: string        // display name (e.g. "solidtime")
  command: string     // executable (e.g. "npx", "uvx", "node")
  args: string        // JSON-encoded string[] of arguments
  env: string         // JSON-encoded Record<string, string> of env vars
  enabled: boolean    // on/off toggle
  agentId: string?    // optional — null = global (all agents), set = agent-specific
  createdAt: u64      // timestamp
  updatedAt: u64      // timestamp
}
```

### SpacetimeDB Reducers (Auto-Generated Types)

| Reducer | Parameters |
|---------|-----------|
| `add_mcp_server` | `{ id, name, command, args, env, agentId }` |
| `update_mcp_server` | `{ id, name, command, args, env, enabled, agentId }` |
| `delete_mcp_server` | `{ id }` |

### Frontend Types (Already Generated)

- `McpServers` type in `lib/spacetimedb/types.ts`
- `McpServersRow` table type in `lib/spacetimedb/mcp_servers_table.ts`
- All three reducer types in `lib/spacetimedb/types/reducers.ts`
- Table and reducer schemas registered in `lib/spacetimedb/index.ts`

---

## Architecture: SpacetimeDB Subscriptions (Not REST)

**All data flows through SpacetimeDB subscriptions**, following the established pattern used by AgentsTab, Settings, and other tabs. No REST polling.

### Pattern (from AgentsTab)

```
┌─────────────┐  subscription  ┌──────────────┐  reducer call  ┌──────────────┐
│  McpTab.tsx  │◄──────────────│ SpacetimeDB  │◄───────────────│  McpTab.tsx  │
│  (renders)   │  auto-update  │  mcp_servers │  add/update/   │  (user acts) │
└─────────────┘               └──────────────┘  delete         └──────────────┘
```

1. **Read**: `useSpacetimeDB()` hook subscribes to table changes → component re-renders automatically
2. **Write**: `getConnection().reducers.addMcpServer(...)` fires reducer → SpacetimeDB updates table → subscription triggers re-render
3. **No fetch/polling/REST** — data is always live

---

## Implementation Plan

### File 1: `lib/spacetimedb-client.ts` — Add MCP accessor functions

Add to the existing client file (following the pattern of `getAgents()`, `getSettings()`, etc.):

```typescript
// === New interface ===
export interface McpServerRow {
  id: string;
  name: string;
  command: string;
  args: string;        // JSON-encoded string[]
  env: string;         // JSON-encoded Record<string, string>
  enabled: boolean;
  agentId: string | null;
  createdAt: bigint;
  updatedAt: bigint;
}

// === New accessor function ===
export function getMcpServers(): McpServerRow[] {
  if (!db) return [];
  return [...db.db.mcp_servers.iter()].map(row => {
    const r = row as unknown as McpServerRow;
    return {
      id: r.id,
      name: r.name,
      command: r.command,
      args: r.args,
      env: r.env,
      enabled: r.enabled,
      agentId: r.agentId || null,
      createdAt: r.createdAt,
      updatedAt: r.updatedAt,
    };
  });
}
```

Also add `getMcpServers` to the import/export list at the top of the file.

### File 2: `hooks/useSpacetimeDB.ts` — Add hook

```typescript
import { ..., getMcpServers, type McpServerRow } from '@/lib/spacetimedb-client';

export function useMcpServers(): McpServerRow[] {
  return useSpacetimeDB(() => getMcpServers());
}
```

### File 3: `app/settings/mcp/McpTab.tsx` — Main component (NEW)

This is the core deliverable. It follows the **exact same structure** as `AgentsTab.tsx`:

#### State Management
```typescript
export default function McpTab() {
  // Live subscription — auto-updates
  const servers = useMcpServers();
  const agents = useAgents();  // for agent assignment dropdown

  // Local UI state
  const [editing, setEditing] = useState<McpServerForm | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
}
```

#### Form Interface
```typescript
interface McpServerForm {
  id: string;
  name: string;
  command: string;
  args: string[];           // parsed from JSON for editing
  env: EnvVar[];            // parsed from JSON for editing
  enabled: boolean;
  agentId: string | null;   // null = "Global (all agents)"
}

interface EnvVar {
  key: string;
  value: string;
  masked: boolean;          // UI-only: hide value with ••••••
}
```

#### CRUD Operations (Reducer Calls)

Following the AgentsTab pattern — `getConnection().reducers.*`:

```typescript
const handleSave = () => {
  const conn = getConnection();
  if (!conn) { setMsg("Not connected to database"); return; }

  const argsJson = JSON.stringify(editing.args);
  const envJson = JSON.stringify(
    Object.fromEntries(editing.env.map(e => [e.key, e.value]))
  );

  if (isNew) {
    conn.reducers.addMcpServer({
      id: generateId(),
      name: editing.name,
      command: editing.command,
      args: argsJson,
      env: envJson,
      agentId: editing.agentId || undefined,  // null → omit for global
    });
  } else {
    conn.reducers.updateMcpServer({
      id: editing.id,
      name: editing.name,
      command: editing.command,
      args: argsJson,
      env: envJson,
      enabled: editing.enabled,
      agentId: editing.agentId || undefined,
    });
  }
  setEditing(null);
  setMsg("Saved.");
};

const handleDelete = (id: string) => {
  const conn = getConnection();
  if (!conn) return;
  conn.reducers.deleteMcpServer({ id });
  setMsg("Deleted.");
  setEditing(null);
};

const handleToggle = (server: McpServerRow) => {
  const conn = getConnection();
  if (!conn) return;
  conn.reducers.updateMcpServer({
    id: server.id,
    name: server.name,
    command: server.command,
    args: server.args,
    env: server.env,
    enabled: !server.enabled,
    agentId: server.agentId || undefined,
  });
};
```

#### UI Layout

**List View** (no server selected):
```
┌──────────────────────────────────────────────────────────────┐
│ MCP Servers                                    [+ Add Server]│
├──────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ 🟢 solidtime                                    [toggle] │ │
│ │ Command: npx -y @solidtime/mcp-server                    │ │
│ │ Scope: Global (all agents)                               │ │
│ │                                          [Edit] [Delete] │ │
│ └──────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ 🔴 github                                       [toggle] │ │
│ │ Command: npx -y @modelcontextprotocol/server-github      │ │
│ │ Scope: Bond only                                         │ │
│ │                                          [Edit] [Delete] │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                              │
│ ℹ️  Global servers are available to all agents.              │
│    Agent-specific servers are only visible to that agent.    │
└──────────────────────────────────────────────────────────────┘
```

**Edit/Add Form** (replaces list, same as AgentsTab pattern):
```
┌──────────────────────────────────────────────────────────────┐
│ ← Back    Add MCP Server                                     │
├──────────────────────────────────────────────────────────────┤
│ Name          [solidtime                              ]      │
│ Command       [npx                                    ]      │
│                                                              │
│ Arguments                                        [+ Add]     │
│   [0] [-y                                             ] [×]  │
│   [1] [@solidtime/mcp-server                          ] [×]  │
│                                                              │
│ Environment Variables                            [+ Add]     │
│   SOLIDTIME_API_KEY  [••••••••••••••••••••] [👁] [×]         │
│   SOLIDTIME_URL      [https://time.example.com ] [👁] [×]   │
│                                                              │
│ Agent Scope                                                  │
│   (●) Global — available to all agents                       │
│   ( ) Specific agent: [▼ Bond          ]                     │
│                                                              │
│                                      [Cancel]  [Save]        │
└──────────────────────────────────────────────────────────────┘
```

#### Key UI Details

- **Env var masking**: Values display as `••••••` by default with an eye toggle to reveal. This is UI-only masking (the actual values are stored in SpacetimeDB as-is, same as current behavior).
- **Agent scope radio**: "Global" sets `agentId` to null. "Specific agent" shows a dropdown populated from `useAgents()`.
- **Args as list**: Parse JSON string into editable list items. Each has an add/remove button. Serialize back to JSON on save.
- **Enabled toggle**: Inline on the list card — no need to open the edit form.
- **Status indicator**: 🟢 enabled, 🔴 disabled (from `enabled` field). Note: live MCP process status is backend-only and not exposed via SpacetimeDB — this is a known limitation (see Future Work).

### File 4: `app/settings/page.tsx` — Wire it in

Three changes:

```typescript
// 1. Add import (line ~9)
import McpTab from "./mcp/McpTab";

// 2. Add to TABS array (after "channels")
const TABS = [
  { id: "agents", label: "Agents" },
  { id: "containers", label: "Container Hosts" },
  { id: "deployment", label: "Deployment" },
  { id: "channels", label: "Channels" },
  { id: "mcp", label: "MCP Servers" },        // ← NEW
  { id: "prompts", label: "Prompts" },
  // ...
] as const;

// 3. Add tab content rendering (after channels block, ~line 248)
{activeTab === "mcp" && <McpTab />}
```

---

## Styling

Use **inline styles** matching the existing settings page conventions. Key style references from `page.tsx` and `AgentsTab.tsx`:

- Card background: `#23262b` with `1px solid #333` border
- Input fields: `background: "#181a1e"`, `border: "1px solid #333"`, `color: "#e0e0e0"`
- Buttons: primary `#2563eb` (blue), danger `#dc2626` (red)
- Labels: `color: "#aaa"`, `fontSize: "0.85rem"`
- Section titles: `color: "#fff"`, `fontSize: "1.1rem"`, `fontWeight: 600`

No Tailwind — all inline `style={{}}` objects, consistent with the rest of the settings page.

---

## What's NOT Needed

| Item | Why |
|------|-----|
| New REST endpoints | Subscriptions handle all reads; reducers handle all writes |
| Polling / intervals | SpacetimeDB subscription auto-updates the UI |
| Backend changes | All reducers and table schema already exist |
| Type generation | `McpServers` types already generated in `lib/spacetimedb/` |
| New SpacetimeDB reducers | `add_mcp_server`, `update_mcp_server`, `delete_mcp_server` all exist |

---

## Files Changed (Summary)

| File | Change | Lines (est.) |
|------|--------|-------------|
| `lib/spacetimedb-client.ts` | Add `McpServerRow` interface + `getMcpServers()` | ~25 |
| `hooks/useSpacetimeDB.ts` | Add `useMcpServers()` hook | ~5 |
| `app/settings/mcp/McpTab.tsx` | **New file** — full CRUD component | ~350-400 |
| `app/settings/page.tsx` | Import + TABS entry + render | ~3 lines changed |

**Total: ~4 files, ~380-430 lines of new code, 0 backend changes.**

---

## Future Work (Out of Scope)

- **Live process status**: The backend's `MCPManager` tracks which MCP servers are actually running with live connection pools. This status isn't in SpacetimeDB — it's in-memory on the backend. A future enhancement could add a REST endpoint or SpacetimeDB field to surface "connected" / "disconnected" / "error" status per server.
- **Tool discovery**: Show which tools each MCP server provides (requires calling the MCP server's `list_tools`).
- **Bulk agent assignment**: Assign one MCP server to multiple (but not all) agents.
- **Import/export**: Import MCP server configs from `claude_desktop_config.json` or similar formats.

---

## Testing

Manual verification:
1. Tab appears in settings navigation
2. Can add a new MCP server with name, command, args, env vars
3. Can assign to "Global" or a specific agent
4. Can toggle enabled/disabled from the list
5. Can edit an existing server
6. Can delete a server
7. Changes appear immediately (subscription — no refresh needed)
8. Env var values are masked by default
9. After adding a global server, all agents can see it in their MCP tools
