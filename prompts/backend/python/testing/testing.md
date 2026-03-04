# Python Testing

## When this applies
Writing or running tests for Bond's Python backend.

## Bond-Specific
- Bond uses `anyio` backend, NOT `pytest-asyncio` — async tests use `asyncio.run()` wrapper pattern
- Run tests: `uv run --extra dev python -m pytest backend/tests/ -v`
- Test database: use in-memory SQLite (`":memory:"`) or temp file, never the real knowledge.db

## Patterns / Gotchas
- `pytest-asyncio` auto mode: if installed, it claims ALL async fixtures/tests — conflicts with anyio. Bond avoids this by not using pytest-asyncio
- Async test pattern without pytest-asyncio:
  ```python
  def test_async_thing():
      async def _inner():
          result = await some_async_func()
          assert result == expected
      asyncio.run(_inner())
  ```
- `httpx.AsyncClient` for testing FastAPI: `async with AsyncClient(app=app, base_url="http://test") as client:` — `base_url` is required even though it's not used
- Mocking async functions: `AsyncMock` (not `MagicMock`) — `MagicMock` returns a `MagicMock` not a coroutine, which breaks `await`
- `monkeypatch.setenv` in pytest: only works for the current process — subprocesses don't see the change
- Fixtures with `yield`: cleanup code after `yield` runs even if test fails — but NOT if the fixture setup fails
- `tmp_path` fixture: provides a `Path` object, unique per test — automatically cleaned up
- `capfd` vs `capsys`: `capfd` captures file descriptors (subprocess output), `capsys` captures Python-level stdout/stderr
- Database fixtures: always create tables in fixture setup, never rely on migration state — tests must be self-contained
