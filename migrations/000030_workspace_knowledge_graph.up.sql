-- ============================================================
-- Migration 000030: Workspace Knowledge Graph (Phase 1)
-- Design Doc 110 — deterministic structural graph for workspaces
-- ============================================================

PRAGMA foreign_keys = ON;

-- ── Nodes ──

CREATE TABLE workspace_graph_nodes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    repo_id TEXT,
    node_type TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    path TEXT,
    language TEXT,
    signature TEXT,
    content_hash TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    embedding_model TEXT,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX idx_wgn_ws_stable_key ON workspace_graph_nodes(workspace_id, stable_key);
CREATE INDEX idx_wgn_ws_type ON workspace_graph_nodes(workspace_id, node_type);
CREATE INDEX idx_wgn_ws_path ON workspace_graph_nodes(workspace_id, path);
CREATE INDEX idx_wgn_ws_repo_type ON workspace_graph_nodes(workspace_id, repo_id, node_type);

CREATE TRIGGER workspace_graph_nodes_updated_at
    AFTER UPDATE ON workspace_graph_nodes FOR EACH ROW
BEGIN
    UPDATE workspace_graph_nodes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ── Edges ──

CREATE TABLE workspace_graph_edges (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    repo_id TEXT,
    source_node_id TEXT NOT NULL REFERENCES workspace_graph_nodes(id) ON DELETE CASCADE,
    target_node_id TEXT NOT NULL REFERENCES workspace_graph_nodes(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('extracted','inferred','ambiguous')),
    confidence REAL NOT NULL DEFAULT 1.0,
    source_kind TEXT NOT NULL,
    run_id TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_confirmed_at TIMESTAMP
);

CREATE UNIQUE INDEX idx_wge_unique ON workspace_graph_edges(workspace_id, source_node_id, target_node_id, edge_type, source_kind);
CREATE INDEX idx_wge_ws_source ON workspace_graph_edges(workspace_id, source_node_id);
CREATE INDEX idx_wge_ws_target ON workspace_graph_edges(workspace_id, target_node_id);
CREATE INDEX idx_wge_ws_edge_type ON workspace_graph_edges(workspace_id, edge_type);
CREATE INDEX idx_wge_ws_mode_conf ON workspace_graph_edges(workspace_id, mode, confidence);

CREATE TRIGGER workspace_graph_edges_updated_at
    AFTER UPDATE ON workspace_graph_edges FOR EACH ROW
BEGIN
    UPDATE workspace_graph_edges SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ── Provenance ──

CREATE TABLE workspace_graph_provenance (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    edge_id TEXT REFERENCES workspace_graph_edges(id) ON DELETE CASCADE,
    node_id TEXT REFERENCES workspace_graph_nodes(id) ON DELETE CASCADE,
    provenance_type TEXT NOT NULL,
    source_path TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    source_ref TEXT,
    excerpt TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_wgp_edge ON workspace_graph_provenance(edge_id);
CREATE INDEX idx_wgp_node ON workspace_graph_provenance(node_id);

-- ── Runs ──

CREATE TABLE workspace_graph_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    repo_id TEXT,
    run_type TEXT NOT NULL CHECK(run_type IN ('full','incremental','on_demand')),
    status TEXT NOT NULL CHECK(status IN ('pending','running','success','failed','partial')),
    trigger TEXT NOT NULL CHECK(trigger IN ('workspace_mount','file_change','git_change','manual','tool_intercept','continuation','startup')),
    files_scanned INTEGER NOT NULL DEFAULT 0,
    nodes_written INTEGER NOT NULL DEFAULT 0,
    edges_written INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    error TEXT
);

CREATE INDEX idx_wgr_ws ON workspace_graph_runs(workspace_id);

-- ── File State Ledger ──

CREATE TABLE workspace_graph_file_state (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    repo_id TEXT,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    language TEXT,
    mtime_ns INTEGER,
    size_bytes INTEGER,
    last_indexed_at TIMESTAMP,
    last_run_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('indexed','skipped','error','deleted')),
    last_error TEXT,
    metadata JSON DEFAULT '{}' CHECK(json_valid(metadata))
);

CREATE UNIQUE INDEX idx_wgfs_ws_path ON workspace_graph_file_state(workspace_id, path);

-- ── FTS side tables ──

CREATE VIRTUAL TABLE workspace_graph_nodes_fts USING fts5(
    display_name,
    stable_key,
    path,
    signature,
    content='workspace_graph_nodes',
    content_rowid='rowid'
);

CREATE TRIGGER workspace_graph_nodes_ai AFTER INSERT ON workspace_graph_nodes BEGIN
    INSERT INTO workspace_graph_nodes_fts(rowid, display_name, stable_key, path, signature)
    VALUES (NEW.rowid, NEW.display_name, NEW.stable_key, NEW.path, NEW.signature);
END;

CREATE TRIGGER workspace_graph_nodes_ad AFTER DELETE ON workspace_graph_nodes BEGIN
    INSERT INTO workspace_graph_nodes_fts(workspace_graph_nodes_fts, rowid, display_name, stable_key, path, signature)
    VALUES ('delete', OLD.rowid, OLD.display_name, OLD.stable_key, OLD.path, OLD.signature);
END;

CREATE TRIGGER workspace_graph_nodes_au AFTER UPDATE ON workspace_graph_nodes BEGIN
    INSERT INTO workspace_graph_nodes_fts(workspace_graph_nodes_fts, rowid, display_name, stable_key, path, signature)
    VALUES ('delete', OLD.rowid, OLD.display_name, OLD.stable_key, OLD.path, OLD.signature);
    INSERT INTO workspace_graph_nodes_fts(rowid, display_name, stable_key, path, signature)
    VALUES (NEW.rowid, NEW.display_name, NEW.stable_key, NEW.path, NEW.signature);
END;

CREATE VIRTUAL TABLE workspace_graph_provenance_fts USING fts5(
    excerpt,
    source_path,
    source_ref,
    content='workspace_graph_provenance',
    content_rowid='rowid'
);

CREATE TRIGGER workspace_graph_provenance_ai AFTER INSERT ON workspace_graph_provenance BEGIN
    INSERT INTO workspace_graph_provenance_fts(rowid, excerpt, source_path, source_ref)
    VALUES (NEW.rowid, NEW.excerpt, NEW.source_path, NEW.source_ref);
END;

CREATE TRIGGER workspace_graph_provenance_ad AFTER DELETE ON workspace_graph_provenance BEGIN
    INSERT INTO workspace_graph_provenance_fts(workspace_graph_provenance_fts, rowid, excerpt, source_path, source_ref)
    VALUES ('delete', OLD.rowid, OLD.excerpt, OLD.source_path, OLD.source_ref);
END;

CREATE TRIGGER workspace_graph_provenance_au AFTER UPDATE ON workspace_graph_provenance BEGIN
    INSERT INTO workspace_graph_provenance_fts(workspace_graph_provenance_fts, rowid, excerpt, source_path, source_ref)
    VALUES ('delete', OLD.rowid, OLD.excerpt, OLD.source_path, OLD.source_ref);
    INSERT INTO workspace_graph_provenance_fts(rowid, excerpt, source_path, source_ref)
    VALUES (NEW.rowid, NEW.excerpt, NEW.source_path, NEW.source_ref);
END;
