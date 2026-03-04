## Must Compile — Zero Warnings, Zero Errors

Code changes are not complete until they compile cleanly. No exceptions.

### Requirements

Before any code change can be considered done:

1. **Build/Compile** — Run the project's build command. Every file you touched (and every file that depends on it) must compile without errors.
2. **Type Checking** — Run the type checker if the project has one (tsc, mypy, pyright, etc.). Zero type errors.
3. **Linter** — Run the project's linter (eslint, ruff, clippy, etc.). Zero warnings, zero errors. Do not disable lint rules to make warnings go away — fix the underlying issue.
4. **Static Analysis** — If the project uses static analysis tools (SonarQube rules, roslyn analyzers, etc.), those must pass too.

### Process

- After every set of code changes, run ALL applicable checks before moving on.
- If you get errors or warnings, fix them immediately — do not defer them.
- If fixing a warning would require a change outside your current scope, document it and get approval before proceeding. Do not suppress it silently.
- Re-run checks after fixing issues to confirm they're actually resolved. Don't assume.

### What Counts as "Clean"

- **0 errors** — non-negotiable
- **0 warnings** — treat warnings as errors. A warning is a bug you haven't hit yet.
- **0 new suppression comments** — no `// @ts-ignore`, `# type: ignore`, `// eslint-disable`, `#pragma warning disable`, or `[SuppressMessage]` unless explicitly approved
- **Existing suppressions** — don't remove them (that's a separate task), but don't add new ones

### If You Don't Know the Build Command

- Check `package.json` scripts, `Makefile`, `Cargo.toml`, `pyproject.toml`, `.csproj`, or `build.gradle`
- Look for CI config (`.github/workflows/`, `Jenkinsfile`, `.gitlab-ci.yml`) — it will have the exact commands
- Ask rather than guess

### Failure Is Not an Option

If the code doesn't compile clean, you are not done. Period. Do not report success. Do not move to the next task. Fix it first.
