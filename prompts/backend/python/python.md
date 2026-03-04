# Python

## When this applies
Writing Python code in the Bond backend.

## Bond-Specific
- Bond uses `uv` for dependency management, NOT pip/poetry/pipenv
- `pyproject.toml` is the single source of truth for dependencies
- Install: `uv sync` or `uv sync --extra dev` for dev dependencies
- Run scripts: `uv run python -m module` or `uv run --extra dev pytest`
- Add dependency: `uv add package` (not `pip install`)

## Patterns / Gotchas
- `uv` resolves faster than pip but has different resolution strategy — if dependency conflicts, check `uv lock --check`
- `uv run` creates/reuses a virtualenv automatically — do not activate venvs manually
- Type hints: use `X | None` not `Optional[X]` (Python 3.10+ syntax, Bond uses 3.12)
- `from __future__ import annotations` makes ALL type hints strings (lazy eval) — needed for forward references but breaks runtime type checking (Pydantic validators)
- f-strings in logging: `logger.info(f"msg {var}")` evaluates even when log level is disabled — use `logger.info("msg %s", var)` for performance
- `match/case` (Python 3.10+): does NOT use `__eq__` — it uses structural pattern matching, which means custom `__eq__` overrides are ignored
- `dict | other_dict` merge (3.9+) creates a new dict — does NOT mutate either operand
- `asyncio.run()` creates a new event loop — cannot be called from within an existing async context (use `await` instead)
- `@dataclass(slots=True)` (3.10+): 15-20% faster attribute access but breaks multiple inheritance and `__dict__`
