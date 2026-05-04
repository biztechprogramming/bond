-- Add per-method permissions to MCP servers.
-- Stores a JSON object mapping tool names to "allow"|"deny".
-- NULL or empty means allow-all (backward compatible).
ALTER TABLE mcp_servers ADD COLUMN method_permissions TEXT DEFAULT '';
