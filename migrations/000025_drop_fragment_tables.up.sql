-- Migration 000025: Drop fragment tables (Doc 027 Phase 1)
--
-- Fragment CRUD and agent attachment have been replaced by the filesystem-based
-- manifest system. All prompt content and metadata now lives in prompts/manifest.yaml
-- and individual markdown files, versioned in git.
--
-- These tables were effectively empty (no fragments were attached to any agent).

DROP TABLE IF EXISTS agent_prompt_fragments;
DROP TABLE IF EXISTS prompt_fragment_versions;
DROP TABLE IF EXISTS prompt_fragments;
