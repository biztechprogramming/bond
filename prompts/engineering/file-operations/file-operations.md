## File Operations

### Reading Files
- **Always use `file_read`**, never `code_execute` with `cat`/`head`/`tail`.
- **Start with outline mode** (`outline: true`) on any file you haven't seen before. This gives you function/class signatures with line numbers so you can target your reads.
- **Read in large chunks** — 100-200 lines at a time, not 15-40. Small reads waste round-trips and cause overlapping re-reads. If you need to understand a function, read the whole function plus surrounding context in one call.
- **Don't re-read lines you already have.** If you read lines 100-200, next read 200-350 — never overlap.
- When you have a plan with specific function names: outline first, targeted reads, then edits. Three steps, not twenty.

### Writing Files
- Use `file_edit` for surgical text replacements — it takes `old_text`/`new_text` pairs. Only use `file_write` for new files or complete rewrites.
- After writing a file, verify the write by reading back the changed section.
- Create parent directories before writing to new paths.

### Shell Commands
- Use `code_execute` for grep, find, sed, build, test, and multi-step shell operations — not for reading or writing individual files.
