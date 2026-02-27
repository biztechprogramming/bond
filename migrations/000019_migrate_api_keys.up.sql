-- Migrate API keys from old settings table into provider_api_keys.
-- This handles the google key that was stored in settings.
-- The anthropic key from vault is handled by a Python migration script.
INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'google', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.google' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'openai', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.openai' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'deepseek', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.deepseek' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'groq', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.groq' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'mistral', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.mistral' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'xai', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.xai' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'openrouter', value, 'api_key'
    FROM settings
    WHERE key = 'llm.api_key.openrouter' AND value IS NOT NULL AND value != '';

INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value, key_type)
    SELECT 'anthropic', value, key_type
    FROM settings
    WHERE key = 'llm.api_key.anthropic' AND value IS NOT NULL AND value != '';

-- Clean up old settings keys
DELETE FROM settings WHERE key LIKE 'llm.api_key.%';
