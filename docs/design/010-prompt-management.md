# Design Doc 010: Prompt Management System

**Status:** Draft
**Depends on:** 008 (Containerized Agent Runtime), 009 (Container Configuration UI)

---

## 1. The Problem

Prompts are scattered across the codebase as hardcoded strings:

| Prompt | Location | Purpose |
|--------|----------|---------|
| Default system prompt | `loop.py:23` | Host-mode agent fallback |
| Memory usage guidance | `worker.py:273` | Appended to every agent turn |
| Entity extraction | `extraction.py:13` | Extract entities from text |

This creates several problems:

- **No visibility** — operators can't see what the agent is being told without reading source code
- **No editability** — changing a prompt requires a code deploy
- **No versioning** — no way to roll back a bad prompt change
- **No testing** — can't A/B test prompts or try a change in staging
- **No audit trail** — no record of who changed what, when, or why
- **No separation of concerns** — prompt engineering is mixed with application code
- **No reuse** — the same guidance (e.g., memory usage) is duplicated or hardcoded per agent

In an enterprise setting, prompts are a core operational asset. They control agent behavior as much as code does — arguably more. They need the same rigor as code: version control, review, rollback, and observability.

---

## 2. Design Principles

1. **Prompts are data, not code** — stored in the database, editable through the UI
2. **Every prompt is versioned** — full history with diffs, rollback to any version
3. **Composition over monoliths** — prompts are assembled from reusable fragments
4. **Separation of layers** — system instructions, agent persona, tool guidance, and task context are distinct
5. **Audit everything** — who changed what, when, why
6. **Safe to change** — preview before publish, rollback on regression

---

## 3. Prompt Architecture

### 3.1 Prompt Types

```
┌─────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPTS (per-agent, define persona + behavior)       │
│                                                              │
│  "You are Bond, a helpful personal AI assistant..."          │
│  Stored in: agents.system_prompt (existing)                  │
│  Edited in: Agent settings (existing)                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  PROMPT FRAGMENTS (reusable, composable blocks)              │
│                                                              │
│  "## Memory Usage                                            │
│   - At the START of complex tasks, use search_memory..."     │
│                                                              │
│  "## File Operations                                         │
│   - Always read before writing..."                           │
│                                                              │
│  Stored in: prompt_fragments table                           │
│  Attached to agents via: agent_prompt_fragments join table   │
│  Edited in: Prompts tab in Settings                          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  INTERNAL PROMPTS (system-level, not per-agent)              │
│                                                              │
│  Entity extraction, memory consolidation, classification     │
│                                                              │
│  Stored in: prompt_templates table                           │
│  Edited in: Prompts tab in Settings (advanced section)       │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Prompt Assembly

The final prompt sent to the LLM is assembled at turn time:

```
┌──────────────────────────────────────────┐
│  1. Agent system_prompt (from agents DB)  │  ← persona, core behavior
├──────────────────────────────────────────┤
│  2. Prompt fragments (ordered by rank)    │  ← memory guidance, tool tips, etc.
├──────────────────────────────────────────┤
│  3. Dynamic context (injected at runtime) │  ← entity graph, RAG results, time
└──────────────────────────────────────────┘
```

Example assembled prompt:

```
You are Bond, a helpful personal AI assistant running locally
on the user's machine. Be concise, helpful, and friendly.

## Memory Usage
- At the START of complex tasks, use `search_memory`...
- Use `memory_save` to remember user preferences...

## File Operations
- Always read a file before overwriting it.
- Use workspace paths (/workspace/...), not host paths.

## Current Context
- User timezone: EST
- Active project: ecoinspector-portal
- Known entities: Andrew (user), EcoInspector (project)
```

---

## 4. Database Schema

### 4.1 Prompt Fragments

Reusable blocks of prompt text that can be attached to one or more agents.

```sql
CREATE TABLE prompt_fragments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,              -- "memory-guidance", "file-ops", "sandbox-env"
    display_name TEXT NOT NULL,             -- "Memory Usage Guidance"
    category TEXT NOT NULL,                 -- "behavior", "tools", "safety", "context"
    content TEXT NOT NULL,                  -- The actual prompt text
    description TEXT DEFAULT '',            -- What this fragment does, for the UI
    is_active INTEGER NOT NULL DEFAULT 1,   -- Soft disable without deleting
    is_system INTEGER NOT NULL DEFAULT 0,   -- System-managed (seeded), can edit but not delete
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER prompt_fragments_updated_at
    AFTER UPDATE ON prompt_fragments FOR EACH ROW
BEGIN
    UPDATE prompt_fragments SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

### 4.2 Fragment Versions

Every edit creates a new version. The `content` column in `prompt_fragments` always holds the current (latest) version.

```sql
CREATE TABLE prompt_fragment_versions (
    id TEXT PRIMARY KEY,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,                  -- The prompt text at this version
    change_reason TEXT,                     -- "Improved memory save instructions"
    changed_by TEXT NOT NULL DEFAULT 'user', -- "user", "system", "migration"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(fragment_id, version)
);

CREATE INDEX idx_pfv_fragment ON prompt_fragment_versions(fragment_id, version DESC);
```

### 4.3 Agent ↔ Fragment Association

Which fragments are attached to which agents, and in what order.

```sql
CREATE TABLE agent_prompt_fragments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL DEFAULT 0,        -- Order in prompt assembly (lower = earlier)
    enabled INTEGER NOT NULL DEFAULT 1,     -- Can disable per-agent without detaching
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, fragment_id)
);

CREATE INDEX idx_apf_agent ON agent_prompt_fragments(agent_id, rank);
```

### 4.4 Internal Prompt Templates

System-level prompts not tied to agents (entity extraction, consolidation, etc.).

```sql
CREATE TABLE prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,              -- "entity-extraction", "memory-consolidation"
    display_name TEXT NOT NULL,
    category TEXT NOT NULL,                 -- "extraction", "consolidation", "classification"
    content TEXT NOT NULL,                  -- Prompt text with {variable} placeholders
    variables JSON NOT NULL DEFAULT '[]',   -- ["content", "context"] — documents expected placeholders
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER prompt_templates_updated_at
    AFTER UPDATE ON prompt_templates FOR EACH ROW
BEGIN
    UPDATE prompt_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE prompt_template_versions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_reason TEXT,
    changed_by TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(template_id, version)
);
```

---

## 5. Prompt Assembly at Runtime

### 5.1 Container Worker

```python
async def _assemble_system_prompt(config: dict, agent_db: aiosqlite.Connection) -> str:
    """Assemble the full system prompt from agent config + fragments."""
    parts = [config["system_prompt"]]

    # Load active fragments for this agent, ordered by rank
    # Fragments are included in the agent config (loaded at container start)
    fragments = config.get("prompt_fragments", [])
    for fragment in fragments:
        if fragment.get("enabled", True):
            parts.append(fragment["content"])

    return "\n\n".join(parts)
```

### 5.2 Config Generation

The manager includes fragments when writing the agent config:

```python
# In _write_agent_config:
config_data = {
    "agent_id": agent_id,
    "model": agent["model"],
    "system_prompt": agent["system_prompt"],
    "tools": agent["tools"],
    "max_iterations": agent["max_iterations"],
    "prompt_fragments": agent.get("prompt_fragments", []),
}
```

### 5.3 Internal Templates

Internal prompts are loaded by name at the call site:

```python
# In entity extraction:
from backend.app.foundations.prompts import get_template

template = await get_template(db, "entity-extraction")
prompt = template.format(content=content)
```

With fallback to hardcoded defaults if the DB entry doesn't exist (migration safety):

```python
async def get_template(db, name: str) -> str:
    result = await db.execute(
        text("SELECT content FROM prompt_templates WHERE name = :name AND is_active = 1"),
        {"name": name},
    )
    row = result.fetchone()
    if row:
        return row[0]
    # Fallback to hardcoded default (logged as warning)
    logger.warning("Prompt template '%s' not found in DB, using hardcoded fallback", name)
    return _HARDCODED_FALLBACKS.get(name, "")
```

---

## 6. API Endpoints

### 6.1 Prompt Fragments

```
GET    /api/v1/prompts/fragments                    — List all fragments
POST   /api/v1/prompts/fragments                    — Create fragment
GET    /api/v1/prompts/fragments/:id                — Get fragment with current content
PUT    /api/v1/prompts/fragments/:id                — Update fragment (creates new version)
DELETE /api/v1/prompts/fragments/:id                — Delete (fails if is_system)
GET    /api/v1/prompts/fragments/:id/versions       — List version history
GET    /api/v1/prompts/fragments/:id/versions/:ver  — Get specific version
POST   /api/v1/prompts/fragments/:id/rollback/:ver  — Rollback to a specific version
```

### 6.2 Internal Templates

```
GET    /api/v1/prompts/templates                    — List all templates
GET    /api/v1/prompts/templates/:id                — Get template with variables
PUT    /api/v1/prompts/templates/:id                — Update template (creates new version)
GET    /api/v1/prompts/templates/:id/versions       — List version history
POST   /api/v1/prompts/templates/:id/rollback/:ver  — Rollback to specific version
POST   /api/v1/prompts/templates/:id/preview        — Preview with sample variables
```

### 6.3 Agent Fragment Management

```
GET    /api/v1/agents/:id/fragments                 — List fragments attached to agent
POST   /api/v1/agents/:id/fragments                 — Attach fragment { fragment_id, rank }
PUT    /api/v1/agents/:id/fragments/:fid            — Update rank or enabled
DELETE /api/v1/agents/:id/fragments/:fid            — Detach fragment
POST   /api/v1/agents/:id/prompt-preview            — Preview assembled prompt
```

### 6.4 Request/Response Types

```typescript
interface PromptFragment {
    id: string;
    name: string;
    display_name: string;
    category: "behavior" | "tools" | "safety" | "context";
    content: string;
    description: string;
    is_active: boolean;
    is_system: boolean;
    version: number;            // Current version number
    agent_count: number;        // How many agents use this
    created_at: string;
    updated_at: string;
}

interface PromptFragmentVersion {
    id: string;
    fragment_id: string;
    version: number;
    content: string;
    change_reason: string;
    changed_by: string;
    created_at: string;
}

interface PromptTemplate {
    id: string;
    name: string;
    display_name: string;
    category: string;
    content: string;
    variables: string[];        // Expected placeholder names
    description: string;
    is_active: boolean;
    version: number;
}

interface AgentFragment {
    fragment_id: string;
    fragment_name: string;
    fragment_display_name: string;
    rank: number;
    enabled: boolean;
}
```

---

## 7. UI Design

### 7.1 Settings Navigation

```
Settings
  ├── General
  ├── API Keys
  ├── Embeddings
  ├── Agents
  ├── Containers        ← from 009
  └── Prompts           ← NEW
```

### 7.2 Prompts Tab — Fragment List

```
┌──────────────────────────────────────────────────────────────┐
│  Prompts                                    [+ New Fragment] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Fragments                          Templates                │
│  ─────────                          ─────────                │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Memory Usage Guidance                    behavior  v3  │  │
│  │ Instructions for when/how to use memory tools          │  │
│  │ Used by: bond-main, bond-coder           ✅ Active     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ File Operations                          tools     v1  │  │
│  │ Best practices for reading/writing files               │  │
│  │ Used by: bond-main                       ✅ Active     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Sandbox Environment                      context   v2  │  │
│  │ Container-specific instructions (paths, SSH, etc.)     │  │
│  │ Used by: bond-main, bond-coder           ✅ Active     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 7.3 Fragment Edit View

```
┌──────────────────────────────────────────────────────────────┐
│  Editing: Memory Usage Guidance (v3)          [Save] [Cancel]│
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Name (slug)              Display Name                       │
│  [memory-guidance       ] [Memory Usage Guidance           ] │
│                                                              │
│  Category                 Status                             │
│  [▼ behavior            ] [✓] Active                         │
│                                                              │
│  Description                                                 │
│  [Instructions for when and how to use memory tools        ] │
│                                                              │
│  ── Content ─────────────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ ## Memory Usage                                       │    │
│  │ - At the START of complex tasks, use `search_memory`  │    │
│  │   to check for relevant context from past             │    │
│  │   interactions.                                       │    │
│  │ - Use `memory_save` to remember:                      │    │
│  │   - User preferences and corrections                  │    │
│  │   - Project structure and key file locations           │    │
│  │   - Solutions that took multiple attempts              │    │
│  │ - Before ending a long task, save what you learned.   │    │
│  │ - Don't save trivial or obvious information.          │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  Change reason (required for save)                           │
│  [Clarified when to save project structure info            ] │
│                                                              │
│  ── Version History ─────────────────────────────────────    │
│                                                              │
│  v3  2026-02-26  user   Clarified when to save...   current │
│  v2  2026-02-25  user   Added memory_save examples  [revert]│
│  v1  2026-02-24  system Initial seed                [revert]│
│                                                              │
│  ── Used By ─────────────────────────────────────────────    │
│                                                              │
│  bond-main (rank 1) · bond-coder (rank 1)                   │
│                                                              │
│                                              [Delete]        │
└──────────────────────────────────────────────────────────────┘
```

### 7.4 Template Edit View

```
┌──────────────────────────────────────────────────────────────┐
│  Editing: Entity Extraction (v2)              [Save] [Cancel]│
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Name: entity-extraction                                     │
│  Category: extraction                                        │
│  Variables: content                                          │
│                                                              │
│  ── Content ─────────────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ Extract entities and relationships from the following │    │
│  │ text.                                                 │    │
│  │                                                       │    │
│  │ Return JSON with this exact structure:                │    │
│  │ {                                                     │    │
│  │   "entities": [                                       │    │
│  │     {"name": "...", "type": "person|project|..."}     │    │
│  │   ],                                                  │    │
│  │   ...                                                 │    │
│  │ }                                                     │    │
│  │                                                       │    │
│  │ Text:                                                 │    │
│  │ {content}                                             │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ── Preview ─────────────────────────────────────────────    │
│  Sample input:                                               │
│  [Andrew mentioned the EcoInspector project uses Next.js   ] │
│  [Preview]                                                   │
│                                                              │
│  ── Version History ─────────────────────────────────────    │
│  v2  2026-02-26  user   Added document entity type  current  │
│  v1  2026-02-24  system Initial seed                [revert] │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 7.5 Agent Settings — Fragment Attachment

In the agent edit form, replace the monolithic system prompt textarea with a structured view:

```
┌──────────────────────────────────────────────────────────────┐
│  ── System Prompt ───────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ You are Bond, a helpful personal AI assistant...      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ── Prompt Fragments ────────────────────── [+ Attach]  ─    │
│                                                              │
│  ≡ 1. Memory Usage Guidance          behavior    ✅  [✕]    │
│  ≡ 2. Sandbox Environment            context     ✅  [✕]    │
│  ≡ 3. File Operations                tools       ✅  [✕]    │
│                                                              │
│  (drag ≡ to reorder)                                         │
│                                                              │
│  [Preview Full Prompt]                                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

The "Preview Full Prompt" button shows the assembled prompt exactly as the LLM will see it.

---

## 8. Seed Data

On first run or migration, seed the fragments and templates from the current hardcoded prompts:

```python
SEED_FRAGMENTS = [
    {
        "name": "memory-guidance",
        "display_name": "Memory Usage Guidance",
        "category": "behavior",
        "content": """## Memory Usage
- At the START of complex tasks, use `search_memory` to check for relevant context.
- Use `memory_save` to remember:
  - User preferences and corrections
  - Project structure and key file locations you discover
  - Solutions to problems that took multiple attempts
  - Important facts the user shares
- Before ending a long task, save what you learned for next time.
- Don't save trivial or obvious information.""",
        "description": "Instructions for when and how the agent should use memory tools.",
        "is_system": True,
    },
    {
        "name": "sandbox-environment",
        "display_name": "Sandbox Environment",
        "category": "context",
        "content": """## Sandbox Environment
When running in a Docker sandbox:
- SSH keys are mounted at /tmp/.ssh and copied to /root/.ssh.
- The container runs as root. You have full access.
- Workspace mounts appear at their configured container paths.
- Use /workspace/ paths, not host paths.""",
        "description": "Container-specific instructions for sandboxed agents.",
        "is_system": True,
    },
]

SEED_TEMPLATES = [
    {
        "name": "entity-extraction",
        "display_name": "Entity Extraction",
        "category": "extraction",
        "content": "...",  # Current EXTRACTION_PROMPT from extraction.py
        "variables": ["content"],
        "description": "Extract entities and relationships from text using an LLM.",
    },
]
```

After seeding, attach the appropriate fragments to the default agent.

---

## 9. Migration Path

1. Create tables: `prompt_fragments`, `prompt_fragment_versions`, `agent_prompt_fragments`, `prompt_templates`, `prompt_template_versions`
2. Seed fragments from hardcoded prompts
3. Seed templates from hardcoded prompts
4. Attach seeded fragments to existing agents
5. Remove hardcoded prompts from code — replace with DB reads
6. The `agents.system_prompt` column stays — it's the agent's core persona prompt
7. The worker's `memory_guidance` string is replaced by a fragment lookup
8. The `EXTRACTION_PROMPT` in `extraction.py` is replaced by a template lookup

### Backward Compatibility

- If fragment tables don't exist (pre-migration), the worker falls back to the agent's `system_prompt` only — no fragments appended
- If a template isn't found in the DB, the code falls back to the hardcoded string with a warning log
- After migration completes and seeds are verified, hardcoded fallbacks can be removed

---

## 10. Implementation Plan

| ID | Story | Effort |
|----|-------|--------|
| PM1 | DB migration: create all prompt tables | S |
| PM2 | Seed fragments and templates from hardcoded prompts | S |
| PM3 | Backend API: CRUD for fragments with versioning | M |
| PM4 | Backend API: CRUD for templates with versioning | M |
| PM5 | Backend API: agent fragment attachment (attach/detach/reorder) | S |
| PM6 | Backend: prompt assembly in config generation (include fragments) | S |
| PM7 | Worker: assemble prompt from config fragments instead of hardcoded guidance | S |
| PM8 | Replace `EXTRACTION_PROMPT` with template DB lookup | S |
| PM9 | Frontend: Prompts tab — fragment list + edit + version history | L |
| PM10 | Frontend: Prompts tab — template list + edit + preview | M |
| PM11 | Frontend: Agent settings — fragment attachment UI with drag reorder | M |
| PM12 | Frontend: Full prompt preview button | S |
| PM13 | Remove hardcoded prompt fallbacks after migration verified | S |

---

## 11. Future Enhancements

Not in scope now, but designed to be addable:

- **A/B testing** — run two versions of a fragment simultaneously, measure which performs better
- **Approval workflow** — prompt changes require review before going live
- **Prompt analytics** — track which prompts lead to better tool usage, fewer errors
- **Prompt inheritance** — agent groups that share a base prompt set
- **Environment-specific prompts** — different fragments for dev vs production
- **Import/export** — share prompt sets across Bond installations
- **LLM-assisted prompt improvement** — suggest edits based on conversation outcomes

---

## 12. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Fragments separate from agent system prompt | System prompt is the agent's identity. Fragments are reusable operational instructions. Different concerns, different edit frequency. |
| Version history on every edit | Prompts directly control agent behavior. Every change needs to be traceable and reversible. |
| Change reason required | Forces intentionality. "Made it better" isn't acceptable when a prompt change can break agent behavior. |
| Rank-ordered fragments per agent | Order matters in prompts. Earlier instructions carry more weight with most LLMs. |
| Templates with declared variables | Self-documenting. The UI can validate that all variables are provided. |
| `is_system` flag on seeded fragments | Prevents accidental deletion of core fragments. Users can edit content but not delete them. |
| Separate tables for fragments vs templates | Different use cases, different lifecycles. Fragments compose into agent prompts. Templates are standalone with variable substitution. |
| Preview capability | Operators must be able to see exactly what the LLM receives before and after changes. |
