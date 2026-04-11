/**
 * Faucet MCP Tool Filter (Design Docs 107 & 109)
 *
 * Filters Faucet MCP tools based on agent access tier.
 * Prefers Bond-native virtual database tools (database_*) over raw
 * faucet_* tools — raw tools are suppressed from agent-facing lists
 * when Bond-native equivalents are present.
 */

// Raw Faucet MCP tool sets (kept for internal/legacy use)
export const READ_ONLY_TOOLS = new Set([
  'faucet_list_services',
  'faucet_list_tables',
  'faucet_describe_table',
  'faucet_query',
]);

export const FULL_CONTROL_TOOLS = new Set([
  ...READ_ONLY_TOOLS,
  'faucet_insert',
  'faucet_update',
  'faucet_delete',
  'faucet_raw_sql',
]);

// Bond-native virtual database tool sets (Design Doc 109)
export const BOND_DB_READ_ONLY_TOOLS = new Set([
  'database_list_databases',
  'database_list_tables',
  'database_describe_table',
  'database_query',
]);

export const BOND_DB_FULL_CONTROL_TOOLS = new Set([
  ...BOND_DB_READ_ONLY_TOOLS,
  'database_insert_rows',
  'database_update_rows',
  'database_delete_rows',
  'database_execute_sql',
]);

export type AccessTier = 'read_only' | 'full_control';

export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

/**
 * Filter tools for agent exposure, preferring Bond-native database_*
 * tools and suppressing raw faucet_* equivalents (Design Doc 109 §4).
 */
export function filterFaucetTools(
  tools: McpTool[],
  accessTier: AccessTier
): McpTool[] {
  const hasBondDbTools = tools.some(t => isBondDatabaseTool(t.name));

  const allowedBond = accessTier === 'read_only'
    ? BOND_DB_READ_ONLY_TOOLS
    : BOND_DB_FULL_CONTROL_TOOLS;

  return tools.filter(t => {
    // If Bond-native DB tools are present, suppress raw faucet_* tools
    if (hasBondDbTools && isFaucetTool(t.name)) {
      return false;
    }
    // Filter Bond-native DB tools by access tier
    if (isBondDatabaseTool(t.name)) {
      return allowedBond.has(t.name);
    }
    // Allow all other tools
    return true;
  });
}

export function isFaucetTool(toolName: string): boolean {
  return toolName.startsWith('faucet_') || toolName.startsWith('mcp_faucet_');
}

export function isBondDatabaseTool(toolName: string): boolean {
  return toolName.startsWith('database_');
}
