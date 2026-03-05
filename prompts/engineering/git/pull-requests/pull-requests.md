# Pull Requests

You are responsible for preparing high-quality pull requests that make the reviewer's job easy.

## PR Structure
1. **Title**: Clear and descriptive, following the same conventions as commit messages.
2. **Summary**: A brief overview of what this PR accomplishes.
3. **Key Changes**: A bulleted list of the most important modifications.
4. **Context/Motivation**: Why are these changes necessary? Link to relevant issues.
5. **Testing**: Describe how the changes were verified (e.g., "Ran unit tests", "Manual verification in sandbox").
6. **Self-Review**: Before submitting, perform a final `git diff` against the target branch to catch debug code or formatting issues.

## Best Practices
- **Keep it Small**: Prefer multiple small PRs over one massive PR.
- **Draft PRs**: Use Draft status if the work is still in progress but you want early feedback.
- **Clean History**: Ensure the branch is rebased against the target branch and has a clean commit history before requesting review.
- **No Noise**: Ensure no unnecessary files (logs, temporary build artifacts) are included.
