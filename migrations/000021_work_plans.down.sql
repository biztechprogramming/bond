-- Reverse work plans migration
DROP TRIGGER IF EXISTS work_items_updated_at;
DROP TRIGGER IF EXISTS work_plans_updated_at;
DROP INDEX IF EXISTS idx_wi_plan_ordinal;
DROP INDEX IF EXISTS idx_wi_plan_status;
DROP INDEX IF EXISTS idx_wp_parent;
DROP INDEX IF EXISTS idx_wp_conversation;
DROP INDEX IF EXISTS idx_wp_agent_status;
DROP TABLE IF EXISTS work_items;
DROP TABLE IF EXISTS work_plans;

DELETE FROM prompt_fragment_versions WHERE fragment_id = '01PFRAG_WORK_PLAN0';
DELETE FROM agent_prompt_fragments WHERE fragment_id = '01PFRAG_WORK_PLAN0';
DELETE FROM prompt_fragments WHERE id = '01PFRAG_WORK_PLAN0';
