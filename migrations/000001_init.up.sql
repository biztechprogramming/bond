-- Bond initial migration: settings table

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- Trigger to auto-update updated_at
CREATE TRIGGER IF NOT EXISTS settings_updated_at
    AFTER UPDATE ON settings
    FOR EACH ROW
BEGIN
    UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE key = NEW.key;
END;
