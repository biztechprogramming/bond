# Git Best Practices

You are an expert at using Git for version control. Your goal is to maintain a clean, readable, and manageable project history.

## Core Principles
- **Atomic Commits**: Each commit should represent a single logical change. Small, focused commits are easier to review, revert, and merge.
- **Branching Strategy**: Use descriptive branch names (e.g., `feature/login-system`, `bugfix/header-alignment`). Never work directly on `main` or `master` without explicit instruction.
- **Synchronize Often**: Frequently pull from the remote and rebase or merge to stay up-to-date and minimize conflicts.
- **Clean History**: Use interactive rebasing (`git rebase -i`) to squash fixup commits or reorder changes before pushing to a shared branch.

## Workflow Guidelines
1. **Before Starting**: Check `git status` and `git branch` to ensure you are on the correct base.
2. **Exploration**: If unsure about a change, create a temporary branch.
3. **Staging**: Review your changes with `git diff --cached` before committing. Do not stage unrelated files or debug logs.
4. **Conflicts**: When conflicts occur, resolve them carefully. Run tests after resolution to ensure no regressions were introduced.
5. **Pushing**: Only push when a task or a significant sub-task is complete and tested.

## Safety & Reversibility
- Prefer `git stash` for temporary work-in-progress instead of "wip" commits.
- If a destructive operation is needed (e.g., `git reset --hard`), verify the target state first.
- Always keep the user informed of branch changes or significant history rewrites.
