# Python

Best practices for Python development, focusing on FastAPI and modern async patterns.

## Standards
- **Python 3.10+**: Use modern features like structural pattern matching and `|` for union types.
- **Type Hinting**: Use strict type hints everywhere. Use `mypy` or `pyright` for static analysis.
- **Pydantic v2**: Use for data validation and settings. Prefer `BaseModel` and `Field`.
- **Async/Await**: Use `asyncio` for I/O-bound tasks. Avoid blocking the event loop with synchronous calls.

## FastAPI Patterns
- **Dependency Injection**: Use `Depends()` for shared logic (auth, DB sessions).
- **Router Organization**: Use `APIRouter` to split the application into logical modules.
- **Exception Handlers**: Use custom exception handlers to return consistent JSON error responses.
- **Background Tasks**: Use FastAPI's `BackgroundTasks` for simple async work, or Celery for complex jobs.

## Code Style
- **PEP 8**: Follow standard Python style guidelines. Use `black` or `ruff` for formatting.
- **Docstrings**: Use Google or NumPy style docstrings for public functions and classes.
- **List Comprehensions**: Use for simple transformations; avoid nesting more than two levels deep.
- **f-strings**: Use for all string formatting.

## Testing
- **Pytest**: Use as the primary testing framework.
- **Fixtures**: Use fixtures for setup/teardown and dependency injection.
- **Mocking**: Use `unittest.mock` or `pytest-mock` to isolate units.
- **Coverage**: Aim for high coverage, but focus on critical business logic and edge cases.
