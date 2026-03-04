# FastAPI

## When this applies
Working with Bond's FastAPI backend (port 18790).

## Patterns / Gotchas
- `@app.on_event("startup")` is DEPRECATED — use lifespan context manager: `@asynccontextmanager async def lifespan(app): yield`
- `def` route handlers run in a threadpool (not async) — FastAPI auto-wraps them. But mixing `def` and `async def` handlers in the same app means some run in threads, others in the event loop — be consistent
- `async def` handler that calls synchronous blocking code (e.g., `sqlite3`, `requests`) blocks the ENTIRE event loop — use `run_in_executor` or use `def` handler instead
- Dependency injection: `Depends()` is re-evaluated per request by default. Use `Depends(get_db, use_cache=False)` (default) — there's no built-in singleton scope
- Background tasks: `BackgroundTasks` run AFTER the response is sent but BEFORE the next request on that worker — long tasks block the worker
- For long-running background work, use a proper task queue, not `BackgroundTasks`
- `Response` model validation: `response_model` validates output AND strips extra fields — if your ORM model has fields not in the response model, they're silently dropped
- Path parameters: `/items/{item_id}` and `/items/special` — order matters! Put literal paths BEFORE parameterized paths or `special` gets captured as `item_id`
- `HTTPException` detail can be any JSON-serializable value, not just strings — useful for structured errors
- Request body + path params + query params all in one handler: FastAPI figures it out by type annotations, but Pydantic model params are always body
- `File` uploads: use `UploadFile` not `bytes` — `bytes` loads entire file into memory, `UploadFile` uses spooled temp file
