# Commit Messages

You follow strict standards for commit messages to ensure the project history is searchable and informative.

## Standard Format
Use the **Conventional Commits** format: `<type>(<scope>): <description>`

### Types
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code (white-space, formatting, etc.)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A code change that improves performance
- `test`: Adding missing tests or correcting existing tests
- `build`: Changes that affect the build system or external dependencies
- `ci`: Changes to CI configuration files and scripts
- `chore`: Other changes that don't modify src or test files

## Guidelines
- **Subject Line**: Concise (50 chars or less), imperative mood ("Add feature" not "Added feature").
- **Body**: (Optional) Use for detailed explanations of *why* the change was made. Wrap at 72 characters.
- **Footer**: (Optional) Reference issue numbers or breaking changes.
- **No Junk**: Never include "fixed stuff", "wip", or "..." as commit messages.
- **Verification**: Always run `git diff --cached` to see exactly what is being committed before finalizing the message.
