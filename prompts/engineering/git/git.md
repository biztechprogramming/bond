## Git Operations

### Branching Strategy
- Always check `git status` and `git branch` before starting work to understand the current state.
- Create feature branches for new work: `git checkout -b feat/<description>` or `fix/<description>`.
- Never force-push to shared branches without explicit approval.
- Compare against main before finishing: `git diff main..HEAD --stat` to see the full scope of changes.

### Hygiene
- Before pushing, run `git diff --stat` to review what changed.
- Commit early and often — don't accumulate a massive diff.
- Keep branches focused on a single concern. If scope expands, consider splitting into multiple branches.
