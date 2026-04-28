# 110 — MCP Runtime Architecture for Local and Container Agents

## Status
Proposed

## Summary
Bond already has the core pieces needed for MCP support:
- `mcp_servers` in **SpaceTimeDB** is the persisted source of truth for MCP server definitions
- `backend/app/mcp/manager.py` is the host-side runtime authority for loading MCP servers, maintaining connection pools, discovering tools, and proxying calls
- the frontend settings UI already receives realtime config updates via SpaceTimeDB subscriptions

The main problem is that MCP runtime behavior diverges depending on where an agent runs:
- **host/local loop** refreshes MCP tools at turn setup and can pick up changes on a later turn
- **container/worker mode** caches MCP proxy tool definitions at worker startup and can remain stale after MCP servers are added, changed, disabled, or removed

This document proposes a unified MCP runtime architecture so MCP servers work correctly whether Bond is running locally or inside Docker, and so all agents observe add/change/remove updates immediately or, at worst, by the next turn without requiring worker restart.

## Goals
- Support MCP servers reliably when Bond is running:
  - directly on the host
  - in Docker containers
- Make MCP server configuration live and authoritative from one source of truth
- Ensure **add**, **change**, **disable**, and **remove** operations propagate to all affected agents quickly
- Ensure removed or disabled MCP tools stop being advertised on the next turn at the latest
- Keep process launching and runtime execution environment clear and predictable
- Reduce special-case differences between local and container agent execution
- Preserve existing host-side MCP proxy architecture where containers use host-managed MCP processes

## Non-Goals
- Running arbitrary MCP stdio processes inside every worker container
- Replacing SpaceTimeDB with another persistence layer
- Designing a full generic event bus for unrelated runtime state
- Reworking all agent tool registration beyond what is needed for MCP freshness and consistency

## Current State

### Source of truth
MCP server definitions are stored in the `mcp_servers` table in SpaceTimeDB and surfaced in generated bindings in:
- `frontend/src/lib/spacetimedb/mcp_servers_table.ts`
- `gateway/src/spacetimedb/mcp_servers_table.ts`

The settings UI subscribes to `SELECT * FROM mcp_servers` in:
- `frontend/src/lib/spacetimedb-client.ts`

### Runtime ownership
The main runtime owner is:
- `backend/app/mcp/manager.py`

`MCPManager` currently handles:
- loading enabled MCP server definitions
- parsing command and args
- starting and maintaining connection pools
- discovering tools from servers
- registering MCP-backed tools into the runtime registry
- proxying MCP tool calls

### Host/local agent mode
In host-side agent execution:
- `backend/app/agent/loop.py`
  - calls `mcp_manager.ensure_servers_loaded(agent_id=agent["id"])`
  - then calls `mcp_manager.refresh_tools(registry)`

This means host/local agents can observe MCP changes on a later turn without restarting the backend process.

### Container/worker mode
In worker mode:
- `backend/app/worker.py` initializes `MCPProxyClient`
- it calls `await _state.mcp_proxy.list_tools()` during startup
- tool metadata is stored in `_mcp_proxy._tool_cache`
- prompt context and tool registration read from that cache

This creates the main architecture gap:
- worker MCP view is **startup-cached**
- add/change/remove does not reliably propagate to already-running workers

### Launch model in Docker
When Bond runs in Docker, workers do not directly own MCP server processes.
Instead:
- workers call the host-side MCP proxy
- the host-side backend owns `MCPManager`
- actual MCP stdio processes run in the backend runtime environment

This is the right broad direction, because host-managed MCP processes avoid requiring every worker container to have every external MCP dependency installed. However, freshness and invalidation are incomplete.

## Problems

### 1. Different freshness semantics in local vs container mode
Host-side loops refresh MCP tools per turn.
Container workers cache MCP tool metadata at startup.
This leads to inconsistent behavior and surprising operational differences.

### 2. No immediate invalidation on add/change/remove
There is currently no single, implemented path that says:
- MCP config changed
- affected runtime state is invalidated
- active workers refresh tool metadata
- next turn uses the new state

### 3. Removed or disabled tools can linger in cached worker state
This is the highest-risk failure mode.
A removed or disabled MCP server should stop appearing immediately, or at least by the next turn.
Today, stale cache makes that unreliable in worker mode.

### 4. Status is split from configuration subscriptions
The frontend receives realtime config changes from SpaceTimeDB, but live server health/status is fetched separately via REST.
That creates split-brain UI behavior and complicates reasoning about actual server state.

### 5. Runtime environment assumptions remain confusing
The command configured for an MCP server runs in the backend MCP host environment, not necessarily in the same filesystem/process environment as a Docker worker.
Without explicit modeling and validation, users can save commands that look valid but fail only at runtime.

## Design Principles
- **Single source of truth:** SpaceTimeDB remains authoritative for MCP definitions and status metadata
- **Single runtime owner:** `MCPManager` remains the owner of actual host-side MCP processes and connections
- **Uniform freshness contract:** all agents should observe MCP changes no later than the next turn; active runtimes should refresh immediately when possible
- **Explicit cache invalidation:** no silent reliance on startup-only caches
- **Execution environment clarity:** MCP commands are validated and described in the environment where they actually run
- **Safe removal:** disabled/deleted servers must be purged aggressively from advertised tool lists

## Proposed Architecture

### 1. Define a versioned MCP runtime snapshot
Introduce a monotonically increasing MCP configuration version representing the effective MCP runtime view.

Recommended shape:
- global version for all global MCP servers
- optional per-agent or per-scope version for agent-specific assignments if agent-scoped MCP servers remain supported

Possible implementation options:
1. Add a small `runtime_state` table or similar metadata row in SpaceTimeDB
2. Add `updated_at` plus a derived version strategy for `mcp_servers`
3. Add a dedicated `mcp_runtime_version` table keyed by scope

Recommended approach:
- create a dedicated version record per scope because it is explicit, cheap to compare, and easy to invalidate

Example conceptual model:
- `scope_type`: `global` or `agent`
- `scope_id`: null for global, agent id for agent scope
- `version`: integer
- `updated_at`
- `reason`: optional string like `add`, `update`, `delete`, `disable`, `enable`

Whenever an MCP server is added, updated, enabled, disabled, or deleted, Bond increments the relevant scope version(s).

### 2. Keep MCP processes host-managed in all modes
Do **not** move stdio MCP process ownership into worker containers.
Instead:
- `MCPManager` remains the only component that launches and maintains MCP server processes
- workers continue to call host-side MCP proxy endpoints for listing tools and invoking them

This preserves the useful architecture already implied by the code and existing design docs:
- one host-managed runtime authority
- one place for connection pooling and health tracking
- one execution environment to validate and troubleshoot

### 3. Replace startup-only worker cache with version-aware refresh
Workers may still keep a local cache, but it must become **version-aware** instead of startup-static.

Required changes:
- `MCPProxyClient` tracks:
  - last seen MCP version per relevant scope
  - cached tool definitions for that version
- before each turn, the worker checks whether the MCP version changed
- if changed, it re-fetches tool definitions from the proxy and replaces its cache atomically

This can be implemented in two phases.

#### Phase A — next-turn consistency
Before each turn:
- worker asks proxy for current MCP version
- if version differs from cached version:
  - fetch fresh tools
  - replace `_tool_cache`
  - rebuild MCP tool registrations for the turn

This guarantees:
- add/change/remove reflected by next turn
- no worker restart required

#### Phase B — immediate push invalidation
Add a lightweight event path so active workers are notified as soon as MCP config changes.
Then workers can eagerly invalidate their cache before the next turn begins.

Examples:
- gateway websocket event
- broker pub/sub event
- internal runtime notification routed through existing worker control channel

On receipt of an MCP invalidation event, a worker should:
- mark MCP cache stale
- optionally prefetch updated tools immediately
- ensure next turn never uses stale tool definitions

### 4. Add explicit runtime refresh API on the MCP proxy path
Add proxy endpoints for version and refresh semantics.

Recommended API shape:
- `GET /api/v1/mcp/version?agent_id=...`
  - returns effective version for the worker scope
- existing `list_tools` should return:
  - tool definitions
  - effective version used to compute them
- optional `POST /api/v1/mcp/refresh`
  - forces manager-side invalidation/reload for troubleshooting or admin actions

Worker behavior:
- use `version` for cheap freshness checks
- call `list_tools` only when version changes or local cache is empty

### 5. Make effective tool computation scope-aware and deterministic
The MCP tool view seen by an agent should be computed from:
- all enabled global MCP servers
- all enabled agent-specific MCP servers assigned to that agent
- current enabled/disabled state
- current reachable/discoverable tools from each server

The backend should own this computation and expose it consistently to both:
- host/local loop tool registration
- container worker proxy tool registration

Recommended refactor:
- move effective tool snapshot generation into a dedicated `MCPManager` method, for example:
  - `get_effective_tools(agent_id)`
  - returns `{version, tools, servers, status_summary}`

Then both execution modes consume the same result.

### 6. Push MCP status into SpaceTimeDB
Extend MCP persisted state so frontend status is subscription-driven rather than split across table subscription plus REST polling.

Recommended status fields for `mcp_servers` or a companion status table:
- `status`: `connected`, `connecting`, `error`, `disabled`, `stopped`
- `last_error`
- `last_checked_at`
- `tool_count`
- `pid` or connection marker if useful
- `runtime_environment`: e.g. `backend-host`
- `resolved_command`: normalized executable
- `resolved_args`: normalized args

If frequent status churn is a concern, use a companion table such as `mcp_server_status` keyed by server id.

Benefits:
- UI updates immediately via existing subscription patterns
- less REST polling complexity
- easier operational debugging and observability

### 7. Invalidate connection pools and stale tool definitions aggressively
When a server changes materially, runtime state must be reconciled immediately.

#### Add server
- increment relevant scope version
- try initial connection/discovery asynchronously
- publish new status
- new tools become eligible for next turn immediately

#### Update server command/env/args/name/scope
- close and discard existing connection pools for that server
- clear any discovered-tool cache for that server
- increment relevant scope version
- reconnect and rediscover tools
- publish updated status

#### Disable server
- mark disabled in source of truth
- close connection pools immediately
- remove server tools from effective tool snapshots
- increment relevant scope version
- publish disabled status

#### Delete server
- close pools immediately
- purge any status rows and discovered tool metadata
- increment relevant scope version
- ensure all effective tool snapshots exclude it

This should happen in a single backend workflow so UI and runtimes converge quickly.

### 8. Validate command/runtime assumptions at save time
The MCP command model should remain structured:
- `command` = executable
- `args[]` = arguments array
- `env` = environment overrides

On save or test connection:
- normalize command via `parse_command()` style logic where needed
- validate executable resolution in the actual backend MCP host environment
- run a bounded connection test when requested
- persist useful diagnostics for the UI

Clear UI language should state:
- this command runs in the backend MCP host environment
- when Bond is Dockerized, this is not necessarily the same as the worker container filesystem

This avoids failures caused by host-local path assumptions and aligns with the existing command parsing/test work.

## Target Runtime Flow

### Add/update/delete flow
1. User changes MCP server config in settings UI
2. SpaceTimeDB reducer updates `mcp_servers`
3. backend MCP runtime handler receives the change
4. backend invalidates affected connection pools and effective tool snapshots
5. backend increments MCP runtime version for affected scopes
6. backend updates status rows in SpaceTimeDB
7. backend emits MCP invalidation event to active workers
8. workers mark MCP cache stale or refresh immediately
9. next turn for every affected agent uses the fresh effective tool set

### Host/local turn flow
1. agent loop begins a turn
2. asks `MCPManager` for effective MCP snapshot for agent
3. if cached snapshot version is current, reuse it
4. otherwise rebuild from current manager state
5. register only tools present in the current snapshot
6. build prompt context from the same snapshot

### Container/worker turn flow
1. worker begins a turn
2. checks current MCP version for the relevant scope
3. if version changed or cache is stale, fetch fresh tool snapshot via proxy
4. replace `_tool_cache` atomically
5. register MCP handlers from that snapshot
6. build prompt context from the same snapshot

The important property is that both modes consume the same logical snapshot contract.

## Data Model Changes

### Option A — minimal schema evolution
Keep `mcp_servers` as-is and add:
- `mcp_runtime_versions`
- `mcp_server_status`

Recommended fields:

#### `mcp_runtime_versions`
- `scope_type`
- `scope_id`
- `version`
- `updated_at`
- `reason`

#### `mcp_server_status`
- `server_id`
- `status`
- `last_error`
- `last_checked_at`
- `tool_count`
- `resolved_command`
- `resolved_args_json`
- `runtime_environment`

This is the preferred approach because it avoids overloading config rows with high-churn status fields.

### Option B — embed status in `mcp_servers`
Add status fields directly to `mcp_servers`.
This is simpler short term but mixes desired configuration with observed runtime state.

Recommendation: prefer **Option A**.

## Implementation Plan

### Phase 1 — establish next-turn consistency
- Add version tracking for effective MCP scope state
- Add backend method to compute effective MCP snapshot by scope/agent
- Update host/local loop to use that snapshot method
- Update `MCPProxyClient` and worker startup/turn flow to check version before each turn
- Remove reliance on startup-only `_tool_cache`

Acceptance criteria:
- add/change/remove of an MCP server is reflected on the next turn for both local and container agents
- deleted/disabled MCP tools are not advertised after the next turn begins

### Phase 2 — add immediate invalidation
- Introduce backend-to-worker invalidation event
- On MCP config change, notify active workers for affected scopes
- Workers invalidate or refresh cache immediately

Acceptance criteria:
- active workers do not require restart
- in most cases, MCP changes are visible immediately before the next user prompt reaches tool selection

### Phase 3 — unify status and observability
- Add `mcp_server_status` subscription-backed runtime state
- Update settings UI to read status primarily from SpaceTimeDB subscription data
- Reduce or eliminate special REST polling for routine status display

Acceptance criteria:
- settings UI shows config and status updates live from subscribed state
- backend troubleshooting still has explicit health/test endpoints where useful

### Phase 4 — improve validation and operations UX
- Validate executable resolution and launch assumptions during test/save workflows
- Show runtime-environment-specific diagnostics in UI
- Add admin refresh/invalidation endpoint for operations and debugging

## Code Areas Likely to Change

### Backend
- `backend/app/mcp/manager.py`
  - add effective snapshot/version handling
  - add pool invalidation by server/scope
  - publish status updates
- `backend/app/api/v1/mcp.py`
  - expose version and snapshot-oriented proxy APIs
  - trigger invalidation/version bumps on CRUD changes
- `backend/app/agent/loop.py`
  - consume effective MCP snapshot instead of ad hoc refresh path
- `backend/app/agent/context_builder.py`
  - build MCP prompt context from snapshot, not stale worker cache
- `backend/app/worker.py`
  - move from startup-only tool fetch to version-aware turn refresh
- `backend/app/agent/tools/mcp_proxy.py`
  - add version check support and atomic snapshot refresh

### Frontend/Gateway
- `frontend/src/app/settings/mcp/McpTab.tsx`
  - consume subscription-backed status if added
  - display clearer environment/diagnostic info
- `frontend/src/lib/spacetimedb-client.ts`
  - subscribe to new status/version tables if introduced
- `gateway` routing layer if worker invalidation events are routed through it

## Testing Strategy

### Unit tests
- version bump on add/update/delete/enable/disable
- effective tool snapshot computation for:
  - global only
  - agent-specific only
  - combined scope
  - disabled server exclusion
- pool invalidation on server config change
- worker cache refresh when version changes

### Integration tests
- local agent sees newly added MCP tool on next turn
- local agent stops seeing deleted/disabled tool on next turn
- container worker sees newly added MCP tool on next turn without restart
- container worker stops seeing removed tool on next turn without restart
- command validation reports clear errors for non-resolvable executables

### UI tests
- settings page reflects MCP config changes live
- status changes appear from subscribed state
- delete/disable transitions remove or mark server state promptly

## Risks and Mitigations

### Risk: extra per-turn latency from version checks
Mitigation:
- version check is much cheaper than full tool discovery
- use lightweight scope version endpoint
- only re-fetch tools when version changes

### Risk: status churn creates too many subscription updates
Mitigation:
- use separate `mcp_server_status` table
- debounce noisy transitions if needed
- avoid writing status for every trivial heartbeat unless state changed

### Risk: agent-specific scoping becomes hard to reason about
Mitigation:
- define effective snapshot contract clearly
- keep scope resolution in one backend method
- test global + agent-specific precedence explicitly

### Risk: stale workers still process one turn with old data during transition
Mitigation:
- version check happens at turn start
- invalidation event eagerly marks stale caches
- tool invocation path can also reject removed servers defensively if needed

## Open Questions
- Should agent-scoped MCP servers remain first-class, or should all MCP servers be global with assignment metadata elsewhere?
- Should discovered tool metadata be persisted, or remain derived runtime state only?
- What worker control channel is the best path for immediate invalidation in the current gateway/backend architecture?
- Should `list_tools` return a full snapshot object including versions, statuses, and server metadata rather than only tool defs?

## Recommendation
Implement this in two practical steps:
1. **Immediately add version-aware per-turn refresh for workers** so add/change/remove is correct by next turn in both host and container modes.
2. **Then add event-driven invalidation and subscription-backed status** so updates feel immediate and the UI/runtime model becomes clean and consistent.

This keeps the existing host-managed MCP proxy architecture, which is the right foundation for Docker support, while closing the biggest functional gap: stale MCP tool visibility in running workers.
