DROP TRIGGER IF EXISTS llm_models_v2_updated_at;

CREATE TABLE llm_models_old (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'chat',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(provider, model_id)
);

DROP TABLE IF EXISTS llm_models;
ALTER TABLE llm_models_old RENAME TO llm_models;
