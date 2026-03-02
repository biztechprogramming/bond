-- SQLite doesn't support DROP COLUMN before 3.35.0, so recreate the table
-- This preserves all data except the new columns

CREATE TABLE prompt_fragments_backup AS
SELECT id, name, display_name, category, content, description, is_active, is_system, created_at, updated_at
FROM prompt_fragments;

DROP TABLE prompt_fragments;

CREATE TABLE prompt_fragments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('behavior', 'tools', 'safety', 'context')),
    content TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

INSERT INTO prompt_fragments SELECT * FROM prompt_fragments_backup;
DROP TABLE prompt_fragments_backup;

CREATE TRIGGER prompt_fragments_updated_at
    AFTER UPDATE ON prompt_fragments FOR EACH ROW
BEGIN
    UPDATE prompt_fragments SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
