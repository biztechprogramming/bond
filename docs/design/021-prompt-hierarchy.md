# Design Doc 021 — Prompt Hierarchy & Agent-Initiated Context Loading

**Status:** Draft  
**Date:** 2026-03-04  
**Author:** Developer Agent

---

## Overview

Bond agents should load exactly the context they need for a given task — no more, no less. This doc defines a hierarchical prompt file system where an agent picks one leaf-level category per task, automatically inheriting all ancestor fragments up the tree. The result is a small, focused context that scales with specificity rather than ballooning with agent configuration.

---

## Core Idea

Prompts are organized as a filesystem tree. Choosing a leaf node gives you that fragment plus every ancestor fragment — one per level of depth. The deeper you go, the more specialized and complete your context becomes, but the total count stays bounded by tree depth (max ~6 fragments including universal).

```
Pick: database/relational/postgresql/query-optimization

Fragments loaded:
  1. universal/communication.md
  2. database/database.md
  3. database/relational/relational.md
  4. database/relational/postgresql/postgresql.md
  5. database/relational/postgresql/query-optimization/query-optimization.md
```

Five focused fragments. Nothing irrelevant.

---

## Selection Rules

- **Universal level** is implicit — always loaded, never chosen
- **Agent picks exactly one leaf node** per task or thread
- **No multi-selection** — if a task spans two domains, spawn two threads, each with their own single selection
- **Ancestors are automatic** — the agent never explicitly requests parent fragments; they load by inheritance
- **Max depth: 6 levels** (including universal) — beyond this, categories become too narrow to maintain

---

## Handling Cross-Domain Tasks

For tasks that genuinely span two domains (e.g. "write a React component that queries PostgreSQL"), the agent spawns focused sub-threads rather than loading multiple context trees:

```
Orchestrator receives: "write a React component that queries PostgreSQL"
  │
  ├── Thread A: context = frontend/react/hooks
  │     Gets: [universal, frontend, react, hooks]
  │     Task: design the component interface and hooks
  │
  └── Thread B: context = database/relational/postgresql/query-optimization
        Gets: [universal, database, relational, postgresql, query-optimization]
        Task: write the query and data access layer
```

Each thread is narrow, focused, and uses minimal context.

---

## Solving Unknown Unknowns

The agent picks by category name alone — it never reads fragment content before selecting. To prevent the agent from missing context it doesn't know exists, the system prompt includes a compact category manifest at the start of every conversation:

```
Available context categories:
  engineering/git, engineering/git/commits, engineering/git/pull-requests,
  engineering/testing, engineering/testing/unit, engineering/testing/integration,
  database, database/relational, database/relational/postgresql,
  database/relational/postgresql/query-optimization,
  database/relational/postgresql/indexing,
  database/relational/sqlite, database/relational/migrations,
  database/relational/migrations/zero-downtime,
  backend/python, backend/python/fastapi, backend/python/async,
  frontend/react, frontend/react/hooks, frontend/nextjs,
  infrastructure/docker, security/auth, security/auth/jwt,
  ...
```

This is tiny (just names, no content) and gives the agent full awareness of what's available.

---

## Folder Structure

```
prompts/
│
├── universal/                          ← Level 1: EVERY agent, EVERY task
│   ├── communication.md                  Response style, tone, format
│   ├── safety.md                         Hard limits, ethical guidelines
│   └── reasoning.md                      How to think through problems
│
├── engineering/                        ← Level 2: All software work
│   ├── engineering.md                    Principles: YAGNI, DRY, SOLID
│   │
│   ├── git/                            ← Level 3
│   │   ├── git.md                        Branching, hygiene, workflow
│   │   ├── commits/                    ← Level 4
│   │   │   └── commits.md                Atomic commits, message format
│   │   ├── pull-requests/
│   │   │   └── pull-requests.md          PR descriptions, review etiquette
│   │   └── conflict-resolution/
│   │       └── conflict-resolution.md
│   │
│   ├── testing/
│   │   ├── testing.md                    Philosophy, coverage expectations
│   │   ├── unit/
│   │   │   └── unit.md
│   │   ├── integration/
│   │   │   └── integration.md
│   │   └── e2e/
│   │       └── e2e.md
│   │
│   ├── code-quality/
│   │   ├── code-quality.md
│   │   ├── refactoring/
│   │   │   └── refactoring.md
│   │   └── code-review/
│   │       └── code-review.md
│   │
│   └── documentation/
│       ├── documentation.md
│       └── api-docs/
│           └── api-docs.md
│
├── database/                           ← Level 2: All database work
│   ├── database.md                       General: transactions, ACID, safety
│   │
│   ├── relational/                     ← Level 3
│   │   ├── relational.md                 SQL fundamentals, normalization
│   │   │
│   │   ├── postgresql/                 ← Level 4
│   │   │   ├── postgresql.md             PG-specific patterns, psycopg
│   │   │   ├── query-optimization/     ← Level 5
│   │   │   │   └── query-optimization.md
│   │   │   ├── indexing/
│   │   │   │   ├── indexing.md
│   │   │   │   └── partial-indexes/    ← Level 6 (most granular)
│   │   │   │       └── partial-indexes.md
│   │   │   └── performance/
│   │   │       └── performance.md
│   │   │
│   │   ├── sqlite/
│   │   │   ├── sqlite.md
│   │   │   ├── wal-mode/
│   │   │   │   └── wal-mode.md
│   │   │   └── migrations/
│   │   │       └── migrations.md
│   │   │
│   │   └── migrations/                 ← Level 4 (cross-db concern)
│   │       ├── migrations.md
│   │       ├── zero-downtime/
│   │       │   └── zero-downtime.md
│   │       └── rollback/
│   │           └── rollback.md
│   │
│   ├── vector/
│   │   ├── vector.md
│   │   └── embeddings/
│   │       └── embeddings.md
│   │
│   └── graph/
│       └── graph.md
│
├── backend/                            ← Level 2
│   ├── backend.md                        API design, error handling patterns
│   │
│   ├── python/                         ← Level 3
│   │   ├── python.md                     Style, idioms, type hints
│   │   ├── fastapi/                    ← Level 4
│   │   │   ├── fastapi.md
│   │   │   ├── routing/
│   │   │   │   └── routing.md
│   │   │   ├── dependencies/
│   │   │   │   └── dependencies.md
│   │   │   └── middleware/
│   │   │       └── middleware.md
│   │   ├── async/
│   │   │   ├── async.md
│   │   │   └── concurrency/
│   │   │       └── concurrency.md
│   │   └── pydantic/
│   │       └── pydantic.md
│   │
│   ├── typescript/                     ← Level 3
│   │   ├── typescript.md
│   │   ├── node/
│   │   │   ├── node.md
│   │   │   └── streams/
│   │   │       └── streams.md
│   │   └── express/
│   │       └── express.md
│   │
│   └── api-design/
│       ├── api-design.md
│       ├── rest/
│       │   └── rest.md
│       └── websockets/
│           └── websockets.md
│
├── frontend/                           ← Level 2
│   ├── frontend.md                       Accessibility, performance baseline
│   │
│   ├── react/                          ← Level 3
│   │   ├── react.md
│   │   ├── hooks/
│   │   │   └── hooks.md
│   │   ├── state-management/
│   │   │   ├── state-management.md
│   │   │   └── zustand/
│   │   │       └── zustand.md
│   │   └── performance/
│   │       └── performance.md
│   │
│   ├── nextjs/                         ← Level 3
│   │   ├── nextjs.md
│   │   ├── app-router/
│   │   │   └── app-router.md
│   │   └── server-components/
│   │       └── server-components.md
│   │
│   └── styling/
│       ├── styling.md
│       └── tailwind/
│           └── tailwind.md
│
├── infrastructure/                     ← Level 2
│   ├── infrastructure.md
│   │
│   ├── docker/                         ← Level 3
│   │   ├── docker.md
│   │   ├── compose/
│   │   │   └── compose.md
│   │   └── security/
│   │       └── container-security.md
│   │
│   ├── ci-cd/
│   │   ├── ci-cd.md
│   │   └── github-actions/
│   │       └── github-actions.md
│   │
│   └── monitoring/
│       ├── monitoring.md
│       └── logging/
│           └── logging.md
│
└── security/                           ← Level 2
    ├── security.md                       Threat modelling, secure defaults
    ├── auth/
    │   ├── auth.md
    │   ├── jwt/
    │   │   └── jwt.md
    │   └── oauth/
    │       └── oauth.md
    ├── encryption/
    │   └── encryption.md
    └── secrets/
        └── secrets.md
```

---

## Fragment Content Guidelines

Each fragment should follow a consistent format so the agent can read and apply them quickly:

```markdown
# [Category Name]

## When this applies
One sentence describing what tasks this fragment is relevant for.

## Core principles
- Bullet points only
- Actionable, not philosophical
- 5–10 points maximum

## Patterns to prefer
Short examples or named patterns.

## Patterns to avoid
Anti-patterns specific to this domain.
```

**Length target:** 150–300 tokens per fragment. At 5 fragments loaded, that's 750–1500 tokens of context — well within budget.

---

## Runtime Implementation

### The `load_context` Tool

```python
# Tool: load_context
# Called by the agent at the start of a task (or mid-task if needs evolve)

SCHEMA = {
    "name": "load_context",
    "description": "Load prompt context for the current task. Pick the most specific relevant category. Call once per task or thread. Available categories are listed in your system prompt.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Dot-separated category path, e.g. 'database.relational.postgresql.query-optimization'"
            }
        },
        "required": ["category"]
    }
}

def execute(category: str) -> str:
    parts = category.replace(".", "/").split("/")
    fragments = []

    # Always load universal
    for f in sorted(Path("prompts/universal").glob("*.md")):
        fragments.append(f.read_text())

    # Walk the path, loading one fragment per level
    current = Path("prompts")
    for part in parts:
        current = current / part
        fragment_file = current / f"{part}.md"
        if fragment_file.exists():
            fragments.append(fragment_file.read_text())

    return "\n\n---\n\n".join(fragments)
```

### System Prompt Manifest (auto-generated)

At worker startup, Bond scans the prompts directory and injects a compact manifest into the system prompt:

```python
def generate_manifest(prompts_dir: Path) -> str:
    categories = []
    for md_file in sorted(prompts_dir.rglob("*.md")):
        # Only include the named fragment (e.g. postgresql/postgresql.md)
        if md_file.stem == md_file.parent.name:
            rel = md_file.parent.relative_to(prompts_dir)
            if str(rel) != "universal":
                categories.append(str(rel).replace("/", "."))
    return "Available context categories:\n  " + ", ".join(categories)
```

### Hot Reload

When a PR merges that touches `prompts/`, the Gateway webhook triggers a manifest regeneration. Running workers reload the manifest on their next turn — no restart required.

---

## Adding New Prompts

1. Create the directory and fragment file following the naming convention
2. Commit to a feature branch, open a PR
3. Once merged, the manifest auto-updates — the category is immediately available to all agents

No database changes. No migrations. No config updates. Just a file in the right place.

---

## Design Decisions & Rationale

**Why exactly one category?**  
Forces the agent to make a judgment call about what the task fundamentally *is*. A task that needs two categories is a task that should be two threads. Single selection keeps context small and intentional.

**Why filesystem over database?**  
Prompts are code. They benefit from version control, diffs, code review, and the ability to revert. A database row has none of that. A markdown file in git has all of it.

**Why not semantic retrieval?**  
Semantic retrieval (embedding similarity) is less reliable for instructions than for facts — the semantic distance between a task description and an instruction fragment is inconsistent. The agent understands its own task better than any embedding model. Named categories give the agent direct control.

**Why not utility LLM pre-selection?**  
The utility LLM already exists and selects tools. Extending it to also select prompts adds latency and another round-trip before the first useful turn. Agent-initiated loading defers this cost — simple tasks that don't need domain context pay nothing.

**Why inherit ancestors?**  
A PostgreSQL query optimization task implicitly requires knowing general SQL principles and general database safety rules. Inheritance encodes this domain knowledge in the tree structure rather than duplicating it in every leaf fragment.

---

## Migration Plan

| Step | What |
|------|------|
| 1 | Create `backend/app/agent/prompts/` directory structure |
| 2 | Write initial fragments for `universal/` and top-level domains |
| 3 | Implement `load_context` tool + `generate_manifest()` |
| 4 | Inject manifest into worker system prompt at startup |
| 5 | Migrate existing DB-stored prompts to files |
| 6 | Remove prompt storage from DB; update worker to use filesystem only |
| 7 | Add Gateway webhook handler to trigger manifest regeneration on merge |

---

## Orchestrator Integration

Bond already has a parallel orchestration system (Design Doc 019) — an Architect phase that decomposes tasks into a `ParallelWorkPlan` using Instructor/Pydantic, dispatching tool calls in parallel batches via `asyncio.gather`. The prompt hierarchy plugs into this as a first-class field on each subtask.

### Why the Orchestrator Solves Reliable Context Assignment

Agents cannot be relied upon to self-identify mid-task that they need a different context and spawn a subtask. The path of least resistance is always to attempt the work with what they have. The orchestrator solves this by making context assignment **upfront and intentional** — the Architect decomposes the task and assigns the right context category to each subtask before any work begins.

### Schema Extension

The existing `ToolInvocation` model in 019 gains a `context_category` field:

```python
class ToolInvocation(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    model_override: Optional[str] = None
    description: str
    context_category: Optional[str] = None  # e.g. "database.relational.postgresql.query-optimization"
```

### Execution Flow

```
Orchestrator receives: "write a report class that queries monthly revenue by region"

Architect decomposes:
  Subtask A:
    description: "write the Python class structure and data interface"
    context_category: "backend.python.fastapi"
  Subtask B:
    description: "write the optimized PostgreSQL query for monthly revenue by region"
    context_category: "database.relational.postgresql.query-optimization"

Worker picks up Subtask A:
  → mandatory: load_context("backend.python.fastapi")
  → gets [universal, backend, python, fastapi]
  → writes the class skeleton and interface
  → returns structured result

Worker picks up Subtask B:
  → mandatory: load_context("database.relational.postgresql.query-optimization")
  → gets [universal, database, relational, postgresql, query-optimization]
  → writes the optimized query
  → returns SQL string

Orchestrator merges results → final output
```

### Reactive Spawning (Escape Hatch)

For cases the Architect didn't anticipate, individual workers can still call `spawn_subtask` mid-task. This is unreliable as a primary mechanism but useful as a fallback. Fragment-level hints make it more likely to fire when appropriate:

```markdown
## When to delegate
If your task requires writing or optimizing SQL queries beyond simple CRUD,
spawn a subtask with context `database.relational.postgresql.query-optimization`
rather than attempting it yourself.
```

`spawn_subtask` enforces an `output_schema` parameter so the calling agent gets structured output it can use directly, not prose it has to parse.

```python
spawn_subtask(
    task="Write a single optimized PostgreSQL SELECT for monthly revenue by region. Return only the SQL string.",
    context_category="database.relational.postgresql.query-optimization",
    output_schema={"type": "string"}
)
```

Depth limit: subtasks may spawn one level of sub-subtasks. Beyond two levels, the task decomposition should be redesigned.

---

## Decisions

| Question | Decision |
|----------|----------|
| Mandatory first call? | **Yes** — worker rejects any tool call before `load_context` |
| Mid-task reload? | **Append, not replace** — agent can call `load_context` again; both sets of fragments stay in context |
| Manifest verbosity? | **Leaves only** — intermediate nodes are inheritance glue, not selectable |
| Who writes fragments? | **Humans initially** — agents may propose new leaf fragments via PR; tree structure is human-curated |
| Primary context assignment? | **Orchestrator upfront** — Architect assigns `context_category` per subtask at decomposition time |
| Reactive spawning? | **Escape hatch only** — prompted by fragment-level hints, not relied upon as primary mechanism |

---

## Open Questions

- **Who writes the fragments?** Initially humans. Eventually agents can propose new fragments via PR. The hierarchy itself should be human-curated — agents can add leaves, not restructure the tree.
- **Architect context?** What context category does the Architect itself load when decomposing a task? Likely a dedicated `orchestration` leaf — prompts that guide task decomposition, subtask scoping, and context category selection.
