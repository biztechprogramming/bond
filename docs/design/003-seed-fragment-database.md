# Design: Seed the Fragment Database

**Author:** Developer Agent  
**Date:** 2026-03-08  
**Status:** Draft

## Problem

The fragment selection pipeline (`_select_relevant_fragments` in `context_pipeline.py`) supports three tiers — **core**, **standard**, and **specialized** — with keyword triggers, LLM-based selection, and budget enforcement. But the fragment database is empty. Zero DB-sourced fragments have appeared in any Langfuse trace.

Meanwhile, 56 prompt files sit on disk under `prompts/`, only reachable if the agent manually calls `load_context` (which it never does — see Design 002). The best of these should live in the DB where the selection pipeline can inject them automatically.

## Fragment Tier Definitions

| Tier | Behavior | When to use |
|------|----------|-------------|
| **core** | Always injected, every turn. No selection needed. | Safety, base behavior, non-negotiable rules |
| **standard** | Injected when keyword triggers match OR the utility model selects them | Technology-specific best practices, workflow patterns |
| **specialized** | Only injected via keyword triggers, never offered to the LLM selector | Narrow, high-token-cost references (e.g., full API docs) |

## Proposed Fragments

### Core Tier (always-on)

These are critical enough that skipping them risks real harm. They should fire on every turn.

| Name | Source | ~Tokens | Rationale |
|------|--------|---------|-----------|
| `safety-rules` | `prompts/universal/safety.md` | 215 | Non-negotiable. Never skip safety. |
| `error-handling` | `prompts/universal/error-handling.md` | 195 | Prevents retry loops and wasted iterations |
| `must-compile` | `prompts/engineering/code-quality/must-compile.md` | 325 | Prevents shipping broken code — the #1 quality gate |

**Note:** `safety.md` and `error-handling.md` are currently universal fragments (always injected). Moving them to `core` tier DB fragments gives the same behavior but brings them under the fragment management system — versioning, A/B testing, per-agent attachment.

`must-compile` is currently a disk-only file the agent never sees unless it calls `load_context("engineering.code-quality.must-compile")`. Making it core ensures the agent always checks builds.

**Core tier total: ~735 tokens**

### Standard Tier (keyword-triggered + LLM-selectable)

These should fire when the task involves their technology but skip on unrelated turns.

| Name | Source | ~Tokens | Triggers |
|------|--------|---------|----------|
| `work-planning` | `prompts/universal/work-planning.md` | 417 | build, implement, create, refactor, migrate, fix, plan, design, deploy |
| `progress-tracking` | `prompts/universal/progress-tracking.md` | 428 | build, implement, create, refactor, fix, deploy, test, review, audit |
| `git-workflow` | `prompts/engineering/git/git.md` | 237 | git, commit, push, branch, merge, rebase, PR, pull request |
| `commit-messages` | `prompts/engineering/git/commits/commits.md` | 197 | commit, push, commit message |
| `python-fastapi` | `prompts/backend/python/fastapi/fastapi.md` | 259 | fastapi, endpoint, route, API, backend |
| `python-testing` | `prompts/backend/python/testing/testing.md` | 227 | test, pytest, unittest, coverage, mock |
| `spacetimedb` | `prompts/database/spacetimedb/spacetimedb.md` | 231 | spacetimedb, stdb, reducer, spacetime |
| `spacetimedb-reducers` | `prompts/database/spacetimedb/reducers/reducers.md` | 181 | reducer, spacetimedb reducer, stdb reducer |
| `docker-sandbox` | `prompts/infrastructure/docker/sandbox/sandbox.md` | 270 | sandbox, container, docker |
| `react-patterns` | `prompts/frontend/react/react.md` | 246 | react, component, hook, jsx, tsx, frontend |
| `code-review` | `prompts/engineering/code-quality/code-review/code-review.md` | 193 | review, PR review, code review |
| `bugfix` | `prompts/engineering/code-quality/bugfix/bugfix.md` | 423 | bug, fix, debug, error, crash, broken |
| `file-operations` | `prompts/engineering/file-operations/file-operations.md` | 189 | file, read, write, edit, create file |

**Standard tier total: ~3,498 tokens (but only a subset fires per turn)**

### Specialized Tier (keyword-only, not offered to LLM selector)

These are narrow enough that the utility model shouldn't waste time evaluating them. They only fire on exact keyword matches.

| Name | Source | ~Tokens | Triggers |
|------|--------|---------|----------|
| `spacetimedb-sql` | `prompts/database/spacetimedb/sql/sql.md` | 210 | spacetimedb sql, stdb query |
| `spacetimedb-typescript-sdk` | `prompts/database/spacetimedb/typescript-sdk/typescript-sdk.md` | 197 | spacetimedb sdk, stdb typescript, spacetimedb client |
| `sqlite-wal` | `prompts/database/relational/sqlite/wal-mode/wal-mode.md` | 195 | wal mode, sqlite wal, wal-mode |
| `jwt-auth` | `prompts/security/auth/jwt/jwt.md` | 277 | jwt, json web token, bearer token |
| `nextjs-app-router` | `prompts/frontend/nextjs/app-router/app-router.md` | 243 | app router, next.js routing, nextjs route |
| `docker-compose` | `prompts/infrastructure/docker/compose/compose.md` | 305 | docker-compose, compose file, docker compose |
| `postgresql-indexing` | `prompts/database/relational/postgresql/indexing/indexing.md` | 254 | index, postgresql index, pg index |
| `zero-downtime-migrations` | `prompts/database/relational/migrations/zero-downtime/zero-downtime.md` | 212 | zero downtime, migration, schema migration |

**Specialized tier total: ~1,893 tokens (rarely more than 1 fires per turn)**

### Not Seeded (leave on disk for `load_context`)

These are either too generic (just intro/category headers) or too niche to justify DB overhead. They stay as disk files for manual `load_context` calls.

| File | Reason |
|------|--------|
| `backend/backend.md` | Generic intro, no actionable patterns |
| `backend/csharp/*` | Bond doesn't use C# — remove or archive |
| `backend/typescript/typescript.md` | Generic, covered by other fragments |
| `database/database.md` | Category header, no content |
| `database/relational/relational.md` | Category header (~115 words) |
| `engineering/engineering.md` | Category header (~108 words) |
| `engineering/planning/planning.md` | Category header (~97 words) |
| `messaging/*` | Bond doesn't currently use Kafka or Azure Service Bus |
| `security/security.md` | Category header (~117 words) |
| `frontend/frontend.md` | Category header |

## Token Budget Analysis

The fragment selection pipeline has a budget of **2,500 tokens** (`FRAGMENT_TOKEN_BUDGET` in `context_pipeline.py`).

Typical turn scenarios:

| Scenario | Core | Triggered | LLM-selected | Total |
|----------|------|-----------|-------------|-------|
| Simple Q&A | 735 | 0 | 0 | **735** |
| "Fix this Python bug" | 735 | bugfix(423) + python-testing(227) | 0 | **1,385** |
| "Add a SpacetimeDB reducer" | 735 | spacetimedb(231) + reducers(181) | work-planning(417) | **1,564** |
| "Review this PR" | 735 | code-review(193) + git-workflow(237) + commit-messages(197) | progress-tracking(428) | **1,790** |
| Worst case (many triggers) | 735 | ~1,200 | ~500 | **2,435** (under budget) |

The budget enforcement layer will drop lowest-rank non-core fragments if we ever exceed 2,500.

## Implementation

### Seed Script

Create `backend/app/seed_fragments.py`:

```python
"""Seed the prompt fragment DB from disk files.

Idempotent — safe to run on every startup. Skips fragments that already exist
(matched by name). Updates content if the disk file has changed.
"""

import json
from pathlib import Path
from ulid import ULID


FRAGMENTS = [
    # Core tier
    {
        "name": "safety-rules",
        "display_name": "Safety Rules",
        "category": "safety",
        "tier": "core",
        "source_file": "universal/safety.md",
        "task_triggers": [],
    },
    {
        "name": "error-handling",
        "display_name": "Error Handling",
        "category": "behavior",
        "tier": "core",
        "source_file": "universal/error-handling.md",
        "task_triggers": [],
    },
    {
        "name": "must-compile",
        "display_name": "Must Compile",
        "category": "behavior",
        "tier": "core",
        "source_file": "engineering/code-quality/must-compile/must-compile.md",
        "task_triggers": [],
    },
    # Standard tier
    {
        "name": "work-planning",
        "display_name": "Work Planning",
        "category": "behavior",
        "tier": "standard",
        "source_file": "universal/work-planning.md",
        "task_triggers": ["build", "implement", "create", "refactor",
                          "migrate", "fix", "plan", "design", "deploy"],
    },
    # ... etc for all standard/specialized fragments
]


async def seed_fragments(db, prompts_dir: Path, agent_ids: list[str]):
    """Seed fragments from disk and attach to agents."""
    for frag_def in FRAGMENTS:
        source = prompts_dir / frag_def["source_file"]
        if not source.exists():
            continue

        content = source.read_text().strip()
        token_estimate = len(content) // 4

        # Check if fragment already exists
        cursor = await db.execute(
            "SELECT id, content FROM prompt_fragments WHERE name = ?",
            (frag_def["name"],),
        )
        row = await cursor.fetchone()

        if row:
            frag_id, existing_content = row
            if existing_content != content:
                # Update content if disk file changed
                await db.execute(
                    "UPDATE prompt_fragments SET content = ?, token_estimate = ? WHERE id = ?",
                    (content, token_estimate, frag_id),
                )
        else:
            frag_id = str(ULID())
            await db.execute(
                "INSERT INTO prompt_fragments "
                "(id, name, display_name, category, content, tier, "
                "task_triggers, token_estimate, is_system) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    frag_id, frag_def["name"], frag_def["display_name"],
                    frag_def["category"], content, frag_def["tier"],
                    json.dumps(frag_def["task_triggers"]), token_estimate,
                ),
            )

            # Attach to all specified agents
            for agent_id in agent_ids:
                apf_id = str(ULID())
                await db.execute(
                    "INSERT OR IGNORE INTO agent_prompt_fragments "
                    "(id, agent_id, fragment_id, rank, enabled) "
                    "VALUES (?, ?, ?, 0, 1)",
                    (apf_id, agent_id, frag_id),
                )

    await db.commit()
```

### Call on Worker Startup

In `worker.py` startup, after DB init:

```python
from backend.app.seed_fragments import seed_fragments
await seed_fragments(_state.agent_db, _prompts_dir, [_state.agent_id])
```

### Deduplicate Universal Fragments

Once `safety-rules` and `error-handling` are in the DB as `core` tier, remove them from `prompts/universal/` to avoid double injection. The remaining universal fragments (`communication`, `memory-guidance`, `proactive-workflow`, `reasoning`, `tool-efficiency`) stay as-is unless moved to DB in a follow-up.

### Migration Path

**Phase 1 (this change):**
- Seed core + standard + specialized fragments into DB
- Remove `safety.md` and `error-handling.md` from `prompts/universal/`
- Remove `work-planning.md` and `progress-tracking.md` from `prompts/universal/` (per Design 001)
- Verify via Langfuse that DB fragments appear in traces

**Phase 2 (follow-up):**
- Move remaining universal fragments to DB as core tier
- Retire the `prompts/universal/` directory entirely
- All prompt management through the DB + dashboard

**Phase 3 (follow-up):**
- A/B test fragment content via Langfuse
- Use the prompt improvement API (`POST /prompts/generate/improve-prompt`) to iterate on underperforming fragments
- Score traces in Langfuse to correlate fragment combinations with output quality

## Verification

1. `uv run --extra dev python -m pytest` — all existing tests pass
2. Start a worker, check logs for "Seeded N fragments"
3. Send a message mentioning "spacetimedb reducer" — Langfuse should show:
   - `fragment_count` includes DB fragments
   - `fragments_injected` has entries with `source: "db"` and `reason: "keyword_trigger"`
4. Send a simple "hello" — Langfuse should show only core fragments from DB
5. Check fragment count via API: `GET /api/v1/prompts/fragments` returns seeded fragments

## Risks

1. **Double injection** — a fragment exists both in DB and on disk, gets loaded twice
   - **Mitigation:** Remove disk files for anything seeded to DB. The seed script's `source_file` field documents the origin for traceability.

2. **Stale triggers** — keyword lists don't match actual usage patterns
   - **Mitigation:** Monitor Langfuse `_selection_reason` distribution. If `llm_selected` is high for a fragment, its triggers need expanding.

3. **Token budget exceeded** — too many triggers match on a complex turn
   - **Mitigation:** The budget enforcement layer already handles this (drops lowest-rank non-core fragments). Monitor `context_compression_log` for budget drops.

4. **Startup latency** — seeding adds time to worker boot
   - **Mitigation:** The seed is idempotent and skips existing fragments. After first run, it's a no-op. Expected overhead: <100ms.

## Files Changed

- `backend/app/seed_fragments.py` — new file
- `backend/app/worker.py` — call seed on startup
- `prompts/universal/safety.md` — deleted (moved to DB)
- `prompts/universal/error-handling.md` — deleted (moved to DB)
- `prompts/universal/work-planning.md` — deleted (moved to DB, per Design 001)
- `prompts/universal/progress-tracking.md` — deleted (moved to DB, per Design 001)
- `tests/test_prompt_hierarchy.py` — update expected counts
- `tests/test_langfuse_audit.py` — update to verify DB fragment presence
