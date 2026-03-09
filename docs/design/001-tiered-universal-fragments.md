# Design: Move progress-tracking and work-planning to Standard Tier

**Author:** Developer Agent  
**Date:** 2026-03-08  
**Status:** Draft

## Problem

Every agent turn injects **all 9 universal fragments + the manifest** into the system prompt — regardless of whether the user asked a simple question or kicked off a multi-step task.

Two of the heaviest fragments provide no value on simple turns:

| Fragment | Tokens | Purpose |
|----------|--------|---------|
| `work-planning` | ~417 | Instructs the agent to create/update work plans |
| `progress-tracking` | ~428 | Status updates, scope control, completion checklists |

That's **~845 tokens wasted on every "what's the weather?" or "explain this error"** turn. Across 62 primary-model calls in our Langfuse data, that's ~52K tokens of Opus capacity burned on instructions that weren't relevant.

## What "Trigger-Gated" Means

The context pipeline already has a mechanism for this: **keyword triggers** on prompt fragments.

Instead of being in the `universal/` directory (which means always-injected), a fragment can live in the **fragment DB** as a `standard` tier fragment with `task_triggers` — a list of keywords. When the user's message matches any trigger keyword, the fragment is auto-included. When it doesn't match, the fragment is skipped entirely — zero token cost.

Example: `work-planning` would have triggers like `["build", "implement", "create", "refactor", "migrate", "fix bug", "add feature", "plan"]`. If the user says "implement OAuth", the keyword `implement` matches, and the work-planning fragment gets injected. If the user says "what does this error mean?", no keyword matches, so the fragment is skipped.

This is the **keyword_trigger** layer in `_select_relevant_fragments()` (`context_pipeline.py` line ~230).

## Current Flow

```
System prompt assembly (worker.py ~645-670):
  1. Base system prompt (agent.system.main.md)
  2. DB fragments via _select_relevant_fragments()  ← currently empty
  3. Universal fragments (all 9 .md files in prompts/universal/)  ← always injected
  4. Prompt manifest  ← always injected
```

Universal fragments bypass the selection pipeline entirely — they're loaded by `load_universal_fragments_with_meta()` and concatenated directly.

## Proposed Change

### Step 1: Remove from universal/

Move these two files out of `prompts/universal/`:
- `prompts/universal/work-planning.md` → delete (content moves to DB)
- `prompts/universal/progress-tracking.md` → delete (content moves to DB)

### Step 2: Seed as DB Fragments

Create two `standard`-tier fragments in the `prompt_fragments` table:

**work-planning:**
```json
{
  "name": "work-planning",
  "display_name": "Work Planning",
  "category": "behavior",
  "tier": "standard",
  "task_triggers": [
    "build", "implement", "create", "refactor", "migrate",
    "fix", "add feature", "plan", "design", "set up",
    "deploy", "upgrade", "rewrite", "restructure"
  ],
  "content": "<current content of work-planning.md>"
}
```

**progress-tracking:**
```json
{
  "name": "progress-tracking",
  "display_name": "Progress Tracking",
  "category": "behavior",
  "tier": "standard",
  "task_triggers": [
    "build", "implement", "create", "refactor", "migrate",
    "fix", "add feature", "deploy", "upgrade", "rewrite",
    "test", "review", "audit"
  ],
  "content": "<current content of progress-tracking.md>"
}
```

### Step 3: Attach to Default Agent

Insert rows into `agent_prompt_fragments` linking both fragments to the default agent (and any other agents that should have them).

### Step 4: Create a Seed Migration

Add a migration script (or a seed function in worker startup) that:
1. Creates the fragments if they don't exist
2. Attaches them to all existing agents
3. Is idempotent (safe to run multiple times)

## Token Impact

| Scenario | Before | After | Savings |
|----------|--------|-------|---------|
| Simple Q&A turn | 3,104 tokens | ~2,259 tokens | **845 tokens (27%)** |
| Multi-step task turn | 3,104 tokens | 3,104 tokens | 0 (triggers match) |

Over 100 turns where ~40% are simple interactions, that's **~33,800 tokens saved**.

## Risks

1. **False negatives** — the agent doesn't get work-planning instructions on a task that needs them because no trigger keyword matched.
   - **Mitigation:** Include broad triggers. The LLM selection layer (Layer 3) acts as a safety net — if the utility model thinks the fragment is relevant, it gets included even without a keyword match.

2. **Behavioral regression** — the agent stops creating work plans for tasks where it currently does.
   - **Mitigation:** A/B test by checking Langfuse traces. Compare plan creation rates before and after the change.

3. **Trigger list maintenance** — keywords need updating as usage patterns evolve.
   - **Mitigation:** Langfuse metadata already tracks `_selection_reason`. Monitor `keyword_trigger` vs `llm_selected` ratios. If the LLM is picking these fragments often when keywords miss, add those keywords.

## Verification

1. Run existing tests: `uv run --extra dev python -m pytest tests/test_prompt_hierarchy.py tests/test_langfuse_audit.py`
2. Send a simple question and verify Langfuse shows `fragment_count: 8` (not 10)
3. Send a "build me X" message and verify both fragments appear with `reason: keyword_trigger`
4. Send an ambiguous message and verify the utility model selects them when relevant

## Files Changed

- `prompts/universal/work-planning.md` — deleted
- `prompts/universal/progress-tracking.md` — deleted
- `backend/app/worker.py` — no changes needed (pipeline already handles DB fragments)
- `migrations/` or `backend/app/seed.py` — new seed script for fragment DB
- `tests/test_prompt_hierarchy.py` — update expected fragment counts
- `tests/test_langfuse_audit.py` — update expected fragment lists
