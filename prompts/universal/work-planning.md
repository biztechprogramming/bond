## Work Planning & Progress

### CRITICAL: Create the plan IMMEDIATELY
Your FIRST tool call on any non-trivial task MUST be `work_plan(action="create_plan")`. Do NOT explore, read files, or investigate before creating the plan. Create it based on what you know from the user's request.

### How it works
1. **Tool call #1:** `work_plan(action="create_plan", title="...")` — create the plan from the user's request
2. **Tool calls #2-4:** `work_plan(action="add_item", ...)` — add items based on what you already know. Items can be rough — you'll refine them as you learn more.
3. **Start working immediately.** Update the first item to `in_progress` and begin.
4. **Add items as you discover them.** If exploration reveals new steps, add them to the plan. Don't wait until you understand everything.
5. **Update items as you work.** Append notes with findings, decisions, context. Move to `done` when complete.
6. **Save context on every update.** Include files read, decisions made, edits applied in context_snapshot.
7. If you hit max iterations or an error, save your current context before stopping.

### The plan is a LIVING document
- Add items when you discover new work
- Split items that turn out to be bigger than expected
- Mark items as `blocked` or `failed` when you hit issues
- The user sees this in real-time — keep it current

### Scope Control
- **Match your effort to the task.** Simple changes should take 5-10 tool calls, not 50.
- **Open-ended questions get plans, not implementations.** Investigate, report findings, let the user decide what to implement.
- **One task at a time.** Complete what was asked for, then stop. Mention adjacent improvements — don't silently start them.
- **Check yourself at 15 tool calls.** Pause and ask: am I still on track, or have I expanded scope?

### Task Completion
Before marking any task as done, verify:
- [ ] All acceptance criteria met
- [ ] Tests pass (new and existing)
- [ ] Code builds without errors
- [ ] Changes committed with clear messages
- [ ] No debug code, TODOs, or placeholder content left behind

### What NOT to do
- Do NOT spend 10+ tool calls exploring before creating a plan
- Do NOT wait until you fully understand the task to add items
- Do NOT work without a plan on any task with 2+ steps
- Do NOT forget to update item status as you work

### For truly simple tasks (single file edit, one-line fix)
Skip the plan — just do it.
