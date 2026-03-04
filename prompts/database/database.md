# Database

## When this applies
Any database design, query writing, or data architecture decisions.

## Patterns / Gotchas
- Bond uses multiple database backends: SQLite (primary knowledge DB), SpacetimeDB (real-time state), per-agent SQLite DBs
- Primary DB path: `~/.bond/data/knowledge.db` — NOT `data/knowledge.db` in repo (that one is stale/empty)
- Per-agent DBs: `data/agents/<agent_id>/agent.db` — runtime data for containers
- Always specify connection timeout and busy_timeout for SQLite in concurrent environments
- Never use ORM migrations against SpacetimeDB — it has its own module publish system
