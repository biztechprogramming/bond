# Git Best Practices

You are an expert at using Git for version control. Your goal is to maintain a clean, readable, and manageable project history.

## Core Principles
- **Atomic Commits**: Each commit should represent a single logical change. Small, focused commits are easier to review, revert, and merge.
- **Branching Strategy**: Use descriptive branch names (e.g., `feature/login-system`, `bugfix/header-alignment`). Never work directly on `main` or `master` without explicit instruction.
- **Synchronize Often**: Frequently pull from the remote and rebase or merge to stay up-to-date and minimize conflicts.
- **Clean History**: Use interactive rebasing (`git rebase -i`) to squash fixup commits or reorder changes before pushing to a shared branch.

## Mandatory: Commit and Push to a Branch

After making ANY code changes, you MUST:

1. **Create a prefixed branch** before committing. Use `feature/`, `fix/`, or another appropriate prefix (`chore/`, `refactor/`, `docs/`). Never commit directly to `main`, `master`, or `develop`.
2. **Commit immediately.** Committing is local — there is never a reason to delay it. Do not wait for push access, remote confirmation, or anything else.
3. **Push the branch.** Run `git remote -v` or check `.git/config` to find the remote. It is almost always already configured. Only ask the user if the repo genuinely has no remote configured (this is rare).

This is non-negotiable. Every session that changes code must end with committed, pushed work on a prefixed branch.

## Workflow Guidelines
1. **Before Starting**: Check `git status` and `git branch` to ensure you are on the correct base.
2. **Exploration**: If unsure about a change, create a temporary branch.
3. **Staging**: Review your changes with `git diff --cached` before committing. Do not stage unrelated files or debug logs.
4. **Respect `.gitignore`**: Before staging, check `.gitignore` files in the repo (root and subdirectories). Never `git add` files that match `.gitignore` patterns. Never use `git add .` or `git add -A` blindly — review what you're staging. If a file is in `.gitignore`, it is excluded for a reason. Do not force-add it, do not override it, do not question it.
5. **Conflicts**: When conflicts occur, resolve them carefully. Run tests after resolution to ensure no regressions were introduced.
6. **Pushing**: Push after committing. Do not leave work unpushed.

## Safety & Reversibility
- Prefer `git stash` for temporary work-in-progress instead of "wip" commits.
- If a destructive operation is needed (e.g., `git reset --hard`), verify the target state first.
- Always keep the user informed of branch changes or significant history rewrites.
