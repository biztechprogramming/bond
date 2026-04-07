-- Recreate embedding_configs table (was moved to SpacetimeDB)
CREATE TABLE IF NOT EXISTS embedding_configs (
    model_name TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    provider TEXT NOT NULL,
    max_dimension INTEGER NOT NULL,
    supported_dimensions TEXT NOT NULL,
    supports_local INTEGER NOT NULL DEFAULT 0,
    supports_api INTEGER NOT NULL DEFAULT 0,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);
