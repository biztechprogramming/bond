# AGENTS.md — Governance Rules for Bond Repository

This file defines what AI agents can and cannot modify when working on the Bond codebase.

## What agents CAN change

### Prompt fragments
- Add new leaf-level `.md` files under `prompts/` following the naming convention (`dirname/dirname.md`)
- Edit the content of existing prompt fragment files
- Add new leaf directories with a matching `.md` fragment inside

### Dynamic tools
- Add new tool definitions in `backend/app/agent/tools/definitions.py`
- Add new tool handlers following existing patterns

### Documentation
- Edit and improve documentation files (`docs/`, `*.md`)
- Add new design docs under `docs/design/`

### Tests
- Add new test files under `backend/tests/`
- Edit existing tests to cover new functionality

### Frontend
- Components, pages, and styles under `frontend/`

## What agents must NOT change

### Core infrastructure (human-only)
- `backend/app/worker.py` — agent loop, LLM orchestration
- `backend/app/agent/tools/native.py` — native tool handlers
- `gateway/src/server.ts` — gateway entrypoint
- `migrations/` — database migration files (create new ones only via `bun run migrate:up`)
- `backend/app/core/` — core configuration, vault, database setup
- `scripts/migrate.js`, `scripts/migrate.sh` — migration infrastructure

### Deployment & config
- `docker-compose*.yml` — container orchestration
- `Makefile` — build targets
- `.env*` files — environment configuration

## Prompt hierarchy governance

### Agents may:
- **Add new leaf fragments** — create a new directory + matching `.md` file at any existing leaf position. Submit via PR.
- **Edit existing fragment content** — improve wording, add patterns, fix errors in any `.md` under `prompts/`. Submit via PR.
- **Add new intermediate nodes** — requires a clear explanation in the PR description of why the new category is needed and what leaf nodes it will contain.

### Human-only operations:
- **Renaming or moving categories** — changing the tree structure affects all agents' manifest and context loading. This requires human review.
- **Deleting categories** — removing a category may break agents that reference it. Human decision only.
- **Modifying `universal/` structure** — universal fragments load for every task. Changes here affect all agent behavior globally.
