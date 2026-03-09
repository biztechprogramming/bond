# Design Doc 022: Semantic Router Integration

**Status:** Draft (Revised 2026-03-09)  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy), 028 (Checkbox Removal)  
**Reference:** [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) (⭐ 3,332)

---

## 1. Scope

This document covers **Tier 3: Context-Dependent fragments only** — domain-specific knowledge that should be injected based on what the user is asking about. Examples: PostgreSQL optimization tips, React patterns, FastAPI routing, SpacetimeDB reducers.

This doc does **NOT** cover:
- **Tier 1 (Always-on rules)** — work plan adherence, safety, error handling. These live in the agent system prompt. See doc 028.
- **Tier 2 (Lifecycle-triggered rules)** — git best practices at commit time, testing requirements during implementation. These are injected by lifecycle phase detection. See doc 024.

### The Three Tiers

```
Tier 1: ALWAYS ON → System prompt (not a fragment)
  - Work plan adherence, safety rules, error handling, core agent behavior
  - Injected every turn, unconditionally
  - Not selectable, not skippable

Tier 2: LIFECYCLE-TRIGGERED → Workflow phase detection (doc 024)
  - Git branch + push rules → fires when agent is committing
  - Testing requirements → fires when agent is implementing
  - PR creation rules → fires when agent is submitting for review
  - Triggered by WHAT THE AGENT IS DOING, not what the user said

Tier 3: CONTEXT-DEPENDENT → Semantic Router (this doc)
  - PostgreSQL optimization → fires when user asks about queries
  - React patterns → fires when user asks about frontend
  - SpacetimeDB reducers → fires when user mentions reducers
  - Triggered by WHAT THE USER IS ASKING ABOUT
```

---

## 2. What Semantic Router Does

Semantic Router is a Python library that routes inputs to handlers using embedding similarity — no LLM call required. You define "routes" with example utterances, embed them once, and at runtime the user's input is embedded and matched to the closest route via cosine similarity.

Key concepts:
- **Route**: A named destination with a list of example utterances and an optional function handler
- **RouteLayer**: Holds all routes, manages the embedding index, performs similarity scoring
- **Encoder**: Pluggable embedding backend (OpenAI, Cohere, HuggingFace, local models)
- **Threshold**: Per-route similarity cutoff — below this, the route doesn't fire

```python
from semantic_router import Route, RouteLayer
from semantic_router.encoders import HuggingFaceEncoder

database_route = Route(
    name="database",
    utterances=[
        "write a SQL query",
        "optimize this database query",
        "create a migration",
        "add an index to the table",
    ],
)

frontend_route = Route(
    name="frontend",
    utterances=[
        "build a React component",
        "fix this CSS layout",
        "add a click handler",
    ],
)

encoder = HuggingFaceEncoder(name="all-MiniLM-L6-v2")
layer = RouteLayer(encoder=encoder, routes=[database_route, frontend_route])

# At runtime — fast vector lookup, no LLM
result = layer("how do I add a partial index on user_id?")
# → Route(name="database", ...)
```

---

## 3. What's Broken in Bond Today

Bond's current Tier 3 fragment selection uses keyword triggers (`_matches_triggers` in `context_pipeline.py`):

```python
def _matches_triggers(fragment: dict, search_text: str) -> bool:
    triggers = json.loads(fragment.get("task_triggers", "[]"))
    search_lower = search_text.lower()
    return any(t.lower() in search_lower for t in triggers)
```

**Problems:**

| Issue | Detail |
|-------|--------|
| **Keyword triggers are brittle** | Substring match misses synonyms and indirect references. "Optimize my query performance" won't match trigger "postgresql". |
| **No semantic understanding** | Zero awareness of meaning. "Speed up my database reads" won't match "indexing". |
| **False positives from substrings** | Trigger "test" matches "latest", "greatest", "contest". |
| **No similarity scoring** | Binary match — either it fires or it doesn't. No confidence level. |
| **Can't rank multiple matches** | If 5 fragments match, they're all treated equally. No way to prefer the most relevant. |

---

## 4. How Semantic Router Replaces Keyword Triggers

### 4.1 Fragment → Route Mapping

Each Tier 3 fragment becomes a semantic route. The `task_triggers` field (currently keyword strings) becomes a list of **example utterances**.

```python
# Current: keyword triggers (brittle)
{
    "name": "spacetimedb-reducers",
    "task_triggers": ["reducer", "spacetimedb reducer", "stdb reducer"]
}

# Proposed: semantic utterances (robust)
{
    "name": "spacetimedb-reducers",
    "utterances": [
        "write a SpacetimeDB reducer",
        "how do reducers work in SpacetimeDB",
        "the reducer is failing with an error",
        "add a new reducer to handle user events",
        "my reducer isn't updating the table",
        "create a reducer that processes incoming data",
    ]
}
```

### 4.2 Which Fragments Become Routes

Only Tier 3 fragments listed in `prompts/manifest.yaml` with `tier: 3` and `utterances`. Tier 1 fragments go into the system prompt (doc 028), Tier 2 fragments are injected by lifecycle hooks (doc 024). Neither participates in semantic selection.

| Fragment | Current Tier | Becomes |
|----------|---|---|
| `safety-rules` | core | **Tier 1 → system prompt** (doc 028) |
| `error-handling` | core | **Tier 1 → system prompt** (doc 028) |
| `must-compile` | core | **Tier 1 → system prompt** (doc 028) |
| `work-planning` | standard | **Tier 1 → system prompt** (doc 028) |
| `progress-tracking` | standard | **Tier 1 → system prompt** (doc 028) |
| `git-workflow` | standard | **Tier 2 → lifecycle hook** (doc 024) |
| `commit-messages` | standard | **Tier 2 → lifecycle hook** (doc 024) |
| `python-fastapi` | standard | **Tier 3 → semantic route** (this doc) ✓ |
| `python-testing` | standard | **Tier 2 → lifecycle hook** (doc 024) |
| `spacetimedb` | standard | **Tier 3 → semantic route** ✓ |
| `spacetimedb-reducers` | specialized | **Tier 3 → semantic route** ✓ |
| `docker-sandbox` | standard | **Tier 3 → semantic route** ✓ |
| `react-patterns` | standard | **Tier 3 → semantic route** ✓ |
| `code-review` | standard | **Tier 2 → lifecycle hook** (doc 024) |
| `bugfix` | standard | **Tier 3 → semantic route** ✓ |
| `file-operations` | standard | **Tier 3 → semantic route** ✓ |
| `spacetimedb-sql` | specialized | **Tier 3 → semantic route** ✓ |
| `spacetimedb-typescript-sdk` | specialized | **Tier 3 → semantic route** ✓ |
| `sqlite-wal` | specialized | **Tier 3 → semantic route** ✓ |
| `jwt-auth` | specialized | **Tier 3 → semantic route** ✓ |
| `nextjs-app-router` | specialized | **Tier 3 → semantic route** ✓ |
| `docker-compose` | specialized | **Tier 3 → semantic route** ✓ |
| `postgresql-indexing` | specialized | **Tier 3 → semantic route** ✓ |
| `zero-downtime-migrations` | specialized | **Tier 3 → semantic route** ✓ |

**13 fragments become semantic routes. 8 move to Tier 1 or Tier 2.**

### 4.3 Implementation

```python
# backend/app/agent/fragment_router.py

from semantic_router import Route, RouteLayer
from semantic_router.encoders import HuggingFaceEncoder
from .manifest import load_manifest, get_tier3_fragments, FragmentMeta
from pathlib import Path

_route_layer: RouteLayer | None = None
_route_fragment_map: dict[str, FragmentMeta] = {}

def build_route_layer(prompts_dir: Path) -> RouteLayer:
    """Build a RouteLayer from Tier 3 prompt files on disk.
    
    Reads prompts/manifest.yaml for utterances and tier classification.
    Called once at worker startup. Hot-reload when manifest changes.
    """
    global _route_layer, _route_fragment_map
    
    manifest = load_manifest(prompts_dir)
    tier3 = get_tier3_fragments(manifest)
    
    encoder = HuggingFaceEncoder(name="all-MiniLM-L6-v2")  # Local, fast, free
    
    routes = []
    _route_fragment_map = {}
    
    for frag in tier3:
        if not frag.utterances:
            continue
        
        route = Route(
            name=frag.path,  # Use file path as route name
            utterances=frag.utterances,
            score_threshold=0.4,
        )
        routes.append(route)
        _route_fragment_map[frag.path] = frag
    
    _route_layer = RouteLayer(encoder=encoder, routes=routes)
    return _route_layer


async def select_fragments_by_similarity(
    user_message: str,
    top_k: int = 5,
) -> list[FragmentMeta]:
    """Fast embedding-based Tier 3 fragment selection. No LLM call.
    
    Returns fragment metadata with content loaded from disk.
    """
    if _route_layer is None:
        return []
    
    results = _route_layer.retrieve_multiple_routes(user_message)
    
    selected = []
    for route_choice in results:
        if route_choice.name in _route_fragment_map:
            frag = _route_fragment_map[route_choice.name]
            selected.append(frag)
    
    return selected[:top_k]
```

### 4.4 Modified Selection Pipeline

```python
# In worker.py — Tier 3 selection is now a direct call, not a pipeline function

from .agent.fragment_router import build_route_layer, select_fragments_by_similarity

# At startup
build_route_layer(prompts_dir)

# Per turn — select Tier 3 fragments from disk via semantic router
tier3_picks = await select_fragments_by_similarity(user_message, top_k=5)

# Assemble: system prompt + Tier 1 (already in system prompt) + Tier 2 (lifecycle) + Tier 3
tier3_content = "\n\n---\n\n".join(f.content for f in tier3_picks)
if tier3_content:
    full_system_prompt += f"\n\n{tier3_content}"
```

The old `_select_relevant_fragments` function in `context_pipeline.py` with its 4-layer pipeline (core → keywords → LLM → budget) is replaced entirely. No keyword triggers, no core tier handling (that's system prompt now), no fragment-from-database loading.

---

## 5. Embedding Model Choice

| Model | Size | Speed | Quality | Cost |
|-------|------|-------|---------|------|
| `all-MiniLM-L6-v2` | 80MB | ~5ms/query | Good | Free, local |
| `nomic-embed-text` (local) | 274MB | ~10ms/query | Very good | Free, local |
| `text-embedding-3-small` (OpenAI) | API | ~100ms/query | Better | $0.02/1M tokens |

**Recommendation:** Start with `all-MiniLM-L6-v2` (local, no API dependency, fast). Upgrade if selection quality is insufficient.

---

## 6. Utterance Generation

Existing `task_triggers` can bootstrap utterances, but they need expansion. An LLM can generate utterances from the fragment content + trigger keywords:

```python
async def generate_utterances(fragment_name: str, fragment_content: str, existing_triggers: list[str]) -> list[str]:
    """Generate 8-12 natural language utterances for a fragment."""
    prompt = f"""Given this prompt fragment and its keyword triggers, generate 8-12 natural language 
    utterances — things a user might say when this fragment would be relevant.
    
    Fragment: {fragment_name}
    Content: {fragment_content[:500]}
    Keywords: {', '.join(existing_triggers)}
    
    Return one utterance per line. Be diverse — include questions, commands, descriptions of problems."""
    
    # ... LLM call ...
```

Run this once for each fragment during migration.

---

## 7. Migration Path

| Step | Work | Risk |
|------|------|------|
| 1 | Classify all 64 prompt files into Tier 1/2/3 (see table in §4.2) | Design decision only |
| 2 | Write `prompts/manifest.yaml` with tier/phase/utterance metadata | New file, versioned in git |
| 3 | `uv add semantic-router sentence-transformers` | Dependency |
| 4 | Implement `manifest.py` — load manifest + read files from disk | New code |
| 5 | Implement `fragment_router.py` — build route layer from manifest | New code |
| 6 | Replace `_select_relevant_fragments` with `select_fragments_by_similarity` in worker | Refactor |
| 7 | Remove `context_pipeline.py` keyword trigger + LLM selection code | Cleanup |
| 8 | Add similarity scores to audit log | Observability |

**Prerequisites:** Doc 028 (checkbox removal + Tier 1 migration to system prompt) must be done first.

---

## 8. What This Doesn't Solve

- **Lifecycle-triggered fragments** — Git best practices at commit time, testing during implementation. That's doc 024.
- **Always-on rules** — Work plan, safety. That's the system prompt (doc 028).
- **Cross-turn context** — Semantic router scores against the current message only. A conversation that started about React but shifted to database work won't re-score.
- **New fragment cold start** — A new fragment needs utterances before it can be matched. Until then, LLM fallback handles it.

---

## 9. Decisions

| Question | Decision |
|----------|----------|
| Which fragments use semantic router? | **Tier 3 only** — context-dependent, domain-specific fragments |
| Replace keywords entirely? | **Yes** — semantic router replaces `_matches_triggers` |
| Local or API embeddings? | **Local** (`all-MiniLM-L6-v2`) |
| When to fall back to LLM? | When max semantic score < 0.6 |
| Rebuild index when? | On fragment create/update/delete |
| Multi-route matching? | **Yes** — return top-k, not just best match |
| Utterances per fragment? | **8-12**, generated from content + existing triggers |
