# Design Doc 113: Clone-Only Workspaces

**Status:** Draft — awaiting review
**Depends on:** 089 (Remote Container Hosts), 112 (Credential Passthrough)
**Supersedes (in part):** the bind-mount path of the workspace-mount feature
**Driver:** Eliminate the local/remote split and the credential-passthrough tax that bind-mounted workspaces impose on a containerized bond.

---

## 1. The Problem

Today, a workspace given to an agent is a **bind mount** of a host directory into the agent container at `/workspace/{name}`. This was simple when bond ran natively on the host: the host path and the agent's mount source were the same string, and an editor on the host could see (and conflict with) the agent's edits live.

It has aged poorly:

- **Bind mounts assume bond and the agent share a filesystem.** Design 089 (remote container hosts) explicitly cannot bind-mount, so it already uses git clones. We now have two divergent workspace models in one codebase — local agents bind-mount, remote agents clone — and the seam between them is the source of subtle bugs (whose path is whose? who writes back to where?).
- **Containerized bond can't see what the host sees.** A bind mount specified as `/home/andrew/projects/foo` is interpreted by the docker daemon (host paths), but `bond-bond-1` checking whether that directory "exists" sees its own filesystem instead. This is the same `Path.home()` trap that drove design 112.
- **Live editing from host + agent on the same tree is the rare case, not the common one.** Most agent work is shaped like a PR: the agent makes a contained change, you review on a branch, you merge or discard. That workflow does not need a bind mount.
- **Bind-mounted workspaces leak host trust into the agent.** Anything writable in the mount, the agent can mutate. Many incidents — accidental `rm`, branch-clobbering `git reset --hard`, package-manager rewrites of `node_modules` — would have been contained inside a clone.

This document proposes replacing host-directory bind mounts with **per-agent git clones** stored in Docker volumes. The agent's working tree lives in the volume; changes leave the volume only via `git push`.

---

## 2. Design Principles

1. **One workspace model.** Local and remote agents acquire workspaces the same way: clone a URL into a volume. Adapter code stops branching on "is this local or remote."
2. **Repos, not directories.** The user thinks in terms of "this agent works on the *bond* repo", not "this agent has access to `/home/andrew/Documents/bond`". The data model follows.
3. **Defaults that beat boilerplate.** Pasting a URL is enough to get a working repo. Name, branch, and credentials should auto-resolve unless the user wants something specific.
4. **Push, not sync.** The agent pushes a branch when it has something to share. There is no background sync, no two-way merge. The host-side reviewer pulls and reviews like any PR.
5. **Branch is a moving target.** The "current branch" is whichever branch the agent has checked out at the moment. Bond observes and surfaces it; the user can also set it to drive the agent toward a specific starting point.

---

## 3. Proposed Architecture

### 3.1 Data model

Two new tables in SpacetimeDB. Names match existing conventions (`agents`, `conversations`, etc.).

#### `git_credentials` — keyed by user, optionally by host

| field | type | notes |
|------|------|------|
| `id` | string (ULID) | primary key |
| `owner_user_id` | string | FK to `users` |
| `name` | string | human label, e.g. "GitHub PAT", "Work GitLab key" |
| `auth_type` | enum | `https_pat` \| `ssh_key` |
| `secret_ref` | string | opaque pointer into the existing vault (already used for API keys) |
| `host_pattern` | string | e.g. `github.com`, `*.gitlab.example.com`, or `*` |
| `username` | string? | optional, for HTTPS PATs that need a username |
| `is_default` | bool | one default per user |
| `created_at` | timestamp | |

#### `agent_repos` — replaces the workspace-mount rows that pointed to git directories

| field | type | notes |
|------|------|------|
| `id` | string (ULID) | primary key |
| `agent_id` | string | FK to `agents` |
| `url` | string | canonical clone URL, ssh or https |
| `name` | string | container mount name (`/workspace/{name}`); defaults to repo basename |
| `default_branch` | string | resolved from `HEAD` at first clone, used for fresh checkouts |
| `active_branch` | string | the branch the agent currently has checked out; updated by the agent |
| `credential_id` | string? | FK to `git_credentials`; null = resolve via host_pattern fallback |
| `last_synced_at` | timestamp? | when the agent last fetched from origin |

Existing `workspace_mounts` rows that point to git directories are migrated; rows that point to non-git directories are flagged (see §6).

### 3.2 Credential resolution

When an agent needs to authenticate against a clone URL:

1. If `agent_repos.credential_id` is set → use that credential. (per-repo override)
2. Otherwise, look up the user's `git_credentials` rows ordered by specificity of `host_pattern` against the URL's host, then by `is_default`. (user-level fallback)
3. If nothing matches, attempt unauthenticated clone (works for public repos).

This is the simplest scheme that supports both "I have one PAT for everything" (set one user-level credential with `host_pattern = '*'`) and "this specific repo uses a different account" (set `credential_id` on that repo).

Implementation cost is low: a single resolver function with a fallback chain.

### 3.3 UX flow

**Adding a repo to an agent:**
1. User opens the agent's repos panel, clicks "Add repo."
2. Pastes URL (`git@github.com:foo/bar.git` or `https://github.com/foo/bar.git`).
3. Frontend pre-fills `name` from the URL basename (`bar`); user edits if desired.
4. Optional: pick a credential from a dropdown of the user's saved credentials, or "Use default for github.com." Showing the resolved credential inline ("will use: GitHub PAT") avoids surprises.
5. Optional: pick a starting branch. Default is the repo's default branch, fetched lazily.
6. Save → row created. No clone happens until the agent next runs.

**Adding a credential:**
1. User opens Settings → Git Credentials.
2. Pastes a PAT or an SSH private key, picks `auth_type`, gives it a name and host pattern.
3. Saved into the existing vault; only the `secret_ref` is stored in the table.

**Branch handling at runtime:**
- Agent starts → bond passes `agent_repos` rows as part of the container spec.
- Entrypoint clones each repo into `/workspace/{name}` (a Docker named volume, one per repo per agent).
- Entrypoint checks out `active_branch` if non-empty, else `default_branch`.
- Each turn the agent runs, the worker reports the current branch back to bond (small heartbeat). Bond updates `active_branch`. The UI reflects it.
- The user can override `active_branch` from the UI; bond writes it through and the next turn checks it out (with a stash on conflict — see §5.2).

### 3.4 Container layout

Per repo per agent: one Docker named volume, `bond-repo-{agent_id}-{repo_id}`. Volume is reused across invocations of the same agent; clone is done once, subsequent runs do `git fetch`.

Mounts on the agent container:
```
-v bond-repo-{agent_id}-{repo_id}:/workspace/{name}:rw
```

Credentials are written to a tmpfs at `/tmp/.git-creds/` by the entrypoint and cleaned on exit. They are passed in via env vars or short-lived secrets, never embedded in the volume.

---

## 4. What's Easy vs. What Needs Thought

**Easy (1–2 days each):**

- **Two tables and CRUD endpoints.** Schema additions, REST handlers, basic frontend forms. Low risk, follows the pattern of existing settings tables.
- **Credential resolver with fallback chain.** Pure function, easy to unit-test.
- **Clone-on-first-run, fetch-on-subsequent-runs in the agent entrypoint.** Already partly implemented for the bond-self repo (see `agent-entrypoint.sh` lines 26–44).
- **Defaulting `name` from URL basename in the frontend.** A few lines of JS.
- **HTTPS clone with a PAT.** The git credential helper in the agent container reads from a file the entrypoint writes; standard pattern.

**Medium (1 week):**

- **SSH key support, including a writable `known_hosts`.** Entrypoint must write the key to `~/.ssh/id_*` with strict perms, run `ssh-keyscan` on first contact, and disable agent forwarding. Most of this exists for the bond-self repo's SSH path; needs to be generalized.
- **Branch heartbeat from agent → bond.** Agent worker watches `git rev-parse --abbrev-ref HEAD` after every tool that could change it (low frequency: only on commit/checkout/reset). Bond persists `active_branch`. Race conditions when the user is editing the value at the same time — not catastrophic, last-writer-wins is fine.
- **UI for credential management with the existing vault.** Mostly forms + show/hide secret toggles, but with care: the secret should never round-trip through the frontend after it's saved.

**Harder, defer or scope down:**

- **What happens if the user changes `active_branch` mid-turn?** Simplest answer: the change applies on the *next* turn, with a stash before checkout. Stash conflict during checkout → fail loudly, don't try to auto-merge.
- **What happens if the agent commits to a branch the user later force-pushes?** Same answer as a human collaborator: agent's next fetch will be ahead-and-behind; the agent should be told to rebase or open a PR. Out of scope for this doc; falls under agent prompting.
- **Multi-repo agents.** Already implied (the `agent_repos` table is many-per-agent), but cross-repo coordination (e.g., a frontend change that requires a backend change) is a workflow question, not a data-model one. Ship the data model; the prompting concerns are separate.
- **Garbage collecting volumes for deleted agents/repos.** Adds a maintenance task. Not blocking.

---

## 5. Notable Decisions

### 5.1 Why volumes, not bind mounts to a managed host directory

A bind mount to `~/.bond/clones/{agent_id}/{repo_id}` would let the user `cd` into it from the host and inspect. Tempting. Rejected because:

- Reintroduces the host-path-vs-container-path coordination problem this design is trying to kill.
- Encourages the user to edit in-place again, which reopens the `git reset` risk.
- Volumes are inspectable when needed (`docker run --rm -v bond-repo-...:/w alpine sh`); the friction is intentional.

If this turns out to be a real pain point, a future addition is a "Files" tab in the UI that lists the volume contents read-only, similar to how container logs are surfaced.

### 5.2 Why the agent owns `active_branch`, not the user

In day-to-day use, the agent is the one checking out branches: it creates `fix/foo`, switches to `feat/bar`, etc. If the user's chosen value were authoritative, every checkout would be "fighting" the user. The user-set value is treated as a *target* the agent will move toward at the start of the next turn (with a stash). Mid-turn, the agent owns the branch.

### 5.3 Why credentials are user-level *and* repo-level

The user's question: is one or the other enough? **Both is cheap** — the resolver is a fallback chain — and each level covers a different real case. User-level handles the 80% (one PAT for all your GitHub work). Repo-level handles the 20% (this one repo uses a different account or a deploy key). Picking one would mean the other 20% gets a worse experience without saving meaningful implementation effort.

### 5.4 Why no host path bind-mount escape hatch for "scratch dirs"

Current bind mounts include things like `~/paperclip` and `/mnt/c/dev/...` which may not be git repos. Two options:

1. **Refuse non-git mounts entirely.** User must `git init` first. Cleanest data model. Forces good hygiene.
2. **Keep bind-mount support as a deprecated path.** Easier migration, but defeats the point.

Recommend (1). The migration tool (§6) generates a per-mount report and suggests `git init` for non-git directories. Users who really need a non-version-controlled workspace can `git init` a throwaway repo.

---

## 6. Migration

A migration script:

1. Walks existing `workspace_mounts` rows.
2. For each, runs `git -C {path} rev-parse --is-inside-work-tree` and reads `git remote get-url origin` if it exists.
3. **If the path is a git repo with a remote**, creates an `agent_repos` row populated from the remote URL and current branch. Removes the workspace_mount.
4. **If the path is a git repo without a remote**, prints a warning and leaves the workspace_mount in place; user must add a remote and re-run.
5. **If the path is not a git repo**, prints a warning and leaves the workspace_mount in place; user decides whether to `git init` or remove.

Bind-mount support remains in the codebase for one release with a deprecation log line, then is removed.

---

## 7. Out of Scope

- Cross-repo dependency management (changes to repo A that need a PR to repo B). The agent can already work across multiple repos — this design does not add or remove that capability.
- Built-in PR creation. Agents already use `gh` via tools; no change needed here.
- Mirror caching for slow networks (`--reference` clones). Useful at scale; not required for v1.
- A "Files" UI tab for inspecting volume contents. Tracked as a follow-on.
- Replacement of the bond-self clone in the agent entrypoint (lines 26–44 of `agent-entrypoint.sh`). That repo is special — the agent *needs* to know about it to run. The repos described here are user-supplied work targets. The two will continue to coexist with the same credential helper underneath.

---

## 8. Open Questions

1. **Should `active_branch` be per-conversation rather than per-repo?** A long-running agent often has multiple in-flight conversations on different branches. Per-conversation is more correct but adds a join. Recommend deferring until the simpler model proves insufficient.
2. **Branch heartbeat frequency.** Every tool call is wasteful; only after `git checkout` / `git commit` / `git reset` is enough. Implementation detail, but worth getting right to avoid SpacetimeDB write churn.
3. **Should the credential vault live entry be visible in the UI after creation?** Existing pattern (API keys) is "show last 4 chars only." Same here.
4. **Migration grace period.** One release feels right; calling out so it doesn't slip.
