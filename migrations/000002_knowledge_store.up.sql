-- ============================================================
-- Migration 000002: Knowledge Store + Persistent Memory
-- ============================================================

-- Enforce foreign keys (idempotent, must be set per-connection)
PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------
-- Embedding configuration reference table
-- -----------------------------------------------------------
CREATE TABLE embedding_configs (
    model_name TEXT PRIMARY KEY,
    family TEXT NOT NULL,              -- 'voyage4', 'qwen3', 'gemini'
    provider TEXT NOT NULL,            -- 'voyage', 'huggingface', 'google'
    max_dimension INTEGER NOT NULL,
    supported_dimensions TEXT NOT NULL, -- JSON array, e.g. '[256,512,1024,2048]'
    supports_local INTEGER NOT NULL DEFAULT 0,
    supports_api INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Seed all supported embedding models
-- Voyage 4 family (shared embedding space — all interchangeable)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('voyage-4-nano',  'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 1, 0, 1),
    ('voyage-4-lite',  'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0),
    ('voyage-4',       'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0),
    ('voyage-4-large', 'voyage4', 'voyage', 2048, '[256,512,1024,2048]', 0, 1, 0);

-- Qwen3 family (each model has its own embedding space, MRL supports any dim up to max)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('Qwen3-Embedding-0.6B', 'qwen3', 'huggingface', 1024, '[256,512,1024]',      1, 0, 0),
    ('Qwen3-Embedding-4B',   'qwen3', 'huggingface', 2560, '[256,512,1024,2560]',  1, 0, 0),
    ('Qwen3-Embedding-8B',   'qwen3', 'huggingface', 4096, '[256,512,1024,4096]',  1, 0, 0);

-- Gemini family (fixed dimension)
INSERT INTO embedding_configs (model_name, family, provider, max_dimension, supported_dimensions, supports_local, supports_api, is_default)
VALUES
    ('gemini-embedding-001', 'gemini', 'google', 768, '[768]', 0, 1, 0);

-- -----------------------------------------------------------
-- content_chunks: indexed content from any source
-- -----------------------------------------------------------
CREATE TABLE content_chunks (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,                -- 'conversation', 'file', 'email', 'web'
    source_id TEXT,                            -- FK to source (session_key, email_id, etc.)
    text TEXT NOT NULL,
    summary TEXT,
    chunk_index INTEGER NOT NULL DEFAULT 0,   -- position within multi-chunk document
    parent_id TEXT REFERENCES content_chunks(id) ON DELETE SET NULL,
    sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK(sensitivity IN ('normal', 'personal', 'secret')),
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_cc_source ON content_chunks(source_type, source_id);
CREATE INDEX idx_cc_parent ON content_chunks(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX idx_cc_unprocessed ON content_chunks(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_cc_sensitivity ON content_chunks(sensitivity);

CREATE TRIGGER content_chunks_updated_at
    AFTER UPDATE ON content_chunks FOR EACH ROW
BEGIN
    UPDATE content_chunks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- NOTE: vec0 virtual tables (content_chunks_vec, memories_vec, session_summaries_vec,
-- entities_vec) are NOT created in migrations. They are created at application startup
-- by ensure_vec_tables() using the user's configured embedding.output_dimension.
-- Example: CREATE VIRTUAL TABLE content_chunks_vec USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[N])
-- where N = embedding.output_dimension from settings (default 1024).

-- FTS5 index for content_chunks
CREATE VIRTUAL TABLE content_chunks_fts USING fts5(
    id UNINDEXED,
    text,
    summary
);

-- FTS sync triggers (FTS5 updates require DELETE + INSERT)
CREATE TRIGGER cc_fts_insert AFTER INSERT ON content_chunks BEGIN
    INSERT INTO content_chunks_fts(id, text, summary)
    VALUES (NEW.id, NEW.text, NEW.summary);
END;

CREATE TRIGGER cc_fts_update AFTER UPDATE OF text, summary ON content_chunks BEGIN
    DELETE FROM content_chunks_fts WHERE id = OLD.id;
    INSERT INTO content_chunks_fts(id, text, summary)
    VALUES (NEW.id, NEW.text, NEW.summary);
END;

CREATE TRIGGER cc_fts_delete AFTER DELETE ON content_chunks BEGIN
    DELETE FROM content_chunks_fts WHERE id = OLD.id;
END;

-- -----------------------------------------------------------
-- memories: persistent facts, solutions, instructions
-- -----------------------------------------------------------
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN ('fact', 'solution', 'instruction', 'preference')),
    content TEXT NOT NULL,
    summary TEXT,
    source_type TEXT,                          -- 'conversation', 'user_explicit', 'extraction'
    source_id TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK(sensitivity IN ('normal', 'personal', 'secret')),
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    importance REAL NOT NULL DEFAULT 0.5
        CHECK(importance BETWEEN 0.0 AND 1.0),
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMP,
    processed_at TIMESTAMP,
    deleted_at TIMESTAMP,                      -- soft delete
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_mem_type ON memories(type);
CREATE INDEX idx_mem_unprocessed ON memories(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_mem_active ON memories(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_mem_sensitivity ON memories(sensitivity);
CREATE INDEX idx_mem_importance ON memories(importance DESC);

CREATE TRIGGER memories_updated_at
    AFTER UPDATE ON memories FOR EACH ROW
BEGIN
    UPDATE memories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for memories: created at runtime by ensure_vec_tables()

-- FTS5 index for memories
CREATE VIRTUAL TABLE memories_fts USING fts5(
    id UNINDEXED,
    content,
    summary
);

-- FTS sync triggers
CREATE TRIGGER mem_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER mem_fts_update AFTER UPDATE OF content, summary ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
    INSERT INTO memories_fts(id, content, summary)
    VALUES (NEW.id, NEW.content, NEW.summary);
END;

CREATE TRIGGER mem_fts_delete AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE id = OLD.id;
END;

-- -----------------------------------------------------------
-- memory_versions: immutable change log (append-only)
-- -----------------------------------------------------------
CREATE TABLE memory_versions (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    previous_content TEXT,            -- NULL for version 1 (creation)
    new_content TEXT NOT NULL,
    previous_type TEXT,               -- NULL for version 1
    new_type TEXT NOT NULL,
    changed_by TEXT NOT NULL,         -- 'agent', 'user', 'system'
    change_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_mv_memory ON memory_versions(memory_id, version);

-- -----------------------------------------------------------
-- session_summaries: compressed conversation history
-- -----------------------------------------------------------
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    key_decisions JSON DEFAULT '[]' CHECK(json_valid(key_decisions)),
    message_count INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_ss_key ON session_summaries(session_key);
CREATE INDEX idx_ss_unprocessed ON session_summaries(processed_at) WHERE processed_at IS NULL;

CREATE TRIGGER session_summaries_updated_at
    AFTER UPDATE ON session_summaries FOR EACH ROW
BEGIN
    UPDATE session_summaries SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for session_summaries: created at runtime by ensure_vec_tables()

-- FTS5 index for session_summaries
CREATE VIRTUAL TABLE session_summaries_fts USING fts5(
    id UNINDEXED,
    summary,
    key_decisions
);

CREATE TRIGGER ss_fts_insert AFTER INSERT ON session_summaries BEGIN
    INSERT INTO session_summaries_fts(id, summary, key_decisions)
    VALUES (NEW.id, NEW.summary, NEW.key_decisions);
END;

CREATE TRIGGER ss_fts_update AFTER UPDATE OF summary, key_decisions ON session_summaries BEGIN
    DELETE FROM session_summaries_fts WHERE id = OLD.id;
    INSERT INTO session_summaries_fts(id, summary, key_decisions)
    VALUES (NEW.id, NEW.summary, NEW.key_decisions);
END;

CREATE TRIGGER ss_fts_delete AFTER DELETE ON session_summaries BEGIN
    DELETE FROM session_summaries_fts WHERE id = OLD.id;
END;
