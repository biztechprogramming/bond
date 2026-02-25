# Design: Entity Graph

**Status:** DRAFT v1 — awaiting approval  
**Author:** Developer Agent  
**Date:** 2026-02-25  
**Refs:** [02-foundations](../architecture/02-foundations.html) (F2), [05-data-architecture](../architecture/05-data-architecture.html), [001-knowledge-store-and-memory](001-knowledge-store-and-memory.md)

---

## 1. Overview

The Entity Graph is Bond's relationship layer — it tracks the people, projects, tasks, decisions, meetings, documents, and events in the user's life, and how they connect to each other.

Built on top of the Knowledge Store (F1), the entity graph:
- **Auto-extracts** entities and relationships from conversations, memories, emails, and files
- **Connects** related concepts (Person → works_on → Project, Task → blocked_by → Task)
- **Enriches search** by providing graph context alongside vector/keyword results
- **Enables proactive intelligence** ("You have a meeting with Sarah tomorrow — here's context from your last 3 conversations with her")

### Design Principles

- **Extraction is async and non-blocking** — entity extraction runs as a background pipeline handler, never slowing down the main conversation loop
- **Entities are merged, not duplicated** — "Sarah", "Sarah Chen", and "sarah.chen@work.com" should resolve to one entity
- **Relationships are evidence-based** — every relationship links back to the source that established it
- **The graph degrades gracefully** — if extraction fails or is disabled, the rest of Bond works fine

---

## 2. Schema

Tables are created in migration 000003 (defined in [001-knowledge-store-and-memory.md](001-knowledge-store-and-memory.md)). This document covers the logic built on top of that schema.

### Entity Types

| Type | Description | Example | Typical Sources |
|------|-------------|---------|----------------|
| `person` | A human the user interacts with | Sarah Chen, Dr. Patel | Conversations, emails, calendar |
| `project` | A named initiative or body of work | Bond, Q4 Migration | Conversations, files, tasks |
| `task` | An action item or deliverable | "Fix the login bug" | Conversations, emails |
| `decision` | A choice that was made | "We chose PostgreSQL" | Conversations, meetings |
| `meeting` | A scheduled or past gathering | "Sprint planning 2/25" | Calendar, conversations |
| `document` | A file, doc, or artifact | "API spec v2" | File mentions, emails |
| `event` | A notable occurrence | "Production outage 2/24" | Conversations, alerts |

### Relationship Types

Relationships are directional: `source --type--> target`. Common types:

| Relationship | Source → Target | Example |
|---|---|---|
| `works_on` | person → project | Sarah → Bond |
| `owns` | person → task | Andrew → "Fix login bug" |
| `blocked_by` | task → task | "Deploy v2" → "Fix login bug" |
| `decided_in` | decision → meeting | "Chose PostgreSQL" → "Sprint planning" |
| `mentioned_in` | entity → document | Bond → "API spec v2" |
| `reports_to` | person → person | Sarah → David |
| `related_to` | any → any | Generic fallback |
| `part_of` | task → project | "Fix login bug" → Bond |
| `attended` | person → meeting | Sarah → "Sprint planning" |
| `created` | person → document | Andrew → "API spec v2" |
| `triggered_by` | event → entity | "Outage" → "Deploy v2" |

The `type` column is a free-form string — not a CHECK constraint — because relationship types will grow as new source integrations are added. The above are conventions, not a closed set.

### Entity Metadata

The `metadata` JSON column stores type-specific structured data:

```json
// person
{
  "email": "sarah.chen@work.com",
  "aliases": ["Sarah", "Sarah Chen", "SC"],
  "company": "Acme Corp",
  "role": "Engineering Lead"
}

// project
{
  "status": "active",
  "repo": "https://github.com/user/bond",
  "tags": ["ai", "personal-assistant"]
}

// task
{
  "status": "in_progress",
  "priority": "high",
  "due_date": "2026-03-01"
}
```

---

## 3. Entity Extraction Pipeline

### 3.1 Architecture

Entity extraction runs as a **notification handler** on content events. It never blocks the main pipeline.

```
  Content saved                     Entity extraction
  (conversation, email, etc.)       (async, background)
       │                                  │
       ├──▶ ContentIndexed event ────────▶ ExtractEntities handler
       │                                  │
       │                                  ├── 1. LLM extracts entities + relationships
       │                                  ├── 2. Entity resolution (merge duplicates)
       │                                  ├── 3. Upsert entities + relationships
       │                                  ├── 4. Record entity_mentions
       │                                  └── 5. Embed new/updated entities
       │
       └──▶ (main pipeline continues unblocked)
```

### 3.2 Extraction via LLM

Extraction uses a structured LLM call with the active model. The prompt asks for entities and relationships in a single pass.

**Extraction prompt template:**

```
Extract entities and relationships from the following text.

Return JSON with this exact structure:
{
  "entities": [
    {"name": "...", "type": "person|project|task|decision|meeting|document|event", "metadata": {...}}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "type": "relationship type", "context": "brief explanation"}
  ]
}

Rules:
- Only extract entities explicitly mentioned or strongly implied
- Use the most specific entity type that fits
- Include metadata fields you can confidently extract (email, role, status, etc.)
- For relationships, include context explaining why the relationship exists
- If no entities are found, return {"entities": [], "relationships": []}

Text:
{content}
```

**Cost control:**
- Extraction uses the cheapest available model (e.g., a small local model, or the configured LLM with low max_tokens)
- Batch extraction: multiple content items can be batched into a single LLM call (up to context window limit)
- Extraction is skippable — if the LLM is unavailable, items are queued for retry

### 3.3 Entity Resolution (Deduplication)

The hardest part: "Sarah", "Sarah Chen", "sarah.chen@work.com" should all map to one entity.

**Resolution strategy (multi-signal):**

```
  New entity candidate
       │
       ├── 1. Exact name match (case-insensitive)
       │      → merge if same type
       │
       ├── 2. Alias match
       │      → check existing entity metadata.aliases
       │
       ├── 3. Metadata match
       │      → email match for persons (definitive)
       │      → repo URL match for projects
       │
       ├── 4. Embedding similarity
       │      → cosine > 0.90 against same-type entities
       │      → candidate for merge, not automatic
       │
       └── 5. Flag as merge candidate (if ambiguous)
              → store in entity metadata: merge_candidates: [{id, similarity, reason}]
              → UI shows a subtle hint badge on the entity (not a modal/prompt)
              → user can merge manually if they want, or ignore
```

**Auto-merge (high confidence):**
- Steps 1–3 (exact name, alias, email match) → auto-merge silently, log to audit
- Step 4 (embedding similarity > 0.95 AND same type) → auto-merge silently

**Merge candidates (ambiguous):**
- Step 4 (embedding similarity 0.90–0.95) → flag as merge candidate
- UI shows a non-invasive indicator on entity cards (e.g., a small "possible duplicate" badge)
- User can view candidates and merge with one click, or dismiss
- Dismissed candidates are stored in `metadata.dismissed_merges` to avoid re-flagging

**Merge behavior:**
- When entities merge, the newer extraction's metadata is merged into the existing entity (new fields added, existing fields kept unless explicitly updated)
- Aliases array is appended to
- All entity_mentions from the merged entity are re-pointed to the surviving entity
- A merge event is logged to audit_log

### 3.4 Confidence and Weight

**Relationship weight** (0.0–1.0) represents confidence:
- 1.0 — explicitly stated ("Sarah is the lead on Bond")
- 0.7 — strongly implied ("Sarah presented the Bond demo" → works_on, 0.7)
- 0.4 — weakly implied ("Sarah mentioned the Bond project" → related_to, 0.4)

Weights are set by the extraction LLM and can be updated:
- Repeated evidence increases weight (capped at 1.0)
- Weight decays over time for relationships not re-confirmed (enabled by default)

**Weight decay formula:**

```
effective_weight = base_weight × 0.5 ^ (days_since_last_confirmed / half_life_days)
```

Where `half_life_days` defaults to 180 (configurable). A relationship at weight 1.0 that hasn't been re-confirmed in 6 months decays to 0.5. Re-confirmation (the relationship is extracted again from new content) resets `last_confirmed_at` and optionally bumps the base weight.

**Decay is computed at query time, not stored.** The `weight` column stores the base weight; `updated_at` serves as `last_confirmed_at`. This avoids periodic batch updates and ensures the graph is always consistent.

Relationships that decay below a configurable floor (default 0.05) are excluded from graph traversal results but not deleted — they can be revived if re-confirmed.

---

## 4. Graph Queries

### 4.1 Core Query Operations

```python
class EntityRepository:
    """Repository for entity CRUD and graph traversal."""

    # --- CRUD ---
    async def create(self, entity: CreateEntityInput) -> Entity
    async def get(self, id: str) -> Entity | None
    async def get_by_name(self, name: str, type: str | None = None) -> list[Entity]
    async def update(self, id: str, updates: UpdateEntityInput) -> Entity
    async def merge(self, keep_id: str, merge_id: str) -> Entity
    async def delete(self, id: str) -> bool

    # --- Relationships ---
    async def add_relationship(self, rel: CreateRelationshipInput) -> Relationship
    async def get_relationships(
        self, entity_id: str,
        direction: str = "both",          # 'outgoing', 'incoming', 'both'
        rel_types: list[str] | None = None,
    ) -> list[Relationship]
    async def update_relationship_weight(self, rel_id: str, weight: float) -> Relationship

    # --- Mentions ---
    async def add_mention(self, entity_id: str, source_type: str, source_id: str) -> EntityMention
    async def get_mentions(self, entity_id: str) -> list[EntityMention]

    # --- Graph Traversal ---
    async def get_neighborhood(
        self, entity_id: str,
        depth: int = 1,                   # max hops from entity
        rel_types: list[str] | None = None,
        min_weight: float = 0.0,
    ) -> EntityGraph

    async def find_path(
        self, source_id: str, target_id: str,
        max_depth: int = 4,
    ) -> list[Relationship] | None

    # --- Search ---
    async def search(
        self, query: str,
        embedding: list[float] | None = None,
        *,
        entity_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]

    # --- Resolution ---
    async def resolve(self, name: str, type: str | None = None, metadata: dict | None = None) -> Entity | None
```

### 4.2 EntityGraph Data Structure

```python
@dataclass
class EntityGraph:
    """A subgraph centered on one or more entities."""
    entities: dict[str, Entity]          # id → Entity
    relationships: list[Relationship]    # edges between entities in the graph
    center_id: str                       # the entity this graph was built around

    def get_related(self, entity_id: str, rel_type: str | None = None) -> list[Entity]:
        """Get entities related to the given entity, optionally filtered by relationship type."""
        ...

    def to_context_string(self) -> str:
        """Render the graph as a natural language summary for LLM context injection."""
        ...
```

### 4.3 Graph Traversal: `get_neighborhood`

BFS traversal up to `depth` hops from the center entity:

```
depth=1:  Sarah → [Bond (works_on), David (reports_to), Sprint Planning (attended)]
depth=2:  + Bond → [Andrew (works_on), "Fix login" (part_of)]
          + David → [Acme Corp (works_at)]
```

Implementation:
```python
async def get_neighborhood(self, entity_id, depth=1, rel_types=None, min_weight=0.0):
    visited = set()
    queue = [(entity_id, 0)]
    entities = {}
    relationships = []

    while queue:
        current_id, current_depth = queue.pop(0)
        if current_id in visited or current_depth > depth:
            continue
        visited.add(current_id)

        entity = await self.get(current_id)
        if entity:
            entities[current_id] = entity

        if current_depth < depth:
            rels = await self.get_relationships(
                current_id, direction="both",
                rel_types=rel_types
            )
            for rel in rels:
                if rel.weight >= min_weight:
                    relationships.append(rel)
                    next_id = rel.target_id if rel.source_id == current_id else rel.source_id
                    queue.append((next_id, current_depth + 1))

    return EntityGraph(entities=entities, relationships=relationships, center_id=entity_id)
```

### 4.4 Context Enrichment

The primary consumer of the entity graph is **context enrichment** — adding relevant entity context to the LLM prompt before each turn.

```
User says: "What did Sarah say about the deadline?"

1. Extract entities from query: ["Sarah"]
2. Resolve: Sarah → Entity(id="abc", type="person", name="Sarah Chen")
3. Get neighborhood(depth=1):
   - Sarah works_on Bond (weight=0.9)
   - Sarah attended Sprint Planning 2/25 (weight=1.0)
   - Sarah owns "Update API docs" (weight=0.8)
4. Inject context into prompt:
   "Known context: Sarah Chen is a person who works on the Bond project,
    attended Sprint Planning on 2/25, and owns the task 'Update API docs'."
5. Also search memories mentioning Sarah for additional context
```

---

## 5. Integration Points

### 5.1 Event-Driven Extraction

Entity extraction subscribes to content events via the mediator notification system:

| Event | Trigger | Extraction Input |
|-------|---------|-----------------|
| `ContentIndexed` | New content_chunk saved | chunk.text |
| `MemorySaved` | New memory created | memory.content |
| `SessionSummarized` | Session summary generated | summary.text + key_decisions |
| `EmailProcessed` | New email indexed (future) | email.subject + body |

### 5.2 Agent Tools

```json
{
  "name": "entity_lookup",
  "description": "Look up a person, project, task, or other entity and their connections",
  "parameters": {
    "name": {"type": "string", "description": "Entity name to look up"},
    "type": {"type": "string", "enum": ["person", "project", "task", "decision", "meeting", "document", "event"], "description": "Optional type filter"},
    "include_relationships": {"type": "boolean", "default": true},
    "depth": {"type": "integer", "default": 1, "description": "Relationship traversal depth"}
  }
}
```

```json
{
  "name": "entity_connect",
  "description": "Manually create or update a relationship between two entities",
  "parameters": {
    "source_name": {"type": "string"},
    "target_name": {"type": "string"},
    "relationship_type": {"type": "string"},
    "context": {"type": "string", "description": "Why this relationship exists"}
  }
}
```

### 5.3 Mediator Commands

```python
# Commands
class ExtractEntities(Command):
    content: str
    source_type: str
    source_id: str

class LookupEntity(Command):
    name: str
    type: str | None = None
    depth: int = 1

class MergeEntities(Command):
    keep_id: str
    merge_id: str

class GetEntityContext(Command):
    """Get enrichment context for entities mentioned in a query."""
    query: str
    max_entities: int = 5
    depth: int = 1
```

---

## 6. Module Structure

```
backend/app/
├── foundations/
│   └── entity_graph/
│       ├── __init__.py
│       ├── repository.py          # EntityRepository (CRUD + graph traversal)
│       ├── extraction.py          # LLM-based entity/relationship extraction
│       ├── resolution.py          # Entity resolution (dedup/merge logic)
│       ├── context.py             # Context enrichment (graph → prompt text)
│       ├── commands.py            # ExtractEntities, LookupEntity, MergeEntities, GetEntityContext
│       └── handlers.py            # Command + notification handlers
```

---

## 7. Performance Considerations

### Query Performance

Graph traversal queries are bounded by depth and use indexed lookups:

| Operation | Indexes Used | Expected Time |
|-----------|-------------|--------------|
| `get_relationships(entity_id)` | `idx_rel_source`, `idx_rel_target` | <1ms |
| `get_neighborhood(depth=1)` | 1 + N relationship lookups | <5ms |
| `get_neighborhood(depth=2)` | Fan-out depends on connectivity | <20ms |
| `resolve(name)` | `idx_ent_name` + embedding search | <15ms |

### Extraction Performance

- LLM extraction: ~500ms–2s per content chunk (model-dependent)
- Extraction is async — latency doesn't affect user experience
- Batch extraction reduces per-item cost

### Storage

Entities and relationships are lightweight rows. At scale:
- 10K entities × ~500 bytes = ~5MB
- 50K relationships × ~200 bytes = ~10MB
- 100K mentions × ~100 bytes = ~10MB

The entity vec0 table follows the same runtime creation and dimension rules as other vec0 tables (see [001](001-knowledge-store-and-memory.md) section 3.3).

---

## 8. Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `entity_graph.enabled` | bool | true | Enable/disable entity extraction |
| `entity_graph.auto_extract` | bool | true | Auto-extract from new content |
| `entity_graph.extraction_model` | string | null | Override model for extraction (null = use default LLM) |
| `entity_graph.resolution_similarity_threshold` | float | 0.90 | Cosine threshold for embedding-based entity resolution |
| `entity_graph.context_depth` | int | 1 | Default traversal depth for context enrichment |
| `entity_graph.max_context_entities` | int | 5 | Max entities to include in context enrichment |
| `entity_graph.weight_decay_enabled` | bool | true | Enable time-based relationship weight decay |
| `entity_graph.weight_decay_half_life_days` | int | 180 | Days for relationship weight to halve (if decay enabled) |
| `entity_graph.extraction_batch_size` | int | 5 | Content items per LLM extraction call |

---

## 9. Testing Plan

| Test | What it validates |
|------|-------------------|
| Entity CRUD | Create, read, update, delete entities |
| Relationship CRUD | Create relationships, unique constraint, cascade delete |
| Entity mentions | Add mention, get mentions, cascade on entity delete |
| `get_neighborhood(depth=1)` | Returns direct connections only |
| `get_neighborhood(depth=2)` | Returns 2-hop connections, no duplicates |
| `find_path` | Finds shortest path, returns None when no path exists |
| Entity resolution — exact name | Matches case-insensitively |
| Entity resolution — alias | Matches against metadata.aliases |
| Entity resolution — email | Matches person by email in metadata |
| Entity resolution — embedding | Cosine > threshold triggers candidate match |
| Merge entities | Metadata merged, mentions re-pointed, audit logged |
| LLM extraction | Valid JSON returned, entities/relationships parsed |
| Extraction handler | ContentIndexed event triggers extraction |
| Context enrichment | Query entities resolved, neighborhood fetched, context string generated |
| Batch extraction | Multiple items processed in single LLM call |
| Extraction failure | Failed extraction logged, item queued for retry |

---

## 10. Implementation Order

1. **EntityRepository** — CRUD for entities, relationships, mentions
2. **Graph traversal** — `get_neighborhood`, `find_path`
3. **Entity resolution** — name/alias/metadata/embedding matching + merge
4. **Extraction pipeline** — LLM extraction prompt, parsing, handler
5. **Context enrichment** — query → entity resolution → neighborhood → context string
6. **Agent tools** — `entity_lookup`, `entity_connect`
7. **Mediator commands** — wire into pipeline
8. **Tests** for each layer

---

## Decisions

1. **Relationship weight decay** — ✅ Build now, enabled by default (half-life 180 days). Decay runs as a periodic maintenance task.

2. **Entity merge UI** — ✅ Non-invasive. Auto-merge on high confidence (exact name/alias/email, or cosine > 0.95). For ambiguous cases (cosine 0.90–0.95), show a subtle badge hint on entity cards. User merges if they want, or ignores. Dismissed candidates aren't re-flagged.

3. **Extraction batching** — ✅ Configurable via `entity_graph.extraction_batch_size`, default 5.
