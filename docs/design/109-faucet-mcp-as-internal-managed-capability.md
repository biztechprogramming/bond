# Design Doc 109: Faucet MCP as an Internal Managed Capability for Attached Databases

**Status:** Implemented (Phase 1–4)
**Date:** 2026-04-11
**Author:** Bond Agent
**Depends on:** 017 (MCP Integration), 054 (Host-Side MCP Proxy), 107 (Faucet Database Integration), 108 (Agent Database Context and Query Behavior)

### Implementation Notes (2026-04-11)

**Phase 1 — Managed Faucet MCP registration:**
- `MCPServerConfig` extended with `managed`, `hidden_from_ui`, `source` fields.
- `MCPManager.ensure_faucet_registered()` added — registers Faucet as an internal managed MCP server, hidden from user-facing server lists.
- `MCPManager.list_tools()` gains `include_managed` parameter; managed servers excluded by default.
- Backend `/mcp/servers` endpoint excludes managed servers from its response.

**Phase 2 — Runtime assignment resolver:**
- New module `backend/app/mcp/database_capability.py` implements the full database capability layer.
- `resolve_agent_databases(agent_id)` queries `agent_database_access` joined with `database_connections` at call time (turn-scoped, not cached).
- `ResolvedDatabaseAssignment` carries normalized names for fuzzy matching.

**Phase 3 — Bond-native virtual database tools:**
- 8 virtual tools defined: `database_list_databases`, `database_list_tables`, `database_describe_table`, `database_query`, `database_insert_rows`, `database_update_rows`, `database_delete_rows`, `database_execute_sql`.
- Each tool has a JSON schema definition and an async handler that delegates to Faucet MCP via `MCPManager.call_tool("faucet", ...)`.
- Handlers registered in `build_registry()` alongside native tools.
- `BOND_TO_FAUCET` mapping connects each virtual tool to its Faucet MCP counterpart.

**Phase 4 — Dynamic exposure + gateway filtering:**
- Backend `/mcp/proxy/tools` injects virtual database tools based on current agent assignments.
- Backend `/mcp/proxy/call` handles `database_*` tool calls with call-time authorization and fuzzy DB resolution before delegating to Faucet.
- Gateway `faucet-filter.ts` updated to prefer Bond-native `database_*` tools and suppress raw `faucet_*` equivalents.
- Gateway broker router applies the same suppression at the tool-list level.

**Phase 5–6 (remaining):**
- Doc 108 context injection (prompt fragment with attached DB metadata) — not yet wired into `context_builder.py`. TODO.
- UI consistency verification — no UI changes in this pass.

**Files changed:**
- `backend/app/mcp/manager.py` — MCPServerConfig fields, `ensure_faucet_registered()`, `list_tools()` filtering
- `backend/app/mcp/database_capability.py` — new; full database capability layer
- `backend/app/agent/tools/__init__.py` — register virtual DB tools in `build_registry()`
- `backend/app/api/v1/mcp.py` — proxy endpoints handle `database_*` tools, inject virtual tools
- `gateway/src/mcp/faucet-filter.ts` — Bond-native tool sets, suppress raw faucet tools
- `gateway/src/broker/router.ts` — import faucet-filter, suppress raw faucet tools in tool list
- `backend/tests/test_database_capability.py` — 20 unit tests for the capability layer

---

## Problem

Design Doc 107 establishes Faucet as Bond's managed database gateway, and Design Doc 108 establishes that agents should treat attached databases as first-class live data sources. But there is still an architectural gap:

1. **Attached databases are not yet a first-class managed MCP capability.** The current design talks about Faucet's built-in MCP server, but does not define how Bond should expose Faucet tools to agents in a way that is automatic, scoped, and reliable.
2. **User-managed MCP configuration would be the wrong abstraction.** Asking users to add Faucet as a generic MCP server in the UI would duplicate the Databases feature, split configuration across two places, and make agent behavior less reliable.
3. **Agent containers must react to access changes without restart.** If a user adds or removes a database from an agent, that change should take effect on the next turn. Requiring a container restart would make the feature feel broken and violate Bond's host-managed MCP model from Doc 054 and the live-refresh goals in Doc 105.
4. **All agents with attached databases should be able to use them.** Database access cannot depend on whether a particular sandbox image has database clients installed or whether the container booted before or after the database was attached.
5. **The tool contract should be Bond-native, not Faucet-native.** Raw Faucet tool names expose implementation details and make prompt behavior, fuzzy database targeting, and future backend flexibility harder than necessary.

The result today is a mismatch between the UI concept of “this agent has database access” and the runtime reality of whether the agent actually sees and uses the right tools.

---

## Decision

Bond should integrate Faucet's MCP server as an **internal managed capability**, not as a user-managed MCP server.

Bond should **immediately introduce Bond-native virtual database tools** backed by Faucet MCP rather than exposing raw Faucet tool names as the primary tool surface.

Bond should enforce access at **both** the tool-list filtering layer and the call-time authorization layer.

Bond should support **fuzzy attached-database name resolution** in tool handlers, with clarification when multiple matches are plausible.

Bond should **re-check assignment access on every tool call**, provided the performance is acceptable. If profiling shows unacceptable overhead, Bond may add short-lived caches, but the authorization model remains per-call.

### This means

- Users add databases through Bond's **Databases** UI.
- Users attach databases to agents through Bond's **Agent settings** UI.
- Bond manages the Faucet server lifecycle on the **host**.
- Bond manages Faucet's MCP server registration **internally**.
- Bond exposes a stable set of **Bond-native virtual database tools** whose handlers delegate to Faucet MCP.
- Bond dynamically exposes the appropriate database tools to each agent based on its current `agent_database_access` assignments.
- Agent containers do **not** need to restart when assignments change; tool visibility, fuzzy resolution targets, and prompt context update on the next turn.

### This does **not** mean

- Users manually create a Faucet MCP server entry in MCP settings.
- Faucet appears as a normal user-editable MCP integration.
- Database access is baked into container startup state.
- Agents are expected to learn raw Faucet tool names as the main product abstraction.

---

## Goals

1. **Make attached databases usable by any agent immediately on the next turn.**
2. **Avoid container restarts when database assignments change.**
3. **Keep database configuration in the Databases/Agents UI, not the generic MCP UI.**
4. **Use Faucet's MCP server under the hood for tool execution.**
5. **Expose Bond-native virtual database tools instead of raw Faucet MCP tools.**
6. **Scope visible tools and effective permissions to the agent's current database assignments and access tier.**
7. **Preserve Bond's host-side MCP architecture from Doc 054.**
8. **Support multiple attached databases per agent.**
9. **Allow natural database references from the model and user through fuzzy name resolution.**

## Non-Goals

- Exposing Faucet as a generic end-user MCP server configuration surface
- Replacing Faucet's server-side RBAC with prompt-only controls
- Requiring per-container installation of database clients or Faucet binaries
- Designing advanced SQL policy authoring beyond the Read Only / Full Control tiers from Doc 107
- Preserving raw Faucet tool naming as the long-term agent-facing contract

---

## User Experience

### Add database

The user adds a database in the Databases tab. Bond stores the connection in `database_connections`, provisions it into Faucet, and tracks health.

### Attach database to agent

The user opens an agent, selects one or more databases, and chooses an access tier. Bond creates or updates `agent_database_access` rows and provisions the corresponding Faucet role/API key mapping.

### Ask the agent a database question

On the agent's next turn:

- the runtime injects attached database context (Doc 108)
- the runtime exposes the relevant Bond-native virtual database tools
- the agent can immediately inspect/query the attached database(s)
- if the user references a database imprecisely, the handler can resolve it fuzzily when unambiguous

No container restart is required.

### Remove or downgrade access

If the user removes a database or changes Full Control → Read Only:

- the next turn sees the updated assignment state
- tool availability changes immediately
- backend-side authorization checks use the new state
- existing containers continue running unchanged

---

## Why Internal Managed MCP Is the Right Model

### Why not user-managed MCP?

Treating Faucet as a normal MCP server would create multiple problems:

1. **Split-brain configuration** — database connection lives in Databases UI, but tool availability lives in MCP UI.
2. **Poor discoverability for the model** — generic MCP registration does not tell the agent which databases are attached *right now*.
3. **Unclear permission story** — users would have to understand both MCP server config and database assignment config.
4. **Harder lifecycle management** — Bond already knows when databases and agent assignments change; requiring separate MCP CRUD would duplicate state.
5. **Worse UX** — “add a database to Bond” should be enough.

### Why MCP underneath anyway?

Using Faucet's MCP server internally still gives us the benefits of MCP:

- standardized tool discovery and invocation
- reuse of Bond's MCPManager and proxy path
- host-side execution per Doc 054
- no database client dependencies in containers
- a clean abstraction for multi-database operations

So the right answer is:

> **MCP as implementation detail, first-class Databases UX as product surface.**

---

## Architecture

```text
User
  │
  ├─ adds database in Databases UI
  ├─ attaches database to agent in Agent settings
  ▼
Frontend
  ▼
Backend API / SpacetimeDB
  ├─ database_connections
  └─ agent_database_access
  ▼
FaucetManager (host)
  ├─ manages Faucet server process
  ├─ provisions DBs / roles / API keys
  └─ ensures Faucet MCP server is available
  ▼
MCPManager (host)
  ├─ maintains Faucet MCP connection pool
  ├─ exposes Faucet-backed execution primitives
  └─ refreshes tool registry independently of container lifecycle
  ▼
Database Capability Layer (host)
  ├─ resolves current agent database assignments
  ├─ performs fuzzy database name resolution
  ├─ exposes Bond-native virtual database tools
  └─ authorizes every call against current assignment state
  ▼
Gateway / Broker MCP proxy
  ├─ authenticates agent turn/tool calls
  ├─ filters tools by current agent assignments
  └─ forwards execution with assignment-aware authorization
  ▼
Worker container
  ├─ receives current tool list each turn
  └─ uses attached database tools without restart
```

---

## Core Design

### 1. Single managed Faucet MCP server on the host

Bond runs Faucet on the host and registers its MCP server internally, similar to a managed integration.

Suggested internal MCP registration shape:

```json
{
  "name": "faucet",
  "transport": "stdio",
  "command": "~/.bond/bin/faucet",
  "args": ["mcp", "--config-dir", "~/.bond/faucet"],
  "managed": true,
  "hidden_from_ui": true,
  "source": "bond-faucet-managed"
}
```

### Requirements

- **Host-only**: Faucet binary runs on the host, not in agent containers.
- **Managed lifecycle**: Bond starts/stops/health-checks Faucet.
- **Internal registration**: Faucet does not appear as a normal editable MCP server card.
- **Shared pool**: MCPManager can reuse a single host-side Faucet connection pool across agents.

This aligns directly with Doc 054's host-side MCP architecture.

---

### 2. Agent database access is runtime-resolved, not container-baked

The source of truth for agent database access is the current contents of:

- `database_connections`
- `agent_database_access`

Bond must resolve these at runtime on each turn/tool-list request, not only when the container starts.

### Why

If access is baked into container startup:

- newly attached databases would be invisible until restart
- removed access could remain visible too long
- long-lived containers would drift from current assignment state

That is explicitly not allowed by this design.

### Rule

> **Database capability is turn-scoped and assignment-driven, not container-scoped.**

On every turn, Bond should be able to answer:

- which databases are attached to this agent?
- what access tier does each have?
- what Faucet role/API key corresponds to each assignment?
- which Bond-native virtual tools should the agent see right now?

---

### 3. Bond-native virtual database tools are the primary contract

Bond should not expose raw global Faucet MCP tools as the main agent-facing database surface.

Instead, Bond should define a stable **Bond-native virtual database tool surface** and implement those tools by delegating to Faucet MCP.

This is the chosen design, not a future refinement.

### Why

1. Better discoverability for the model than raw Faucet-branded tools.
2. Clearer semantics for attached databases and multi-database workflows.
3. Easier prompt design in Doc 108.
4. More freedom to normalize naming across future database backends.
5. Simpler fuzzy database targeting and argument shaping.
6. Lower coupling to Faucet-specific naming if the implementation evolves later.

### Initial virtual tool set

#### Read Only
- `database_list_databases`
- `database_list_tables`
- `database_describe_table`
- `database_query`

#### Full Control
- all read-only tools, plus:
- `database_insert_rows`
- `database_update_rows`
- `database_delete_rows`
- `database_execute_sql`

The handler implementation may call one or more Faucet MCP tools internally, but the agent-facing names remain Bond-native.

---

### 4. Dynamic tool exposure per agent, per turn

Bond should derive an **effective attached-database tool surface** for the current agent.

This surface is computed from current assignment state and access tier, then presented as Bond-native tools with current database metadata in their descriptions/examples.

Examples:

- if the agent has only read-only assignments, write tools are omitted entirely
- if the agent has no attached databases, database tools are omitted entirely
- if the agent has one attached database, descriptions may indicate the database argument is optional
- if the agent has several attached databases, descriptions should encourage explicit selection when the request is ambiguous

This computation must happen without container restart.

---

### 5. Assignment-aware authorization on every tool call

Tool visibility alone is not enough. Every database tool call must also be checked against the agent's **current** assignments.

For each call, Bond should verify:

1. the agent still has access to the referenced database
2. the requested operation is allowed by the current access tier
3. the assignment status is healthy/usable enough for execution
4. the corresponding Faucet role/API key is still valid

This check must happen at call time so that access changes apply immediately without restart.

### Defense in depth

There should be multiple enforcement layers:

1. **Prompt guidance** (Doc 108)
2. **Tool list filtering** (agent only sees tools it should use)
3. **Bond-side authorization** (call-time assignment check)
4. **Faucet RBAC** (server-side role enforcement)

No single layer is sufficient on its own.

### Performance note

The default model is to **re-check on every tool call**. If this proves too expensive, Bond may introduce short-lived caches or memoized assignment snapshots, but those optimizations must preserve near-immediate revocation and must not require container restart.

---

### 6. Fuzzy attached-database name resolution

Tool handlers should support fuzzy resolution of attached database references so the model and the user can use natural names.

Examples:

- `resume prettier`
- `resume-prettier`
- `Resume Prettier DB`
- `the analytics database`

### Resolution rules

1. Prefer exact ID match when a database ID is provided.
2. Prefer exact normalized name match.
3. Allow case-insensitive and punctuation-insensitive matching.
4. Allow prefix and close-name matching among the agent's currently attached databases.
5. If exactly one attached database is a strong match, use it.
6. If multiple databases are plausible matches, ask for clarification rather than guessing.
7. Never resolve to a database the agent is not currently assigned.

### Why this belongs in the handler layer

Fuzzy resolution should happen inside the database capability layer rather than in the prompt alone because:

- it keeps the tool contract ergonomic
- it avoids fragile prompt-only name matching
- it centralizes ambiguity handling and logging
- it ensures all resolution still respects current assignment state

---

### 7. Prompt/context injection remains required

Even with excellent virtual tool exposure, agents still need explicit runtime context about attached databases.

Doc 108 remains necessary and should be treated as a companion to this design:

- attached database list injected each turn
- health/access tier surfaced clearly
- prompt fragment teaching the model to use attached DB tools first for live-data questions
- examples updated to use Bond-native virtual database tools rather than Faucet-native names

This doc does **not** replace Doc 108. It supplies the runtime/tooling architecture that makes Doc 108 effective.

---

## Detailed Runtime Flow

### Turn start

When a user sends a message to an agent:

1. Backend resolves the agent.
2. Backend loads current `agent_database_access` rows for that agent.
3. Backend joins current `database_connections` metadata.
4. Backend builds the attached-database context block (Doc 108).
5. Backend computes the effective Bond-native database tool list for the agent.
6. Backend refreshes/filters the tool definitions for this turn.
7. Worker receives the updated prompt + tool surface.

No container restart is involved.

### Tool invocation

When the agent calls a database tool:

1. The call goes through Bond's normal tool path / MCP proxy path.
2. Bond resolves the database target using explicit or fuzzy matching against current assignments.
3. Bond re-checks the agent's current assignment state.
4. Bond verifies the requested operation is still allowed.
5. Bond resolves the correct Faucet service/database target.
6. Bond executes via Faucet MCP using the assignment's effective permissions.
7. Result is returned to the model.

### Assignment change

When the user adds/removes/updates a database assignment:

1. `agent_database_access` is updated.
2. Faucet role/API key state is updated if needed.
3. Any relevant runtime caches are invalidated.
4. No sandbox restart occurs.
5. The next turn/tool-list request sees the new state automatically.

---

## Data Model Implications

The existing tables from Doc 107 are the right foundation:

- `database_connections`
- `agent_database_access`

Current `agent_database_access` fields already support the managed model well:

- `agentId`
- `databaseId`
- `accessTier`
- `faucetApiKeyVaultRef`
- `faucetRoleName`
- `status`
- `assignedAt`

### Additional derived runtime state

Bond may also need a runtime resolver that produces a normalized structure like:

```json
{
  "database_id": "db_resume_prettier",
  "database_name": "resume prettier",
  "driver": "postgres",
  "status": "healthy",
  "access_tier": "read_only",
  "faucet_role": "bond_agent_read_db_resume_prettier",
  "allowed_operations": ["list_tables", "describe_table", "query"],
  "tool_scope": "attached_database",
  "normalized_names": ["resume prettier", "resume-prettier", "resumeprettier"]
}
```

This does not need to be persisted if it can be computed efficiently.

---

## Tool Surface Design

### Preferred user-facing tools

The initial agent-facing tool set should be compact and capability-oriented:

#### Read Only
- `database_list_databases`
- `database_list_tables`
- `database_describe_table`
- `database_query`

#### Full Control
- all read-only tools, plus:
- `database_insert_rows`
- `database_update_rows`
- `database_delete_rows`
- `database_execute_sql`

### Tool argument pattern

Each tool should accept either:

- an explicit attached database identifier/name
- or omit it when only one attached database exists

Example:

```json
{
  "database": "resume prettier",
  "table": "resumes",
  "limit": 10
}
```

### Resolution behavior

- If exactly one attached database exists and `database` is omitted, Bond uses it automatically.
- If multiple attached databases exist and `database` is omitted, the handler should attempt inference from the user/tool context only when unambiguous.
- If ambiguity remains, the tool should return a clarification error that the model can relay cleanly.

This is friendlier than exposing raw Faucet service names directly to the model.

---

## No-Restart Requirement

This is a hard requirement.

### Requirement

Changes to attached databases must take effect **without restarting the agent container**.

### Why this is feasible

Bond's MCP architecture already supports host-side tool execution and dynamic tool discovery. The worker does not need a local Faucet process. The backend/gateway can change the effective tool set independently of sandbox lifecycle.

### Implications

- Do not cache attached database assignments only at container boot.
- Do not require container env var mutation for DB access.
- Do not rely on per-container installed DB clients.
- Do not require MCP server startup inside the container.

### Acceptable refresh timing

- **Required:** next turn reflects assignment changes
- **Preferred:** tool-list refresh can also happen immediately for already-open UIs
- **Required if performance allows:** authorization re-check on each tool call
- **Not required:** mid-tool-call mutation handling beyond normal authorization checks

---

## Integration with Existing Bond Components

### MCPManager (`backend/app/mcp/manager.py`)

MCPManager remains the host-side owner of Faucet MCP connectivity.

Needed capabilities:

- ensure managed Faucet MCP server is registered/loaded
- provide execution access for Faucet-backed DB operations
- support refresh without container restart
- cooperate with a higher-level database capability layer rather than exposing raw Faucet tools directly

### Database capability layer

Bond should add a database capability layer responsible for:

- resolving current attached databases per agent
- deriving allowed operations from `accessTier`
- fuzzy name resolution
- generating Bond-native virtual tool definitions
- enforcing call-time authorization
- delegating execution to Faucet MCP

### Context builder (`backend/app/agent/context_builder.py`)

Context builder should inject attached database context per Doc 108 using current assignment state and virtual tool names.

### Agent/tool registry

The tool registry should treat attached database tools as runtime-available tools rather than static container capabilities.

### Gateway/Broker MCP proxy

The proxy layer should enforce assignment-aware filtering and authorization before forwarding to Faucet MCP.

This is intentionally **both** a gateway concern and a backend concern:

- gateway/broker provides early filtering, auth, and audit
- backend/database capability layer provides authoritative assignment resolution and execution checks

---

## Implementation Plan

### Phase 1 — Formalize managed Faucet MCP registration

1. Add an internal/managed MCP registration path for Faucet.
2. Mark Faucet as hidden from end-user MCP CRUD UI.
3. Ensure Faucet lifecycle is host-managed and health-checked.

### Phase 2 — Runtime assignment resolver

1. Add a backend resolver for current attached databases per agent.
2. Join `agent_database_access` with `database_connections`.
3. Derive allowed operations from `accessTier`.
4. Add normalized names/fuzzy matching metadata.

### Phase 3 — Bond-native virtual database tools

1. Define Bond-native database tool schemas and names.
2. Generate or register these tools independently from raw Faucet tool names.
3. Implement handlers that map each virtual tool to Faucet MCP calls.
4. Make tool descriptions reflect the current attached database set.

### Phase 4 — Dynamic exposure + dual enforcement

1. Filter/enable virtual tools based on current assignments.
2. Ensure the effective tool set is recomputed per turn/tool-list request.
3. Add gateway-side filtering and audit.
4. Add backend-side call-time authorization checks against current assignment state.
5. Re-check on every tool call, adding caching only if profiling justifies it.

### Phase 5 — Prompt/runtime behavior

1. Implement Doc 108 context injection.
2. Update the attached-database prompt fragment to reference Bond-native virtual tools.
3. Ensure DB questions prefer live DB tools over file/code inspection.
4. Ensure ambiguous fuzzy matches lead to clarification rather than unsafe guessing.

### Phase 6 — UX consistency

1. Keep database setup in Databases UI.
2. Keep agent assignment in Agent settings.
3. Do not require MCP settings interaction for attached databases.
4. Surface clear status/health in the UI.

---

## Alternatives Considered

### 1. User-managed Faucet MCP server in MCP settings

Rejected.

This would make the feature harder to understand, split configuration across UI surfaces, and weaken the connection between “database attached to agent” and “database tools available now.”

### 2. Expose raw Faucet MCP tools first, virtualize later

Rejected.

This would lock prompt behavior, examples, and user expectations to implementation details we already know we do not want as the main abstraction.

### 3. Direct DSN injection into agent containers

Rejected.

This would require container restarts for access changes, bypass host-side MCP architecture, weaken governance, and reintroduce DB-client/runtime dependency issues.

### 4. Custom non-MCP database tool stack

Rejected for now.

Possible, but unnecessary when Faucet already provides an MCP server and Bond already has host-side MCP infrastructure.

### 5. Per-agent Faucet MCP server instance

Rejected.

Unnecessary process overhead and worse lifecycle complexity. A shared managed Faucet server with assignment-aware filtering is the better fit.

---

## Risks

### Tool-selection ambiguity

Even with virtual tools, the model may choose the wrong operation or target database. Mitigation:

- implement Doc 108 prompt/context injection
- keep the initial tool set compact
- use fuzzy resolution with clarification on ambiguity

### Stale caches

If assignment state or tool definitions are cached too aggressively, access changes may not apply immediately. Mitigation:

- resolve assignments per turn
- invalidate relevant caches on assignment mutation
- enforce authorization again at call time
- keep any performance cache short-lived and revocation-friendly

### Multi-database confusion

If an agent has several attached databases, the model may choose the wrong one. Mitigation:

- inject explicit attached database names/status into prompt context
- support fuzzy matching only within the attached set
- require clarification when the user's reference is ambiguous

### Per-call authorization overhead

Re-checking authorization on every tool call may add latency. Mitigation:

- measure before optimizing
- cache normalized assignment snapshots briefly if needed
- keep the correctness model per-call even if the implementation uses a short TTL

### Over-coupling to Faucet internals

If Bond leaks Faucet-specific assumptions too far upward, future backend changes become harder. Mitigation:

- keep the user-facing abstraction centered on Bond-native database tools
- isolate Faucet-specific translation in the database capability layer

---

## Acceptance Criteria

1. A user can add a database in Bond and attach it to an agent without configuring MCP settings manually.
2. Any agent with an attached database can use Bond-native database tools on its next turn.
3. Adding, removing, or changing database access does **not** require restarting the agent container.
4. Tool visibility reflects the agent's current attached databases and access tier.
5. Tool execution is re-authorized against current assignment state at call time.
6. Faucet runs on the host as a managed service and its MCP server is treated as an internal Bond capability.
7. Attached database context is injected each turn so the model understands which live databases it can use.
8. Agents prefer attached database tools over codebase inspection for live-data questions, per Doc 108.
9. Bond-native virtual database tools are the primary agent-facing contract; raw Faucet tool names are not required in prompts or UI.
10. Fuzzy database references resolve correctly when unambiguous and ask for clarification when ambiguous.

---

## Open Questions

1. What short-lived cache budget is acceptable for per-call authorization without weakening revocation semantics?
2. Should fuzzy matching use only deterministic normalization at first, or include ranked similarity scoring from day one?
3. How should long-lived streaming turns behave if access is revoked mid-turn? Current recommendation: re-check on each tool call, not every token.
4. Should `database_execute_sql` be restricted to structured safe subsets for some drivers even under Full Control, or should Faucet policy remain the sole execution guardrail?

---

## References

- [Design Doc 017: MCP Integration](017-mcp-integration.md)
- [Design Doc 054: Host-Side MCP Proxy](054-host-side-mcp-proxy.md)
- [Design Doc 105: MCP Live Status & Connection Testing](105-mcp-live-status-and-connection-testing.md)
- [Design Doc 107: Faucet Database Integration](107-faucet-database-integration.md)
- [Design Doc 108: Agent Database Context and Query Behavior](108-agent-database-context-and-query-behavior.md)
