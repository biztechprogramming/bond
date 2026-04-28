-- ============================================================
-- Migration 000030: Workspace Knowledge Graph (Phase 1)
-- Design Doc 110 — SUPERSEDED
--
-- This migration has been replaced by SpacetimeDB tables and
-- reducers in spacetimedb/spacetimedb/src/index.ts.
--
-- The WKG schema is now defined as SpacetimeDB tables:
--   workspace_graph_nodes
--   workspace_graph_edges
--   workspace_graph_provenance
--   workspace_graph_runs
--   workspace_graph_file_state
--
-- This file is intentionally a no-op to keep migration numbering
-- consistent. Do NOT add SQLite tables here.
-- ============================================================

SELECT 1; -- no-op placeholder
