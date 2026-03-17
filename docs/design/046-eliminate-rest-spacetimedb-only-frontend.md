# Design Doc 046: Eliminate REST — SpacetimeDB-Only Frontend

## Status: Implemented (Phases 1-3 complete, cleanup remaining)
## Author: Bond AI
## Date: 2026-03-16

---

## 1. Problem Statement

The Bond frontend is a hybrid: some pages read data via SpacetimeDB WebSocket subscriptions while writing via REST `fetch()` calls to the backend (Python FastAPI) or gateway (Node/Express). This creates:

- **Inconsistent data flow** — reads are push-based (live), writes are request/response (stale until next subscription event)
- **Redundant infrastructure** — the backend REST endpoints duplicate logic that SpacetimeDB reducers already handle
- **Race conditions** — REST writes that go through the backend may not trigger STDB subscription updates immediately
- **Complexity** — developers must understand two data paths and keep them in sync

The Agents tab was successfully migrated to SpacetimeDB-only on 2026-03-16, proving the pattern works. This doc defines the plan to apply the same pattern to every page.

---

## 2. Architecture: Before and After

### Before (current hybrid)
```
┌──────────┐    fetch()     ┌──────────┐    SQL/HTTP     ┌──────────────┐
│ Frontend  │──────────────▸│ Backend  │───────────────▸│ SpacetimeDB  │
│ (Next.js) │               │ (FastAPI)│                │              │
│           │◂──────────────│          │                │              │
│           │   JSON resp   │          │                │              │
│           │               └──────────┘                │              │
│           │                                           │              │
│           │◂─────── WebSocket subscription ──────────▸│              │
└──────────┘         (read-only today)                  └──────────────┘
```

### After (target)
```
┌──────────┐         WebSocket (bidirectional)          ┌──────────────┐
│ Frontend  │◂─────────────────────────────────────────▸│ SpacetimeDB  │
│ (Next.js) │   subscribe (reads) + reducers (writes)   │              │
└──────────┘                                            └──────────────┘

┌──────────┐    (internal only, not called by frontend) ┌──────────────┐
│ Backend  │───────────────────────────────────────────▸│ SpacetimeDB  │
│ (FastAPI)│  worker writes (messages, tool logs, etc.) │              │
└──────────┘                                            └──────────────┘
```

The frontend talks **only** to SpacetimeDB via WebSocket. The backend continues writing to STDB for agent worker operations (message logging, tool execution) but is never called by the frontend for CRUD.

---

## 3. The Pattern (proven on Agents tab)

### 3.1 Reading: Subscription Hooks

```typescript
// Declare what data you need — it auto-updates on any table change
const agents = useSpacetimeDB(() => getAgents());
const plans = useSpacetimeDB(() => getWorkPlans());
```

The `useSpacetimeDB` hook subscribes to `onDataChange` events. When any subscribed table row is inserted, updated, or deleted server-side, every component using the hook re-renders with fresh data. No polling, no manual refetch.

### 3.2 Writing: Reducer Calls

```typescript
const conn = getConnection();
conn.reducers.updateAgent({ id, name, displayName, ... });
conn.reducers.deleteAgent({ id });
```

Reducers execute server-side inside SpacetimeDB. The mutation is atomic, and the subscription pushes the result back to all connected clients immediately. No REST round-trip.

### 3.3 Connection Status

```typescript
const { connected } = useSpacetimeConnection();
// Show green/red dot, disable buttons when disconnected
```

### 3.4 Client-Side ID Generation

Since there's no server to generate IDs, the frontend generates them:

```typescript
function generateId(): string {
  return crypto.randomUUID().replace(/-/g, '');
}
```

---

## 4. Migration Inventory

### 4.1 Tier 1 — Core Pages (high impact, data already in STDB)

| Page / Component | Current Source | REST Calls | STDB Tables | Reducers Needed |
|---|---|---|---|---|
| **Settings > Agents** | ✅ Done | 0 remaining | agents, agent_channels, agent_workspace_mounts | ✅ All exist |
| **Main Chat (page.tsx)** | Backend + Gateway | agents list, conversation messages, message edit/delete | agents, conversations, conversationMessages | updateConversation ✅, deleteConversationMessage ✅ |
| **Board (Kanban)** | Backend | agents list, plans CRUD, items CRUD, plan resume | agents, workPlans, workItems | All exist ✅ |
| **Board > Plans** | Backend | plans list, plan detail, plan delete | workPlans, workItems | All exist ✅ |
| **Settings > Prompts** | Backend | fragments list, fragment edit, template edit, versions, rollback, generate system prompt | prompt_fragments, prompt_templates, prompt_fragment_versions, prompt_template_versions, agent_prompt_fragments | **Exception: keep REST** — fragments are files on disk (`prompts/`), not DB CRUD. Templates are DB-backed but unused by worker. AI generation is compute. |
| **Settings Page (LLM/Embedding/Keys)** | Backend | settings get/set, embedding config, API keys | settings, providers, provider_api_keys | setSetting ✅, setProviderApiKey ✅ |

### 4.2 Tier 2 — Deployment (many components, data in STDB)

| Component | REST Calls | STDB Tables | Notes |
|---|---|---|---|
| DeploymentTab | agents, settings, models, sandbox-images | Multiple | Hub page, loads config |
| EnvironmentDashboard | components, resources, receipts, monitoring | environments, resources, components, alerts | Read-heavy, some via gateway |
| AlertRulesEditor | alert rules CRUD | alert_rules | Reducers exist ✅ |
| TriggerConfig | triggers CRUD | triggers | Reducers exist ✅ |
| ComponentDetail | component + resources + scripts + secrets | components, component_resources, component_scripts, component_secrets | Reducers exist ✅ |
| SecretManager | secrets CRUD, rotate, import | component_secrets | Partially covered |
| InfraMap | resources, manifests, components | resources, components | Read-heavy |
| MonitoringSection | monitoring status, issues | alerts, alert_rules | Read-heavy |
| PipelineSection | promotions, pipeline | promotions | Reducers exist ✅ |

### 4.3 Tier 3 — Gateway-Dependent (requires gateway changes)

| Component | Why Gateway | Migration Path |
|---|---|---|
| **ChannelsTab** | Channel start/stop/delete are runtime operations | Keep REST for channel lifecycle (imperative actions), migrate config reads to STDB |
| **OnboardServerWizard** | SSH probing, broker deployment | Keep REST for imperative actions (probe, deploy), migrate resource/component CRUD to STDB |
| **ScriptRegistration** | Script validation, promote | Keep REST for validation (compute), migrate script metadata to STDB |
| **LiveLogViewer** | Streaming logs | Keep REST/SSE for log streaming (not a CRUD pattern) |
| **BuildStrategyDetector** | Analyzes repo via gateway | Keep REST (compute, not data) |
| **QuickDeployForm** | Triggers deployment via gateway | Keep REST (imperative action) |

### 4.4 Exceptions — REST is Correct

These should **not** be migrated because they're imperative actions, not data CRUD:

- `prompts/fragments` — Filesystem reads/writes (`prompts/` directory), not database CRUD
- `prompts/fragments/{path}` — Read/write markdown files on disk
- `prompts/templates` — DB-backed but not consumed by worker; version history and rollback are complex multi-step operations
- `prompts/generate/*` — AI compute endpoints
- `agents/sandbox-images` — Docker image listing (host system query)
- `agents/browse-dirs` — File system browsing (host system query)
- `channels/*/start|stop` — Runtime channel lifecycle
- `broker/deploy` — Triggers deployment execution
- `resources/*/probe` — SSH probe execution
- `deployments/validate-*` — Server-side validation
- `deployments/runs/*` — Execution logs
- `api/stdb-ws-token` — Token exchange (bootstrap)
- `conversations/*/messages/*/edit` — Gateway WebSocket message forwarding

---

## 5. Implementation Strategy

### Phase 1: Core Pages (1-2 days)
1. **Main Chat page** — Remove `fetch(BACKEND_API/agents)` (use `useAgents()` hook), keep Gateway WebSocket for live chat
2. **Board page** — Remove `fetch(API_BASE/plans/*)` (use `useWorkPlans()`, `useWorkItems()` hooks + reducers)
3. **Board Plans page** — Same as board

### Phase 2: Settings Pages (2-3 days)
4. **Settings main (LLM/Embedding/Keys)** — Replace settings fetch with `useSpacetimeDB(() => getSettings())`, writes via `setSetting` reducer
5. **Prompts tab** — Exception: keep REST (filesystem-backed fragments, see §4.4)
6. **Channels tab** — Migrate config reads to STDB, keep channel lifecycle REST

### Phase 3: Deployment (3-5 days)
7. **Environment/Component/Alert data** — Already in STDB tables with reducers. Replace fetch reads with subscription hooks, writes with reducer calls.
8. **Pipeline/Promotion** — Same pattern.
9. **Audit remaining deployment components** — Some will keep REST for imperative operations.

### Phase 4: Cleanup (1 day)
10. **Remove dead backend REST endpoints** — Once no frontend code calls them
11. **Remove gateway persistence router sync endpoints** — No longer needed
12. **Update AGENTS.md** — Document the "no REST" rule

---

## 6. Required New Reducers

No new reducers needed. Prompts stay REST (filesystem-backed fragments, see §4.4).
All other tables already have full CRUD reducers.

---

## 7. Required New Hooks

Add to `frontend/src/hooks/useSpacetimeDB.ts`:

```typescript
export function useSettings(): SettingRow[] {
  return useSpacetimeDB(() => getSettings());
}

export function useProviders(): ProviderRow[] {
  return useSpacetimeDB(() => getProviders());
}

export function usePromptFragments(): PromptFragmentRow[] {
  return useSpacetimeDB(() => getPromptFragments());
}

export function usePromptTemplates(): PromptTemplateRow[] {
  return useSpacetimeDB(() => getPromptTemplates());
}

export function useEnvironments(): EnvironmentRow[] {
  return useSpacetimeDB(() => getEnvironments());
}

export function useResources(environment?: string): ResourceRow[] {
  return useSpacetimeDB(() => getResources(environment), [environment]);
}

export function useComponents(environment?: string): ComponentRow[] {
  return useSpacetimeDB(() => getComponents(environment), [environment]);
}

export function useAlerts(environment?: string): AlertRow[] {
  return useSpacetimeDB(() => getAlerts(environment), [environment]);
}
```

Corresponding getter functions need to be added to `spacetimedb-client.ts`.

---

## 8. Connection Bootstrap

Every page that uses SpacetimeDB data must ensure the connection is established. The pattern:

```typescript
// In the top-level page component (not in child tabs)
import { connectToSpacetimeDB } from "@/lib/spacetimedb-client";
import { STDB_WS } from "@/lib/config";

useEffect(() => {
  connectToSpacetimeDB(STDB_WS).catch(console.error);
}, []);
```

**Current status:**
- ✅ `page.tsx` (main chat) — connects
- ✅ `board/page.tsx` — connects
- ✅ `settings/page.tsx` — connects (added 2026-03-16)
- ❌ `board/plans/page.tsx` — needs connection call (or relies on board/page.tsx having been visited first)

**Recommendation:** Move connection initialization to the root layout (`app/layout.tsx`) so it happens once regardless of entry point. Use a `SpacetimeDBProvider` context component.

---

## 9. Subscription Management

Currently, `connectToSpacetimeDB` subscribes to a fixed set of tables:

```typescript
.subscribe([
  "SELECT * FROM agents",
  "SELECT * FROM agent_channels",
  "SELECT * FROM agent_workspace_mounts",
  "SELECT * FROM conversations",
  "SELECT * FROM conversation_messages",
  ...
])
```

As we add more tables to the frontend, this list needs to include all tables the UI reads from. For the full migration, the subscription should include **all public tables** or use `SELECT * FROM *` if SpacetimeDB supports it.

If subscribing to all tables causes performance concerns (e.g., `conversation_messages` with millions of rows), use filtered subscriptions:

```sql
SELECT * FROM conversation_messages WHERE conversation_id = ?
```

---

## 10. Rules for Future Development

Once migration is complete, enforce these rules:

1. **No `fetch()` to backend/gateway for data CRUD** — Use SpacetimeDB reducers
2. **No `fetch()` for data reads** — Use `useSpacetimeDB()` hooks
3. **`fetch()` is only acceptable for:**
   - Imperative actions (deploy, probe, start/stop channels)
   - Host system queries (Docker images, file browsing)
   - Token exchange (bootstrap)
   - Streaming (SSE/WebSocket for live logs)
4. **All new tables get reducers** — Every new STDB table must have insert/update/delete reducers
5. **All new pages call `connectToSpacetimeDB`** — Or use the layout-level provider
6. **Generate bindings after every module change** — `spacetime generate --lang typescript --out-dir <abs-path> --js-path ./spacetimedb/dist/bundle.js`

---

## 11. Benefits

- **Real-time everywhere** — Every page shows live data. If another client or the backend changes a row, all browsers update instantly.
- **Simpler frontend code** — No loading states for reads (data is always available after initial sync). No error handling for write responses (reducers are fire-and-forget with subscription confirmation).
- **Less backend code** — Remove ~500 lines of REST endpoint handlers that just proxy to SpacetimeDB.
- **Offline resilience** — SpacetimeDB SDK handles reconnection automatically. The `useSpacetimeConnection()` hook shows status.
- **Single source of truth** — No more "did the REST write land?" ambiguity.

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Large table subscriptions (conversation_messages) impact performance | Use filtered subscriptions with WHERE clauses |
| Reducer errors are silent (no HTTP status code) | Add `onReducerError` callbacks, show toast notifications |
| SpacetimeDB downtime breaks entire UI | Show connection status prominently, consider read-only fallback |
| Generated bindings out of sync with module | Add CI check: `spacetime generate` + `git diff --exit-code` |
| Multiple browser tabs fight over connection | SpacetimeDB SDK handles this (each tab gets its own connection) |

---

## 13. Migration Checklist

### Completed (2026-03-16)
- [x] Agents tab — subscriptions + reducers
- [x] Main chat page — `useAgents()` replaces REST fetch
- [x] Board page — STDB hooks + reducers for agents/plans/items
- [x] Board plans page — fully client-side with STDB, client filtering/pagination
- [x] Settings main — `useSettings()`/`useSettingsMap()` + `setSetting`/`setProviderApiKey` reducers
- [x] Prompts tab — Exception: keep REST (filesystem-backed fragments, see §4.4)
- [x] Deployment — `DeploymentTab`, `SingleAgentEditor`, `SetupWizard` migrated via `useAgentsWithRelations()`, `useSettingsMap()`, `callReducer()`
- [x] Move connection to root layout — `SpacetimeDBProvider` in `layout.tsx`
- [x] Add missing getter functions — `getSettings`, `getSetting`, `getProviderApiKeys` in `spacetimedb-client.ts`
- [x] Add missing hooks — `useSettingsMap`, `useAgentsWithRelations`, `callReducer`, `useProviders`, `useProviderApiKeys` in `useSpacetimeDB.ts`
- [x] Expand subscriptions — 7 new tables (settings, provider_api_keys, prompt_fragments, prompt_templates, prompt_fragment_versions, prompt_template_versions, agent_prompt_fragments)

### Remaining
- [ ] Channels tab — all calls go to Gateway (lifecycle/operational), likely stays REST
- [ ] Remaining deployment sub-components — all hit Gateway API, not backend REST; stays REST
- [ ] Remove dead backend REST endpoints (agents, settings, plans CRUD)
- [ ] Add CI binding freshness check
