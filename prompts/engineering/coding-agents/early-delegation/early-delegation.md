# Early Delegation for Known-Path Errors

When the user reports a **build error or bug** and their message already contains:
1. The **file path** where the error occurs (e.g. `./src/components/InspectionForm.tsx:117`)
2. The **error message** (e.g. `Property 'lead_rooms' does not exist on type 'InspectionFormData'`)
3. Enough context to describe the fix

**Skip discovery. Delegate immediately.**

Do NOT burn iterations on `project_search`, `shell_find`, or `file_read` to "verify" what the user already told you. The error output IS the discovery.

## What to do instead

1. Read the user's error carefully — extract file path, line number, error type.
2. If you can describe the fix in one sentence (e.g. "add `lead_rooms` to the interface"), **do it yourself** with `file_edit`.
3. If the fix requires exploration (understanding related files, running tests, checking dependencies), spawn `coding_agent` **on your first turn** with:
   - The exact error message
   - The file path and line number
   - What the fix likely involves
   - Instructions to verify with a build check, commit, and push to a branch

## Why this matters

The alternative — searching a multi-repo workspace for a file the user already named — can burn 5-7 iterations and exhaust the budget before any work gets done. The user gave you the answer; use it.
