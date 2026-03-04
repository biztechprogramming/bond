-- Up migration: Add agent_id column to mcp_servers
-- This column was missing from 000023 when it was first applied.
-- ALTER TABLE ADD COLUMN is idempotent in SQLite (errors if column exists,
-- but golang-migrate tracks version so this only runs once).
ALTER TABLE mcp_servers ADD COLUMN agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_mcp_servers_agent ON mcp_servers(agent_id);
