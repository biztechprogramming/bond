# Design Doc 028: Checkbox Removal & Tier Migration

**Status:** Draft (Revised 2026-03-09)  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy)  
**Blocks:** 022 (Semantic Router), 024 (Lifecycle Routing)

---

## 1. The Problem

Bond currently uses **manual checkbox attachment** to assign prompt fragments to agents. In the frontend Agent editor (`AgentsTab.tsx`), each fragment appears as a checkbox — check it to attach, uncheck to detach. The join table `agent_prompt_fragments` stores these associations, and the worker loads all attached fragments for the agent, then runs `_select_relevant_fragments` to pick from them.

This is broken in two ways:

1. **Static assignment defeats dynamic selection.** The whole point of the selection pipeline (keyword triggers, LLM selector, budget enforcement) is to pick the right fragments per turn. But the selection pool is pre-filtered by human checkboxes. If you don't check a fragment, it can never be selected — even when it's clearly relevant.

2. **No fragments are actually attached.** The fragment database is empty (per doc 003 findings). `prompt_fragments` in the agent config is `[]`. The selection pipeline runs on an empty list every turn. The entire system is a no-op.

The fix: remove the checkbox model entirely. Fragments are no longer "attached" to agents. Instead:
- **Tier 1** (always-on) rules are concatenated into the system prompt at runtime from disk files
- **Tier 2** (lifecycle-triggered) rules are injected by phase detection (doc 024) from disk files
- **Tier 3** (context-dependent) rules are selected by semantic router (doc 022) from disk files

**All prompts live on the filesystem at `~/bond/prompts/`, versioned in git.** No database storage for prompt content. This was decided in doc 021 — prompts are code.

---

## 2. What Gets Removed

### 2.1 Frontend — Checkbox UI

**File:** `frontend/src/app/settings/agents/AgentsTab.tsx`

Remove the fragment checkbox grid (lines ~540-570):
```tsx
// REMOVE THIS ENTIRE BLOCK
<div style={styles.checkboxGrid}>
  {allFragments.filter(f => f.is_active).map((frag) => {
    const attached = isNew
      ? pendingFragmentIds.has(frag.id)
      : agentFragments.some(af => af.id === frag.id);
    return (
      <label key={frag.id} style={styles.checkboxLabel}>
        <input
          type="checkbox"
          checked={attached}
          onChange={() => { /* ... */ }}
        />
        {frag.display_name}
      </label>
    );
  })}
</div>
```

Remove related state and fetch calls:
```tsx
// REMOVE
const [agentFragments, setAgentFragments] = useState([]);
// REMOVE — fetch of all fragments for checkbox list
const fragRes = await fetch(".../prompts/fragments");
// REMOVE — fetch of agent's attached fragments
const res = await fetch(`.../prompts/agents/${agentId}/fragments`);
// REMOVE
const toggleAgentFragment = async (agentId, fragmentId, isAttached) => { ... };
// REMOVE — pending fragment IDs for new agent creation
const [pendingFragmentIds, setPendingFragmentIds] = useState(new Set());
```

### 2.2 Backend API — Attachment Endpoints

**File:** `backend/app/api/v1/prompts.py`

Remove these endpoints:
- `GET /agents/{agent_id}/fragments` — list attached fragments
- `POST /agents/{agent_id}/fragments` — attach a fragment
- `PUT /agents/{agent_id}/fragments/{fragment_id}` — update attachment
- `DELETE /agents/{agent_id}/fragments/{fragment_id}` — detach a fragment

The entire fragment CRUD API can also be removed — fragments are files on disk now, not database rows. Editing a fragment means editing a markdown file and committing.

### 2.3 Database — Fragment Tables

These tables can be dropped. All fragment content and metadata lives on the filesystem.

```sql
DROP TABLE IF EXISTS agent_prompt_fragments;
DROP TABLE IF EXISTS prompt_fragment_versions;
DROP TABLE IF EXISTS prompt_fragments;
```

### 2.4 Worker — Config Loading

**File:** `backend/app/api/v1/conversations.py` (line ~565)

Currently:
```python
prompt_fragments = []  # Already empty — nothing loads from DB
```

Remove this line entirely. Fragments are not part of agent config anymore.

**File:** `backend/app/worker.py` (line ~610)

Currently:
```python
fragments = config.get("prompt_fragments", [])
enabled_fragments = [f for f in fragments if f.get("enabled", True)]
```

Replace with filesystem-based loading:
```python
# Tier 3 fragments loaded from disk, selected dynamically per turn
from .agent.fragment_router import select_fragments_by_similarity
from .agent.lifecycle import detect_phase, load_lifecycle_fragments

# Tier 3: semantic router picks from prompts/ directory
tier3_fragments = await select_fragments_by_similarity(user_message)

# Tier 2: lifecycle hooks inject based on agent phase
tier2_fragments = load_lifecycle_fragments(current_phase, prompts_dir)
```

---

## 3. Prompt Manifest

The 64 prompt files at `~/bond/prompts/` need metadata: which tier, what utterances (for semantic router), what phase (for lifecycle hooks). This metadata lives in a manifest file alongside the prompts — versioned in git, not stored in a database.

### 3.1 Manifest Format

```yaml
# prompts/manifest.yaml
#
# Tier 1 (always-on): loaded into system prompt every turn
# Tier 2 (lifecycle):  injected when agent enters a specific work phase
# Tier 3 (context):    selected by semantic router based on user message
#
# Files not listed here are ignored by the selection system.
# Universal fragments (universal/*.md) are Tier 1 by default.

# ── Tier 1: Always On ──────────────────────────────────────────────
# These are concatenated into the system prompt at runtime.
# No selection logic — always present.

universal/safety.md:
  tier: 1

universal/reasoning.md:
  tier: 1

universal/communication.md:
  tier: 1

universal/error-handling.md:
  tier: 1

universal/work-planning.md:
  tier: 1

universal/progress-tracking.md:
  tier: 1

engineering/code-quality/must-compile/must-compile.md:
  tier: 1

# ── Tier 2: Lifecycle-Triggered ─────────────────────────────────────
# Injected when the agent enters the specified phase.
# Phase detection is based on tool call observation (doc 024).

engineering/git/git.md:
  tier: 2
  phase: committing

engineering/git/commits/commits.md:
  tier: 2
  phase: committing

engineering/git/pull-requests/pull-requests.md:
  tier: 2
  phase: reviewing

engineering/code-quality/code-review/code-review.md:
  tier: 2
  phase: reviewing

engineering/code-quality/bugfix/bugfix.md:
  tier: 2
  phase: implementing

# ── Tier 3: Context-Dependent ──────────────────────────────────────
# Selected by semantic router when the user's message is similar
# to the utterances listed here.

database/spacetimedb/spacetimedb.md:
  tier: 3
  utterances:
    - "write a SpacetimeDB reducer"
    - "how do reducers work in SpacetimeDB"
    - "the reducer is failing with an error"
    - "SpacetimeDB module"
    - "spacetime publish"

database/spacetimedb/reducers/reducers.md:
  tier: 3
  utterances:
    - "write a SpacetimeDB reducer"
    - "reducer is not updating the table"
    - "add a new reducer function"
    - "stdb reducer error"

database/spacetimedb/sql/sql.md:
  tier: 3
  utterances:
    - "SpacetimeDB SQL query"
    - "query SpacetimeDB tables"
    - "stdb sql syntax"

database/spacetimedb/typescript-sdk/typescript-sdk.md:
  tier: 3
  utterances:
    - "SpacetimeDB TypeScript client"
    - "connect to SpacetimeDB from frontend"
    - "stdb SDK subscription"

database/relational/postgresql/postgresql.md:
  tier: 3
  utterances:
    - "PostgreSQL query"
    - "psycopg connection"
    - "Postgres-specific syntax"

database/relational/postgresql/indexing/indexing.md:
  tier: 3
  utterances:
    - "add a database index"
    - "PostgreSQL index performance"
    - "partial index"
    - "the query is doing a sequential scan"

database/relational/postgresql/query-optimization/query-optimization.md:
  tier: 3
  utterances:
    - "optimize this SQL query"
    - "query is running slow"
    - "explain analyze output"
    - "reduce query execution time"

database/relational/sqlite/sqlite.md:
  tier: 3
  utterances:
    - "SQLite database"
    - "sqlite3 connection"
    - "aiosqlite"

database/relational/sqlite/wal-mode/wal-mode.md:
  tier: 3
  utterances:
    - "SQLite WAL mode"
    - "journal_mode WAL"
    - "concurrent SQLite reads"

database/relational/migrations/migrations.md:
  tier: 3
  utterances:
    - "database migration"
    - "schema migration"
    - "add a column migration"

database/relational/migrations/zero-downtime/zero-downtime.md:
  tier: 3
  utterances:
    - "zero downtime migration"
    - "migrate without downtime"
    - "online schema change"

backend/python/fastapi/fastapi.md:
  tier: 3
  utterances:
    - "FastAPI endpoint"
    - "add an API route"
    - "FastAPI dependency injection"
    - "Pydantic model for request"

backend/python/testing/testing.md:
  tier: 3
  utterances:
    - "write a pytest test"
    - "unit test this function"
    - "mock a dependency in tests"
    - "test coverage"

frontend/react/react.md:
  tier: 3
  utterances:
    - "React component"
    - "useState hook"
    - "React rendering issue"
    - "build a UI component"

frontend/nextjs/app-router/app-router.md:
  tier: 3
  utterances:
    - "Next.js app router"
    - "server component"
    - "Next.js routing"

infrastructure/docker/docker.md:
  tier: 3
  utterances:
    - "Dockerfile"
    - "Docker container"
    - "build a Docker image"
    - "container networking"

infrastructure/docker/compose/compose.md:
  tier: 3
  utterances:
    - "docker-compose file"
    - "Docker Compose services"
    - "multi-container setup"

security/auth/jwt/jwt.md:
  tier: 3
  utterances:
    - "JWT authentication"
    - "JSON web token"
    - "bearer token validation"

engineering/file-operations/file-operations.md:
  tier: 3
  utterances:
    - "read a file"
    - "write to a file"
    - "file operations"
    - "create a new file"
```

### 3.2 Manifest Loader

```python
# backend/app/agent/manifest.py

import yaml
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class FragmentMeta:
    path: str
    tier: int
    phase: str | None = None          # Tier 2 only
    utterances: list[str] = field(default_factory=list)  # Tier 3 only
    content: str = ""                  # Loaded from disk

_manifest: dict[str, FragmentMeta] | None = None

def load_manifest(prompts_dir: Path) -> dict[str, FragmentMeta]:
    """Load the prompt manifest and read all referenced files.
    
    Called once at worker startup. Hot-reloaded when manifest changes.
    """
    global _manifest
    
    manifest_path = prompts_dir / "manifest.yaml"
    if not manifest_path.exists():
        _manifest = {}
        return _manifest
    
    raw = yaml.safe_load(manifest_path.read_text())
    
    result = {}
    for rel_path, meta in raw.items():
        full_path = prompts_dir / rel_path
        if not full_path.exists():
            continue
        
        result[rel_path] = FragmentMeta(
            path=rel_path,
            tier=meta.get("tier", 3),
            phase=meta.get("phase"),
            utterances=meta.get("utterances", []),
            content=full_path.read_text().strip(),
        )
    
    _manifest = result
    return result


def get_tier1_fragments(manifest: dict[str, FragmentMeta]) -> list[FragmentMeta]:
    return [f for f in manifest.values() if f.tier == 1]

def get_tier2_fragments(manifest: dict[str, FragmentMeta], phase: str) -> list[FragmentMeta]:
    return [f for f in manifest.values() if f.tier == 2 and f.phase == phase]

def get_tier3_fragments(manifest: dict[str, FragmentMeta]) -> list[FragmentMeta]:
    return [f for f in manifest.values() if f.tier == 3]
```

---

## 4. Tier 1 Migration: Always-On Rules → System Prompt

### 4.1 What's Tier 1

| File | Tokens | Why Always-On |
|---|---|---|
| `universal/safety.md` | ~215 | Non-negotiable safety boundaries |
| `universal/reasoning.md` | ~200 | Thinking patterns |
| `universal/communication.md` | ~180 | Response style |
| `universal/error-handling.md` | ~195 | Prevents retry loops |
| `universal/work-planning.md` | ~417 | Core workflow — agent must follow the plan |
| `universal/progress-tracking.md` | ~428 | Multi-step task tracking |
| `engineering/code-quality/must-compile.md` | ~325 | Quality gate — must check builds |

**Total: ~1,960 tokens** added to the ~800 token system prompt = ~2,760 total.

### 4.2 Assembly at Runtime

The worker concatenates Tier 1 files into the system prompt. The stored `agents.system_prompt` stays clean — just persona and core behavior.

```python
# In worker.py — system prompt assembly

from .agent.manifest import load_manifest, get_tier1_fragments

manifest = load_manifest(prompts_dir)

# Agent's stored system prompt (persona, core behavior)
base_prompt = config["system_prompt"]

# Tier 1: always-on fragments from disk
tier1 = get_tier1_fragments(manifest)
tier1_content = "\n\n---\n\n".join(f.content for f in tier1)

full_system_prompt = f"{base_prompt}\n\n{tier1_content}"
```

### 4.3 Replaces `load_universal_fragments()`

The existing `load_universal_fragments()` in `dynamic_loader.py` currently reads `prompts/universal/*.md` and appends them to the system prompt. This function is replaced by the manifest-driven Tier 1 loading — which loads the same universal files plus `must-compile.md`.

Remove:
- `load_universal_fragments()` in `dynamic_loader.py`
- `load_universal_fragments_with_meta()` in `dynamic_loader.py`
- The universal fragment injection block in `worker.py` (lines ~645-670)

Replace with the manifest-based `get_tier1_fragments()` call above.

---

## 5. What Remains After Removal

### 5.1 Prompt Files (Unchanged)

All 64 files in `~/bond/prompts/` stay exactly where they are. Nothing moves. A new `manifest.yaml` is added alongside them.

### 5.2 Agent Settings UI (Simplified)

The Agent editor loses the fragment checkbox section. What remains:
- Agent name, display name
- System prompt (editable text)
- Model selection
- Utility model selection
- Tool checkboxes (keep — tools are still per-agent)
- Sandbox image
- Max iterations

### 5.3 Prompts Tab in Settings

The Prompts tab can be simplified or removed. Fragment CRUD against a database is no longer needed — editing a prompt means editing a file. The tab could become a read-only viewer of `prompts/` contents, or be removed entirely.

---

## 6. Migration Steps

| Step | File(s) | Change |
|------|---------|--------|
| 1 | `prompts/manifest.yaml` | Create manifest with tier/phase/utterance metadata for all 64 prompt files |
| 2 | `backend/app/agent/manifest.py` | Implement manifest loader |
| 3 | `backend/app/worker.py` | Replace `load_universal_fragments()` with manifest-based Tier 1 loading |
| 4 | `backend/app/worker.py` | Remove `config.get("prompt_fragments")` — fragments come from disk, not config |
| 5 | `backend/app/agent/context_pipeline.py` | Remove `_select_relevant_fragments` core tier handling — Tier 1 is in system prompt now |
| 6 | `backend/app/agent/tools/dynamic_loader.py` | Remove `load_universal_fragments()` and `load_universal_fragments_with_meta()` |
| 7 | `backend/app/api/v1/prompts.py` | Remove attachment endpoints and fragment CRUD endpoints |
| 8 | `backend/app/api/v1/conversations.py` | Remove `prompt_fragments: []` from agent config dict |
| 9 | `frontend/src/app/settings/agents/AgentsTab.tsx` | Remove checkbox grid, fragment state, toggle function, pending fragment IDs |
| 10 | Database migration | Drop `agent_prompt_fragments`, `prompt_fragment_versions`, `prompt_fragments` tables |

### Verification

| Test | Expected Result |
|------|-----------------|
| Agent turn with any message | System prompt contains safety, work-planning, must-compile, error-handling, reasoning, communication |
| Agent settings page loads | No checkbox grid. No fragment section. |
| Create new agent | No fragment attachment step. Works immediately. |
| Edit a prompt | Edit the markdown file on disk, commit to git |
| `git log prompts/` | Full version history of all prompt changes |

---

## 7. Rollback Plan

The checkbox model is currently a no-op (empty fragment list). Removing it changes nothing about runtime behavior. If needed:

1. **Tier 1 rollback** — Revert to `load_universal_fragments()`. Identical behavior.
2. **Database rollback** — Re-run the table creation migration. No data to restore (tables were empty).
3. **Checkbox UI rollback** — Git revert the frontend changes.

---

## 8. Decisions

| Question | Decision |
|----------|----------|
| Remove checkboxes entirely? | **Yes** — no per-agent fragment attachment |
| Where do prompts live? | **Filesystem** at `~/bond/prompts/`, versioned in git (per doc 021) |
| Where does metadata live? | **`prompts/manifest.yaml`**, versioned in git alongside the prompts |
| Database for fragments? | **No** — drop all fragment tables. Prompts are files, not rows. |
| Keep fragment CRUD API? | **No** — editing a prompt = editing a file. No API needed. |
| Keep Prompts tab in frontend? | **Simplify or remove** — read-only viewer at most |
| Keep tool checkboxes? | **Yes** — tools are still per-agent (different concern) |
| Migration strategy? | **Big bang** — checkbox model is a no-op, removing it is safe |
