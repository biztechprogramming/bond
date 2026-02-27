-- ============================================================
-- Migration 000011: LLM Model Catalog
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_models (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'chat',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(provider, model_id)
);

CREATE INDEX idx_llm_models_provider ON llm_models(provider);
CREATE INDEX idx_llm_models_category ON llm_models(category);

CREATE TRIGGER llm_models_updated_at
    AFTER UPDATE ON llm_models FOR EACH ROW
BEGIN
    UPDATE llm_models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
