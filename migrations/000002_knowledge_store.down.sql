-- Drop in reverse order of creation
DROP TRIGGER IF EXISTS ss_fts_delete;
DROP TRIGGER IF EXISTS ss_fts_update;
DROP TRIGGER IF EXISTS ss_fts_insert;
DROP TRIGGER IF EXISTS session_summaries_updated_at;
DROP TABLE IF EXISTS session_summaries_fts;
DROP TABLE IF EXISTS session_summaries_vec;
DROP TABLE IF EXISTS session_summaries;

DROP TABLE IF EXISTS memory_versions;

DROP TRIGGER IF EXISTS mem_fts_delete;
DROP TRIGGER IF EXISTS mem_fts_update;
DROP TRIGGER IF EXISTS mem_fts_insert;
DROP TRIGGER IF EXISTS memories_updated_at;
DROP TABLE IF EXISTS memories_fts;
DROP TABLE IF EXISTS memories_vec;
DROP TABLE IF EXISTS memories;

DROP TRIGGER IF EXISTS cc_fts_delete;
DROP TRIGGER IF EXISTS cc_fts_update;
DROP TRIGGER IF EXISTS cc_fts_insert;
DROP TRIGGER IF EXISTS content_chunks_updated_at;
DROP TABLE IF EXISTS content_chunks_fts;
DROP TABLE IF EXISTS content_chunks_vec;
DROP TABLE IF EXISTS content_chunks;

DROP TABLE IF EXISTS embedding_configs;
