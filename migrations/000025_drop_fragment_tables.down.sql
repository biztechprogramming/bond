-- Rollback migration 000025: Recreate fragment tables
-- Note: data cannot be restored (tables were empty before drop).

CREATE TABLE IF NOT EXISTS prompt_fragments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'behavior',
    content TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'standard',
    task_triggers TEXT NOT NULL DEFAULT '[]',
    token_estimate INTEGER NOT NULL DEFAULT 0,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prompt_fragment_versions (
    id TEXT PRIMARY KEY,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_reason TEXT NOT NULL DEFAULT '',
    changed_by TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_prompt_fragments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_id, fragment_id)
);
