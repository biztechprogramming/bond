## Reasoning

### Think Before Acting
- Read the relevant code before proposing changes. Understand what exists before writing something new.
- Form a hypothesis about what's happening before diving into debugging. Then verify it.
- Consider side effects — what else depends on the code you're about to change?

### Verify Assumptions
- Don't assume a file exists — check. Don't assume a function works a certain way — read it.
- After making changes, verify they work. Run tests, check builds, read back the modified file.
- If your mental model of the codebase doesn't match what you're seeing, update your model — don't force the code to match your expectations.

### Problem Decomposition
- Break complex problems into smaller, independently solvable pieces.
- Solve the simplest version first, then handle edge cases.
- If a solution requires more than 3 steps of reasoning to understand, it's probably too complex — simplify.
