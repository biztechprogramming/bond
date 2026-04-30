# Design Doc 115: Database Schema Standards

**Status:** Draft — awaiting review
**Driver:** The schema in `spacetimedb/spacetimedb/src/index.ts` has accumulated 42 tables under two different naming conventions and inconsistent audit-field practice. This doc establishes the standards new tables must meet and proposes a remediation plan for the existing offenders.

---

## 1. Why we need this

A snapshot of the current schema (2026-04-30):

- **Naming:** 21 tables use camelCase fields (`agents`, `conversations`, `mcp_servers`, `workPlans` …); 17 use snake_case (`prompt_fragments`, `environments`, `resources` …); the split corresponds to roughly when the tables were authored, not to any deliberate decision.
- **`createdAt`:** present on 33 tables, missing on 9 (`agent_workspace_mounts`, `provider_aliases`, `llm_models`, `environment_approvers`, `environment_history`, `promotions`, `approvals`, `alerts`, `agent_database_access`).
- **`updatedAt`:** present on only 13 tables.
- **Soft-delete (`isActive`):** present on 6 tables; the other 36 hard-delete.
- **Table-name capitalization:** mostly `snake_case`, but five outliers in camelCase (`embeddingModels`, `conversationMessages`, `workPlans`, `workItems`, `prompt_fragments` … wait, that's snake; the camelCase outliers are: `embeddingModels`, `conversationMessages`, `workPlans`, `workItems`).

The cost of the inconsistency is real:

- Every developer has to look up which convention the table they're touching uses; mistakes (`agentId` vs `agent_id`) become silent runtime errors.
- Tables without `createdAt` cannot be sorted chronologically without a join.
- Tables without `updatedAt` cannot be sync'd or cached incrementally.
- Hard-delete-only tables make audit and recovery harder.

---

## 2. Standards (binding for all new tables)

### 2.1 Required fields

| Field | Required when | Type | Convention |
|---|---|---|---|
| `id` | always (unless a natural PK is more appropriate, e.g. `settings.key`) | `t.string().primaryKey()` | ULID |
| `createdAt` | **always** | `t.u64()` | milliseconds since Unix epoch |
| `updatedAt` | **always for mutable tables** | `t.u64()` | ms since epoch; equal to `createdAt` on insert |
| `createdBy` | deferred until multi-user identity exists | `t.string()` | user ID |
| `updatedBy` | deferred until multi-user identity exists | `t.string()` | user ID |
| `isActive` | when soft-delete + audit history are valuable | `t.bool()` | default `true` |

**"Mutable tables" means:** any table whose rows can be edited by user action or by an automated process. Pure log tables (`tool_logs`, `conversationMessages`, `system_events`) are append-only and do not require `updatedAt`. Pure join tables (`agent_prompt_fragments`, `component_resources`) usually don't either.

**"Worth soft-deleting" means:** tables where the record's existence has historical or auditing value (`agents`, `conversations`, `prompt_templates`, `environments`). Pure join tables and ephemeral state tables (`alerts`, `tool_logs`) hard-delete.

### 2.2 Naming

- **Field names: `camelCase`.** Rationale: the user-facing tables (`agents`, `conversations`) are already camelCase, and migrating their columns is far more disruptive (every API route, every TS binding, every reducer signature) than migrating the snake_case tables. We pick the convention with the lower migration cost.
- **Table names: `snake_case`.** Already dominant; the camelCase table-name outliers are wart-level.
- **Boolean fields: `is{Adjective}` or `has{Noun}` or `can{Verb}`.** Examples: `isActive`, `isDefault`, `hasGithubToken`. Avoid `enabled` ↔ `disabled` ambiguity.
- **Timestamp fields: `{verb}At`.** Examples: `createdAt`, `updatedAt`, `lastSyncedAt`, `completedAt`. Always milliseconds since epoch as `t.u64()`.
- **Foreign-key fields: `{referenced_table_singular}Id`.** Examples: `agentId`, `conversationId`, `credentialId`. (No need for a "Fk" suffix — the `Id` is enough.)

### 2.3 Types

- **Strings:** `t.string()`. Use `.optional()` only when "not present" is genuinely different from "empty string" in business logic (rare in this schema). Otherwise default to non-optional with empty-string sentinel — matches existing pattern (`mcp_servers.agentId`, comment "NULL/empty means global").
- **Numbers:** `t.u32()` for counts/durations/indices, `t.u64()` for timestamps and large counters. Prefer unsigned unless the field can legitimately be negative.
- **Booleans:** `t.bool()` with `.default(...)` where useful.
- **JSON-encoded structures:** `t.string()` with a comment naming the schema (`tools: t.string(), // JSON array of tool names`). SpacetimeDB does not have native JSON.
- **Enums:** `t.string()` with the comment listing valid values (`status: t.string(), // 'queued' | 'delivered' | 'failed'`). Keep enum values as snake_case strings for readability inside the JSON.

### 2.4 Reducers and cascade deletes

When a table has a foreign key to another, the parent's `delete*` reducer **must cascade** to clean up the child. See `deleteAgent` (line 900 of index.ts) for the canonical pattern. New child tables added later must update the parent's cascade. This is a manual discipline; CI lint can be added later (out of scope).

### 2.5 Index on hot foreign keys

SpacetimeDB indexes the primary key automatically. For tables that are frequently queried by a foreign key (e.g. `agent_repos.agentId`, `conversations.agentId`), declare a secondary index. (Today the schema does not — this is a separate cleanup we should track.)

### 2.6 Prohibited patterns

- **Mixing camelCase and snake_case in the same table.** Pick one (camelCase per §2.2).
- **Storing secrets in the table.** Secrets go in the vault; the table holds a `secretRef` pointer (see `git_credentials.secretRef`).
- **Storing user-supplied JSON without a documented shape.** The string column is fine; the comment naming the shape is required.
- **Soft-delete without timestamps.** If you add `isActive`, also add `deactivatedAt` (or use `updatedAt` if the soft-delete is the only mutation).

---

## 3. Tables that don't yet meet the standard

### 3.1 Naming-style migration (camelCase ⇐ snake_case)

These tables would be renamed:

`prompt_fragments`, `prompt_fragment_versions`, `agent_prompt_fragments`, `prompt_templates`, `prompt_template_versions`, `environments`, `environment_approvers`, `environment_history`, `promotions`, `approvals`, `resources`, `triggers`, `alerts`, `alert_rules`, `components`, `resource_environments`, `component_resources`, `component_scripts`, `component_secrets`.

Field renames per table: `display_name → displayName`, `is_active → isActive`, `created_at → createdAt`, etc.

**Migration cost:** medium. STDB treats column renames as breaking changes — they would require either:
- A `--clear-database` publish (loses data; not acceptable), or
- A manual data migration: add new columns, copy data via reducer, drop old columns over multiple publishes.

This is real work. Recommend doing it in **one batch PR per table** to keep blast radius manageable.

### 3.2 Tables missing `createdAt`

Add as a new column with default `0` so existing rows don't break, then backfill from logs/best-guess where possible:

`agent_workspace_mounts`, `provider_aliases`, `llm_models`, `environment_approvers`, `environment_history`, `promotions`, `approvals`, `alerts`, `agent_database_access`.

**Migration cost:** low. Adding a column is non-breaking in STDB. Backfilling can be done lazily.

### 3.3 Tables missing `updatedAt` (29 tables)

Same low-cost mechanism as 3.2. Should be done in the same migration PR per table.

### 3.4 Camel-cased table names

`embeddingModels → embedding_models`, `conversationMessages → conversation_messages`, `workPlans → work_plans`, `workItems → work_items`. Plus reducer references and TS binding regeneration.

**Migration cost:** medium for the same reasons as 3.1. Defer until the field-naming pass.

---

## 4. Remediation Plan

Three separable pieces of work, in increasing order of risk:

### Phase A — Add missing audit fields (1 day, low risk)

For each table missing `createdAt`/`updatedAt`, add the columns with default `0`. Update CRUD endpoints to populate them on insert/update. Backfill `createdAt = 1` on existing rows so downstream sorts don't crash. No data loss possible since this is purely additive.

### Phase B — Cascade-delete audit (1–2 days, low risk)

Walk every parent table's `delete*` reducer. For each child table with an FK, ensure it's cleaned up. Today this is patchy — `deleteAgent` handles channels and mounts (now plus repos), but other parents may leak orphans. Audit + fix.

### Phase C — Naming convention migration (1–2 weeks, medium risk)

Per-table mini-migrations (snake → camel for field names, camelCase tables → snake_case names). Each runs as: add new columns, dual-write reducers, backfill, switch reads to new columns, drop old columns. Multiple publishes per table; pause between each to verify in-prod. Order: lowest-traffic tables first to derisk the playbook.

---

## 5. Out of Scope

- **Multi-user `createdBy`/`updatedBy`.** Bond has no user identity yet. Revisit when it does.
- **Indexes on hot FKs.** Worth doing, but separate concern from naming/audit standards.
- **Optimistic concurrency (`version` columns).** Nice-to-have; defer until we hit a concrete write-conflict bug.
- **Encryption at rest beyond the vault.** SpacetimeDB's storage is the trust boundary; we don't add field-level encryption today.

---

## 6. Open Questions

1. **What about read-only tables seeded at startup (e.g., `embeddingModels`, `providers`)?** These get rewritten on every boot from a Python seed. Adding `updatedAt` is still useful for diagnostics but not strictly enforced. Recommend including it for consistency.
2. **Should `id` always be a ULID, or do we accept natural keys (`settings.key`, `provider_aliases.alias`, `embeddingModels.modelName`)?** Natural keys are fine when they're stable and unique by construction. Do not retrofit ULIDs onto tables where the natural key is already in use.
3. **Where does this doc live in the lifecycle?** Recommend treating it as living: every table-introducing PR cites this doc and either complies or explicitly justifies non-compliance in the PR description.
