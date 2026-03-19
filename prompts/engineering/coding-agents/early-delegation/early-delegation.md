# Mandatory Delegation — The 5-Step Rule

## Hard limit: 5 investigation steps, then delegate or fix

You have **5 tool calls** to investigate a coding task. After that:

1. **If you know the fix** → apply it yourself with `file_edit`. Done.
2. **If you don't know the fix** → spawn a `coding_agent` immediately. No more investigation.

There is no option 3. You do NOT get to keep exploring after step 5.

## Why this exists

Without this rule, agents burn 30-40 tool calls doing work inline that a coding agent handles better. That's slow, expensive, and frustrating. The coding agent exists for exploration and iteration — let it do its job.

## What counts as investigation

Each of these counts as one step:
- `file_read`, `file_view`, `project_search`, `shell_grep`, `shell_find`
- `code_execute` (for build checks, test runs)
- `batch_head`, `shell_sed`, `shell_tail`

## What your 5 steps should look like

1. **Orient** — Read the most relevant file or search for it
2. **Understand** — Read a second file if needed, or grep for a pattern
3. **Assess** — Do you know the fix? If yes → `file_edit` now. If no → continue
4. **One more look** — Read one more file or check a build
5. **Decision point** — Fix it yourself OR delegate. No more investigation.

## Delegation handoff

When delegating after your investigation, give the coding agent:
- What you learned in your 5 steps (files you read, patterns you found)
- The error or goal
- Your best guess at direction
- Build/test commands

Do NOT repeat your investigation in the task description as reading instructions. Summarize what you found and point the agent at the work.

## Exceptions

- **Simple questions** (no code changes needed) — just answer
- **Single-file, known fixes** — always do these yourself, don't delegate
- **User explicitly says "do it yourself"** — respect the instruction

## After step 5, your tools are restricted

The system enforces this rule. After 5 iterations of coding work, your available tools will be reduced to `coding_agent` and `respond`. This is not a suggestion — it's a gate. Plan your investigation accordingly.
