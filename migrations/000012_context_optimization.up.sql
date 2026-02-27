-- ============================================================
-- Migration 000012: Context Optimization Support
-- ============================================================

-- Rolling summary for sliding window history
ALTER TABLE conversations ADD COLUMN rolling_summary TEXT DEFAULT '';
ALTER TABLE conversations ADD COLUMN summary_covers_to INTEGER DEFAULT 0;

-- Track recent tool usage for conversation-aware tool selection
ALTER TABLE conversations ADD COLUMN recent_tools_used TEXT DEFAULT '[]';

-- Tool result cache for reference-based compression
CREATE TABLE IF NOT EXISTS tool_result_cache (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args TEXT,
    result_content TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_trc_conversation ON tool_result_cache(conversation_id);
CREATE INDEX idx_trc_tool ON tool_result_cache(conversation_id, tool_name);
