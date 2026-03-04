## Code Review Standards
When reviewing code (PRs, diffs, or files):

### What to Check
- **Correctness** — Does it do what it's supposed to? Are edge cases handled?
- **Readability** — Can the next developer understand this without explanation?
- **Testing** — Are there tests? Do they cover the important paths?
- **Security** — Input validation, auth checks, no hardcoded secrets.
- **Performance** — Any obvious N+1 queries, unnecessary loops, or memory issues?
- **Conventions** — Does it follow the project's existing patterns?

### How to Review
- Start with `git diff --stat` to understand the scope.
- Read the diff in logical order (models -> logic -> tests -> config).
- Comment on specific lines with concrete suggestions, not vague criticism.
- Distinguish between "must fix" (blocking) and "consider" (nice to have).
- If the change is good, say so — don't only point out problems.

### Approval
- Approve if the code is correct, tested, and maintainable.
- Request changes if there are blocking issues — be specific about what needs to change.
- If you're unsure about a domain-specific decision, flag it as a question rather than a blocker.
