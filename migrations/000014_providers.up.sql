-- 1. providers table
CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    litellm_prefix TEXT NOT NULL,
    api_base_url TEXT,
    models_endpoint TEXT,
    models_fetch_method TEXT NOT NULL DEFAULT 'openai_compat'
        CHECK(models_fetch_method IN ('anthropic_api', 'anthropic_scrape', 'google_api', 'openai_compat')),
    auth_type TEXT NOT NULL DEFAULT 'bearer'
        CHECK(auth_type IN ('bearer', 'x-api-key', 'query_param')),
    is_enabled INTEGER NOT NULL DEFAULT 1,
    config JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
