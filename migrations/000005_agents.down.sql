-- ============================================================
-- Migration 000005 DOWN: Drop Agent Profiles
-- ============================================================

DROP INDEX IF EXISTS idx_ac_channel;
DROP INDEX IF EXISTS idx_ac_agent;
DROP TABLE IF EXISTS agent_channels;

DROP INDEX IF EXISTS idx_awm_agent;
DROP TABLE IF EXISTS agent_workspace_mounts;

DROP TRIGGER IF EXISTS agents_updated_at;
DROP TABLE IF EXISTS agents;
