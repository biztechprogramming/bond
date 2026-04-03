## File Operations

### Reading Files
- **Always use `file_read`**, never `code_execute` with `cat`/`head`/`tail`, never `file_read`.
- **`file_read` is the ONLY reading tool.** It handles full reads, head (line_start=1, line_end=N), tail (line_start=-N), mid-file ranges, and outline mode. You never need another tool to read a file.
- **If you have the exact path, call `file_read` directly.** Do not search, ls, find, or wc first. One tool call.
- **Start with outline mode** (`outline: true`) on any file you haven't seen before. This gives you function/class signatures with line numbers so you can target your reads.
- **Read in large chunks** — 100-200 lines at a time, not 15-40. Small reads waste round-trips and cause overlapping re-reads. If you need to understand a function, read the whole function plus surrounding context in one call.
- **Don't re-read lines you already have.** If you read lines 100-200, next read 200-350 — never overlap.
- When you have a plan with specific function names: outline first, targeted reads, then edits. Three steps, not twenty.

### Writing Files
- **Pick the right tool based on scope:**
  - `file_edit` — surgical text replacements (`old_text`/`new_text` pairs). Best for code changes, config tweaks, or swapping a few lines.
  - `file_write` — new files OR full rewrites of existing files. Best for updating docs/markdown where you're rewriting most of the content.
- **Updating an existing doc/markdown file:** `file_read` it once → `file_write` the updated version. Two tool calls total. Do NOT re-read the file, open it into a buffer, or verify with `code_execute`.
- **Never use `file_open`, `file_view`, `file_replace`, `file_search`, or `file_smart_edit`.** These are buffer-based tools for a different agent. Bond's writing tools are `file_edit` and `file_write` only.
- **Don't re-read before writing.** If you already read the file earlier in the conversation, write directly — you have the content.
- **Verify with `file_read`**, not `code_execute`. Read back only the changed section, and only when the edit was complex or error-prone. For straightforward `file_write` of a doc, no verification is needed.
- Create parent directories before writing to new paths.

### Shell Commands
- Use `code_execute` for grep, find, sed, build, test, and multi-step shell operations — not for reading or writing individual files.
