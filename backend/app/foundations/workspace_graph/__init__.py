"""Workspace Knowledge Graph — Phase 1.

Deterministic structural graph for workspaces, repositories, files, and symbols.
Design Doc 110.

DEPRECATED (2026-04-17): The SQLite-backed repository in this package is superseded
by SpacetimeDB tables and reducers (see spacetimedb/spacetimedb/src/index.ts).
Migration 000030 is a no-op; the WKG schema now lives entirely in SpacetimeDB.
The models and extractor remain usable as data transfer objects but should not be
persisted via the SQLite WorkspaceGraphRepository.  See Doc 018 for the migration
strategy.
"""
