# Design Doc 047: Skills Federation — Multi-Source Skill Discovery & Adaptive Loading

**Status:** Implemented (Phases 1–4)  
**Author:** Bond Agent  
**Date:** 2026-03-17  
**Depends on:** 010 (Prompt Management)

---

## 1. Problem

Bond has a stub `skills` tool (Phase 3 placeholder) and a `prompts/` directory for static prompt fragments. Meanwhile, the ecosystem has exploded:

| Repository | Format | Focus |
|---|---|---|
| [anthropics/skills](https://github.com/anthropics/skills) | AgentSkills spec (`SKILL.md` + `references/`) | Claude-native skills — API guides, document generation, design |
| [openai/skills](https://github.com/openai/skills) | AgentSkills spec (`SKILL.md` + `references/`) | Codex-oriented — frameworks, CI/CD, devops |
| [vercel-labs/skills](https://github.com/vercel-labs/skills) | AgentSkills spec + CLI (`npx skills`) | Cross-agent installer, growing catalog |
| [obra/superpowers](https://github.com/obra/superpowers) | Skills + agents + commands + hooks | Development methodology — planning, TDD, code review, subagent workflows |
| [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | Context database (filesystem paradigm) | L0/L1/L2 tiered context loading, recursive retrieval, self-evolving memory |

Bond needs to:

1. **Pull skills from multiple upstream repos** without vendoring hundreds of files permanently.
2. **Discover which skill to load at runtime** based on the current task — not dump everything into the system prompt.
3. **Stay current** as upstreams evolve (new skills added, existing ones updated).
4. **Preserve local skills** (OpenClaw's own `~/openclaw/skills/` and `~/.openclaw/skills/`) as first-class citizens.

---

## 2. Design Principles

1. **Lazy loading** — skills are indexed eagerly but content is loaded only when matched. Inspired by OpenViking's L0/L1/L2 tiering.
2. **Spec convergence** — the AgentSkills spec (Anthropic + OpenAI + Vercel all converge on it) is the canonical format. Non-conforming repos get thin adapters.
3. **Git submodules for sync** — upstream repos are submodules, updated on a schedule. No runtime HTTP fetches to GitHub.
4. **Semantic matching** — skill selection uses embedding similarity against the user's task, not just keyword triggers.
5. **Local-first precedence** — local skills override upstream skills with the same name.

---

## 3. Architecture

### 3.1 Skill Sources & Sync

```
bond/
├── vendor/skills/                    # git submodules (read-only)
│   ├── anthropics/                   # github.com/anthropics/skills
│   ├── openai/                       # github.com/openai/skills
│   ├── vercel-labs/                  # github.com/vercel-labs/skills
│   └── superpowers/                  # github.com/obra/superpowers
├── skills/                           # bond-local skills (editable)
│   └── ...
└── ~/.openclaw/skills/               # user-local skills (editable)
    └── ...
```

**Submodule update strategy:**

- `git submodule update --remote` runs as a **scheduled job** (daily cron or on gateway startup, like the existing backup pattern).
- A post-update hook triggers **re-indexing** (see §3.2).
- Submodules are pinned to `main` branch HEAD, not a fixed SHA — we want to track upstream changes.
- If a submodule update fails (network, force-push), the last-good checkout remains and an alert is logged.

### 3.2 Skill Index (The Catalog)

Every skill source is scanned to produce a **unified catalog** — a lightweight index stored in SQLite (or SpacetimeDB if we want UI visibility).

```
┌─────────────────────────────────────────────────────────────┐
│  skill_index                                                │
├─────────────────────────────────────────────────────────────┤
│  id          TEXT PRIMARY KEY   -- "anthropics/claude-api"  │
│  name        TEXT               -- "claude-api"             │
│  source      TEXT               -- "anthropics"             │
│  source_type TEXT               -- "submodule" | "local"    │
│  path        TEXT               -- relative path to SKILL.md│
│  description TEXT               -- from SKILL.md frontmatter│
│  triggers    TEXT               -- extracted trigger phrases │
│  embedding   BLOB               -- vector of description    │
│  l0_summary  TEXT               -- one-line abstract        │
│  l1_overview TEXT               -- ~500 token overview      │
│  updated_at  TIMESTAMP                                      │
│  priority    INT                -- local=100, submodule=50  │
└─────────────────────────────────────────────────────────────┘
```

**Indexing pipeline:**

1. Walk all skill directories, find `SKILL.md` files.
2. Parse YAML frontmatter → extract `name`, `description`, trigger conditions.
3. Generate embedding for the description + trigger text.
4. Generate L0 (one-sentence) and L1 (structural overview) summaries via LLM or template extraction.
5. Store in `skill_index`.

For **obra/superpowers** (which doesn't follow AgentSkills spec exactly), an adapter reads its `skills/*/SKILL.md` files and also indexes `agents/*.md` and `commands/*.md` as pseudo-skills with synthesized descriptions.

### 3.3 Runtime Skill Selection (The Router)

This is the core innovation — deciding which skill(s) to load for a given turn. Inspired by OpenViking's directory recursive retrieval but adapted for Bond's architecture.

```
User message
    │
    ▼
┌─────────────────────┐
│  1. Embed the query  │  (same model as skill indexing)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  2. Vector similarity against skill_index    │
│     → top-K candidates (K=5-10)             │
│     → filter by minimum similarity threshold │
└─────────┬───────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  3. L0 re-ranking                            │
│     Present L0 summaries of candidates to    │
│     the LLM in a lightweight re-rank prompt: │
│     "Which of these skills are relevant to   │
│     the user's request? Return 0-3."         │
└─────────┬───────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  4. L1 injection                             │
│     For selected skills, inject L1 overview  │
│     into context as available_skills block.   │
│     Agent can then read full SKILL.md via    │
│     tool call if it decides to use the skill.│
└─────────────────────────────────────────────┘
```

**Key differences from current OpenClaw approach:**

| Current (OpenClaw) | Proposed (Bond) |
|---|---|
| All skill descriptions injected into system prompt | Only matched skills injected per-turn |
| Agent reads SKILL.md manually | Agent sees L1 overview, reads L2 (full SKILL.md) on demand |
| Static list, human-curated | Dynamically discovered from multiple sources |
| ~15-20 skills manageable | Scales to hundreds of skills |

**Cost budget:** The re-ranking step (step 3) is a small prompt (~500 tokens input, ~50 tokens output). At hundreds of skills, the embedding search (step 2) is sub-millisecond with a local vector index. Total overhead: ~100ms + one small LLM call per turn.

### 3.4 Tiered Context Loading (Borrowing from OpenViking)

OpenViking's L0/L1/L2 pattern maps directly to skills:

| Tier | Content | When Loaded | Token Cost |
|---|---|---|---|
| **L0** | One-sentence description | Always available in index | ~20 tokens/skill |
| **L1** | Structural overview: what the skill does, key references, when to use | Injected when skill matches | ~200-500 tokens/skill |
| **L2** | Full `SKILL.md` + `references/*.md` | Agent reads via tool when it decides to use the skill | 1K-50K tokens |

This means even with 500 indexed skills, the L0 cost for the re-ranking step is only ~10K tokens — well within budget.

### 3.5 Skill Format Normalization

All upstream repos converge on (or are close to) the AgentSkills spec:

```yaml
---
name: skill-name
description: "When to trigger this skill."
---
# Skill Title
Instructions...
```

**Adapters needed:**

| Source | Adaptation |
|---|---|
| `anthropics/skills` | Native AgentSkills format ✅ |
| `openai/skills` | Native AgentSkills format ✅ |
| `vercel-labs/skills` | Native AgentSkills format ✅ |
| `obra/superpowers` | Skills dir is AgentSkills ✅; `agents/` and `commands/` need frontmatter synthesis from file content |

No adapter needed for OpenViking — it's not a skill repo, it's an infrastructure pattern we borrow concepts from.

---

## 4. Skill Precedence & Conflict Resolution

When multiple sources provide a skill for the same domain:

```
Priority (highest first):
1. ~/.openclaw/skills/        (user-local overrides)
2. bond/skills/               (bond-local skills)
3. vendor/skills/anthropics/  (upstream submodules — ordered by config)
4. vendor/skills/openai/
5. vendor/skills/vercel-labs/
6. vendor/skills/superpowers/
```

**Conflict rules:**
- Skills with identical `name` → highest priority wins, lower-priority version hidden.
- Skills with overlapping descriptions but different names → both indexed, router picks the best match.
- User can pin/exclude skills via config: `bond.skills.exclude: ["openai/aspnet-core"]` or `bond.skills.pin: ["anthropics/claude-api"]`.

---

## 5. Self-Evolution: Learning Which Skills Work

Borrowing from OpenViking's session management concept, Bond can track skill effectiveness:

```
┌─────────────────────────────────────────────────────────────┐
│  skill_usage                                                │
├─────────────────────────────────────────────────────────────┤
│  skill_id       TEXT       -- FK to skill_index             │
│  session_id     TEXT       -- conversation where used       │
│  loaded_at      TIMESTAMP -- when L2 was loaded             │
│  task_category  TEXT       -- inferred task type             │
│  outcome        TEXT       -- "used" | "loaded_but_unused"  │
│                              | "user_rejected"              │
│  feedback_score FLOAT     -- optional explicit feedback     │
└─────────────────────────────────────────────────────────────┘
```

Over time, this table feeds back into the router:
- Skills that are consistently loaded but unused get deprioritized.
- Skills with high usage for certain task categories get boosted.
- The LLM re-ranker prompt can include "this skill was helpful 8/10 times for similar tasks."

This is the "constantly adapt" piece — not just tracking upstream changes, but learning which upstream skills are actually useful for this particular Bond instance.

### 5.2 Skill Scoring Model

Each skill accumulates a composite score (0.0–1.0) from implicit and explicit signals:

**Implicit signals** (collected automatically from the agent loop):

| Signal | Weight | What It Measures |
|---|---|---|
| Precision (used / loaded) | 0.25 | Was the skill actually relevant when matched? |
| Depth (references read, scripts run) | 0.20 | Did the agent go beyond SKILL.md? |
| Task completion after skill | 0.15 | Did the turn end with a `respond` after activation? |
| Re-activation rate | 0.10 | Is the skill consistently useful for similar queries? |
| Recency (exponential decay, 30-day half-life) | 0.10 | Has it been used lately? |

**Explicit signals** (from user feedback):

| Signal | Weight | Source |
|---|---|---|
| Thumbs up/down | 0.20 | Persistent toast notification in the UI (see §5.3) |

**Cold start:** New skills default to 0.5. Skills from first-party repos (Anthropic, OpenAI) start at 0.55. Score inherits from similar skills (by description embedding distance) if available.

**Score decay:** Skills that haven't been loaded in 60+ days decay toward 0.3 (not zero — they may just not have been needed).

### 5.3 Skill Feedback UI — Persistent Toast

When a skill is activated during a conversation, the frontend shows a **persistent toast notification** in the bottom-right corner. The toast:

- **Does not auto-dismiss** — it stays until the user votes (👍/👎) or explicitly closes (✕).
- Shows the skill name and source (e.g., "claude-api from anthropics").
- After voting, shows a confirmation message and fades out after 1.5s.
- Multiple skill activations stack vertically.

**Component:** `frontend/src/components/shared/SkillFeedbackToast.tsx`

**Integration flow:**

1. **Backend emits a `skill_activated` WebSocket message** when the agent loads a skill's full SKILL.md content. Payload:
   ```json
   {
     "type": "skill_activated",
     "content": "{\"id\":\"act_abc123\",\"skillName\":\"claude-api\",\"skillSource\":\"anthropics\",\"activatedAt\":1710712800}"
   }
   ```

2. **Frontend `page.tsx`** receives the message in the existing WebSocket handler, adds it to `skillActivations` state.

3. **`SkillFeedbackStack`** renders the persistent toasts.

4. **On vote**, the frontend sends a `skill_feedback` message back through the WebSocket:
   ```json
   {
     "type": "skill_feedback",
     "activationId": "act_abc123",
     "vote": "up"
   }
   ```

5. **Backend records** the vote in the `skill_usage` table and updates the skill's composite score.

### 5.4 Updated Schema

```sql
CREATE TABLE skill_usage (
    id              TEXT PRIMARY KEY,     -- activation ID (act_xxx)
    skill_id        TEXT NOT NULL,        -- FK to skill_index
    session_id      TEXT NOT NULL,        -- conversation where used
    activated_at    TIMESTAMP NOT NULL,   -- when description matched
    loaded_at       TIMESTAMP,            -- when full SKILL.md was read
    
    -- Implicit signals
    references_read INTEGER DEFAULT 0,    -- count of references/*.md reads
    scripts_run     INTEGER DEFAULT 0,    -- count of scripts/* executions
    task_completed  BOOLEAN DEFAULT FALSE,-- did the turn end with respond?
    turns_after     INTEGER DEFAULT 0,    -- turns between load and task end
    tokens_used     INTEGER DEFAULT 0,    -- skill tokens in context
    
    -- Explicit signals
    user_vote       TEXT CHECK(user_vote IN ('up', 'down')),
    voted_at        TIMESTAMP,
    
    -- Derived
    task_category   TEXT,                 -- inferred from query embedding cluster
    query_embedding BLOB                  -- for finding similar future queries
);

CREATE TABLE skill_scores (
    skill_id        TEXT PRIMARY KEY,     -- FK to skill_index
    score           FLOAT NOT NULL DEFAULT 0.5,
    precision_rate  FLOAT,
    depth_rate      FLOAT,
    total_loads     INTEGER DEFAULT 0,
    total_uses      INTEGER DEFAULT 0,
    thumbs_up       INTEGER DEFAULT 0,
    thumbs_down     INTEGER DEFAULT 0,
    last_used       TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL
);
```

---

## 6. Implementation Plan

### Phase 1: Submodules + Static Index (Week 1-2)

1. Add the four repos as git submodules under `vendor/skills/`.
2. Write a `scripts/index-skills.py` that walks all sources, parses SKILL.md frontmatter, and produces a `skills.json` catalog.
3. Modify the system prompt builder to use the catalog instead of hardcoded `<available_skills>`.
4. Add a gateway startup hook to run `git submodule update --remote` and re-index.
5. Replace the `handle_skills` stub with a real tool that can search/read from the catalog.

### Phase 2: Semantic Router (Week 3-4)

1. Generate embeddings for all skill descriptions (use the existing embedding model — `gemini-embedding-001` or similar).
2. Store embeddings in the skill index (SQLite with a vector extension, or just numpy cosine similarity for <500 skills).
3. Implement the 4-step routing pipeline (§3.3) as middleware in the agent loop.
4. Add L0/L1 generation for all indexed skills.
5. Benchmark: measure latency and token cost of the routing step.

### Phase 3: Adaptive Learning (Week 5-6)

1. Add `skill_usage` tracking table.
2. Instrument the agent loop to record when skills are loaded and whether they're actually used.
3. Feed usage data back into the re-ranker prompt.
4. Add a `skills` management UI page (list all sources, see usage stats, pin/exclude skills).

### Phase 4: OpenViking Integration (Optional, Week 7+)

If the catalog grows beyond ~500 skills, or if we want the full recursive retrieval capability:

1. Deploy OpenViking as a sidecar service.
2. Ingest the skill catalog into OpenViking's `viking://agent/skills/` namespace.
3. Replace the homegrown vector search with OpenViking's directory recursive retrieval.
4. Potentially extend to `viking://resources/` for project-specific context and `viking://user/` for user preferences.

This phase is optional — the homegrown approach in Phase 2 should handle the realistic scale (hundreds, not thousands of skills) well.

---

## 7. Configuration

```yaml
# bond.yaml or agent config
skills:
  sources:
    - type: submodule
      path: vendor/skills/anthropics
      repo: https://github.com/anthropics/skills
      enabled: true
    - type: submodule
      path: vendor/skills/openai
      repo: https://github.com/openai/skills
      enabled: true
    - type: submodule
      path: vendor/skills/vercel-labs
      repo: https://github.com/vercel-labs/skills
      enabled: true
    - type: submodule
      path: vendor/skills/superpowers
      repo: https://github.com/obra/superpowers
      enabled: true
    - type: local
      path: skills/
      priority: 100
    - type: local
      path: ~/.openclaw/skills/
      priority: 110

  router:
    embedding_model: gemini-embedding-001
    top_k: 8
    min_similarity: 0.35
    max_skills_per_turn: 3
    rerank_model: default  # uses the agent's current model

  sync:
    schedule: "0 4 * * *"  # daily at 4am
    on_startup: true
    alert_on_failure: true

  exclude: []
  pin: []
```

---

## 8. How This Compares to Alternatives

### vs. `npx skills add` (Vercel approach)
Vercel's CLI copies/symlinks skill files into agent-specific directories (`.claude/`, `.codex/`, etc.). This is great for single-agent setups but doesn't work for Bond where:
- Multiple agents share a skill catalog.
- We want runtime selection, not install-time selection.
- We don't want N copies of the same skill for N agents.

We can still use `npx skills` as an additional install method — it just lands files in the local skills directory, which we index like anything else.

### vs. OpenViking as primary store
OpenViking is powerful but heavyweight — it requires Python 3.10+, Go 1.22+, a C++ compiler, and runs its own server. For Bond's current scale (<500 skills), a simple SQLite index with embeddings is sufficient. OpenViking becomes worthwhile if:
- We want to unify skills, memory, and resources into one context system.
- The skill catalog grows past 1000+ entries.
- We want the visualization/observability features.

We design for this as an optional Phase 4 — the index schema is compatible.

### vs. Static curated list (current OpenClaw approach)
The current approach (hand-maintained `<available_skills>` in the system prompt) works at ~15-20 skills. It breaks at 100+. The design here is a direct evolution: same concept (description-based matching), but automated, scaled, and adaptive.

---

## 9. Open Questions

1. **Submodule vs. sparse checkout?** Some repos (especially `openai/skills`) may grow very large. Sparse checkout could limit to just the `skills/` directory. Trade-off: more complex git setup vs. smaller checkout.

2. **Cross-agent skill compatibility.** AgentSkills written for Claude Code may reference Claude-specific features (thinking blocks, `AskUserQuestion`). Should we tag skills with agent compatibility and filter accordingly?

3. **Skill versioning.** If an upstream skill changes in a breaking way, should we pin submodules to tags/releases rather than tracking HEAD? This conflicts with "constantly adapt" but adds stability.

4. **Re-ranking cost.** The LLM re-ranking step (§3.3 step 3) adds one small inference call per turn. For high-frequency agents, this could add up. Alternative: skip re-ranking and use pure vector similarity with a high threshold. Need to benchmark quality vs. cost.

5. **Superpowers as a methodology vs. skills.** Superpowers isn't just skills — it's a development workflow (plan → TDD → subagent-driven-development → review). Should Bond adopt the methodology holistically, or cherry-pick individual skills?

---

## 10. References

- [AgentSkills Spec](https://agentskills.io/specification)
- [OpenViking Documentation](https://www.openviking.ai/docs)
- [Superpowers README](https://github.com/obra/superpowers)
- [Vercel Skills CLI](https://github.com/vercel-labs/skills)
- [Bond Design Doc 010 — Prompt Management](docs/design/010-prompt-management.md)
- [Bond Design Doc 013 — OpenSandbox Submodule](docs/design/013-opensandbox-submodule.md) (precedent for submodule pattern)
