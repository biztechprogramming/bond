# FastAPI Routing

## When this applies
Defining API routes and routers in FastAPI.

## Patterns / Gotchas
- Router inclusion order matters: `app.include_router(specific_router)` before `app.include_router(generic_router)` — first match wins for ambiguous paths
- Path operation order within a single router also matters: `/users/me` must be defined BEFORE `/users/{user_id}` or "me" is treated as a user_id
- `APIRouter(prefix="/api/v1")` — prefix must NOT end with `/` or you get double slashes
- `tags=["items"]` on router groups all routes in OpenAPI docs — useful for organization but no runtime effect
- Nested routers: `parent_router.include_router(child_router, prefix="/child")` — prefixes concatenate
- `response_model_exclude_unset=True` on route decorator omits fields not explicitly set — different from `exclude_none` which omits None values
- Redirect trailing slashes: FastAPI does 307 redirect by default for `/path` → `/path/` — set `redirect_slashes=False` on the app to disable
- WebSocket routes don't support `Depends()` the same way — must extract dependencies manually in the handler
- `status_code=201` on POST routes — FastAPI defaults to 200 for all methods; 201 must be explicit
- Multiple response models: use `responses={200: {"model": Success}, 404: {"model": NotFound}}` — only `response_model` validates, `responses` is just documentation
