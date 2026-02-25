CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'webchat',
    title TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary_id TEXT REFERENCES session_summaries(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX idx_conv_agent ON conversations(agent_id);
CREATE INDEX idx_conv_channel ON conversations(channel);
CREATE INDEX idx_conv_active ON conversations(is_active) WHERE is_active = 1;
CREATE INDEX idx_conv_updated ON conversations(updated_at DESC);
CREATE TRIGGER conversations_updated_at AFTER UPDATE ON conversations FOR EACH ROW
BEGIN UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id; END;

CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    tool_calls JSON,
    tool_call_id TEXT,
    token_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
CREATE INDEX idx_cm_conv ON conversation_messages(conversation_id, created_at);
CREATE INDEX idx_cm_role ON conversation_messages(conversation_id, role);
