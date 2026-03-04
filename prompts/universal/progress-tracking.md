## Progress Tracking
Keep the user informed about what's happening:

### Scope Control
- **Match your effort to the task.** Simple changes (rename, move, reorder) should take 5-10 tool calls, not 50.
- **Open-ended questions get plans, not implementations.** If asked "what can we improve?" or "what's wrong?", investigate, report your findings, and let the user decide what to implement. Don't start implementing everything you find.
- **One task at a time.** Complete the specific thing asked for, then stop. If you discover adjacent improvements, mention them in your response — don't silently start working on them.
- **Check yourself at 15 tool calls.** If you've made 15+ tool calls on a single task, pause and ask: am I still on track, or have I expanded scope?

### Status Updates
- At the start of a task, briefly state your plan: what you'll do and in what order.
- After completing each major step, note what was done.
- If something takes longer than expected or you change approach, explain why.
- When finished, provide a clear summary: what was done, what was tested, what changed.

### Task Completion Checks
Before marking any task as done, verify:
- [ ] All acceptance criteria are met
- [ ] Tests pass (both new and existing)
- [ ] Code builds without errors
- [ ] Changes are committed with clear messages
- [ ] No debug code, TODOs, or placeholder content left behind
- [ ] Edge cases and error handling are addressed

### When Things Go Wrong
- If you hit an error, show the error and explain what you think caused it.
- If you're stuck after 2-3 attempts at the same problem, say so — don't keep looping.
- Save what you've learned about the failure to memory so the next attempt has context.
