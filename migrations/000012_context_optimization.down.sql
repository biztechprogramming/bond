DROP INDEX IF EXISTS idx_trc_tool;
DROP INDEX IF EXISTS idx_trc_conversation;
DROP TABLE IF EXISTS tool_result_cache;
-- SQLite doesn't support DROP COLUMN before 3.35.0; these are safe as defaults are empty
