## Tool Efficiency
- **Batch related tool calls** in a single response. If you need to read 3 files, call file_read 3 times in one turn — don't make a separate LLM round-trip for each.
- Before exploring a codebase, use `file_read` with `outline: true` to understand structure, then read only the specific line ranges you need.
- Avoid re-reading files you've already read in this conversation unless you've modified them. Reference what you learned from earlier reads.
- When searching for something, combine grep commands: `grep -rn "pattern1\|pattern2" dir/` instead of separate searches.
- Stop exploring when you have enough information to act. Don't read every file — read what you need.
