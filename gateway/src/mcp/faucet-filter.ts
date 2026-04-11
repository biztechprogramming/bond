/**
 * Faucet MCP Tool Filter (Design Doc 107)
 *
 * Filters Faucet MCP tools based on agent access tier.
 */

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

export type AccessTier = 'read_only' | 'full_control';

export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export function filterFaucetTools(
  tools: McpTool[],
  accessTier: AccessTier
): McpTool[] {
  const allowed = accessTier === 'read_only' ? READ_ONLY_TOOLS : FULL_CONTROL_TOOLS;
  return tools.filter(t => allowed.has(t.name));
}

export function isFaucetTool(toolName: string): boolean {
  return toolName.startsWith('faucet_');
}
