-- Recreate llm_models with proper FKs and clean schema
CREATE TABLE llm_models_v2 (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_slug TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'chat'
        CHECK(category IN ('chat', 'embedding', 'image', 'audio', 'code')),
    is_available INTEGER NOT NULL DEFAULT 1,
    context_window INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider_id, model_slug)
);

CREATE INDEX idx_lm_provider ON llm_models_v2(provider_id, is_available);
CREATE INDEX idx_lm_category ON llm_models_v2(category);

CREATE TRIGGER llm_models_v2_updated_at
    AFTER UPDATE ON llm_models_v2 FOR EACH ROW
BEGIN
    UPDATE llm_models_v2 SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

DROP TABLE IF EXISTS llm_models;
ALTER TABLE llm_models_v2 RENAME TO llm_models;
