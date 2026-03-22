# Design Doc 060: Prompt Conflict Resolution — Message Handling vs Proactive Workflow

**Status:** Planned  
**Created:** 2026-03-22  
**Branch:** fix/open-sandbox-get-executor  

## Problem

Two Tier 1 (always-on) prompts give contradictory instructions:

### `universal/message-handling.md` (new, added in this branch)
> "If more work is needed, ASK before proceeding. Don't autonomously continue
> into multi-step workflows without user confirmation."
>
> "One message → one response. Handle what was asked. Report what you found.
> If the situation needs more work, propose it and wait for the user to say
> 'go ahead.'"

### `universal/proactive-workflow.md` (existing)
> "But **never ask permission to execute something the user already requested.**"

### Observed Behavior

User says: "Have a coding agent fix X."  
Agent responds: "I've prepared the details for the coding agent. Should I spawn it?"

The agent interprets "spawn + configure coding agent" as a "multi-step workflow"
and asks for confirmation — despite the user having already requested exactly that.

## Root Cause

`message-handling.md` uses absolute language ("ASK before proceeding", "wait for
the user to say go ahead") that the LLM treats as a hard rule. When it conflicts
with `proactive-workflow.md`'s "never ask permission for what was already
requested," the more restrictive rule wins — the LLM prefers caution.

The original intent of `message-handling.md` was to prevent:
- Runaway retry loops on failing tool calls
- Silent multi-step cascades where step 1 fails but step 2 proceeds
- Agents autonomously expanding scope beyond what was asked

These are valid concerns, but the blanket "ask first" language catches legitimate
explicit user requests too.

## Proposed Fix

Rewrite `message-handling.md` to preserve the anti-pattern protections while
respecting explicit user requests:

1. **Scope the "ask first" rule to ambiguous/expanded work** — not to executing
   what the user literally asked for
2. **Move the failing-tool anti-patterns** into `tool-efficiency.md` where they
   thematically belong
3. **Add a clear carve-out:** "If the user explicitly requested an action, execute
   it. Questions are for ambiguity, not confirmation."

### Alternative: Demote to Tier 2/3

If rewriting doesn't fully resolve the conflict, demote `message-handling.md`
from Tier 1 to Tier 2 (phase: implementing) so it only loads during active work
phases, not during simple request handling.

## Files Affected

- `prompts/universal/message-handling.md` — rewrite
- `prompts/universal/tool-efficiency.md` — absorb anti-pattern rules
- `prompts/manifest.yaml` — possible tier change

## Success Criteria

- User says "have a coding agent do X" → agent spawns it without asking
- User says "check the logs" → agent checks them without asking
- Agent still stops and reports when a tool call fails (no runaway retries)
- Agent still asks when the user's request is genuinely ambiguous
