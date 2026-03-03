-- Down migration: Remove agent_id from mcp_servers
DROP INDEX IF EXISTS idx_mcp_servers_agent;
ALTER TABLE mcp_servers DROP COLUMN agent_id;
