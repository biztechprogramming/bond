# Delegation Guidelines — Know When to Hand Off

## The rule

You get **8 iterations** to investigate a coding task. After that, if you've used 3 or more distinct coding tools (file reads, searches, edits, grep, etc.), your available tools are reduced to `coding_agent` and `respond`.

If you know the fix before hitting the gate — apply it yourself with `file_edit`. If you don't — delegate. Simple.

## How to use your investigation steps

1. Read the most relevant file(s)
2. Search or grep if needed
3. Assess: can you fix it inline? If yes → `file_edit`. If no → delegate early, don't burn iterations.

## What to include in the delegation

- What the user asked for
- Files you read and what you found
- Your best guess at direction
- Build/test commands
- Constraints (what NOT to change)

Summarize what you learned — don't paste file contents into the task.

## Exceptions

- **Simple questions** — no code changes needed, just answer
- **Single known fix** — you can describe it in one sentence → `file_edit`
- **User says "do it yourself"** — respect the instruction
