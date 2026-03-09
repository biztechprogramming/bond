## Tool Efficiency
- **Batch related tool calls** in a single response. If you need to read 3 files, call file_read 3 times in one turn — don't make a separate LLM round-trip for each.
- Before exploring a codebase, use `file_read` with `outline: true` to understand structure, then read only the specific line ranges you need.
- Avoid re-reading files you've already read in this conversation unless you've modified them. Reference what you learned from earlier reads.
- When searching for something, combine grep commands: `grep -rn "pattern1\|pattern2" dir/` instead of separate searches.
- Stop exploring when you have enough information to act. Don't read every file — read what you need.

### Finding Files: Use project_search First
When looking for a file, document, or code reference, use `project_search` as your **first choice**. It automatically tries multiple strategies (filename matching, content search, path matching, zero-padded numbers) in a single call.

**Examples:**
- `project_search(query="design doc 27")` → finds `docs/design/027-fragment-selection-roadmap.md`
- `project_search(query="worker tests")` → finds test files related to the worker
- `project_search(query="manifest yaml")` → finds manifest files across the project

Only fall back to `shell_find` or `shell_grep` when you need their specific features (glob patterns, regex with context lines, etc.).

### Project Structure Conventions
- **Design docs:** `docs/design/NNN-slug.md` (zero-padded 3-digit prefix)
- **Prompts:** `prompts/` directory tree organized by topic
- **Tests:** `backend/tests/test_*.py` and `gateway/src/__tests__/*.test.ts`

### Discovery Phase (First Turn)
For any non-trivial task, your **first tool-call turn** must batch all available discovery calls together. Emit these in a single response so they execute in parallel:
1. `search_memory` — check for past context, decisions, and known issues related to the task.
2. `project_search` — find relevant files by name or topic.
3. `file_read` (with `outline: true`) — map the project structure or the specific area you'll be working in.

**Rule:** Never emit a single read/search tool call if you can identify 2+ independent information needs. The system executes independent tool calls concurrently — batching them saves wall-clock time, not just LLM turns.
