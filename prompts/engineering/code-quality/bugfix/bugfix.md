## Bug Fix Discipline

You are fixing a bug, not improving the codebase. Your goal is the **smallest possible change** that resolves the issue.

### Core Principles

1. **Minimal diff** — Every line you change is a line that could introduce a new bug. Change only what is necessary to fix the reported issue.
2. **Preserve structure** — The code is organized the way it is for a reason. Do not reorganize, rename, reformat, or "clean up" anything outside the fix.
3. **No drive-by refactors** — If you see something ugly, outdated, or suboptimal while fixing the bug, leave it alone. Note it if you want, but don't touch it.
4. **Match existing style exactly** — Use the same patterns, naming conventions, indentation, spacing, and idioms already present in the file. Your fix should look like the original author wrote it.

### Process

1. **Reproduce first** — Confirm you can trigger the bug before changing anything. If you can't reproduce it, say so.
2. **Understand the root cause** — Read the relevant code paths. Trace the data flow. Don't guess — know why it's broken before writing a fix.
3. **Identify the minimum fix** — Ask yourself: what is the fewest lines I can change to correct this behavior? If the answer is one line, change one line.
4. **Don't expand scope** — If the bug is in function A, don't refactor functions B and C "while you're in there." Fix function A.
5. **Write a targeted test** — Write a test that fails before your fix and passes after. The test should cover the specific bug, not be a broad rewrite of the test suite.
6. **Verify nothing else broke** — Run the existing test suite. Your change should not cause new failures.

### What NOT To Do

- Don't rename variables unless the rename IS the fix
- Don't change formatting or whitespace in lines you didn't need to touch
- Don't add abstractions — if the fix works with an if-statement, use an if-statement
- Don't upgrade dependencies unless the bug is caused by the dependency version
- Don't move code between files unless the bug is caused by the file organization
- Don't change method signatures if you can fix it within the existing signature

### Commit

- Commit message: `fix: <what was broken and why>`
- The diff should be reviewable in under 2 minutes
- If your fix requires changing more than ~20 lines, pause and reconsider — are you fixing the bug or rewriting the feature?
