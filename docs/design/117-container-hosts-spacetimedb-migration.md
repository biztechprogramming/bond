# Design Doc 117: Migrate `container_hosts` from SQLite to SpacetimeDB

**Status:** Draft — awaiting review
**Driver:** `container_hosts` is the last user-facing table still living in SQLite (`knowledge.db`). The SQLite migration runner (`scripts/migrate.sh`) is currently gated off and `make migrate` publishes to SpacetimeDB; that means the table never gets created on fresh installs. PR #332 shipped a `[]` graceful-empty fallback so the UI doesn't 500, but the real fix is to move the table to the canonical store. This doc proposes the schema, reducers, and call-site changes needed to do that.

---

## 1. Why we need this

- **Single source of truth.** Every other CRUD-ish entity (`agents`, `conversations`, `mcp_servers`, `environments`, `git_credentials`, …) lives in SpacetimeDB. `container_hosts` is the lone holdout.
- **Migration runner is gated off.** `scripts/migrate.sh` keeps the SQLite section behind `if false` because the migrate Docker image doesn't include the SQLite driver and the bond runtime hasn't applied SQLite migrations in months. Adding new SQL migrations is a dead path.
- **The `[]` fallback masks a real bug.** A fresh `~/.bond/data/` directory has no `container_hosts` table → `list_all` swallows the `no such table` error and returns empty. Local-host placement still works because `host_registry` falls back to its in-memory `LocalHost` default; the user just can't see or edit any host config in the UI.
- **The host registry's DB cache layer (`host_registry.py:91`) is built around `SELECT * FROM container_hosts`.** Swapping that one query is the cleanest place to land the migration without changing the registry's contract.

---

## 2. Schema

New SpacetimeDB table, named per Design Doc 115 (snake_case table, camelCase fields, `createdAt`/`updatedAt` audit fields, `t.u64()` ms-since-epoch timestamps).

```typescript
container_hosts: table(
  { public: true },
  {
    id: t.string().primaryKey(),       // ULID, or "local" for the seeded local row
    name: t.string(),
    host: t.string(),                  // hostname or IP
    port: t.u32(),                     // SSH port; 0 for local
    user: t.string(),                  // SSH user; empty for local
    sshKeySecretRef: t.string(),       // pointer into the vault, NOT the encrypted blob
    daemonPort: t.u32(),
    maxAgents: t.u32(),
    memoryMb: t.u32(),
    labels: t.string(),                // JSON array of strings
    enabled: t.bool(),
    status: t.string(),                // 'active' | 'draining' | 'offline'
    isLocal: t.bool(),
    authTokenSecretRef: t.string(),    // pointer into the vault; "" means daemon not installed
    createdAt: t.u64(),
    updatedAt: t.u64(),
  }
)
```

**Differences vs the SQLite schema:**

| SQLite column | STDB field | Why it changed |
|---|---|---|
| `port INTEGER DEFAULT 22` | `port: t.u32()` | Caller passes 22 explicitly; STDB 2.0.2 has no column-level default |
| `ssh_key_encrypted TEXT` | `sshKeySecretRef: t.string()` | Per Design 115 §2.6: secrets go in the vault, the table holds a pointer |
| `auth_token TEXT` | `authTokenSecretRef: t.string()` | Same reasoning |
| `labels TEXT DEFAULT '[]'` | `labels: t.string()` | Caller passes `"[]"`; semantics unchanged |
| `created_at TEXT` (ISO) | `createdAt: t.u64()` | ms-since-epoch per Design 115 §2.1 |

**Constraint on STDB version.** Production is pinned to SpacetimeDB v2.0.2 (per memory `project_spacetimedb_pinned_2_0_2`). The schema above does not use `.default()` annotations, so a clean `make migrate` publish creates the table without `--clear-database`. No ADD COLUMN, no side-table workaround needed — this is a brand-new table.

---

## 3. Reducers

Mirroring the existing service surface (`container_host_service.py`) and the routes in `hosts.py`:

| Reducer | Maps to | Notes |
|---|---|---|
| `addContainerHost` | `ContainerHostService.create` | Inserts `createdAt = updatedAt = ctx.timestamp` |
| `updateContainerHost` | `ContainerHostService.update` | Spread-update pattern (Design 115 reducer guidance); bumps `updatedAt` |
| `deleteContainerHost` | `ContainerHostService.delete` | Refuses if `id == "local"` or `isLocal == true` |
| `setContainerHostAuthToken` | install-daemon endpoint inline UPDATE | Writes `authTokenSecretRef`; bumps `updatedAt` |
| `clearContainerHostAuthToken` | uninstall-daemon endpoint inline UPDATE | Empties the ref; bumps `updatedAt` |
| `seedLocalHost` | called from `init` lifecycle hook | Idempotent; only inserts if `id == "local"` row absent |

**Cascade deletes:** none required today. `agent_workspace_mounts.host_id` could in theory reference a host id, but in practice agents pin to `"local"` and we don't currently delete remote hosts that have running agents. Note this in the cascade audit (Design 115 Phase B) but no action in this PR.

---

## 4. Backend changes

### 4.1 `container_host_service.py`

Rewrite the seven methods to call STDB instead of `db.execute(text(...))`. Pattern follows `mcp_server_service.py` and `agent_service.py`:

- Read paths (`list_all`, `get`, `get_container_settings`) — query STDB directly via the read SDK.
- Write paths (`create`, `update`, `delete`, `update_container_settings`, `import_from_config`) — call the corresponding reducer; let the subscription propagate the change to the read cache.
- Drop the `AsyncSession` argument from every method signature. Callers that thread `db: AsyncSession` through can stop doing so for these specific calls; the FastAPI dep is no longer needed.
- Drop `_row_to_dict` SQLite-encoding helpers. The `ssh_key_decrypted` convenience field becomes `resolve_ssh_key(secret_ref) → str | None` that reads from the vault on demand.

### 4.2 `host_registry.py`

The registry's `load_from_db` (line 91) is the only consumer that *needs* both reads from the table AND a periodic refresh. Replace the `SELECT * FROM container_hosts` block with an STDB read. The 30-second cache TTL stays; it's still useful for amortizing the read.

### 4.3 `hosts.py`

The two inline `UPDATE container_hosts SET auth_token = …` calls in `install_daemon` and `uninstall_daemon` (lines 372 and 411) become `setContainerHostAuthToken` / `clearContainerHostAuthToken` reducer calls. The `db: AsyncSession = Depends(get_db)` parameter on every route can be dropped once the service no longer needs it.

### 4.4 Vault adapter

The vault is already used for `git_credentials.secretRef` and `provider_api_keys`. We reuse the existing helpers; no new vault code. The migration of existing encrypted values is covered in §6.

---

## 5. Frontend changes

The API shape (`/api/v1/hosts` GET/POST/PUT/DELETE) is unchanged. The frontend's `Settings → Containers` panel does not need changes.

Bindings are regenerated as part of `make migrate`; the new `container_hosts` table will appear in `frontend/src/lib/spacetimedb`. The frontend doesn't currently subscribe to it (the panel uses the REST API, not a STDB subscription) — that stays the same. Future work could swap to a subscription for live host status; out of scope here.

---

## 6. Migration plan

### 6.1 Data migration

There is no production `container_hosts` data to migrate today: the table doesn't exist on fresh installs, and the only existing rows in legacy installs are the seeded `"local"` row plus whatever `import_from_config` has imported from `bond.json`. So the rollout is essentially **fresh start, plus a one-shot import of `bond.json`-defined hosts**.

Steps:

1. **Pre-deploy on a staging clone** that *has* a populated `container_hosts` SQLite table. Run a one-off Python script (`scripts/migrate_container_hosts_to_stdb.py`) that reads each row, decrypts `ssh_key_encrypted` and `auth_token` with the legacy crypto helpers, writes them to the vault, and calls `addContainerHost` with the resulting `secretRef`s. Verify the host shows up correctly in the new table and that daemon installation still works.
2. **Publish the schema change.** `make migrate` adds `container_hosts` cleanly.
3. **`init` reducer seeds the `"local"` row** if absent, so fresh installs behave identically to the SQLite seed in `000027_container_hosts.up.sql`.
4. **Run the import script in production** as a one-off (gated on the SQLite file existing). After it succeeds, delete the SQLite migration files (`000027_*.sql`, `000028_*.sql`) — they're now dead code.

### 6.2 Rollback

If anything goes wrong before step 4, the SQLite migrations are still on disk and `host_registry` already has a `try/except` fallback. We can revert the service rewrite without data loss because nothing touched SQLite. Step 4 is the point of no return; gate it behind an explicit `--commit` flag.

---

## 7. Risks

- **Vault dependency for boot.** `host_registry.load_from_db` runs early in startup. If the vault is unavailable, decrypting `authTokenSecretRef` fails. Mitigation: log + fall back to "no auth token; daemon shows as not installed" rather than crashing the registry. The registry already does this for the legacy `Failed to decrypt auth_token` case.
- **Subscription latency for read-after-write.** STDB writes propagate via subscription, not synchronous return. Existing services (`agent_service`, `mcp_server_service`) already deal with this by calling the reducer + reading back via a short retry loop. Use the same pattern.
- **`secretRef` collision risk.** Two hosts with the same `id` would produce the same vault key. The id is the primary key already; not a new concern, but worth noting in the import script.
- **`is_local` boolean migration.** Existing SQLite stores `is_local INTEGER` (0/1). The import script must coerce to `bool`. Trivial but easy to miss.

---

## 8. Out of scope

- **Live host-status subscription on the frontend.** The panel can keep polling `/api/v1/hosts` for now.
- **Cascade-delete audit for `agent_workspace_mounts.host_id`.** Tracked under Design 115 Phase B.
- **Renaming `host_registry.RemoteHost.ssh_key` to `ssh_key_secret_ref`.** Internal data class; rename for clarity but keep the public `ssh_key` field if any caller still passes a raw key. Defer the rename to a follow-up.
- **Multi-vault / per-host vault scoping.** All hosts share the bond-instance vault today; per-host scoping is a separate concern.

---

## 9. Open questions

1. **Seed via `init` reducer or via Python on first boot?** STDB's `init` runs once per database; if a user blows away `~/.bond/spacetimedb` and republishes, `init` runs again and re-seeds. Python seeding gives us idempotency on every boot but adds startup coupling. Recommend `init` for simplicity, since the seed row is itself idempotent (`addContainerHost` checks for existing).
2. **Should `sshKeySecretRef` accept the literal SSH key as a setter for backwards compat?** The current REST `POST /hosts` accepts `ssh_key` as a string. Either (a) rewrite the endpoint to require a `secretRef` (cleaner, breaking) or (b) keep accepting raw and have the route write to the vault before calling the reducer. (b) is the path of least resistance and matches how `git_credentials` works today.
3. **Do we keep `import_from_config`?** It exists to one-time-import from `bond.json`. After this migration the canonical input is the UI, not a config file. Recommend deprecating but not removing — leave the route 410 Gone'd behind a flag for one release.
4. **Do we want a STDB index on `enabled` or `isLocal`?** The registry reads `WHERE enabled = 1`. Today SQLite scans (no index). STDB also has no index here. With <10 hosts in any realistic deployment this is fine; defer to Design 115 §2.5 cleanup.
