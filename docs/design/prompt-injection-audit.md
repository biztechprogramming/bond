# Design Doc: Prompt Injection Auditing with Langfuse

**Author:** Developer Agent  
**Date:** 2026-03-07  
**Status:** Draft  

---

## Problem

We have 64 prompt fragments across the `prompts/` directory tree, plus DB-managed fragments (SpacetimeDB `prompt_fragments` table), all injected into LLM context on every turn. Currently there's no visibility into:

1. **Which fragments are actually being used** — and how often
2. **Per-request breakdown** — what went into a specific LLM call (for debugging)
3. **Usage trends** — daily/weekly/monthly injection frequency per fragment
4. **Cost attribution** — token spend per fragment over time

## Solution: Langfuse

[Langfuse](https://langfuse.com) is an open-source LLM observability platform. Self-hosted via Docker, it gives us:

- **Trace waterfall views** — see every LLM call, what went in, what came out
- **Token usage / cost dashboards** — built-in charts by model, time period, user
- **Latency histograms** — P50/P95/P99 response times
- **Custom metadata on every trace** — this is where we attach fragment injection data
- **Session grouping** — group traces by conversation
- **Filtering / search** — find traces by metadata, tags, model, time range
- **Prompt management** (bonus) — version and A/B test prompts natively

We get all the dashboards, charts, and debugging UI for free. We don't build any visualization layer.

## Why This Is Easy

**LiteLLM already has a native Langfuse integration.** The worker calls `litellm.acompletion()` for every LLM request. LiteLLM has a built-in Langfuse callback that automatically logs:

- Model, provider
- Input messages (full prompt)
- Output (response)
- Token usage (prompt, completion, cache hits)
- Latency
- Cost (litellm calculates this per-model)
- Error details

We just enable it. Then we enrich the metadata with fragment injection details.

---

## Architecture

```
Worker (container)
  │
  ├── [context pipeline: select fragments, build system prompt]
  │
  ├── litellm.acompletion(
  │       model=...,
  │       messages=...,
  │       metadata={                          ← fragment data rides here
  │           "trace_name": "agent-turn",
  │           "session_id": conversation_id,
  │           "tags": ["agent:bond"],
  │           "fragments_injected": [...],
  │           "fragment_count": 12,
  │           "selection_reasons": {...},
  │           ...
  │       }
  │   )
  │   │
  │   └── litellm Langfuse callback ──────► Langfuse (self-hosted)
  │                                              │
  │                                              ├── Postgres (trace storage)
  │                                              └── Web UI (dashboards, trace explorer)
  │
  └── [tool loop continues...]

Frontend
  └── Link to Langfuse UI (or embed via iframe)
```

### No Custom Write Endpoints

There are no `POST /audit/*` endpoints. No SQLite audit DB. No gateway changes for writes. LiteLLM's callback handles all logging automatically. Langfuse's built-in UI handles all visualization.

The only code changes are in the worker:
1. Enable the Langfuse callback
2. Attach fragment metadata to each `litellm.acompletion()` call

---

## Infrastructure: Self-Hosted Langfuse

### Docker Compose

New file: `docker-compose.langfuse.yml`

```yaml
version: '3.8'

services:
  langfuse-db:
    image: postgres:16
    container_name: bond-langfuse-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    ports:
      - "5436:5432"
    volumes:
      - langfuse-db-data:/var/lib/postgresql/data

  langfuse:
    image: langfuse/langfuse:latest
    container_name: bond-langfuse
    restart: unless-stopped
    depends_on:
      - langfuse-db
    ports:
      - "18786:3000"
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:18786
      NEXTAUTH_SECRET: bond-langfuse-secret-change-me
      SALT: bond-langfuse-salt-change-me
      TELEMETRY_ENABLED: "false"
      # Allow sign-up for initial admin user, then disable
      AUTH_DISABLE_SIGNUP: "false"

volumes:
  langfuse-db-data:
```

Runs alongside SpacetimeDB: `docker compose -f docker-compose.langfuse.yml up -d`

Langfuse UI at `http://localhost:18786`.

After first login, create a project and get the API keys (public key + secret key).

### Port Allocation

| Service | Port |
|---------|------|
| SpacetimeDB | 18787 |
| Frontend | 18788 |
| Gateway WS | 18789 |
| Backend API | 18790 |
| **Langfuse UI** | **18786** |
| Langfuse Postgres | 5436 (internal) |

---

## Implementation

### Phase 1: Enable LiteLLM → Langfuse Callback

**Files changed:** `backend/app/worker.py`

At worker startup (in `_startup()` or `_lifespan()`), enable the callback:

```python
import litellm

# Enable Langfuse logging for all LLM calls
litellm.success_callback = ["langfuse"]
litellm.failure_callback = ["langfuse"]
```

**Environment variables** (injected into the worker container):

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...      # from Langfuse project settings
LANGFUSE_SECRET_KEY=sk-lf-...      # from Langfuse project settings
LANGFUSE_HOST=http://host.docker.internal:18786   # worker runs in Docker
```

That's it for basic integration. Every `litellm.acompletion()` call now automatically logs to Langfuse with:
- Full input/output messages
- Token usage + cost
- Model, latency, errors
- Cache hit stats (Anthropic prompt caching)

### Phase 2: Attach Fragment Metadata

**Files changed:**
- `backend/app/agent/context_pipeline.py` — return selection metadata from `_select_relevant_fragments`
- `backend/app/agent/tools/dynamic_loader.py` — return file metadata from `load_universal_fragments`
- `backend/app/worker.py` — build metadata dict, pass to `litellm.acompletion()`

#### 2a. `_select_relevant_fragments` → richer return

```python
@dataclass
class FragmentDetail:
    source: str          # "db"
    id: str
    name: str
    tier: str
    selection_reason: str  # "core_always" | "keyword_trigger" | "llm_selected"
    token_estimate: int

@dataclass
class FragmentSelectionResult:
    fragments: list[dict]                    # same list as before (backward-compat)
    details: list[FragmentDetail]            # per-fragment metadata
    selection_time_ms: int
```

#### 2b. `load_universal_fragments_with_meta()`

```python
def load_universal_fragments_with_meta(prompts_dir: Path) -> tuple[str, list[dict]]:
    """Returns (content_string, fragment_metadata_list)."""
    # Each entry: {"source": "universal", "path": "universal/safety.md",
    #              "name": "safety", "tokenEstimate": 320}
```

#### 2c. Build metadata and pass to litellm

In `_run_agent_loop`, after context assembly, build the metadata dict:

```python
import hashlib

# Collect fragment metadata
fragments_meta = []

# DB fragments (from selection result)
for detail in selection_result.details:
    fragments_meta.append({
        "source": detail.source,
        "id": detail.id,
        "name": detail.name,
        "tier": detail.tier,
        "reason": detail.selection_reason,
        "tokens": detail.token_estimate,
    })

# Universal fragments
for meta in universal_meta:
    fragments_meta.append({
        "source": "universal",
        "name": meta["name"],
        "path": meta["path"],
        "tokens": meta["token_estimate"],
    })

# Manifest
if _manifest:
    fragments_meta.append({
        "source": "manifest",
        "name": "prompt_manifest",
        "tokens": _estimate_tokens(_manifest),
    })

# Build Langfuse metadata
langfuse_metadata = {
    # Trace-level
    "trace_name": f"agent-turn-{_state.agent_id}",
    "session_id": conversation_id,
    "tags": [
        f"agent:{_state.agent_id}",
        f"model:{model}",
        f"fragments:{len(fragments_meta)}",
    ],

    # Fragment injection data (custom metadata — visible in Langfuse trace detail)
    "fragments_injected": fragments_meta,
    "fragment_count": len(fragments_meta),
    "fragment_names": [f["name"] for f in fragments_meta],
    "fragment_sources": list(set(f["source"] for f in fragments_meta)),
    "fragment_total_tokens": sum(f.get("tokens", 0) for f in fragments_meta),

    # Selection breakdown
    "selection_reasons": {
        "core_always": sum(1 for f in fragments_meta if f.get("reason") == "core_always"),
        "keyword_trigger": sum(1 for f in fragments_meta if f.get("reason") == "keyword_trigger"),
        "llm_selected": sum(1 for f in fragments_meta if f.get("reason") == "llm_selected"),
        "tool_requested": sum(1 for f in fragments_meta if f.get("reason") == "tool_requested"),
    },

    # Context pipeline stats
    "system_prompt_hash": hashlib.sha256(full_system_prompt.encode()).hexdigest()[:16],
    "system_prompt_tokens": _estimate_tokens(full_system_prompt),
    "had_history_compression": compression_stats.get("original_tokens", 0) > COMPRESSION_THRESHOLD,
    "had_sliding_window": len(history) != len(windowed_history),
    "fragment_selection_ms": selection_result.selection_time_ms,
}
```

Then pass it to every `litellm.acompletion()` call in the tool loop:

```python
response = await litellm.acompletion(
    model=_iter_model,
    messages=_call_messages,
    tools=tool_defs if tool_defs else None,
    temperature=0.7,
    max_tokens=current_max_tokens,
    metadata=langfuse_metadata,         # ← this is all it takes
    **_iter_kwargs,
)
```

LiteLLM's Langfuse callback picks up `metadata` and attaches it to the trace. `session_id` groups traces by conversation. `tags` enable filtering. `fragments_injected` appears in the trace detail view.

### Phase 3: `load_context` Tool Traces

When the agent calls `load_context` mid-conversation, we want it to appear as a child span in the Langfuse trace. LiteLLM supports `trace_id` and `parent_observation_id` in metadata for nesting.

In the tool dispatch section of the worker loop, when `load_context` is called:

```python
# After load_context_fragments() returns
langfuse_metadata["fragments_injected"].extend(new_category_fragments)
langfuse_metadata["fragment_count"] = len(langfuse_metadata["fragments_injected"])
# The next litellm call in the loop will carry the updated metadata
```

### Phase 4: Langfuse Scores (Optional)

Langfuse supports attaching **scores** to traces — useful for tracking quality:

```python
from langfuse import Langfuse

langfuse = Langfuse()

# After the agent turn completes, score the trace
langfuse.score(
    trace_id=trace_id,
    name="fragment_efficiency",
    value=useful_fragments / total_fragments,  # what % of fragments were relevant
    comment="Ratio of fragments used vs injected",
)
```

This enables Langfuse's built-in score analytics — track fragment efficiency over time.

---

## What Langfuse Gives Us (Built-In)

### Dashboards (no code)
- **Token usage over time** — by model, by day/week/month
- **Cost tracking** — per-model, per-conversation
- **Latency charts** — P50/P95/P99
- **Request volume** — total LLM calls per period
- **Error rate** — failed calls over time

### Trace Explorer (debugging)
- **Full trace view** — click any trace to see: input messages, output, token counts, latency, cost, all metadata
- **Filter by metadata** — find traces where `fragment_count > 20` or `fragments_injected` contains a specific name
- **Filter by tags** — e.g. all traces for `agent:bond` or `model:claude-sonnet`
- **Session view** — see all traces in a conversation grouped together, in order
- **Nested spans** — if the agent makes multiple LLM calls in one turn (tool loop), they nest under one trace

### Fragment Analysis (via metadata filters)
- "Which conversations used `database.spacetimedb.reducers`?" → filter traces by `fragment_names` contains `reducers`
- "What's the average fragment count per request?" → Langfuse analytics on `fragment_count` metadata
- "Show me requests with `llm_selected` fragments" → filter by tags or metadata

---

## What We Lose vs Custom SQLite

| Capability | Langfuse | Custom SQLite |
|-----------|----------|---------------|
| Per-fragment GROUP BY aggregation | Via metadata filters + export | Native SQL |
| "Least used fragments" leaderboard | Manual (export → query) or Langfuse API | Native SQL |
| Offline access (no server needed) | Needs Langfuse running | Just a file |
| Custom query flexibility | Limited to Langfuse's filter UI + API | Full SQL |
| Pre-built charts & dashboards | ✅ Built-in | ❌ Build from scratch |
| Trace waterfall / debugging UI | ✅ Built-in | ❌ Build from scratch |
| Cost tracking | ✅ Automatic | ❌ Manual |
| Prompt version tracking | ✅ Built-in | ❌ Not included |
| Setup effort | Docker Compose + 3 env vars | New dependency + schema + router + endpoints |

**If we need the SQL flexibility later** (e.g. "which fragments are never used"), we can add the SQLite audit layer alongside Langfuse — they're not mutually exclusive. The worker instrumentation is the same either way.

---

## Implementation Summary

| What | Where | Effort |
|------|-------|--------|
| Langfuse Docker Compose | `docker-compose.langfuse.yml` | New file, ~30 lines |
| Enable litellm callback | `worker.py` startup | 3 lines |
| Environment variables | Worker container env | 3 vars |
| Fragment metadata collection | `context_pipeline.py`, `dynamic_loader.py` | Modify return types |
| Attach metadata to LLM calls | `worker.py` `_run_agent_loop` | ~40 lines |
| `load_context` tracking | `worker.py` tool dispatch | ~10 lines |

**Total: 2 stories.**

1. **Infrastructure + basic integration** — Docker Compose, env vars, enable callback, verify traces appear
2. **Fragment metadata enrichment** — modify context pipeline returns, build metadata dict, attach to calls

No gateway changes. No new endpoints. No new DB. No frontend work. Langfuse is the frontend.

---

## Getting Started

```bash
# 1. Start Langfuse
docker compose -f docker-compose.langfuse.yml up -d

# 2. Open http://localhost:18786, create account + project

# 3. Copy API keys from project settings, add to worker env:
#    LANGFUSE_PUBLIC_KEY=pk-lf-...
#    LANGFUSE_SECRET_KEY=sk-lf-...
#    LANGFUSE_HOST=http://host.docker.internal:18786

# 4. Deploy worker with callback enabled — traces start flowing
```
