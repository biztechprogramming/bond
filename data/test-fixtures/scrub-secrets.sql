-- ============================================================
-- scrub-secrets.sql
-- Sanitizes sensitive data from a bond SQLite database snapshot.
-- Run this after seeding to ensure no real secrets leak into
-- the test fixture committed to version control.
-- ============================================================

-- Settings: NULL out values for secret-like keys
UPDATE settings SET value = NULL
WHERE key LIKE '%api_key%'
   OR key LIKE '%token%'
   OR key LIKE '%secret%'
   OR key LIKE '%password%'
   OR key LIKE '%credential%';

-- Provider API keys: scrub encrypted values
UPDATE provider_api_keys SET encrypted_value = 'SCRUBBED';

-- Container hosts: remove SSH keys and auth tokens
UPDATE container_hosts SET ssh_key_encrypted = NULL, auth_token = NULL;

-- MCP servers: clear env (may contain secrets)
UPDATE mcp_servers SET env = '{}';

-- Content chunks (knowledge): replace with placeholder text
UPDATE content_chunks SET
    text = 'Test knowledge entry ' || CAST(rowid AS TEXT),
    summary = 'Test summary ' || CAST(rowid AS TEXT);

-- Memories: replace content with placeholder
UPDATE memories SET
    content = 'Test memory ' || CAST(rowid AS TEXT),
    summary = 'Test memory summary ' || CAST(rowid AS TEXT);

-- Session summaries: replace with placeholder text
UPDATE session_summaries SET
    summary = 'Test session summary ' || CAST(rowid AS TEXT),
    key_decisions = '["Test decision"]';

-- Conversations: clear rolling summary
UPDATE conversations SET rolling_summary = '';
