CREATE TRIGGER providers_updated_at
    AFTER UPDATE ON providers FOR EACH ROW
BEGIN
    UPDATE providers SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE provider_aliases (
    alias TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE
);
