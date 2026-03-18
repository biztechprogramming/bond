# Fast Resolution for Known-Path Errors

When the user reports a **build error or bug** and their message already contains:
1. The **file path** where the error occurs (e.g. `./src/components/InspectionForm.tsx:117`)
2. The **error message** (e.g. `Property 'lead_rooms' does not exist on type 'InspectionFormData'`)
3. Enough context to describe the fix

**Skip discovery.** Do NOT burn iterations on `project_search`, `shell_find`, or `file_read` to "verify" what the user already told you. The error output IS the discovery.

## CRITICAL: Fix it yourself first

**If you can describe the fix in one sentence, you MUST do it yourself with `file_edit`. Do NOT spawn a coding agent.**

Examples of fixes you MUST do yourself:
- "Change `sampling_date: string | null` to `Date | null`" → **just edit the file**
- "Add `lead_rooms` to the interface" → **just edit the file**
- "Add the missing import for `UserRole`" → **just edit the file**
- "Update the Prisma include to add `inspector: true`" → **just edit the file**

Spawning a coding agent for a fix you can already articulate is slower, more expensive, and **frustrating for the user**. The coding agent adds value through *exploration and iteration* — if the answer is already known, it's pure overhead.

## When to delegate instead

Only spawn a coding agent if the fix genuinely requires exploration:
- You can see the error but the root cause could be in multiple places
- The fix involves coordinated changes across 5+ files you haven't seen
- You need to run tests iteratively to get it right

**Delegate early — don't over-investigate.** If you're going to delegate, do it within 8-10 tool calls. Give the agent:
- The error message and file path
- Your rough sense of direction ("probably a type mismatch in the Prisma model" is enough)
- Build/test instructions

Do NOT read every related file, trace every import, and map the full dependency graph before delegating. That's the agent's job. If you do all that work yourself, you'll already know the fix — at which point you should just apply it directly instead of delegating.

## Why this matters

The alternative — searching a multi-repo workspace for a file the user already named — can burn 5-7 iterations and exhaust the budget before any work gets done. The user gave you the answer; use it.
