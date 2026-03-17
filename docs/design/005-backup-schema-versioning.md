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

| Table | Reducer | Preserves Timestamps? |
|---|---|---|
| `conversations` | `import_conversation` | ✅ Yes |
| `conversation_messages` | `import_conversation_message` | ✅ Yes |
| `work_plans` | `import_work_plan` | ✅ Yes |
| `work_items` | `import_work_item` | ✅ Yes |
| `agents` | `add_agent` | ❌ Needs import reducer |
| `agent_channels` | `add_agent_channel` | ❌ Needs import reducer |
| `agent_workspace_mounts` | `add_agent_mount` | ❌ Needs import reducer |
| `settings` | `set_setting` | ❌ Needs import reducer |
| `providers` | `add_provider` | ✅ Has created_at/updated_at args |
| `provider_api_keys` | `set_provider_api_key` | ✅ Has created_at/updated_at args |
| `provider_aliases` | `set_provider_alias` | ❌ No timestamps |
| `llm_models` | `add_model` | ❌ Needs import reducer |
| `prompt_fragments` | `add_prompt_fragment` | ❌ Needs import reducer |
| `prompt_templates` | `add_prompt_template` | ❌ Needs import reducer |
| `prompt_fragment_versions` | `add_prompt_fragment_version` | ❌ Needs import reducer |
| `prompt_template_versions` | `add_prompt_template_version` | ❌ Needs import reducer |
| `agent_prompt_fragments` | `add_agent_prompt_fragment` | ❌ Needs import reducer |

**Action item**: Tables marked "Needs import reducer" should get dedicated `import_*`
reducers in the SpacetimeDB module that accept all fields including timestamps,
inserting the row exactly as provided (no server-side timestamp generation).

### Version Detection

When reading from a backup's temp instance, the restore process:

1. Tries known module names in order: `bond-core-v2`, `bond-core`, `bond`
2. For the first module that responds, queries its tables
3. Compares the returned column names against the schema registry to determine
   which version the backup is from
4. Uses the registry to fill in defaults for missing fields

### Migration Logic

For each table being restored:

```
for each row in backup_data:
  mapped_row = {}
  for each field in CURRENT_SCHEMA[table]:
    if field.name exists in row:
      mapped_row[field.name] = row[field.name]
    else:
      mapped_row[field.name] = field.default
  call import_reducer(table, mapped_row)
```

Fields present in the backup but NOT in the current schema are silently dropped.

### How to Bump the Version

When making schema changes that require `--delete-data`:

1. Add a new version entry to `schema-versions.ts`
2. For each new field, specify its default value
3. For removed fields, just don't include them (they'll be dropped on import)
4. Run backup before publishing: `bash scripts/backup-spacetimedb.sh`
5. Publish: `spacetime publish --delete-data bond-core-v2`
6. The restore dialog will handle migration from any previous version

## File Structure

```
gateway/src/backups/
├── router.ts              # REST endpoints (list, preview, restore)
├── schema-versions.ts     # Version registry with field definitions & defaults
└── migrator.ts            # Read from temp instance → map fields → call reducers
```
