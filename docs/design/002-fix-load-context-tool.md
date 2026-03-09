# Design: Fix load_context Tool Adoption

**Author:** Developer Agent  
**Date:** 2026-03-08  
**Status:** Draft

## Problem

Bond has 56 category-specific prompt fragments on disk (backend, database, engineering, frontend, infrastructure, messaging, security) and a `load_context` tool for the agent to pull them in. The Langfuse data shows **zero `load_context` calls across 100 generations**.

The prompts exist. The tool exists. The manifest listing all categories is injected every turn (~347 tokens). But the agent never uses it.

## Root Causes

### 1. Tool Description is Vague

Current tool description:
```
"Load prompt context for the current task. Pick the most specific relevant
category from the manifest in your system prompt. Call this as your FIRST
action on any non-trivial task."
```

Problems:
- "prompt context" is abstract — the agent doesn't know what it'll get
- "Pick the most specific relevant category from the manifest" requires parsing a dense comma-separated list buried at the bottom of a ~3K token system prompt
- "FIRST action" conflicts with `work-planning.md` which says the FIRST tool call must be `work_plan(action="create_plan")`
- No examples of when or why to call it

### 2. Manifest Format is Not Scannable

The manifest is injected as:
```
Available context categories:
  backend, backend.csharp, backend.csharp.mediatr, backend.python.async, ...
```

This is a wall of 56 comma-separated strings at the end of the system prompt. The agent likely treats it as noise.

### 3. Competing Instructions

The system prompt has three different "do this FIRST" instructions:
1. `work-planning.md`: "Your FIRST tool call MUST be `work_plan(action="create_plan")`"
2. `tool-efficiency.md`: "Your first tool-call turn must batch all available discovery calls"
3. `load_context` tool: "Call this as your FIRST action"

The agent can't do all three first.

### 4. No Feedback Loop

When the agent doesn't call `load_context`, nothing bad happens. There's no reinforcement. The agent produces decent output using the base system prompt + universal fragments, so it never learns that category-specific context would improve quality.

## Proposed Changes

### Fix 1: Rewrite the Tool Description

```json
{
  "name": "load_context",
  "description": "Load expert guidelines for a specific technology or task. Returns detailed best practices, patterns, and gotchas. Example: load_context('database.spacetimedb') returns SpacetimeDB schema patterns, reducer conventions, and SDK usage. Call this when working with a specific technology stack.",
  "parameters": {
    "type": "object",
    "properties": {
      "category": {
        "type": "string",
        "description": "Technology path from the context manifest. Examples: 'backend.python.fastapi', 'database.spacetimedb.reducers', 'engineering.git.commits', 'frontend.react.hooks'"
      }
    },
    "required": ["category"]
  }
}
```

Key changes:
- Concrete description of what it returns ("expert guidelines", "best practices, patterns, and gotchas")
- Inline example showing a real category and what you get
- Removed "FIRST action" — it's a tool to use when relevant, not a mandatory first step

### Fix 2: Replace the Dense Manifest with a Grouped Summary

Instead of:
```
Available context categories:
  backend, backend.csharp, backend.csharp.mediatr, ...
```

Generate:
```
## Expert Context Available (use load_context tool)

| Domain | Categories |
|--------|-----------|
| Backend | python, python.fastapi, python.pydantic, python.testing, typescript, typescript.node, csharp, csharp.mediatr |
| Database | spacetimedb, spacetimedb.reducers, spacetimedb.sql, sqlite, postgresql, migrations |
| Engineering | git.commits, git.pull-requests, code-quality, bugfix, planning |
| Frontend | react, react.hooks, nextjs, nextjs.app-router, nextjs.server-components |
| Infrastructure | docker, docker.compose, docker.sandbox |
| Security | auth, auth.jwt, secrets |

Call `load_context("backend.python.fastapi")` when working on FastAPI routes.
Call `load_context("database.spacetimedb.reducers")` when writing SpacetimeDB reducers.
```

This costs roughly the same tokens but is **scannable** and includes **usage examples**.

### Fix 3: Resolve the "First Action" Conflict

Update the instruction hierarchy:

1. **Remove** "Call this as your FIRST action" from `load_context` description
2. **Update** `tool-efficiency.md` discovery phase to include `load_context`:

```markdown
### Discovery Phase (First Turn)
For any non-trivial task, your **first tool-call turn** should batch:
1. `search_memory` — check for past context
2. `load_context` — load expert guidelines for the relevant technology
3. `code_execute` — run `git status && git log --oneline -5`
4. `file_read` (with `outline: true`) — map the project structure
```

This makes `load_context` part of the existing discovery batch rather than a competing "first action".

### Fix 4: Add Keyword-Based Auto-Injection (Optional)

For the most commonly needed categories, bypass the tool entirely and use keyword triggers in the fragment DB — similar to Design 001.

Candidates for auto-injection:
- `database.spacetimedb` — triggered by "spacetimedb", "reducer", "stdb"
- `engineering.git.commits` — triggered by "commit", "push", "branch"
- `infrastructure.docker.sandbox` — triggered by "sandbox", "container", "docker"

This provides a **belt-and-suspenders** approach: keyword triggers catch the obvious cases, `load_context` handles the rest.

## Implementation

### File Changes

**`backend/app/agent/tools/definitions.py`** (~line 557):
- Rewrite `load_context` tool description and parameter descriptions

**`backend/app/agent/tools/dynamic_loader.py`** (`generate_manifest()`):
- Replace flat comma list with grouped markdown table
- Add 2-3 inline usage examples
- Keep within similar token budget (~350 tokens)

**`prompts/universal/tool-efficiency.md`**:
- Add `load_context` to the discovery phase batch
- Remove conflicting "FIRST action" language

**`prompts/universal/work-planning.md`** (if not already moved per Design 001):
- Soften "FIRST tool call MUST be work_plan" to "include work_plan in your first batch"

### generate_manifest() Rewrite

```python
def generate_manifest(prompts_dir: Path) -> str:
    """Generate a grouped, scannable manifest of available context categories."""
    if not prompts_dir.exists():
        return ""

    # Collect categories grouped by top-level domain
    groups: dict[str, list[str]] = {}
    for md_file in sorted(prompts_dir.rglob("*.md")):
        if md_file.stem != md_file.parent.name:
            continue
        try:
            rel = md_file.parent.relative_to(prompts_dir)
        except ValueError:
            continue
        parts = rel.parts
        if not parts or parts[0] == "universal":
            continue
        domain = parts[0]
        category = ".".join(parts)
        # Store the leaf path relative to domain
        leaf = ".".join(parts[1:]) if len(parts) > 1 else parts[0]
        groups.setdefault(domain, []).append(leaf)

    if not groups:
        return ""

    lines = ["## Expert Context Available (use `load_context` tool)"]
    lines.append("")
    lines.append("| Domain | Categories |")
    lines.append("|--------|-----------|")
    for domain in sorted(groups):
        cats = ", ".join(sorted(groups[domain]))
        lines.append(f"| {domain} | {cats} |")

    lines.append("")
    lines.append('Example: `load_context("backend.python.fastapi")` for FastAPI patterns.')
    lines.append('Example: `load_context("database.spacetimedb")` for SpacetimeDB guidelines.')

    return "\n".join(lines)
```

## Measuring Success

### Before (baseline from Langfuse)
- `load_context` calls per 100 generations: **0**
- Prompt manifest tokens per turn: **~347**

### After (targets)
- `load_context` calls per 100 generations: **>15** (at least when working on specific tech)
- Manifest tokens per turn: **~350** (roughly same budget, better format)
- Categories loaded should correlate with the actual task (check Langfuse `tool_calls` data)

### How to Measure
1. Deploy the changes
2. Run a few conversations touching different tech stacks (SpacetimeDB, Docker, FastAPI)
3. Check Langfuse for `load_context` tool call observations
4. Compare agent output quality (does it follow SpacetimeDB conventions when it loads that context vs. when it doesn't?)

## Risks

1. **Agent over-calls load_context** — loads 3-4 categories per turn, wasting tokens
   - **Mitigation:** The tool description says "for the relevant technology", not "load everything". Monitor call frequency in Langfuse.

2. **Manifest table format confuses the model** — some models handle tables poorly
   - **Mitigation:** Test with claude-opus-4-6 first. If table parsing is an issue, fall back to a grouped list format.

3. **Category-specific prompts are stale or low quality** — loading them could hurt more than help
   - **Mitigation:** Audit prompt content quality before deploying (see Design 003).

## Testing

1. `uv run --extra dev python -m pytest tests/test_prompt_hierarchy.py` — verify manifest generation
2. Manual test: send "help me write a SpacetimeDB reducer" and confirm `load_context` is called
3. Manual test: send "what's 2+2" and confirm `load_context` is NOT called
4. Check Langfuse traces for new `load_context` observations after deploy
