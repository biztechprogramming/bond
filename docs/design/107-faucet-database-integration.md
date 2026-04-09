# Design Doc 107: Faucet Database Integration

**Status:** Proposed  
**Date:** 2025-07-14  
**Author:** Bond Agent  
**Depends on:** 017 (MCP Integration), 036 (Permission Broker), 054 (Host-Side MCP Proxy), 055 (tbls Database Discovery)  

---

## Problem

Agents frequently need to interact with databases — investigating production incidents, debugging data issues, running migrations, seeding development data, or building features against a live schema. Today, giving an agent database access requires:

1. **Manual credential plumbing.** The user must figure out how to get a DSN into the agent's environment — via vault secrets, environment variables, or MCP server configuration. There's no standard path.

2. **No access governance.** Once an agent has a connection string, it has whatever privileges that database user has. There's no Bond-level control over read vs. write, no per-table restrictions, and no audit trail of what the agent did. Giving an agent access to a production database is an all-or-nothing trust decision.

3. **No schema awareness without extra tooling.** The agent must either run exploratory queries (`SHOW TABLES`, `\d+`, `PRAGMA table_info`) across multiple turns, or rely on tbls (Doc 055) which requires separate setup. There's no integrated discovery path.

4. **No unified multi-database story.** An agent investigating a microservices issue might need to query three different databases. Each requires separate configuration, separate credentials, and separate tooling.

5. **No UI for any of this.** Database connections are invisible in the Bond frontend. Users configure them through config files, environment variables, or MCP server JSON — none of which surface in the agent settings UI.

The result: database access is the most common agent capability that users want, and the hardest to set up correctly and safely.

## Proposed Solution

Integrate [Faucet](https://github.com/faucetdb/faucet) as Bond's **managed database gateway**. Faucet is an open-source, single-binary server that connects to SQL databases, introspects their schemas, and exposes governed access through both a REST API and a built-in MCP server — with role-based access control, API key authentication, and row-level security.

Bond manages Faucet's lifecycle, configures database connections through the UI, and exposes Faucet's MCP tools to agents based on two predefined access tiers:

- **Read Only** — for production investigation agents. Can list, describe, and query tables. Cannot insert, update, delete, or run raw SQL.
- **Full Control** — for development agents. Full CRUD access plus raw SQL execution.

This gives users a point-and-click path from "I have a database" to "my agent can safely query it" — with appropriate guardrails enforced server-side by Faucet's RBAC.

### Why Faucet

| Property | Value |
|----------|-------|
| **Single binary** | Go binary, no runtime dependencies. Download and run. |
| **Schema introspection** | Auto-discovers tables, columns, types, constraints at startup. |
| **Built-in MCP server** | 8 tools + 2 resources, stdio transport. Plugs directly into Bond's existing MCP proxy. |
| **RBAC** | Per-table verb permissions (GET/POST/PUT/DELETE). Enforced server-side. |
| **Row-level security** | SQL filter expressions per role. E.g., `tenant_id = 42`. |
| **API key auth** | SHA-256 hashed keys with per-key role assignment. |
| **Multi-database** | Single instance connects to PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Snowflake, SQLite simultaneously. |
| **REST + MCP** | Agents use MCP tools; external integrations can use the REST API. |
| **Open source** | MIT licensed. No vendor lock-in. |

---

## Design

### Architecture Overview

```
┌─────────────┐     ┌───────────┐     ┌──────────┐     ┌────────────────┐
│  Agent      │────▶│  Gateway   │────▶│  Faucet  │────▶│  PostgreSQL    │
│  (sandbox)  │ MCP │  (broker/  │ MCP │  (host)  │ SQL│  MySQL         │
│             │proxy│   proxy)   │stdio│          │    │  SQLite        │
└─────────────┘     └───────────┘     └──────────┘     │  SQL Server    │
                                                        │  ...           │
                                                        └────────────────┘
```

1. **Faucet runs on the host** as a managed process, similar to how MCP servers run host-side (Doc 054).
2. **Bond's backend** manages Faucet's lifecycle — install, start, configure, health-check.
3. **Faucet's MCP server** is registered as a host-side MCP server and proxied to agent containers through the Gateway's existing MCP proxy infrastructure.
4. **Per-agent tool filtering** — the broker/proxy exposes only the MCP tools permitted by the agent's access tier for each assigned database.
5. **Database connections and agent assignments** are stored in SpacetimeDB and configured through the Bond frontend.

### Data Model

#### New SpacetimeDB Tables

```
┌─────────────────────────────────┐
│     database_connection         │
├─────────────────────────────────┤
│ id: Identity (PK)               │
│ name: String (unique)           │
│ driver: String                  │  -- postgres, mysql, mariadb, mssql, oracle, snowflake, sqlite
│ description: String?            │
│ dsn_vault_key: String           │  -- reference to encrypted DSN in Bond vault
│ faucet_service_name: String     │  -- name registered in Faucet (auto-derived from `name`)
│ status: String                  │  -- active, error, disconnected
│ last_health_check: Timestamp    │
│ created_at: Timestamp           │
│ updated_at: Timestamp           │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│   agent_database_access         │
├─────────────────────────────────┤
│ id: Identity (PK)               │
│ agent_id: Identity (FK)         │  -- references agent
│ database_id: Identity (FK)      │  -- references database_connection
│ access_tier: String             │  -- "read_only" or "full_control"
│ faucet_api_key_hash: String     │  -- the Faucet API key assigned (hashed)
│ faucet_role_name: String        │  -- the Faucet role name for this assignment
│ created_at: Timestamp           │
│ updated_at: Timestamp           │
└─────────────────────────────────┘
```

#### Access Tier Definitions

| Tier | Faucet Role Permissions | MCP Tools Available | Use Case |
|------|------------------------|--------------------|-----------| 
| **Read Only** | GET on all tables | `faucet_list_services`, `faucet_list_tables`, `faucet_describe_table`, `faucet_query` | Production investigation, reporting, monitoring |
| **Full Control** | GET, POST, PUT, PATCH, DELETE on all tables | All 8 tools including `faucet_insert`, `faucet_update`, `faucet_delete`, `faucet_raw_sql` | Development, migrations, data seeding, schema changes |

### Faucet Lifecycle Management

#### Installation

Bond downloads the Faucet binary on first use, similar to how tbls is managed (Doc 055):

```python
# backend/app/services/faucet_manager.py

class FaucetManager:
    FAUCET_VERSION = "latest"  # pin to specific release in production
    FAUCET_BIN = Path.home() / ".bond" / "bin" / "faucet"
    FAUCET_CONFIG = Path.home() / ".bond" / "faucet"
    FAUCET_PORT = 18795  # dedicated port, outside Bond's existing range

    async def ensure_installed(self) -> Path:
        """Download Faucet binary if not present."""
        if self.FAUCET_BIN.exists():
            return self.FAUCET_BIN
        # Detect OS/arch, download from GitHub releases
        # Verify checksum, make executable
        ...

    async def start(self) -> None:
        """Start Faucet server as a managed subprocess."""
        await self.ensure_installed()
        self._process = await asyncio.create_subprocess_exec(
            str(self.FAUCET_BIN), "serve",
            "--port", str(self.FAUCET_PORT),
            "--config-dir", str(self.FAUCET_CONFIG),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._wait_for_healthy()

    async def stop(self) -> None:
        """Gracefully stop Faucet."""
        if self._process:
            self._process.terminate()
            await self._process.wait()
```

#### Configuration via CLI

When a user adds a database connection in the UI, Bond shells out to the Faucet CLI:

```bash
# Add a database
faucet db add myapp_prod \
  --driver postgres \
  --dsn "postgres://readonly:pass@prod-host:5432/myapp?sslmode=require"

# Create a read-only role
faucet role create prod_reader \
  --permission "myapp_prod:*:GET"

# Create a full-control role  
faucet role create dev_writer \
  --permission "myapp_dev:*:GET,POST,PUT,PATCH,DELETE" \
  --permission "myapp_dev:*:RAW_SQL"

# Create an API key for a specific agent
faucet key create \
  --role prod_reader \
  --name "agent-bond-prod-access"
```

#### Health Monitoring

Bond periodically checks Faucet's health and the status of each database connection:

```python
async def health_check(self) -> dict:
    """Check Faucet server and all database connections."""
    # GET http://localhost:18795/api/v1/system/health
    response = await self._http.get(f"{self.base_url}/api/v1/system/health")
    return response.json()
```

Connection status is written back to SpacetimeDB so the UI can show live health indicators.

### MCP Integration

Faucet's MCP server is registered as a host-side MCP server, leveraging the architecture from Doc 054.

#### Registration

When Faucet starts, Bond registers it in the MCP server registry:

```json
{
  "name": "faucet",
  "transport": "stdio",
  "command": "~/.bond/bin/faucet",
  "args": ["mcp", "--config-dir", "~/.bond/faucet"],
  "managed": true,
  "source": "bond-faucet-integration"
}
```

#### Per-Agent Tool Filtering

The critical piece: not every agent should see every Faucet tool. The Gateway's MCP proxy filters tools based on the agent's `agent_database_access` records.

When an agent requests its available MCP tools:

1. Gateway looks up the agent's database assignments in SpacetimeDB.
2. For each assignment, Gateway determines the access tier.
3. Gateway filters Faucet's MCP tool list:
   - **Read Only agents** see: `faucet_list_services`, `faucet_list_tables`, `faucet_describe_table`, `faucet_query`
   - **Full Control agents** see: all 8 tools
4. Tool calls are proxied with the agent's Faucet API key, so Faucet's server-side RBAC is the enforcement layer.

This is **defense in depth** — the proxy filters tools at the Bond level, and Faucet enforces RBAC at the database level. Even if tool filtering were bypassed, the API key's role would prevent unauthorized operations.

```typescript
// gateway/src/mcp/faucet-filter.ts

const READ_ONLY_TOOLS = new Set([
  'faucet_list_services',
  'faucet_list_tables', 
  'faucet_describe_table',
  'faucet_query',
]);

const FULL_CONTROL_TOOLS = new Set([
  ...READ_ONLY_TOOLS,
  'faucet_insert',
  'faucet_update',
  'faucet_delete',
  'faucet_raw_sql',
]);

function filterFaucetTools(
  tools: McpTool[],
  accessTier: 'read_only' | 'full_control'
): McpTool[] {
  const allowed = accessTier === 'read_only' ? READ_ONLY_TOOLS : FULL_CONTROL_TOOLS;
  return tools.filter(t => allowed.has(t.name));
}
```

#### API Key Injection

When the proxy forwards a tool call to Faucet's MCP server, it injects the agent's API key:

```typescript
async function proxyFaucetToolCall(
  agentId: string,
  toolName: string,
  args: Record<string, unknown>
): Promise<McpResult> {
  const access = await getAgentDatabaseAccess(agentId);
  const apiKey = await vault.decrypt(access.faucet_api_key_vault_ref);
  
  // Inject API key into the MCP call environment
  return await mcpProxy.call('faucet', toolName, args, {
    env: { FAUCET_API_KEY: apiKey }
  });
}
```

### UI Design

#### 1. Settings > Databases (Global Management)

A new top-level settings page for managing database connections. Accessible from the main settings sidebar.

```
┌─────────────────────────────────────────────────────────────────┐
│  Settings > Databases                                    [+ Add]│
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🟢 myapp_prod          PostgreSQL    3 agents connected   │  │
│  │    prod-host:5432/myapp              Added 3 days ago     │  │
│  │                                          [Edit] [Delete]  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🟢 myapp_dev            PostgreSQL    1 agent connected   │  │
│  │    localhost:5432/myapp_dev           Added 1 day ago     │  │
│  │                                          [Edit] [Delete]  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🔴 analytics_dw         Snowflake     0 agents connected  │  │
│  │    account.snowflakecomputing.com     Connection error     │  │
│  │                                          [Edit] [Delete]  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Add Database Dialog:**

```
┌──────────────────────────────────────────────┐
│  Add Database Connection                     │
├──────────────────────────────────────────────┤
│                                              │
│  Name:        [myapp_prod              ]     │
│  Description: [Production application DB]    │
│                                              │
│  Driver:      [▼ PostgreSQL            ]     │
│               ┌────────────────────────┐     │
│               │ PostgreSQL             │     │
│               │ MySQL                  │     │
│               │ MariaDB                │     │
│               │ SQL Server             │     │
│               │ Oracle                 │     │
│               │ Snowflake              │     │
│               │ SQLite                 │     │
│               └────────────────────────┘     │
│                                              │
│  Host:        [prod-host               ]     │
│  Port:        [5432                    ]     │
│  Database:    [myapp                   ]     │
│  Username:    [readonly                ]     │
│  Password:    [••••••••                ]     │
│  SSL Mode:    [▼ require               ]     │
│                                              │
│  —— or ——                                    │
│                                              │
│  DSN:         [postgres://...          ]     │
│                                              │
│           [Test Connection]  [Cancel] [Save] │
└──────────────────────────────────────────────┘
```

The form supports both structured fields (host/port/database/user/pass) and raw DSN input. Structured fields auto-compose the DSN. The "Test Connection" button validates the connection before saving.

#### 2. Agent Settings > Databases Tab

A new tab in the agent settings panel, alongside existing tabs like MCP, Environment, etc.

```
┌─────────────────────────────────────────────────────────────────┐
│  Agent: incident-investigator                                   │
│  [General] [MCP] [Databases] [Environment] [Sandbox]           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Databases                                              [+ Add] │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🟢 myapp_prod          PostgreSQL                         │  │
│  │    Access: [▼ Read Only    ]                               │  │
│  │           ┌────────────────┐                               │  │
│  │           │ 🔒 Read Only   │  Can query and describe       │  │
│  │           │ 🔓 Full Control│  Can query, insert, update,   │  │
│  │           └────────────────┘  delete, and run raw SQL      │  │
│  │                                              [Remove]      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🟢 myapp_dev            PostgreSQL                         │  │
│  │    Access: Full Control                                    │  │
│  │                                              [Remove]      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  —— Access Tier Guide ——————————————————————————————————————    │
│                                                                 │
│  🔒 Read Only                                                   │
│     Best for: Production databases, investigation, reporting    │
│     Tools: list_services, list_tables, describe_table, query    │
│                                                                 │
│  🔓 Full Control                                                │
│     Best for: Development databases, migrations, data seeding   │
│     Tools: All read tools + insert, update, delete, raw_sql     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### API Changes

#### Backend REST Endpoints

```
# Database Connection CRUD
GET    /api/v1/databases                    # List all connections
POST   /api/v1/databases                    # Create connection
GET    /api/v1/databases/{id}               # Get connection details
PUT    /api/v1/databases/{id}               # Update connection
DELETE /api/v1/databases/{id}               # Delete connection
POST   /api/v1/databases/{id}/test          # Test connection
GET    /api/v1/databases/{id}/health        # Health check
GET    /api/v1/databases/{id}/schema        # Get schema (via Faucet)

# Agent Database Access
GET    /api/v1/agents/{id}/databases        # List agent's database assignments
POST   /api/v1/agents/{id}/databases        # Assign database to agent
PUT    /api/v1/agents/{id}/databases/{db_id} # Update access tier
DELETE /api/v1/agents/{id}/databases/{db_id} # Remove database from agent
```

#### Request/Response Models

```python
# backend/app/models/database.py

class DatabaseDriver(str, Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    MSSQL = "mssql"
    ORACLE = "oracle"
    SNOWFLAKE = "snowflake"
    SQLITE = "sqlite"

class AccessTier(str, Enum):
    READ_ONLY = "read_only"
    FULL_CONTROL = "full_control"

class DatabaseConnectionCreate(BaseModel):
    name: str = Field(..., pattern=r'^[a-z][a-z0-9_]{1,62}$')
    driver: DatabaseDriver
    dsn: SecretStr  # encrypted before storage
    description: str | None = None

class DatabaseConnectionResponse(BaseModel):
    id: str
    name: str
    driver: DatabaseDriver
    description: str | None
    status: str  # active, error, disconnected
    agent_count: int  # number of agents using this connection
    created_at: datetime
    updated_at: datetime
    # Note: DSN is NEVER returned in responses

class AgentDatabaseAssign(BaseModel):
    database_id: str
    access_tier: AccessTier

class AgentDatabaseResponse(BaseModel):
    database_id: str
    database_name: str
    driver: DatabaseDriver
    access_tier: AccessTier
    status: str
    assigned_at: datetime
```

### Faucet Configuration Flow

When a user assigns a database to an agent, Bond orchestrates the following:

```
User clicks "Add" in Agent > Databases tab
         │
         ▼
┌─ Bond Backend ──────────────────────────────────────────────┐
│                                                              │
│  1. Validate database_id exists and is healthy               │
│  2. Determine Faucet role name:                              │
│     "{db_name}_{access_tier}"  (e.g. myapp_prod_reader)     │
│  3. Check if Faucet role exists; create if not:              │
│     faucet role create myapp_prod_reader \                   │
│       --permission "myapp_prod:*:GET"                        │
│  4. Create Faucet API key for this agent:                    │
│     faucet key create \                                      │
│       --role myapp_prod_reader \                             │
│       --name "agent-{agent_id}-{db_name}"                   │
│  5. Store API key (encrypted) in vault                       │
│  6. Create agent_database_access record in SpacetimeDB       │
│  7. Notify Gateway to refresh MCP tool filtering             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
Agent's next turn sees new Faucet MCP tools
```

### Interaction with Existing Systems

#### tbls (Doc 055)

Faucet's `faucet_describe_table` and `faucet_list_tables` provide schema discovery that overlaps with tbls. The relationship:

- **Faucet-managed databases**: Agents use Faucet's MCP tools for discovery. No need for tbls.
- **Non-Faucet databases**: tbls remains the discovery tool (e.g., databases accessed via direct connection strings in the agent's environment).
- **No breaking change**: tbls continues to work as before. Faucet is additive.

#### Permission Broker (Doc 036)

Faucet tool calls flow through the existing MCP proxy, which is already subject to broker policy. The broker's allow/deny rules apply to MCP tool calls. Faucet-specific filtering is an additional layer on top.

#### Host-Side MCP Proxy (Doc 054)

Faucet's MCP server runs on the host and is proxied to containers exactly like any other host-side MCP server. The only addition is per-agent tool filtering based on access tier, which is a new capability in the proxy layer.

---

## Security

### Credential Storage

- Database DSNs are **encrypted at rest** in Bond's vault (`~/.bond/vault/`). The `database_connection` table stores only a vault key reference, never the plaintext DSN.
- Faucet API keys are similarly encrypted in the vault. The `agent_database_access` table stores only a hash for identification.
- DSNs are decrypted only when passed to the Faucet CLI during `db add` operations. They are never logged, never included in API responses, and never sent to agent containers.

### Defense in Depth

Access control is enforced at **three layers**:

| Layer | Mechanism | What It Prevents |
|-------|-----------|------------------|
| **1. MCP Proxy (Gateway)** | Tool filtering by access tier | Agent never sees tools it shouldn't have |
| **2. Faucet RBAC** | API key → role → per-table verb permissions | Even if proxy is bypassed, Faucet rejects unauthorized operations |
| **3. Database User** | The DSN's database user has its own privileges | Even if Faucet is bypassed, the DB user limits damage |

**Recommendation for production databases:** Use a database user with minimal privileges (e.g., `SELECT` only) in the DSN itself. This way, Faucet's RBAC and the database's native permissions are both enforcing read-only access.

### Row-Level Security

For advanced use cases, Faucet supports row-level security filters per role:

```bash
faucet role create tenant_42_reader \
  --permission "myapp_prod:*:GET" \
  --filter "myapp_prod:orders:tenant_id = 42" \
  --filter "myapp_prod:customers:tenant_id = 42"
```

This is exposed as an optional advanced configuration in the UI (Phase 2).

### Audit Trail

All database operations through Faucet are logged with:
- Agent identity (which agent made the call)
- API key used
- Operation type (query, insert, update, delete, raw_sql)
- Table affected
- Timestamp
- Success/failure

These logs are stored alongside Bond's existing audit infrastructure (Doc 085).

### Network Isolation

Faucet listens on `localhost:18795` only — it is not exposed to the network. Agent containers reach it exclusively through the Gateway's MCP proxy, which authenticates requests via the agent's JWT token (`BOND_AGENT_TOKEN`).

---

## Implementation Phases

### Phase 1: Core Infrastructure (MVP)

**Goal:** A user can add a database, assign it to an agent with an access tier, and the agent can use Faucet MCP tools.

- [ ] `FaucetManager` — download binary, start/stop, health check
- [ ] SpacetimeDB tables: `database_connection`, `agent_database_access`
- [ ] Backend CRUD endpoints for database connections
- [ ] Backend endpoints for agent-database assignments
- [ ] Faucet CLI orchestration: `db add`, `role create`, `key create`
- [ ] MCP proxy tool filtering by access tier
- [ ] API key injection in proxied MCP calls
- [ ] Vault integration for DSN and API key storage
- [ ] Frontend: Settings > Databases page (list, add, edit, delete, test)
- [ ] Frontend: Agent Settings > Databases tab (assign, set tier, remove)

### Phase 2: Polish & Advanced Features

- [ ] Connection health monitoring with periodic checks and status indicators
- [ ] Schema browser in the UI (powered by Faucet's describe endpoints)
- [ ] Row-level security filter configuration in the UI
- [ ] Custom role creation (beyond the two predefined tiers)
- [ ] Query history / audit log viewer per agent per database
- [ ] Connection pooling configuration
- [ ] SSL/TLS certificate management for database connections

### Phase 3: Advanced Governance

- [ ] Query cost estimation and limits (prevent expensive full-table scans)
- [ ] Rate limiting per agent per database
- [ ] Approval gates for write operations on sensitive tables
- [ ] Automatic schema change detection and notification
- [ ] Data masking rules (e.g., mask PII columns for certain roles)

---

## Migration & Compatibility

- **No breaking changes.** Faucet is additive. Existing agents with direct database access (via environment variables or custom MCP servers) continue to work.
- **Gradual adoption.** Users can start by adding one database through the UI. There's no requirement to migrate all database access to Faucet.
- **tbls coexistence.** tbls remains available and functional. For databases managed by Faucet, agents naturally prefer Faucet's discovery tools since they're in the MCP tool list.

---

## Success Criteria

- [ ] User can add a PostgreSQL database connection through the UI in under 60 seconds
- [ ] User can assign a database to an agent with "Read Only" access in 2 clicks
- [ ] Read Only agent can list tables, describe schema, and query data
- [ ] Read Only agent **cannot** insert, update, delete, or run raw SQL (enforced at both proxy and Faucet levels)
- [ ] Full Control agent can perform all CRUD operations and run raw SQL
- [ ] DSNs are never visible in API responses, logs, or agent context
- [ ] Multiple agents can share the same database connection with different access tiers
- [ ] Faucet process is automatically started when needed and health-checked
- [ ] Connection test validates reachability before saving
- [ ] Works with at least: PostgreSQL, MySQL, SQLite

---

## Alternatives Considered

| Option | Rejected Because |
|--------|-----------------|
| **Direct DSN in agent environment** | No access governance. Agent has full privileges of the DB user. No audit trail. No UI. |
| **Custom MCP server per database** | Requires building and maintaining our own MCP server. Faucet already does this with RBAC built in. |
| **tbls + raw SQL tool** | tbls is read-only discovery. Adding a raw SQL tool would have no RBAC, no per-table permissions, no row-level security. We'd be rebuilding what Faucet already provides. |
| **Hasura / PostgREST** | Heavier dependencies. Hasura requires Docker + PostgreSQL. PostgREST is PostgreSQL-only. Neither has built-in MCP support. |
| **Prisma / Drizzle proxy** | Node.js dependencies. ORM-oriented, not API-gateway-oriented. No RBAC. No MCP. |
| **Build our own** | Significant engineering effort to replicate what Faucet provides out of the box. RBAC, multi-database support, MCP server, API key auth — all already built and tested. |

---

## References

- [Faucet GitHub](https://github.com/faucetdb/faucet)
- [Design Doc 017: MCP Integration](017-mcp-integration.md)
- [Design Doc 036: Permission Broker](036-permission-broker.md)
- [Design Doc 054: Host-Side MCP Proxy](054-host-side-mcp-proxy.md)
- [Design Doc 055: tbls Database Discovery](055-tbls-database-discovery.md)
- [Design Doc 085: Audit Trails and Activity Logging](085-audit-trails-and-activity-logging.md)
- [Design Doc 009: Container Configuration UI](009-container-configuration-ui.md)
- [Design Doc 105: MCP Live Status & Connection Testing](105-mcp-live-status-and-connection-testing.md)
