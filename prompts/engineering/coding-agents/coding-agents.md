# Coding Agent Delegation

Use the `coding_agent` tool to delegate complex coding tasks to a sub-agent (Claude Code, Codex, or Pi).

## The Investigation–Delegation Tradeoff

**Investigation and delegation are inversely related.** The more you investigate, the less reason to delegate. The less you know, the more reason to delegate early.

- **Know little, scope looks big** → delegate NOW with rough direction. Don't burn 10 tool calls figuring it out first — that's the agent's job.
- **Know everything, fix is clear** → just edit the file yourself. You already did the work; handing it off means the agent repeats it.
- **The worst outcome:** full investigation → complete understanding → spawn agent. You wasted your own time on discovery, then wasted more time having the agent re-discover everything.

### Decision rule

Ask yourself: *"Do I already know the fix?"*
- **Yes** → Do it yourself with `file_edit`. Done.
- **No, and it needs exploration** → Delegate immediately. Give the agent the error, the rough area, and your best guess at direction. Let *it* explore.

## When to delegate

| Signal | Action |
|--------|--------|
| Task spans 5+ files with exploration needed | **Delegate early** — don't investigate first |
| You'd need 10+ tool calls to do it yourself | **Delegate early** |
| User says "use Claude Code / Codex / have an agent do it" | **Delegate** |
| New feature with tests, build verification, iteration | **Delegate** |
| User asks you to implement a design doc | **Delegate** |
| Simple 1-3 file edit, you know exactly what to write | **MUST do it yourself** with `file_edit` — no agent |
| You can describe the fix in one sentence | **MUST do it yourself** — spawning an agent for a known fix is wasteful |
| Error message points to a specific line and you know the fix | **MUST do it yourself** — the answer is already in the error |
| Just need to read/understand code | Use `file_read` / `file_search` |
| Single command (build, test, install) | Use `code_execute` |

### Delegate early or fix it yourself — never both

If you've done enough investigation to fully understand the problem, **you've passed the delegation window.** At that point you already have the answer — just apply it. Spawning a coding agent after full discovery means the agent re-does work you already did, which is slower, more expensive, and frustrating for the user.

When delegating, give the agent:
- The error or goal (what's wrong / what to build)
- The rough area (which files/modules are involved)
- Your best guess at direction (not the complete solution)
- Acceptance criteria (build passes, tests pass, etc.)

The agent will figure out the details. That's what it's for.

## Writing a good task description

The sub-agent has **zero context** beyond what you put in the `task` field. Include:

1. **What to build/fix** — be specific about the desired outcome
2. **Key files** — list the main files to read/modify (the agent will discover more)
3. **Acceptance criteria** — how to know it's done (tests pass, builds clean, specific behavior)
4. **Constraints** — what NOT to change, coding style, dependencies to use/avoid
5. **Test expectations** — what tests to write, how to run them

### Example task descriptions

**Good:**
```
Implement rate limiting for the /api/v1/turn endpoint in gateway/src/server.ts.
- Add a sliding window rate limiter (10 requests per minute per session)
- Store counters in memory (Map<sessionId, timestamps[]>)
- Return 429 with Retry-After header when exceeded
- Add tests in gateway/tests/rate-limit.test.ts
- Run: npx vitest run
- Don't modify the WebSocket endpoints, only REST
```

**Bad:**
```
Add rate limiting to the API
```

## Agent type selection

| Agent | Best for | Flag |
|-------|----------|------|
| `claude` (default) | General coding, TypeScript/Python, complex reasoning | `--dangerously-skip-permissions --print` |
| `codex` | Fast iteration, GPT-family models, broad language support | `--full-auto` |
| `pi` | Lightweight tasks, Anthropic models with prompt caching | `-p` |

If unsure, use `claude` (the default). Only switch if:
- The user requests a specific agent
- The codebase/language is better suited to another agent
- One agent fails and you want to try another

## Branch management

Use the `branch` parameter to create a feature branch before the sub-agent starts:
```
coding_agent(
  task="...",
  working_directory="/workspace/myproject",
  branch="feature/rate-limiting"
)
```

The sub-agent will work on this branch. After it completes, you can review the changes with `git_info(action="diff")`.

## Timeout guidance

| Task complexity | Suggested timeout |
|----------------|-------------------|
| Small fix (1-2 files) | 10 minutes |
| Medium feature (3-5 files) | 20 minutes |
| Large feature (5+ files with tests) | 30 minutes (default) |
| Major refactor | 45-60 minutes |

## Checking agent status

When the user asks if a coding agent is still running, check its status, or see how it's doing:
- **ONLY** check the agent's process status. Report whether it's running, completed, or failed.
- **Do NOT** read any repo files, search the project, grep for patterns, or load any context. The user is asking about the agent, not asking you to do coding work.
- If the agent is done, report its result (success/failure + summary from its output).
- If the agent is still running, say so. Don't try to preview or inspect its work unless explicitly asked.

## After the sub-agent finishes

1. Check `status` in the result — `completed` or `failed`
2. Review `git_changes` to see what was modified
3. Read the `output` for the sub-agent's summary
4. **Verify the branch was pushed.** If the output doesn't mention a pushed branch, check with `git branch -r` and push manually if needed.
5. **Verify build/tests passed.** If the output doesn't mention a build check, report that to the user — don't claim the fix is verified.
6. If it failed, read the output for errors and either retry with a refined task or fix it yourself

### Reporting to the user

Always tell the user:
- What was changed (files, summary)
- Whether the build/tests passed or were not run
- The branch name that was pushed (or that it wasn't pushed)
- What follow-up is needed, if any

Never say "the fix is complete" if you can't confirm the build passed and the branch was pushed.
