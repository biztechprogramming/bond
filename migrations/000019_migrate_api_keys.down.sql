-- Cannot fully reverse: the old settings keys were deleted.
-- Best effort: move keys back to settings.
INSERT OR IGNORE INTO settings (key, value)
    SELECT 'llm.api_key.' || provider_id, encrypted_value
    FROM provider_api_keys;

DELETE FROM provider_api_keys;
