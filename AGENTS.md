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

### SpacetimeDB — CRITICAL
- **NEVER run `spacetime publish` with `--delete-data`**. This wipes the entire database. There is no undo. If a migration requires schema changes, work with the human to plan it. No exceptions.

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

## Project Structure Conventions
- **Design docs:** `docs/design/NNN-slug.md` (zero-padded 3-digit prefix)
- **Prompts:** `prompts/` directory tree organized by topic
- **Tests:** `backend/tests/test_*.py` and `gateway/src/__tests__/*.test.ts`

## Repo Autonomy

Agents have their own writable clone of the Bond repo at `/bond`. All changes flow through pull requests via the `repo_pr` tool.

### Workflow
1. Agent identifies an improvement (new tool, prompt fix, bug)
2. Agent calls `repo_pr` with branch name, files, commit message, and PR details
3. PR is created on GitHub for human review
4. On merge to `main`, the Gateway webhook triggers `/reload` on all workers

### Dynamic tools
Agent-created tools land in `backend/app/agent/tools/dynamic/`. Each file must export `SCHEMA` and `execute()` — see `dynamic/README.md` for the contract.
