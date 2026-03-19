# 053 — SolidTime Integration via MCP

**Status:** Draft  
**Created:** 2026-03-19  
**Author:** Bond Agent  

## Problem

We want agents to interact with a self-hosted SolidTime instance — logging time, managing projects/tasks/clients, starting and stopping timers. The first attempt built custom dynamic tools (`backend/app/agent/tools/dynamic/solidtime_*.py`) with hand-rolled HTTP calls and keyword injection into the tool selector. This works but has problems:

1. **Network fragility** — dynamic tools run in the backend Python process. If the backend moves into Docker (`docker-compose.dev.yml` already defines a `backend` service), `localhost:8734` breaks. You'd need `host.docker.internal` or Docker network names, which differ per platform.
2. **Maintenance burden** — 5 custom tool files + a config loader + keyword hacks in `tool_selection.py` to maintain. Any SolidTime API changes require manual updates.
3. **Reinventing the wheel** — an existing [`solidtime-mcp-server`](https://github.com/SwamiRama/solidtime-mcp-server) npm package provides 22 tools with auto member_id resolution, aggregated reports, and actionable error messages.
4. **Ignoring existing infrastructure** — Bond already has a full MCP subsystem (`MCPManager`, SpacetimeDB `mcp_servers` table, `POST /api/v1/mcp` CRUD, Pydantic model generation, tool registry integration). Using it means zero new backend plumbing.

## Design

### Approach: MCP Server + Setup UI

Replace the custom dynamic tools with the `solidtime-mcp-server` npm package, configured as an MCP server entry in SpacetimeDB. Add a setup card in the frontend Channels tab that automates the configuration.

### Architecture

```
┌─────────────────────────────────────────────────────┐
│ Frontend (ChannelsTab → SolidTimeCard)              │
│  User enters: SolidTime URL + API Token             │
│  Card calls: POST /api/v1/integrations/solidtime/   │
│              setup                                   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Gateway (integrations/solidtime.ts)                 │
│  1. Validates token via GET {url}/api/v1/users/me   │
│  2. Fetches org ID via GET /users/me/memberships    │
│  3. Creates MCP server entry in SpacetimeDB:        │
│     POST /api/v1/mcp                                │
│       name: "solidtime"                             │
│       command: "npx"                                │
│       args: ["-y", "solidtime-mcp-server"]          │
│       env: {                                        │
│         SOLIDTIME_API_TOKEN: "<token>",             │
│         SOLIDTIME_ORGANIZATION_ID: "<org_id>",      │
│         SOLIDTIME_API_URL: "<url>"                  │
│       }                                             │
│  4. Triggers backend MCP reload                     │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ Backend (MCPManager)                                │
│  load_servers_from_db() picks up "solidtime" entry  │
│  Spawns: npx -y solidtime-mcp-server (subprocess)   │
│  Registers 22 tools as mcp_solidtime_*              │
│  Tools are available to all agents immediately      │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│ solidtime-mcp-server (stdio subprocess)             │
│  Makes HTTP calls to SolidTime from its own process │
│  No Docker networking issues — runs on the host     │
│  22 tools: time entries, timer, projects, clients,  │
│            tasks, tags, aggregations, user info      │
└─────────────────────────────────────────────────────┘
```

### Why MCP Wins

| Concern | Custom Dynamic Tools | MCP Server |
|---------|---------------------|------------|
| Docker networking | Breaks if backend moves to container | MCP subprocess runs on host — always works |
| Tool count | 5 tools (basic) | 22 tools (comprehensive) |
| Maintenance | Manual — we own the code | npm package — maintained upstream |
| Backend changes | New files + keyword hacks in `tool_selection.py` | Zero backend changes — existing `MCPManager` handles everything |
| Tool selection | Required injecting keywords into heuristic selector | MCP tools already auto-added to `enabled_tools` via `mcp_*` prefix logic in worker |
| Config storage | Custom `integrations.json` file | SpacetimeDB `mcp_servers` table (already exists) |
| Error handling | Basic `raise_for_status()` | Actionable error messages built into the MCP server |

### Available MCP Tools (from solidtime-mcp-server)

The package provides these tools (all prefixed `mcp_solidtime_` in Bond):

**Time Entries:** `list_time_entries`, `create_time_entry`, `update_time_entry`, `delete_time_entry`, `aggregate_time_entries`  
**Timer:** `get_active_timer`, `start_timer`, `stop_timer`  
**Projects:** `list_projects`, `create_project`, `update_project`, `delete_project`  
**Clients:** `list_clients`, `create_client`, `update_client`, `delete_client`  
**Tags:** `list_tags`, `create_tag`, `update_tag`, `delete_tag`  
**Tasks:** `list_tasks`, `create_task`, `update_task`, `delete_task`  
**User:** `get_current_user`  

### Tool Selection

MCP tools are already handled by the worker loop. In `worker.py`:

```python
# Refresh MCP tools
await mcp_manager.refresh_tools(registry)
# Add any mcp tools to the enabled set for heuristic selection
for name in registry.registered_names:
    if name.startswith("mcp_") and name not in agent_tools:
        agent_tools.append(name)
```

However, the heuristic selector (`tool_selection.py`) still needs keyword entries so that "solid time", "timer", "log time", etc. trigger inclusion of the MCP tools. We should add keyword entries for the `mcp_solidtime_*` tool names, matching the same patterns used in the dynamic tool approach.

The group-trigger pattern (if one solidtime tool matches, include all of them) should also be preserved since the agent often needs to look up a project ID before creating a time entry.

### Frontend: SolidTimeCard

Keep the existing `SolidTimeCard.tsx` component but change its backend calls:

**Setup flow:**
1. User enters SolidTime URL + API token
2. `POST /api/v1/integrations/solidtime/setup` validates the token, resolves org/member IDs
3. Gateway creates MCP server entry via `POST /api/v1/mcp`
4. Gateway calls backend `/reload` to trigger MCP reconnection
5. Card shows "Connected" with org name + user name

**Disconnect flow:**
1. `DELETE /api/v1/integrations/solidtime`
2. Gateway deletes the MCP server entry from SpacetimeDB
3. Gateway calls backend `/reload`

**Status:**
- `GET /api/v1/integrations/solidtime/status` checks if a `solidtime` MCP server exists in the DB and reports its config

### Gateway: Integration Routes

Simplify `gateway/src/integrations/solidtime.ts`:

- **`POST /setup`** — validate token, create/update MCP server in SpacetimeDB
- **`GET /status`** — check if solidtime MCP server exists in DB
- **`DELETE /`** — remove MCP server from SpacetimeDB

Remove `integrations/index.ts` config file management — we don't need `gateway/data/integrations.json` anymore since SpacetimeDB is the source of truth.

### Cleanup: Remove Dynamic Tools

Delete the following files (superseded by MCP):

```
backend/app/agent/tools/dynamic/_solidtime_config.py
backend/app/agent/tools/dynamic/solidtime_time_entries.py
backend/app/agent/tools/dynamic/solidtime_projects.py
backend/app/agent/tools/dynamic/solidtime_tasks.py
backend/app/agent/tools/dynamic/solidtime_timer.py
backend/app/agent/tools/dynamic/solidtime_summary.py
gateway/data/integrations.json
```

Revert the keyword additions in `tool_selection.py` for `solidtime_*` tools and replace with `mcp_solidtime_*` equivalents.

## Implementation Plan

### Phase 1: Wire up MCP (minimal changes)

1. Install/verify `solidtime-mcp-server` is available via npx
2. Manually add an MCP server entry to SpacetimeDB (via existing `POST /api/v1/mcp`) to validate the approach works end-to-end
3. Test that agents can discover and use the `mcp_solidtime_*` tools
4. Add `mcp_solidtime_*` keywords to `tool_selection.py`

### Phase 2: Setup UI

1. Update `SolidTimeCard.tsx` to create MCP server entries instead of writing `integrations.json`
2. Update `gateway/src/integrations/solidtime.ts` to CRUD MCP servers via SpacetimeDB
3. Remove `integrations/index.ts` file-based config

### Phase 3: Cleanup

1. Delete dynamic tool files
2. Remove `solidtime_*` keyword entries from `tool_selection.py`
3. Remove `gateway/data/integrations.json`
4. Revert the keyword auto-registration addition to `_dynamic_tools.py` (optional — it's a good feature for other dynamic tools)

### Phase 4: Git-to-Time-Entries Skill

Create an agent skill that analyzes git history and generates SolidTime time entries from commits. This is the killer feature — instead of manually logging time, the agent reconstructs your work sessions from the commit record.

**Skill: `git-timesheet`**

Trigger phrases: "log my time from git", "create time entries from commits", "what did I work on today", "fill in my timesheet", "backfill time entries"

**How it works:**

1. Agent runs `git log` with date range, author filter, and `--stat` (files changed, insertions/deletions)
2. Groups commits into work sessions — commits within ~30min of each other are one session, gaps >30min start a new entry
3. Maps commits to SolidTime projects by:
   - Matching repo name or branch name to existing projects (via `mcp_solidtime_list_projects`)
   - Checking for project tags in commit messages (e.g., `[frontend]`, `feat(api):`)
   - Asking the user if no match is found
4. Builds time entries with:
   - **Start/end** derived from first/last commit timestamps in each session (with padding for pre-commit work)
   - **Description** summarized from commit messages
   - **Project** matched from step 3
   - **Task** matched if commit references an issue/ticket
   - **Tags** inferred from commit conventional-commit prefixes (`feat:` → feature, `fix:` → bugfix)
5. Presents the proposed entries for review before creating them
6. Creates entries via `mcp_solidtime_create_time_entry`

**Multi-repo support:** The skill should accept a list of repo paths (or scan `~/Projects/`) and merge timelines across repos. A developer switching between frontend and backend repos in the same afternoon should get coherent, non-overlapping entries.

**Edge cases:**
- Rebased/squashed commits — use author date, not commit date
- Pair programming — detect co-authored-by and note it in description
- Long gaps with no commits — don't fabricate entries, but note the gap
- Existing entries — check for overlaps before creating duplicates

**Example interaction:**
```
User: "Log my time for this week from git"
Agent: I'll check your git history across your repos...

Found 23 commits across 3 repos this week:
  bond (14 commits) → matched project "Bond"
  email-pipeline (6 commits) → matched project "Email Pipeline"  
  dotfiles (3 commits) → no project match

Proposed time entries:
  Mon 3/17  9:15–11:45  Bond — "MCP integration, tool selection refactor" (2.5h)
  Mon 3/17  13:00–14:30  Email Pipeline — "OAuth flow fix, test coverage" (1.5h)
  Tue 3/18  10:00–12:15  Bond — "SolidTime dynamic tools, frontend card" (2.25h)
  ...

3 commits to dotfiles not mapped. Create a "Personal/Infra" project? [y/n]
Total: 18.5h across 8 entries. Create these? [y/n]
```

## Open Questions

1. **npx cold start** — first invocation of `npx -y solidtime-mcp-server` downloads the package. This adds ~5-10s to the first MCP connection. Should we pre-install globally (`npm i -g solidtime-mcp-server`) during setup, or accept the one-time delay?

2. **Tool selection noise** — 22 MCP tools is a lot. The group-trigger means all 22 get included when any one matches. Should we cap to a subset (e.g., the 10 most common) or let the model sort it out? The compact schema stripping should keep token cost manageable.

3. **Keyword auto-registration for dynamic tools** — the `_dynamic_tools.py` change to read `SCHEMA["keywords"]` is actually useful infrastructure for future dynamic tools. Keep it even after removing the solidtime dynamic tools?

4. **Multiple SolidTime instances** — current design assumes one instance. If the user runs multiple (e.g., personal + work), we'd need to namespace the MCP server names (`solidtime-personal`, `solidtime-work`). Not needed now but worth noting.

## References

- [solidtime-mcp-server](https://github.com/SwamiRama/solidtime-mcp-server) — npm package
- [SolidTime API docs](http://localhost:8734/docs/api.json) — local OpenAPI spec
- `backend/app/mcp/manager.py` — Bond's MCP infrastructure
- `gateway/src/persistence/router.ts` — MCP server CRUD routes
- `frontend/src/lib/spacetimedb/mcp_servers_table.ts` — SpacetimeDB schema
