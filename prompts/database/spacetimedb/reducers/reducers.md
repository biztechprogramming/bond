# Reducers

## When this applies
Calling or defining SpacetimeDB reducers in Bond.

## Bond Reducers (bond-core-v2 module)
- `create_work_plan {id, agentId, conversationId, title}`
- `update_work_plan_status {id, status}`
- `add_work_item {id, planId, title, ordinal}`
- `update_work_item {id, status, notes?, filesChanged?}`
- `rename_work_item {id, title}`
- `delete_work_plan {id}` — deletes items first, then plan (cascading)
- `create_conversation {id, agentId, channel, title}`
- `add_conversation_message {id, conversationId, role, content}`

## Patterns / Gotchas
- Args are POSITIONAL JSON arrays when called via HTTP: `["id-val", "status-val"]` not `{"id": "...", "status": "..."}`
- Each reducer runs in its own transaction; returning error aborts all changes
- Lifecycle reducers (`client_connected`, `client_disconnected`) auto-fire on connection events — no webhook setup needed
- Reducer context provides caller identity automatically — no auth middleware layer
- Reducers cannot call other reducers directly; use shared helper functions for code reuse
- `delete_work_plan` must delete child items first — SpacetimeDB has no CASCADE constraints
- Status values are string enums validated in the reducer, not DB-level constraints
- Schedule reducers with `#[reducer(repeat = "1h")]` for periodic tasks — no external cron needed
