-- Up migration: Create mcp_servers table
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    args JSON NOT NULL DEFAULT '[]',
    env JSON NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE, -- NULL means global/available to all
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_name ON mcp_servers(name);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_agent ON mcp_servers(agent_id);
