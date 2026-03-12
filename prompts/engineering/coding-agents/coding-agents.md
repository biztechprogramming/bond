# Coding Agent Delegation

Use the `coding_agent` tool to delegate complex coding tasks to a sub-agent (Claude Code, Codex, or Pi).

## CRITICAL: Delegation is mandatory, not optional

If a task meets ANY delegation signal below, you **MUST** spawn a coding agent. Do not stop after planning. Do not save a "ready to execute" checkpoint. Do not ask the user to tell you to continue. **Spawn the agent and let it work.**

Failing to delegate when the task requires it is the same as not doing the task at all.

## When to delegate

| Signal | Action |
|--------|--------|
| Task spans 5+ files with exploration needed | **MUST delegate** |
| You'd need 10+ tool calls to do it yourself | **MUST delegate** |
| User says "use Claude Code / Codex / have an agent do it" | **MUST delegate** |
| New feature with tests, build verification, iteration | **MUST delegate** |
| User asks you to implement a design doc | **MUST delegate** — design docs describe multi-file features by definition |
| You've finished discovery and have a plan but haven't written code | **MUST delegate NOW** |
| Simple 1-3 file edit, you know exactly what to write | Do it yourself with `file_edit` |
| Just need to read/understand code | Use `file_read` / `shell_grep` |
| Single command (build, test, install) | Use `code_execute` |

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

## After the sub-agent finishes

1. Check `status` in the result — `completed` or `failed`
2. Review `git_changes` to see what was modified
3. Read the `output` for the sub-agent's summary
4. If it failed, read the output for errors and either retry with a refined task or fix it yourself
