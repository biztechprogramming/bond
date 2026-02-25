-- ============================================================
-- Migration 000004: Audit Log
-- ============================================================

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    command TEXT NOT NULL,
    actor TEXT,
    capability TEXT,
    context JSON DEFAULT '{}' CHECK(json_valid(context)),
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_al_timestamp ON audit_log(timestamp);
CREATE INDEX idx_al_command ON audit_log(command);
CREATE INDEX idx_al_actor ON audit_log(actor) WHERE actor IS NOT NULL;
