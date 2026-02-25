-- ============================================================
-- Migration 000005: Agent Profiles
-- ============================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------
-- agents: configurable agent profiles
-- -----------------------------------------------------------
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    sandbox_image TEXT,                     -- Docker image name, NULL = host execution
    tools JSON NOT NULL DEFAULT '[]',       -- JSON array of enabled tool names
    max_iterations INTEGER NOT NULL DEFAULT 25,
    auto_rag INTEGER NOT NULL DEFAULT 1,
    auto_rag_limit INTEGER NOT NULL DEFAULT 5,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER agents_updated_at
    AFTER UPDATE ON agents FOR EACH ROW
BEGIN
    UPDATE agents SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- -----------------------------------------------------------
-- agent_workspace_mounts: host directories mapped into sandbox
-- -----------------------------------------------------------
CREATE TABLE agent_workspace_mounts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    host_path TEXT NOT NULL,
    mount_name TEXT NOT NULL,                -- becomes /workspace/{mount_name}
    readonly INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, mount_name)
);

CREATE INDEX idx_awm_agent ON agent_workspace_mounts(agent_id);

-- -----------------------------------------------------------
-- agent_channels: which communication channels each agent listens on
-- -----------------------------------------------------------
CREATE TABLE agent_channels (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,                   -- 'webchat', 'signal', 'telegram', etc.
    sandbox_override TEXT,                   -- override sandbox image for this channel
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, channel)
);

CREATE INDEX idx_ac_agent ON agent_channels(agent_id);
CREATE INDEX idx_ac_channel ON agent_channels(channel);

-- -----------------------------------------------------------
-- Seed default agent
-- -----------------------------------------------------------
INSERT INTO agents (id, name, display_name, system_prompt, model, sandbox_image, tools, max_iterations, auto_rag, auto_rag_limit, is_default, is_active)
VALUES (
    '01JBOND0000000000000DEFAULT',
    'bond',
    'Bond',
    'You are Bond, a helpful personal AI assistant running locally on the user''s machine. Be concise, helpful, and friendly. You have tools to search your memory, save information, read and write files, and execute code. Use them when needed.',
    'anthropic/claude-sonnet-4-20250514',
    NULL,
    '["respond","search_memory","memory_save","memory_update","code_execute","file_read","file_write","web_search","skills","notify"]',
    25,
    1,
    5,
    1,
    1
);

-- Seed webchat channel for default agent
INSERT INTO agent_channels (id, agent_id, channel, enabled)
VALUES (
    '01JBOND0000000000000WEBCHAT',
    '01JBOND0000000000000DEFAULT',
    'webchat',
    1
);
