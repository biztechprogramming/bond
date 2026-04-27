-- ============================================================
-- Migration 000030: Workspace Knowledge Graph — rollback
-- ============================================================

DROP TRIGGER IF EXISTS workspace_graph_provenance_au;
DROP TRIGGER IF EXISTS workspace_graph_provenance_ad;
DROP TRIGGER IF EXISTS workspace_graph_provenance_ai;
DROP TABLE IF EXISTS workspace_graph_provenance_fts;

DROP TRIGGER IF EXISTS workspace_graph_nodes_au;
DROP TRIGGER IF EXISTS workspace_graph_nodes_ad;
DROP TRIGGER IF EXISTS workspace_graph_nodes_ai;
DROP TABLE IF EXISTS workspace_graph_nodes_fts;

DROP TABLE IF EXISTS workspace_graph_file_state;
DROP TABLE IF EXISTS workspace_graph_runs;
DROP TABLE IF EXISTS workspace_graph_provenance;
DROP TABLE IF EXISTS workspace_graph_edges;
DROP TRIGGER IF EXISTS workspace_graph_edges_updated_at;
DROP TRIGGER IF EXISTS workspace_graph_nodes_updated_at;
DROP TABLE IF EXISTS workspace_graph_nodes;
