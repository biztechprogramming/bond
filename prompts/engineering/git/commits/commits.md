## Atomic Commits

### Commit Message Format
Use conventional commits with clear, descriptive messages:
- `feat: add minio targets to Makefile`
- `fix: resolve path translation in file handler`
- `refactor: extract prompt assembly into separate module`

### Principles
- Each commit should represent one logical change that can be understood and reverted independently.
- Commit early and often — don't accumulate a massive diff.
- The commit message should explain *what* changed and *why*, not *how*.
- If you need more than one line to describe the commit, break the work into smaller commits.
- Never mix formatting changes with behavior changes in the same commit.
