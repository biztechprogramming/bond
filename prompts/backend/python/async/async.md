# Python Async

## When this applies
Writing async Python code (asyncio, aiohttp, aiosqlite).

## Patterns / Gotchas
- `await` in a `def` function is a SyntaxError — but calling an async function from `def` without `await` silently returns a coroutine object that never executes
- `asyncio.run()` cannot be nested — calling it inside an already-running event loop raises RuntimeError. Use `await` directly or `asyncio.create_task()`
- `aiosqlite`: wraps sync sqlite3 in a thread, not true async I/O — but still prevents event loop blocking
- `async for` on aiosqlite cursors: `async for row in cursor:` — forgetting `async` gives sync iteration that may work but defeats the purpose
- `asyncio.gather(*tasks)`: if one task raises, OTHER tasks continue running — use `return_exceptions=True` to collect all errors, or `asyncio.TaskGroup` (3.11+) for automatic cancellation
- `TaskGroup` (3.11+): if any task fails, ALL other tasks in the group are cancelled — different from `gather` behavior
- Fire-and-forget: `asyncio.create_task(coro())` — but the task can be garbage collected if you don't keep a reference! Store in a set: `background_tasks.add(task); task.add_done_callback(background_tasks.discard)`
- `asyncio.sleep(0)`: yields control to the event loop — useful for preventing starvation in CPU-bound async loops
- Sync code in async context: `await asyncio.to_thread(blocking_func, args)` (3.9+) — runs in default executor
- `async with` for context managers: forgetting `async` with an async CM silently succeeds in some cases but doesn't actually enter/exit properly
- Timeouts: `async with asyncio.timeout(5.0):` (3.11+) — raises `TimeoutError`, NOT `asyncio.TimeoutError` (they unified in 3.11)
