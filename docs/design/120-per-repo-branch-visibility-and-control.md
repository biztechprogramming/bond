# Design Doc 120: Per-repo branch visibility and control

**Status:** Implemented on `feat/per-repo-branches` (uncommitted). Schema published live to the dev STDB; `bond-bond` image rebuilds clean (frontend `next build` + backend import OK). NOT yet deployed to the running stack and not merged — live deploy + end-to-end verification pending. Worker-side changes (branches endpoint, heartbeat baseline) only take effect once an agent's `/bond` is on a branch carrying these commits (§7 deploy note). Builds on Doc 113 (clone-only workspaces) and the conversational branch-conflict flow from Doc 119 §7 / `feat/startup-time`.
**Driver:** An agent's workspace can mount multiple repos (`/workspace/{name}` each), but during a conversation the user can neither **see** which branch each repo is on nor **change** it without digging into Settings → Agent Repos. The prominent header control (`ConversationInfoPanel`) only drives Bond's *own* `/bond` branch, not the workspace repos. We want each repo's branch visible and easily changeable from the chat, for all repos at once.

---

## 1. Why we need this

- **The state exists but is invisible where it matters.** Each repo's branch lives in SpacetimeDB (`agent_repos.activeBranch`) and is kept fresh by the worker's branch heartbeat, but the only UI is an edit form buried in Settings → Agent Repos (`AgentReposTab.tsx`). Mid-conversation there is no way to glance at "which branch is each repo on?"
- **Changing a repo's branch is a settings round-trip.** To point a repo at a different branch today you leave the chat, open settings, edit the repo, save. For a multi-repo workspace that's repeated per repo.
- **The header already trains the user to look there for branches** — but only for `/bond`. Extending it to the workspace repos is the natural, consistent place.
- **All the backend plumbing is already built.** Reconciliation, the conversational conflict flow, the heartbeat, REST endpoints, and reactive SpacetimeDB subscriptions already exist. This is mostly a surfacing + one-correctness-fix job, not new infrastructure.

---

## 2. What already exists (do not rebuild)

Backend / worker:
- **Data model** — `agent_repos` table (`spacetimedb/spacetimedb/src/index.ts` ~L670): `id, agentId, url, name, defaultBranch, activeBranch, credentialId, lastSyncedAt, createdAt, updatedAt`.
- **Runtime config** — backend `_prepare_agent_repos()` (`backend/app/sandbox/adapters.py`) writes `/config/repos.json` (read-only mount) consumed by the entrypoint and worker.
- **Reconciliation** — `_reconcile_repo_branches()` (`backend/app/worker.py` ~L479) runs at each turn boundary: reads desired branch, switches if the tree is clean, records a `conflict` note if dirty (leaves repo untouched). `_build_branch_conflict_preamble()` (~L627) turns conflicts into an in-chat ask. SSE event `repo_branch_reconcile` is emitted per note and proxied through `turn_stdb.py`.
- **Heartbeat** — `_branch_heartbeat_loop()` (~L337) polls `git rev-parse --abbrev-ref HEAD` per repo every `BOND_BRANCH_HEARTBEAT_INTERVAL` (default 60s) and POSTs changes to `.../repos/{repo_id}/branch`.
- **REST** (`backend/app/api/v1/agent_repos.py`): `GET /agents/{id}/repos`, `POST /agents/{id}/repos`, `PUT /agents/{id}/repos/{repo_id}` (sets `active_branch`), `POST /agents/{id}/repos/{repo_id}/branch` (worker→bond branch report), `DELETE`.

Frontend:
- **`ConversationInfoPanel.tsx`** — header panel next to the agent name; drives the `/bond` branch (dropdown, Pull, Fetch, HEAD SHA, worker status). This is where the new per-repo controls go.
- **`AgentReposTab.tsx`** — settings editor showing `active_branch` per repo (kept as the full editor; not the chat surface).
- Reactive SpacetimeDB table subscriptions already wired (`spacetimedb-client.ts`, `useSpacetimeDB.ts`); `apiFetch` helper in `config.ts`.

---

## 3. The one real correctness bug to fix

`activeBranch` is currently written by **both** the user (desired target, via `PUT`) **and** the heartbeat (actual observed branch, via `.../branch`). These are different concepts conflated into one column:

> User requests branch **X**. Repo is dirty, so reconcile leaves it on **Y** and surfaces a conflict. The heartbeat then observes **Y** and POSTs it, **overwriting the user's desired X**. On the next turn reconcile sees `activeBranch == Y == actual`, attempts no switch — the user's intent is silently lost.

**Fix:** split desired vs. actual.
- `activeBranch` stays the **user-desired** target (set only by `PUT`; read by reconcile).
- New `observedBranch` holds the **heartbeat-observed** actual branch (set only by `.../branch`).
- When `activeBranch != observedBranch`, the UI shows a pending/blocked state ("on `Y`, switching to `X`…"; "blocked: uncommitted changes" if a conflict note is live).

---

## 4. Design decisions (settled)

- **Placement:** in the header info panel (`ConversationInfoPanel`), alongside the existing `/bond` branch control. One consistent place for all branch state.
- **Branch picker:** dropdown of **real** branches per repo (requires a new worker endpoint to list them), with the option to also accept a typed branch name later.
- **Apply timing:** _TBD with user_ — either (a) apply on next turn boundary (zero new mechanism; reconcile already runs there), or (b) add a "reconcile now" trigger so an idle agent switches immediately, mirroring the `/bond` `/checkout` button. Until decided, ship (a) and treat (b) as a fast follow.

---

## 5. Implementation plan

### 5.1 Schema — split desired vs. observed
- `spacetimedb/spacetimedb/src/index.ts`: add `observedBranch: t.string().default("unknown")` to `agent_repos`, **appended at the end of the column list**. Two SpacetimeDB-v2.2.0 gotchas, both hit during implementation:
  - **Append, don't insert.** A mid-table column insert is treated as a column *reorder* and fails with "Reordering table … requires a manual migration". New columns must go last.
  - **Default must be non-empty.** The TS SDK omits *falsy* defaults from the published module schema (`index.mjs`: `if (meta.defaultValue) { … }`), so `.default("")` publishes a column with **no** default annotation and the migration aborts with "requires a default value annotation". Use a non-empty sentinel (`"unknown"`). It only ever lands on rows that exist at migration time and is overwritten within one heartbeat tick; `create_repo` inserts `''` explicitly for new rows (SQL writes are unaffected by the quirk), and the UI maps both `""` and `"unknown"` to `—`.
  - `make migrate` (default CLI) republishes the module and regenerates all four binding dirs. The CLI version is not the issue here — both the default `spacetime` and the v2.2.0 binary report the same migration errors; fixing the two points above is what makes the publish succeed.

### 5.2 Worker — list a repo's branches
- `backend/app/worker.py`: new endpoint `GET /repos/{repo_id}/branches` → resolve the repo's mount dir from `/config/repos.json`, run `git -C /workspace/{name} branch -r --format='%(refname:short)'` (strip `origin/`, dedupe, drop `HEAD`), return `{ "branches": [...] }`. Only the worker container has the repos mounted, so this must live on the worker.

### 5.3 Backend — heartbeat target + branches proxy
- `backend/app/api/v1/agent_repos.py`: change `POST .../repos/{repo_id}/branch` to write `observed_branch` (not `active_branch`) + `last_synced_at`.
- Add `GET /agents/{id}/repos/{repo_id}/branches` proxying to the worker endpoint (5.2), same path the existing branch/pull/fetch proxies use.
- Update `_branch_heartbeat_loop()` if it depends on the prior write semantics (it reports changes; the receiving endpoint changes, not the loop).
- Surface `observed_branch` in the repo serialization and the SpacetimeDB reducer that backs `.../branch`.

### 5.4 Frontend — per-repo controls in the header
- `ConversationInfoPanel.tsx`: subscribe to `agent_repos` for the active agent; render one row per repo: **name + observed-branch badge + branch dropdown**.
- Dropdown options come from `GET .../repos/{repo_id}/branches`; selecting one does `PUT .../repos/{repo_id}` with `{ active_branch }`.
- Pending/blocked state: when `activeBranch != observedBranch`, show "switching to X…"; if a `repo_branch_reconcile` conflict event is live for that repo, show "blocked: uncommitted changes" (lines up with the in-chat conversation).

### 5.5 Apply timing (pending decision §4)
- Default: rely on next-turn reconcile.
- If immediate-while-idle is wanted: add a lightweight worker "reconcile now" trigger invoked after the `PUT` when the agent is idle, mirroring the `/bond` `/checkout` immediate path.

---

## 6. Files touched

| Area | File | Change |
|---|---|---|
| Schema | `spacetimedb/spacetimedb/src/index.ts` | add `observedBranch` to `agent_repos` |
| Worker | `backend/app/worker.py` | `GET /repos/{repo_id}/branches` |
| Backend | `backend/app/api/v1/agent_repos.py` | heartbeat → `observed_branch`; branches proxy; serialize `observed_branch` |
| Frontend | `frontend/src/components/ConversationInfoPanel.tsx` | per-repo branch list + picker |
| Frontend | `frontend/src/lib/*` types as needed | `observed_branch` field |

---

## 7. Deploy note (which process runs which change)

- **Schema** — published live by `make migrate` (already applied).
- **Backend** (`agent_repos.py`) + **frontend** (`ConversationInfoPanel.tsx`) — run in `bond-bond` from the baked image; live after `docker compose up -d --build`.
- **Worker** (`worker.py`: branches endpoint + heartbeat baseline) — runs from the *agent's* `/bond` clone (pulled from `origin/<branch>`), NOT the agent image. So `observed_branch` only starts updating, and the branch picker only returns options, once the agent's `/bond` is on a branch that contains these commits (i.e. after merge to `dev`, or a dev-mounted agent). Until then the panel still renders, the desired-branch `PUT` still persists, and reconcile still switches at turn boundaries — only the live observed-branch readout lags.

## 7.1 Verification

- Cold-start an agent with ≥2 repos; confirm the panel lists each repo with its real current branch.
- Change a repo's branch via the dropdown on a **clean** tree → switches on next turn; `observedBranch` catches up via heartbeat; badge updates.
- Change a branch on a **dirty** tree → reconcile records a conflict, the agent asks in chat, and the panel shows "blocked"; the desired branch is **not** overwritten by the heartbeat (the §3 bug is gone).
- Settings → Agent Repos still works as the full editor.
