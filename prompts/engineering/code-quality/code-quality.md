## Code Quality

Standards that apply to all code changes, regardless of language or framework:

- **Correctness over cleverness** — Code that works and is easy to understand beats elegant code that's hard to debug.
- **Consistent style** — Follow the project's existing conventions. Don't introduce a new style mid-codebase.
- **Meaningful names** — Variables, functions, and classes should describe what they do, not how they do it.
- **Small functions** — Each function does one thing. If you need a comment to explain what a block does, extract it.
- **Handle errors explicitly** — Don't swallow exceptions. Don't return null when you should throw. Be intentional.
- **No dead code** — Remove unused imports, commented-out blocks, and unreachable branches.
