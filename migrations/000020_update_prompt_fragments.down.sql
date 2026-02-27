-- Revert to original fragment content
UPDATE prompt_fragments SET content = '## File Operations
- Always read a file before overwriting it — understand the current content.
- When editing large files, prefer targeted changes over rewriting the entire file.
- After writing a file, read it back to verify the write succeeded.
- Use `code_execute` with shell commands for bulk file operations (find, grep, sed).
- Create parent directories before writing to new paths.
- Be careful with file encodings — default to UTF-8.'
WHERE name = 'file-operations';

DELETE FROM prompt_fragments WHERE id = '01PFRAG_EFFICIENCY0';
DELETE FROM agent_prompt_fragments WHERE fragment_id = '01PFRAG_EFFICIENCY0';
DELETE FROM prompt_fragment_versions WHERE id LIKE 'v2_%';
