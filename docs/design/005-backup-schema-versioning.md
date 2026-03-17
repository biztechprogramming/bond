# 005 — Backup Schema Versioning & Migration

## Problem

When SpacetimeDB is republished with `--delete-data` (due to breaking schema changes),
all data is lost. Backups exist as tar.gz archives of the STDB data directory, but
restoring from them requires migrating data from the **old schema** into the **new schema**.

Fields get added, removed, or renamed between versions. Without a version registry,
the restore process doesn't know:
- Which fields exist in the backup vs. the current schema
- What default values to use for newly added fields
- Which fields to ignore (removed in the new schema)

## Design

### Two-Phase Approach

**Phase 1 — Restore:** Load data from a backup archive into the live database.
The backup contains binary STDB data, so we start a temporary standalone STDB
instance to read from it, then import into the live `bond-core-v2` via reducers.

**Phase 2 — Migrate:** When the schema changes, dump all tables from the live DB
before publishing, then re-import after the schema update. This is handled by
`scripts/migrate-schema.sh`.

### Schema Version Registry

A single TypeScript file at `gateway/src/backups/schema-versions.ts` that defines:

1. **A version number** — monotonically increasing integer, bumped every time
   `spacetime publish --delete-data` is run with schema changes.
2. **The current version** — always the latest entry.
3. **Per-table field definitions** with:
   - `name` — the SQL column name (snake_case)
   - `type` — `"string" | "u32" | "u64" | "bool" | "option_string" | "option_u64"`
   - `default` — the value to use when the field doesn't exist in older data
4. **A changelog** — what changed from the previous version (for human reference).

### Table Registry

Every table that should be backed up and restored is registered. Tables are grouped:

- **Core**: `conversations`, `conversation_messages`
- **Agents**: `agents`, `agent_channels`, `agent_workspace_mounts`, `agent_prompt_fragments`
- **Work**: `work_plans`, `work_items`
- **Config**: `settings`, `providers`, `provider_api_keys`, `provider_aliases`, `llm_models`
- **Prompts**: `prompt_fragments`, `prompt_templates`, `prompt_fragment_versions`, `prompt_template_versions`

### Import Reducer Mapping

Each table maps to an import reducer. For tables that don't have a dedicated `import_*`
reducer, we document which reducer to use (e.g., `add_agent`, `set_setting`).

| Table | Reducer | Upsert? | Preserves Timestamps? |
|---|---|---|---|
| `conversations` | `import_conversation` | ✅ | ✅ Yes |
| `conversation_messages` | `import_conversation_message` | ✅ | ✅ Yes |
| `work_plans` | `import_work_plan` | ✅ | ✅ Yes |
| `work_items` | `import_work_item` | ✅ | ✅ Yes |
| `agents` | `import_agent` | ✅ | ✅ Yes |
| `agent_channels` | `import_agent_channel` | ✅ | ✅ Yes |
| `agent_workspace_mounts` | `import_agent_mount` | ✅ | N/A (no timestamps) |
| `settings` | `import_setting` | ✅ | ✅ Yes |
| `providers` | `import_provider` | ✅ | ✅ Yes |
| `provider_api_keys` | `import_provider_api_key` | ✅ | ✅ Yes |
| `provider_aliases` | `import_provider_alias` | ✅ | N/A (no timestamps) |
| `llm_models` | `import_model` | ✅ | N/A (no timestamps) |
| `prompt_fragments` | `import_prompt_fragment` | ✅ | ✅ Yes |
| `prompt_templates` | `import_prompt_template` | ✅ | ✅ Yes |
| `prompt_fragment_versions` | `import_prompt_fragment_version` | ✅ | ✅ Yes |
| `prompt_template_versions` | `import_prompt_template_version` | ✅ | ✅ Yes |
| `agent_prompt_fragments` | `import_agent_prompt_fragment` | ✅ | ✅ Yes |

All import reducers use the upsert pattern (delete-if-exists, then insert) so they
are safe to call on a database that already has data.

---

## Restore Flow (from backup archive)

The restore dialog lets users pick a backup file and import its data into the live
`bond-core-v2` database.

### How it works

1. User selects a backup `.tar.gz` file from the list
2. Gateway extracts the archive to a temp directory
3. Gateway starts a **temporary standalone SpacetimeDB instance** from the extracted
   data directory on a random high port (19000–19999)
4. Gateway probes the temp instance for known module names
   (`bond-core-v2`, `bond-core`, `bond`) to find which one has data
5. Gateway queries all registered tables via `SELECT * FROM <table>`
6. For each row, maps it through the schema migration logic (fill defaults,
   drop removed fields, encode option types)
7. Calls import reducers on the **live** `bond-core-v2` database
8. Kills the temp instance and cleans up the temp directory

### Why a temp standalone instance?

The backup is a tar.gz of SpacetimeDB's binary data directory (BSATN snapshots
and commit logs). The only way to read this data is to have SpacetimeDB load it.
A standalone instance on a different port is isolated from the live database —
if anything goes wrong, the live DB is unaffected.

### Endpoints

```
GET  /api/backups           — List available backup files with size/date/tier
POST /api/backups/preview   — Preview a backup's contents (table counts, date ranges)
POST /api/backups/restore   — Restore all data from a backup into the live DB
```

---

## Migration Flow (schema changes)

When the SpacetimeDB schema changes and requires `--delete-data`, use the
migration script instead of running `spacetime publish` directly.

### Script: `scripts/migrate-schema.sh`

```bash
# Full migration: dump → publish --delete-data → re-import
./scripts/migrate-schema.sh

# Just dump (for testing or backup before manual publish)
./scripts/migrate-schema.sh --dump-only

# Re-import from a previous dump (if import failed partway)
./scripts/migrate-schema.sh --import-only ~/.bond/backups/spacetimedb/migrations/20260317_094500
```

### How it works

1. **Dump**: Queries every registered table from the live DB via the HTTP SQL API,
   saves each as a JSON file under `~/.bond/backups/spacetimedb/migrations/<timestamp>/`
2. **Publish**: Runs `spacetime publish --delete-data always` with the new module
   (prompts for confirmation)
3. **Import**: Reads each JSON dump file, maps rows through the schema migration
   logic (via `gateway/dist/backups/schema-versions.js`), and calls import reducers
   on the now-empty database

### Safety

- Dump files are preserved even if import fails — re-run with `--import-only`
- The tar.gz backups from the daily backup script provide an additional safety net
- The script requires typing `YES` before the destructive publish step

### Typical workflow

```bash
# 1. Make schema changes in spacetimedb/spacetimedb/src/
# 2. Update schema-versions.ts with new version, field definitions, and defaults
# 3. Run the migration
./scripts/migrate-schema.sh
# 4. Verify data
spacetime sql bond-core-v2 "SELECT COUNT(*) FROM conversations" -s bond-local
```

---

## Schema Migration Logic

For each table being restored or migrated:

```
for each row in source_data:
  mapped_row = {}
  for each field in CURRENT_SCHEMA[table]:
    if field.name exists in row:
      mapped_row[field.name] = row[field.name]
    else:
      mapped_row[field.name] = field.default
  call import_reducer(table, mapped_row)
```

Fields present in the source but NOT in the current schema are silently dropped.

Option types (`option_string`, `option_u64`) are encoded as SpacetimeDB sum types:
- Present: `{ "some": value }`
- Absent: `{ "none": [] }`

### How to Bump the Version

When making schema changes that require `--delete-data`:

1. Add a new version entry to `schema-versions.ts`
2. For each new field, specify its default value
3. For removed fields, just don't include them (they'll be dropped on import)
4. Run the migration script (which handles backup + publish + import)

---

## File Structure

```
gateway/src/backups/
├── router.ts              # REST endpoints (list, preview, restore)
└── schema-versions.ts     # Version registry with field definitions & defaults

scripts/
└── migrate-schema.sh      # Dump → publish --delete-data → re-import
```
