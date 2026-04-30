# Design Doc 114: Git Provider Typeahead

**Status:** Draft — awaiting review
**Depends on:** 113 (Clone-Only Workspaces)
**Driver:** Make adding a repo to an agent feel like picking a contact from autocomplete, not pasting a URL. Users connect each git provider once; bond caches their repo list; the typeahead searches across all of them.

---

## 1. The Problem

Doc 113 establishes that a workspace is a repo, identified by a URL. The default UX it describes is "paste a URL, the name auto-fills." That works but it makes the user do three things in sequence: switch tab to GitHub/Azure DevOps/GitLab, find the right repo, copy a URL, switch back. For users with many repos across multiple providers, this is the dominant friction in setting up a new agent.

The target experience is a single typeahead in the "Add repo" dialog: the user types `bond` and gets a list of every repo they have access to whose name contains "bond" — across personal GitHub, work GitHub, Azure DevOps orgs, self-hosted GitLab — sorted by recency or name. They click one. Done.

Two reasons not to chase the obvious "Sign in with GitHub" button as the primary path:

- **OAuth requires per-install app registration.** Every self-hosted bond would need to register its own OAuth app with each provider (client ID + secret + redirect URI). For a tool meant to be self-hosted and dropped into varied environments, that's a setup tax we'd inflict on every user.
- **Azure DevOps and most self-hosted instances treat PATs as the dominant pattern.** OAuth there is awkward (Microsoft Identity Platform for ADO, custom OIDC for self-hosted GitLab/Gitea). Building OAuth across the long tail returns little — PAT was already going to be required for those.

So the design is **PAT first, OAuth as a phase-2 cherry on top for the providers where it shines (mainly GitHub).**

---

## 2. Design Principles

1. **One paste, then forget about it.** Connecting a provider is a one-time act. From then on, the user thinks in terms of repo names, not URLs or tokens.
2. **No infrastructure churn.** The cache lives in SpacetimeDB, alongside everything else. No new Redis, no new background daemon. A scheduled refresh tick is enough.
3. **Self-hosted is first-class.** GitHub Enterprise, self-hosted GitLab, Gitea, on-prem Bitbucket Data Center, Azure DevOps Server — all accept a `base_url` on the same provider type as their SaaS counterpart.
4. **Manual URL entry never goes away.** A user adding a repo they have access to but haven't connected a provider for (a public repo someone linked them, a one-off open-source repo) can still paste a URL.
5. **The typeahead source of truth is the cache, not a live API call.** Live calls per keystroke would be too slow and rate-limited. The cache is refreshed on connect, on demand, and on a slow timer.

---

## 3. Proposed Architecture

### 3.1 Data model

Two new tables in SpacetimeDB.

#### `git_provider_connections` — one row per (user, provider, account, base_url)

| field | type | notes |
|------|------|------|
| `id` | string (ULID) | primary key |
| `owner_user_id` | string | FK to `users` |
| `provider_type` | enum | `github`, `gitlab`, `azure_devops`, `bitbucket`, `gitea` |
| `display_name` | string | user-set, e.g. "Personal GitHub", "Work Azure DevOps" |
| `base_url` | string? | null for SaaS (`github.com`, `gitlab.com`, etc.), set for self-hosted (`gitlab.example.com`) |
| `secret_ref` | string | vault pointer to the PAT/token |
| `account_login` | string? | the username/handle the token belongs to, fetched on connect |
| `last_synced_at` | timestamp? | when the cache was last refreshed |
| `sync_status` | enum | `idle`, `syncing`, `error` |
| `error_message` | string? | last error if sync_status = `error` |

Multiple connections per provider type are allowed (personal + work GitHub). The user disambiguates via `display_name`.

#### `cached_repos` — flat catalog of repos visible across all connections

| field | type | notes |
|------|------|------|
| `id` | string (ULID) | primary key |
| `connection_id` | string | FK to `git_provider_connections` |
| `provider_repo_id` | string | the provider's stable ID (used for upsert/dedup within a connection) |
| `full_name` | string | e.g. `biztechprogramming/bond`, `myorg/myproj/repo` for ADO |
| `clone_url_https` | string | |
| `clone_url_ssh` | string? | not all providers expose this universally |
| `default_branch` | string | |
| `is_private` | bool | |
| `description` | string? | trimmed to ~200 chars |
| `last_pushed_at` | timestamp? | provider-reported, used for sort-by-recent |
| `cached_at` | timestamp | when bond last saw this row from the provider |

Selecting a `cached_repos` row in the typeahead populates the "Add repo" form (URL, name, default branch). Saving creates an `agent_repos` row (per doc 113); the cache row stays put.

### 3.2 Provider abstraction

A small Python protocol:

```python
class GitProvider(Protocol):
    provider_type: str

    async def validate(self, token: str, base_url: str | None) -> AccountInfo:
        """Test the token. Returns the account login or raises."""

    async def list_repos(
        self, token: str, base_url: str | None
    ) -> AsyncIterator[CachedRepoData]:
        """Stream all repos accessible to this token, paginated."""
```

Implementations live in `backend/app/providers/{github,gitlab,azure_devops,bitbucket,gitea}.py`. Each is ~100 lines: an HTTP client, pagination, error handling, response → `CachedRepoData` mapping.

### 3.3 Per-provider notes (the parts that actually differ)

- **GitHub / GitHub Enterprise.** `GET /user/repos?per_page=100&type=all`, paginated via `Link` header. Token in `Authorization: Bearer`. Watch `X-RateLimit-Remaining`. Authenticated quota is 5000/hr — plenty.
- **GitLab.com / self-hosted.** `GET /api/v4/projects?membership=true&simple=true&per_page=100`, paginated via `X-Next-Page`. Token in `PRIVATE-TOKEN` header. `path_with_namespace` is the `full_name`.
- **Azure DevOps.** Token typically scoped to one organization, so **one connection = one org**. List repos: `GET https://dev.azure.com/{org}/_apis/git/repositories?api-version=7.0`. PAT goes in basic auth as the password (any username). Multi-org users add multiple connections.
- **Bitbucket Cloud / Data Center.** `GET /2.0/repositories?role=member&pagelen=100`. Bitbucket's API is quirkier (`pagelen`, not `per_page`); the provider module hides it.
- **Gitea / Forgejo.** `GET /api/v1/repos/search?limit=50` for owned repos, plus `GET /api/v1/user/orgs/{org}/repos` for org repos. Self-hosted only — `base_url` required.

### 3.4 Sync lifecycle

Three triggers refresh the cache:

1. **On token save** (`POST /api/v1/git-providers/{id}/sync`). Synchronous-ish: validate token, then kick off background fetch, return the connection so the UI can poll `sync_status`.
2. **On manual "Refresh" click in the UI.** Same endpoint as above.
3. **Hourly tick** for any connection with `last_synced_at > 1h ago`. Single in-process scheduler, no new daemon. Runs after backend startup, sleeps in between.

Refresh strategy is **full list, then upsert**: fetch all repos for the connection, upsert rows by `(connection_id, provider_repo_id)`, delete rows that didn't appear in this fetch. For a user with 500 repos across providers, full refresh takes a few seconds. For 5000+ repos, see §5.4.

### 3.5 Search behavior

The frontend subscribes to `cached_repos` via SpacetimeDB. With ~hundreds-to-low-thousands of repos per user, the full list lives in browser memory. Typeahead does **client-side substring + fuzzy match** (a small library like `fuse.js`, or a hand-rolled scorer): match `full_name`, `description`, `default_branch`. Sort by match quality, break ties by `last_pushed_at` desc.

Server-side search is the fallback once a user crosses some threshold (~5000 cached repos) — at that point the subscription becomes too much to push, and search moves to a `GET /api/v1/git-providers/search?q=...` endpoint that runs the same scorer in Python. This isn't built in v1; the threshold is just a known cliff.

### 3.6 UX

**Settings → Git Providers:**
- "Connect a provider" button → modal with: provider dropdown, display name, base URL (shown only if provider supports self-hosting), token field with masked input, link to "How to create a PAT" docs per provider.
- On save: backend calls `provider.validate()`, populates `account_login`, kicks off first sync. Shows progress; on completion, says "Connected — N repos found."
- Listed connections show: provider icon, display name, account login, repo count, last synced, "Refresh" / "Disconnect" actions.

**Add Repo dialog (the typeahead):**
- Single input. As the user types, results appear: repo `full_name`, provider icon (so it's obvious which connection it came from), default branch.
- Empty state shows recently-pushed repos across all connections.
- Bottom of the list: "Or paste a URL manually" — collapses the typeahead and shows the doc-113 paste form.

---

## 4. What's Easy vs. What Needs Thought

**Easy (1–2 days each):**

- The two tables and CRUD endpoints. Same pattern as the credential CRUD in doc 113.
- GitHub provider implementation. Cleanest API of the bunch; great docs.
- Connection settings UI. Standard form + state machine for the sync lifecycle.
- Client-side typeahead. Subscribe, filter, render.
- Token validation on save (provider's `/user` endpoint with the token).

**Medium (~1 week total):**

- GitLab, Bitbucket, Gitea provider modules. Each has API quirks but the protocol abstracts them.
- Azure DevOps. The "one PAT per org" constraint shapes the connection model — worth getting the docs and dialog copy right so users aren't surprised.
- Hourly refresh scheduler. Boring but needs care around overlap (don't start a new sync while one is running for the same connection).
- Error states and recovery. PAT expired? Surface clearly, link to settings, don't spam logs. Provider down? Exponential backoff.

**Harder, defer or scope down:**

- **OAuth phase 2.** Add only where it materially improves UX (GitHub primarily, maybe GitLab.com). Each provider's OAuth flow is a multi-day build with token refresh and scope juggling. Skip for v1.
- **Webhook-based incremental updates.** Each provider has a different webhook format and authentication scheme. Real-time updates are nice but full refresh hourly is good enough for v1.
- **Server-side fuzzy search.** Only needed when a user has thousands of repos; build when someone hits the cliff.
- **Multi-org Azure DevOps from a single token.** Generally not possible — PATs are usually org-scoped. Document the limitation; users add one connection per org.

---

## 5. Notable Decisions

### 5.1 Why PAT first instead of OAuth

OAuth is materially better UX for the user *exactly once*: the first time they connect. After that, both feel identical (a stored token bond uses on their behalf). The cost difference is enormous: PAT is "user pastes a string"; OAuth is "register an app per provider, run a redirect flow, handle refresh tokens, deal with provider-specific scope models." For a self-hosted tool, the per-install OAuth app registration is the killer. PATs ship today; OAuth ships when GitHub-only OAuth becomes the highest-leverage thing left to do.

### 5.2 Why the cache lives in SpacetimeDB

Three reasons:

1. The frontend already subscribes to STDB tables and gets real-time updates for free. A refresh completing pushes new rows to the typeahead automatically.
2. We avoid introducing Redis or another store. Bond's deploy story stays "STDB + bond container."
3. Repo metadata is small. A row is a few hundred bytes; a power user's catalog is ~1MB. STDB handles that comfortably.

The tradeoff is STDB write churn during full refreshes. Mitigation: only upsert rows that changed (compare by `provider_repo_id` + relevant fields) rather than rewriting the world.

### 5.3 Why client-side search up to ~5000 repos

The break-even where server-side search wins isn't latency (network round-trip dominates) — it's payload size and subscription cost. Sending 5000 rows of ~300 bytes each is ~1.5MB on initial load, doable. 50,000 starts to hurt. The threshold is approximate; instrument it once we have real users.

### 5.4 Multiple connections per provider type are first-class

Treating "personal GitHub" and "work GitHub" as separate connections (rather than one connection with multiple identities) is simpler in every way: no token-juggling logic, no "which identity are we acting as right now?" ambiguity, clear blast radius when one token is compromised. The cost is the user has two list items in settings instead of one — fine.

### 5.5 Why we keep the manual-URL fallback (from doc 113)

A typeahead-of-everything-you-have-access-to is great, but real users sometimes need to clone a public repo they don't own (a sample from an open-source project, a fork to inspect). Forcing them to connect a provider for that is hostile. Doc 113's paste-a-URL flow stays as the "I just want to add this one thing" path.

### 5.6 What the provider abstraction does *not* cover

Listing repos is the only common operation across all providers. PR creation, code search, issue listing, branch protection — all provider-specific. Bond agents do these via the provider's CLI (`gh`, `glab`, `az repos`, etc.) inside the agent container, which is the right boundary: agent code is already provider-aware where it needs to be.

---

## 6. Migration & Rollout

This is purely additive. Existing users continue with manual URL entry until they connect a provider. No data migration needed.

Suggested rollout:

1. Ship the data model and GitHub provider behind a feature flag for early testers.
2. Ship GitLab and Azure DevOps — covers most users.
3. Remove the feature flag.
4. Ship Bitbucket and Gitea based on user demand.
5. Phase 2: GitHub OAuth.

---

## 7. Out of Scope

- **OAuth flows.** Deferred to phase 2. Doc covers PAT only.
- **Repo creation / forking from bond.** Add-repo is a *select-existing* operation. Creating new repos is a separate feature surface.
- **Pull request management UI.** Agents create PRs via `gh` / `glab` / `az repos pr create` inside their containers. Bond does not need a PR list view in this design.
- **Cross-provider repo deduplication.** A repo mirrored on GitHub and self-hosted GitLab shows up as two cache entries. Treating them as one would require a fingerprint (last commit SHA, etc.) and isn't worth the code.
- **Code search.** Each provider has its own; bond doesn't try to unify.
- **Org / team browsing.** The flat repo list, sortable by recency, is enough for typeahead. A tree-style org browser is a future addition.

---

## 8. Open Questions

1. **Minimum PAT scopes per provider.** GitHub needs `repo` (or `public_repo` for public-only). GitLab needs `read_api` + `read_repository`. Azure DevOps needs `Code (Read)`. Document the minimum in the "How to create a PAT" link; do not request more.
2. **Token expiration handling.** GitHub fine-grained tokens expire. Do we proactively notify the user before expiration, or only on first failed sync? Recommend the latter for v1 — the failure mode is obvious and the UI surface for "your token expires in N days" is non-trivial.
3. **Encrypting cached repo metadata at rest.** Not super sensitive (most data is already retrievable via the same API the cache uses), but private repo names and descriptions do leak some info. STDB is already inside the trust boundary; recommend not encrypting cache rows. Tokens stay in the vault.
4. **Per-connection repo count limit.** A user with 50,000 repos in one GitHub org will hit pain. v1 caps at 10,000 per connection (drop the rest, surface a warning). Real users will tell us if this is wrong.
5. **What happens when a connection is disconnected?** Drop the cached repos for that connection. Any `agent_repos` rows that were created from those cache entries keep working — the URL is already saved on the agent_repos row, the cache was just the picker.
