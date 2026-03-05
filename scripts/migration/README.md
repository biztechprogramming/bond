# SpacetimeDB Migration Scripts

This directory contains scripts for migrating data from SQLite to SpacetimeDB in the Bond project.

## Overview

The Bond project is migrating from SQLite to SpacetimeDB as the primary database. These scripts handle the data migration process.

## Migration Status

### Successfully Migrated Tables
- `agents` - Agent definitions
- `agent_workspace_mounts` - Agent workspace mounts
- `agent_channels` - Agent channel configurations
- `conversations` - Conversation metadata
- `conversation_messages` - Message history (560 rows migrated)
- `settings` - System settings
- `mcp_servers` - MCP server configurations

### Tables with Issues
- `work_plans` - Work plans (Kanban board)
- `work_items` - Work items
- `providers` - LLM provider configurations
- `llm_models` - Available LLM models
- `provider_api_keys` - Encrypted API keys

The main issue is with **optional columns** in SpacetimeDB. When a column is defined as `t.u64().optional()` in the TypeScript schema, SpacetimeDB represents it as an algebraic type `(some: U64 | none: ())`. The SQL syntax for inserting values into these columns is not standard SQL.

## Key Migration Scripts

### `migrate_final.py`
Comprehensive migration script that attempts to migrate all tables. Handles most tables but fails on tables with optional columns.

### `migrate_work_tables.py`
Specifically for migrating `work_plans` and `work_items`. Shows the optional column issue clearly.

### `migrate_agents_fixed.py`
Fixed version for migrating agents table (handles column name mismatches).

### `migrate_messages.py`
For migrating conversation messages.

### `migrate_settings.py`
For migrating settings.

## The Optional Column Problem

SpacetimeDB represents optional columns as algebraic sum types. For example:
- `t.u64().optional()` becomes `(some: U64 | none: ())`
- `t.string().optional()` becomes `(some: String | none: ())`

In SQL, you cannot use standard `NULL` or omit the column. You need to use SpacetimeDB's syntax for optional values.

### Error Examples
```
SpacetimeDB SQL failed (400): The literal expression `1772459092305` cannot be parsed as type `(some: U64 | none: ())`
SpacetimeDB SQL failed (400): Unsupported literal expression: NULL
```

### Possible Solutions
1. Use SpacetimeDB reducers instead of direct SQL (e.g., `importWorkPlan`, `importWorkItem` reducers)
2. Figure out the correct SQL syntax for optional values
3. Modify the schema to use default values instead of optional columns

## Using Reducers for Migration

The SpacetimeDB schema includes import reducers:
- `importWorkPlan` - For importing work plans
- `importWorkItem` - For importing work items
- `importConversation` - For importing conversations
- `importConversationMessage` - For importing messages

These reducers accept the exact TypeScript types and handle optional values correctly.

## Testing Scripts

### `test_optional_syntax.py`
Tests different SQL syntaxes for optional columns.

### `test_stdb_sql.py`
Tests basic SQL queries against SpacetimeDB.

### `check_*.py`
Various scripts to check schema and data.

## Running Migration Scripts

```bash
cd ~/bond
source .venv/bin/activate

# Run a specific migration
python3 scripts/migration/migrate_final.py

# Check current data in SpacetimeDB
python3 scripts/migration/check_stdb.py
```

## Next Steps

1. **Fix optional column handling** - Either figure out SQL syntax or use reducers
2. **Complete work tables migration** - `work_plans` and `work_items`
3. **Migrate remaining tables** - `providers`, `llm_models`, `provider_api_keys`
4. **Update backend code** - Fix reducer name inconsistencies (camelCase vs snake_case)
5. **Test end-to-end** - Ensure all functionality works with SpacetimeDB

## Notes

- The backend currently has inconsistent reducer naming (some camelCase, some snake_case)
- The `turn_stdb.py` file was missing imports (fixed in recent commit)
- Some reducers may not exist or have wrong names (e.g., `add_conversation_message` vs `addConversationMessage`)