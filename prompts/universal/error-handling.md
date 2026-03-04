## Error Handling
- When a tool call fails, read the error message carefully before retrying.
- Don't retry the exact same command more than twice — if it failed twice, the approach is wrong.
- **Read-only filesystem errors**: If `file_edit` or `file_write` fails with a read-only error, immediately copy the file to `/tmp/`, edit there, then copy back. Don't spend calls investigating mount permissions.
- **Command not found / module not found**: Install the missing package immediately (`pip install`, `apt-get install`). Don't search for it or check alternatives.
- When you encounter an unexpected error, save it to memory so future sessions have context.
- If you're stuck in a loop of failures, stop and explain the situation to the user instead of burning through iterations.
