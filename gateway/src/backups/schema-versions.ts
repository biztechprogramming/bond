/**
 * Schema version registry for SpacetimeDB backup migration.
 *
 * Every time `spacetime publish --delete-data` is run with schema changes,
 * add a new version here. The restore process uses this to fill in default
 * values for fields that didn't exist in older backups.
 *
 * Field names use snake_case (matching SQL column names from SpacetimeDB).
 */

export type FieldType = "string" | "u32" | "u64" | "bool" | "option_string" | "option_u64";

export interface FieldDef {
  name: string;
  type: FieldType;
  default: unknown;
}

export interface TableDef {
  /** SQL table name */
  table: string;
  /** Reducer to call for importing (must accept all fields including timestamps) */
  importReducer: string;
  /** Ordered list of fields (order matters — reducer args are positional) */
  fields: FieldDef[];
}

export interface SchemaVersion {
  version: number;
  /** Module name used in this version */
  moduleName: string;
  /** Human-readable description of what changed */
  changelog: string;
  /** Table definitions at this version */
  tables: TableDef[];
}

// ─── Known module names (tried in order when probing a backup) ───────────────

export const KNOWN_MODULE_NAMES = ["bond-core-v2", "bond-core", "bond"];

// ─── Current schema (version 2 — bond-core-v2) ──────────────────────────────

const V2_TABLES: TableDef[] = [
  {
    table: "conversations",
    importReducer: "import_conversation",
    fields: [
      { name: "id",                type: "string", default: "" },
      { name: "agent_id",          type: "string", default: "" },
      { name: "channel",           type: "string", default: "webchat" },
      { name: "title",             type: "string", default: "" },
      { name: "is_active",         type: "bool",   default: true },
      { name: "message_count",     type: "u32",    default: 0 },
      { name: "rolling_summary",   type: "string", default: "" },
      { name: "summary_coversto",  type: "u32",    default: 0 },
      { name: "recent_tools_used", type: "string", default: "" },
      { name: "created_at",        type: "u64",    default: 0 },
      { name: "updated_at",        type: "u64",    default: 0 },
    ],
  },
  {
    table: "conversation_messages",
    importReducer: "import_conversation_message",
    fields: [
      { name: "id",              type: "string", default: "" },
      { name: "conversation_id", type: "string", default: "" },
      { name: "role",            type: "string", default: "" },
      { name: "content",         type: "string", default: "" },
      { name: "tool_calls",      type: "string", default: "" },
      { name: "tool_call_id",    type: "string", default: "" },
      { name: "token_count",     type: "u32",    default: 0 },
      { name: "status",          type: "string", default: "delivered" },
      { name: "created_at",      type: "u64",    default: 0 },
    ],
  },
  {
    table: "work_plans",
    importReducer: "import_work_plan",
    fields: [
      { name: "id",              type: "string",      default: "" },
      { name: "agent_id",        type: "string",      default: "" },
      { name: "conversation_id", type: "string",      default: "" },
      { name: "parent_plan_id",  type: "string",      default: "" },
      { name: "title",           type: "string",      default: "" },
      { name: "status",          type: "string",      default: "active" },
      { name: "created_at",      type: "u64",         default: 0 },
      { name: "updated_at",      type: "u64",         default: 0 },
      { name: "completed_at",    type: "option_u64",  default: null },
    ],
  },
  {
    table: "work_items",
    importReducer: "import_work_item",
    fields: [
      { name: "id",               type: "string",      default: "" },
      { name: "plan_id",          type: "string",      default: "" },
      { name: "title",            type: "string",      default: "" },
      { name: "status",           type: "string",      default: "new" },
      { name: "ordinal",          type: "u32",         default: 0 },
      { name: "context_snapshot", type: "string",      default: "" },
      { name: "notes",            type: "string",      default: "" },
      { name: "files_changed",    type: "string",      default: "" },
      { name: "started_at",       type: "option_u64",  default: null },
      { name: "completed_at",     type: "option_u64",  default: null },
      { name: "created_at",       type: "u64",         default: 0 },
      { name: "updated_at",       type: "u64",         default: 0 },
      { name: "description",      type: "string",      default: "" },
    ],
  },
  {
    table: "agents",
    importReducer: "import_agent",
    fields: [
      { name: "id",             type: "string", default: "" },
      { name: "name",           type: "string", default: "" },
      { name: "display_name",   type: "string", default: "" },
      { name: "system_prompt",  type: "string", default: "" },
      { name: "model",          type: "string", default: "" },
      { name: "utility_model",  type: "string", default: "" },
      { name: "tools",          type: "string", default: "" },
      { name: "sandbox_image",  type: "string", default: "" },
      { name: "max_iterations", type: "u32",    default: 200 },
      { name: "is_active",      type: "bool",   default: true },
      { name: "is_default",     type: "bool",   default: false },
      { name: "created_at",     type: "u64",    default: 0 },
    ],
  },
  {
    table: "agent_channels",
    importReducer: "import_agent_channel",
    fields: [
      { name: "id",               type: "string", default: "" },
      { name: "agent_id",         type: "string", default: "" },
      { name: "channel",          type: "string", default: "" },
      { name: "sandbox_override", type: "string", default: "" },
      { name: "enabled",          type: "bool",   default: true },
      { name: "created_at",       type: "u64",    default: 0 },
    ],
  },
  {
    table: "agent_workspace_mounts",
    importReducer: "import_agent_mount",
    fields: [
      { name: "id",             type: "string", default: "" },
      { name: "agent_id",       type: "string", default: "" },
      { name: "host_path",      type: "string", default: "" },
      { name: "mount_name",     type: "string", default: "" },
      { name: "container_path", type: "string", default: "" },
      { name: "readonly",       type: "bool",   default: false },
    ],
  },
  {
    table: "settings",
    importReducer: "import_setting",
    fields: [
      { name: "key",        type: "string", default: "" },
      { name: "value",      type: "string", default: "" },
      { name: "key_type",   type: "string", default: "string" },
      { name: "created_at", type: "u64",    default: 0 },
      { name: "updated_at", type: "u64",    default: 0 },
    ],
  },
  {
    table: "providers",
    importReducer: "import_provider",
    fields: [
      { name: "id",                  type: "string",        default: "" },
      { name: "display_name",        type: "string",        default: "" },
      { name: "litellm_prefix",      type: "string",        default: "" },
      { name: "api_base_url",        type: "option_string", default: null },
      { name: "models_endpoint",     type: "option_string", default: null },
      { name: "models_fetch_method", type: "string",        default: "none" },
      { name: "auth_type",           type: "string",        default: "bearer" },
      { name: "is_enabled",          type: "bool",          default: true },
      { name: "config",              type: "string",        default: "{}" },
      { name: "created_at",          type: "u64",           default: 0 },
      { name: "updated_at",          type: "u64",           default: 0 },
    ],
  },
  {
    table: "provider_api_keys",
    importReducer: "import_provider_api_key",
    fields: [
      { name: "provider_id",     type: "string", default: "" },
      { name: "encrypted_value", type: "string", default: "" },
      { name: "key_type",        type: "string", default: "api_key" },
      { name: "created_at",      type: "u64",    default: 0 },
      { name: "updated_at",      type: "u64",    default: 0 },
    ],
  },
  {
    table: "provider_aliases",
    importReducer: "import_provider_alias",
    fields: [
      { name: "alias",       type: "string", default: "" },
      { name: "provider_id", type: "string", default: "" },
    ],
  },
  {
    table: "llm_models",
    importReducer: "import_model",
    fields: [
      { name: "id",             type: "string", default: "" },
      { name: "provider",       type: "string", default: "" },
      { name: "model_id",       type: "string", default: "" },
      { name: "display_name",   type: "string", default: "" },
      { name: "context_window", type: "u32",    default: 128000 },
      { name: "is_enabled",     type: "bool",   default: true },
    ],
  },
  {
    table: "prompt_fragments",
    importReducer: "import_prompt_fragment",
    fields: [
      { name: "id",             type: "string", default: "" },
      { name: "name",           type: "string", default: "" },
      { name: "display_name",   type: "string", default: "" },
      { name: "category",       type: "string", default: "" },
      { name: "content",        type: "string", default: "" },
      { name: "description",    type: "string", default: "" },
      { name: "is_active",      type: "bool",   default: true },
      { name: "is_system",      type: "bool",   default: false },
      { name: "summary",        type: "string", default: "" },
      { name: "tier",           type: "string", default: "optional" },
      { name: "task_triggers",   type: "string", default: "" },
      { name: "token_estimate", type: "u32",    default: 0 },
      { name: "created_at",     type: "u64",    default: 0 },
      { name: "updated_at",     type: "u64",    default: 0 },
    ],
  },
  {
    table: "prompt_templates",
    importReducer: "import_prompt_template",
    fields: [
      { name: "id",           type: "string", default: "" },
      { name: "name",         type: "string", default: "" },
      { name: "display_name", type: "string", default: "" },
      { name: "category",     type: "string", default: "" },
      { name: "content",      type: "string", default: "" },
      { name: "variables",    type: "string", default: "" },
      { name: "description",  type: "string", default: "" },
      { name: "is_active",    type: "bool",   default: true },
      { name: "created_at",   type: "u64",    default: 0 },
      { name: "updated_at",   type: "u64",    default: 0 },
    ],
  },
  {
    table: "prompt_fragment_versions",
    importReducer: "import_prompt_fragment_version",
    fields: [
      { name: "id",            type: "string", default: "" },
      { name: "fragment_id",   type: "string", default: "" },
      { name: "version",       type: "u32",    default: 1 },
      { name: "content",       type: "string", default: "" },
      { name: "change_reason", type: "string", default: "" },
      { name: "changed_by",    type: "string", default: "system" },
      { name: "created_at",    type: "u64",    default: 0 },
    ],
  },
  {
    table: "prompt_template_versions",
    importReducer: "import_prompt_template_version",
    fields: [
      { name: "id",            type: "string", default: "" },
      { name: "template_id",   type: "string", default: "" },
      { name: "version",       type: "u32",    default: 1 },
      { name: "content",       type: "string", default: "" },
      { name: "change_reason", type: "string", default: "" },
      { name: "changed_by",    type: "string", default: "system" },
      { name: "created_at",    type: "u64",    default: 0 },
    ],
  },
  {
    table: "agent_prompt_fragments",
    importReducer: "import_agent_prompt_fragment",
    fields: [
      { name: "id",          type: "string", default: "" },
      { name: "agent_id",    type: "string", default: "" },
      { name: "fragment_id", type: "string", default: "" },
      { name: "rank",        type: "u32",    default: 0 },
      { name: "enabled",     type: "bool",   default: true },
      { name: "created_at",  type: "u64",    default: 0 },
    ],
  },
];

// ─── Version history ─────────────────────────────────────────────────────────

export const SCHEMA_VERSIONS: SchemaVersion[] = [
  {
    version: 1,
    moduleName: "bond-core",
    changelog: "Initial schema. Tables: conversations, conversation_messages, agents, llm_models, work_plans.",
    tables: [], // V1 field definitions not needed — V2 defaults cover all missing fields
  },
  {
    version: 2,
    moduleName: "bond-core-v2",
    changelog: [
      "Renamed module from bond-core to bond-core-v2.",
      "Added tables: agent_channels, agent_workspace_mounts, settings, providers,",
      "  provider_api_keys, provider_aliases, prompt_fragments, prompt_templates,",
      "  prompt_fragment_versions, prompt_template_versions, agent_prompt_fragments.",
      "Added to work_plans: parent_plan_id, completed_at.",
      "Added to work_items: context_snapshot, started_at, completed_at, description.",
      "Added to agents: created_at.",
    ].join("\n"),
    tables: V2_TABLES,
  },
];

export const CURRENT_VERSION = SCHEMA_VERSIONS[SCHEMA_VERSIONS.length - 1];

/**
 * Get the current table definitions (always the latest version).
 */
export function getCurrentTables(): TableDef[] {
  return CURRENT_VERSION.tables;
}

/**
 * Map a row from a backup to the current schema, filling in defaults for missing fields.
 * Fields in the row that don't exist in the current schema are silently dropped.
 *
 * @param tableName - The SQL table name
 * @param row - Object with snake_case keys from the backup
 * @returns Array of values in field order, ready to pass to the import reducer
 */
export function mapRowToCurrentSchema(tableName: string, row: Record<string, unknown>): unknown[] {
  const tableDef = CURRENT_VERSION.tables.find(t => t.table === tableName);
  if (!tableDef) throw new Error(`Unknown table: ${tableName}`);

  return tableDef.fields.map(field => {
    const value = row[field.name];

    // Field not present in backup → use default
    if (value === undefined) {
      return encodeValue(field.type, field.default);
    }

    return encodeValue(field.type, value);
  });
}

/**
 * Encode a value for SpacetimeDB reducer call.
 * Option types need { some: value } / { none: [] } encoding.
 */
function encodeValue(type: FieldType, value: unknown): unknown {
  if (type === "option_string") {
    if (value === null || value === undefined) return { none: [] as [] };
    // Handle already-encoded options from SpacetimeDB SQL results
    if (typeof value === "object" && value !== null && "some" in value) return value;
    if (typeof value === "object" && value !== null && "none" in value) return value;
    return { some: String(value) };
  }
  if (type === "option_u64") {
    if (value === null || value === undefined) return { none: [] as [] };
    if (typeof value === "object" && value !== null && "some" in value) return value;
    if (typeof value === "object" && value !== null && "none" in value) return value;
    return { some: Number(value) };
  }
  if (type === "u32" || type === "u64") return Number(value ?? 0);
  if (type === "bool") return Boolean(value ?? false);
  return String(value ?? "");
}
