CREATE TABLE provider_api_keys (
    provider_id TEXT PRIMARY KEY REFERENCES providers(id) ON DELETE CASCADE,
    encrypted_value TEXT NOT NULL,
    key_type TEXT NOT NULL DEFAULT 'api_key'
        CHECK(key_type IN ('api_key', 'oauth_token')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER provider_api_keys_updated_at
    AFTER UPDATE ON provider_api_keys FOR EACH ROW
BEGIN
    UPDATE provider_api_keys SET updated_at = CURRENT_TIMESTAMP WHERE provider_id = NEW.provider_id;
END;
