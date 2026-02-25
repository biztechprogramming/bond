-- ============================================================
-- Migration 000003: Entity Graph
-- ============================================================

PRAGMA foreign_keys = ON;

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL CHECK(type IN (
        'person', 'project', 'task', 'decision', 'meeting', 'document', 'event'
    )),
    name TEXT NOT NULL,
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_ent_type ON entities(type);
CREATE INDEX idx_ent_name ON entities(name);
CREATE INDEX idx_ent_unprocessed ON entities(processed_at) WHERE processed_at IS NULL;

CREATE TRIGGER entities_updated_at
    AFTER UPDATE ON entities FOR EACH ROW
BEGIN
    UPDATE entities SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Vector index for entities: created at runtime by ensure_vec_tables()

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
        CHECK(weight BETWEEN 0.0 AND 1.0),
    context TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_rel_source ON relationships(source_id);
CREATE INDEX idx_rel_target ON relationships(target_id);
CREATE INDEX idx_rel_type ON relationships(type);
CREATE UNIQUE INDEX idx_rel_unique ON relationships(source_id, target_id, type);

CREATE TRIGGER relationships_updated_at
    AFTER UPDATE ON relationships FOR EACH ROW
BEGIN
    UPDATE relationships SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE entity_mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_em_entity ON entity_mentions(entity_id);
CREATE INDEX idx_em_source ON entity_mentions(source_type, source_id);
