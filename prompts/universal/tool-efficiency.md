## Tool Efficiency
- **Batch related tool calls** in a single response. If you need to read 3 files, call file_read 3 times in one turn — don't make a separate LLM round-trip for each.
- Before exploring a codebase, use `file_read` with `outline: true` to understand structure, then read only the specific line ranges you need.
- Avoid re-reading files you've already read in this conversation unless you've modified them. Reference what you learned from earlier reads.
- When searching for something, combine grep commands: `grep -rn "pattern1\|pattern2" dir/` instead of separate searches.
- Stop exploring when you have enough information to act. Don't read every file — read what you need.

### Discovery Phase (First Turn)
For any non-trivial task, your **first tool-call turn** must batch all available discovery calls together. Emit these in a single response so they execute in parallel:
1. `search_memory` — check for past context, decisions, and known issues related to the task.
2. `code_execute` — run `git status && git log --oneline -5` and any relevant directory listing.
3. `file_read` (with `outline: true`) — map the project structure or the specific area you'll be working in.

**Rule:** Never emit a single read/search tool call if you can identify 2+ independent information needs. The system executes independent tool calls concurrently — batching them saves wall-clock time, not just LLM turns.
