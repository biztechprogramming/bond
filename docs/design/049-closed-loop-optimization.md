# 049 — Closed-Loop Optimization Engine

## Problem

Doc 048 introduced a self-optimizing prompt system: a critic agent reviews execution traces and surfaces recurring lessons. It works — lessons accumulate and get injected into the system prompt. But it has structural gaps that prevent it from becoming a true self-improving system:

1. **No outcome measurement.** The critic generates lessons, but nothing measures whether those lessons actually improve agent performance. A lesson could make things worse and we'd never know.
2. **Prompt-only optimization.** The system only generates text lessons. It can't tune the ~30 numeric parameters that govern agent behavior: compression thresholds, token tiers, similarity scores, cycle detection windows, delegation thresholds, etc.
3. **Wrong embedding stack.** The critic uses FastEmbed (`BAAI/bge-small-en-v1.5`) for candidate similarity — a small local model. Meanwhile, the rest of the system uses Voyage 4 Large (1024-dim) with sqlite-vec for knowledge/memory search. The critic should use the same infrastructure.
4. **Flat-file storage.** `candidates.jsonl` doesn't scale and can't be queried efficiently. The system already has sqlite-vec tables for vector search — candidates should live there too.
5. **No A/B testing.** There's no mechanism to compare the effect of a change against a baseline.

## Goal

Build a **closed-loop optimization engine** that:

- Measures agent performance per-turn with structured outcome signals
- Proposes changes to both prompts **and** numeric parameters
- Tests changes against baselines using A/B cohorts
- Stores all embeddings in sqlite-vec via the existing Voyage 4 pipeline
- Promotes changes only when statistically supported by outcome data

The human remains in the loop for final approval. The system does the analysis.

## Design Principles

- **Measure before you optimize.** Outcome tracking comes first. Without it, optimization is guessing.
- **Use what exists.** Voyage 4 + sqlite-vec + the knowledge store's embedding pipeline are already battle-tested. Don't build parallel infrastructure.
- **Small blast radius.** Parameter changes apply to one agent at a time. Rollback is instant (restore previous config snapshot).
- **Transparent.** Every change has a provenance trail: which observations triggered it, what the expected improvement was, what the measured improvement actually was.

---

## Architecture Overview

```
                    ┌─────────────────────────┐
                    │    Turn Execution        │
                    │  (worker.py agent loop)  │
                    └────────┬────────────────┘
                             │
                    ┌────────▼────────────────┐
                    │   Outcome Collector      │
                    │  (structured signals)    │
                    └────────┬────────────────┘
                             │
              ┌──────────────▼──────────────────┐
              │       Observation Store          │
              │  (sqlite-vec: outcomes + traces) │
              └──────────────┬──────────────────┘
                             │
                    ┌────────▼────────────────┐
                    │    Analysis Engine       │
                    │  (periodic background)   │
                    └────────┬────────────────┘
                             │
              ┌──────────────▼──────────────────┐
              │       Proposal Queue             │
              │  (prompt changes + param tweaks) │
              └──────────────┬──────────────────┘
                             │
                    ┌────────▼────────────────┐
                    │    A/B Cohort Runner     │
                    │  (shadow evaluation)     │
                    └────────┬────────────────┘
                             │
                    ┌────────▼────────────────┐
                    │    Human Review          │
                    │  (approve / reject)      │
                    └─────────────────────────┘
```

---

## 1. Outcome Collector

### Signals

Every turn already records cost and iteration data (Phase 4B in worker.py). Extend the existing audit event with structured outcome signals:

| Signal | Source | Type | Notes |
|--------|--------|------|-------|
| `tool_calls` | Worker loop | int | Total tool calls in the turn |
| `iterations` | Worker loop | int | LLM call iterations |
| `total_cost` | litellm cost tracker | float | Dollar cost of the turn |
| `input_tokens` | litellm | int | Total input tokens |
| `output_tokens` | litellm | int | Total output tokens |
| `wall_time_ms` | Worker loop | int | End-to-end turn latency |
| `had_loop_intervention` | Loop detection | bool | Repetition/cycle breaker fired |
| `had_continuation` | Token tier escalation | bool | Response was truncated and continued |
| `had_compression` | Context pipeline | bool | History compression was triggered |
| `fragments_selected` | Already tracked | int | Tier 3 fragments selected |
| `fragment_names` | Already tracked | list[str] | Which fragments were active |
| `user_correction` | Heuristic | bool | Next user message corrects/retries |
| `task_category` | Classifier | str | coding / research / chat / etc. |
| `outcome_score` | Composite | float | 0.0–1.0 composite quality score |

### Composite Score

The `outcome_score` is computed from the raw signals. Lower cost, fewer loops, no user corrections = higher score.

```python
def compute_outcome_score(signals: dict) -> float:
    score = 1.0

    # Penalties
    if signals["had_loop_intervention"]:
        score -= 0.3
    if signals["user_correction"]:
        score -= 0.4
    if signals["had_continuation"]:
        score -= 0.1

    # Efficiency bonus/penalty relative to task category baseline
    category = signals["task_category"]
    baseline = CATEGORY_BASELINES.get(category, {})
    if baseline:
        cost_ratio = signals["total_cost"] / max(baseline["median_cost"], 0.001)
        if cost_ratio > 2.0:
            score -= 0.2  # 2x more expensive than typical
        elif cost_ratio < 0.5:
            score += 0.1  # Notably efficient

    return max(0.0, min(1.0, score))
```

### User Correction Detection

A simple heuristic: if the next user message in the same conversation contains correction patterns ("no", "wrong", "try again", "that's not", "I said", "actually"), flag the prior turn as corrected. This runs retroactively when the next message arrives.

---

## 2. Observation Store (sqlite-vec)

### Why Not JSONL

Doc 048 uses `candidates.jsonl` for candidate storage. Problems:

- Linear scan for similarity search (O(n) per query)
- No concurrent access safety
- No structured queries ("show me all candidates from the last week with score > 0.7")
- Different infrastructure from the rest of the system

### Schema

Add two new tables to the agent's local sqlite database (same DB that already has `memories`, `content_chunks`, etc.):

```sql
-- Turn-level observations with outcome signals
CREATE TABLE IF NOT EXISTS optimization_observations (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    task_category TEXT,
    user_message_preview TEXT,        -- first 200 chars
    signals_json TEXT NOT NULL,       -- full structured signals
    outcome_score REAL NOT NULL,
    config_snapshot_json TEXT,        -- active params at time of turn
    active_lessons_hash TEXT,         -- hash of injected lessons content
    cohort TEXT DEFAULT 'control'     -- 'control' or experiment ID
);

-- Vec0 table for semantic search over observations
CREATE VIRTUAL TABLE IF NOT EXISTS optimization_observations_vec
    USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[1024]);

-- Lesson candidates (replaces candidates.jsonl)
CREATE TABLE IF NOT EXISTS optimization_candidates (
    id TEXT PRIMARY KEY,
    lesson_text TEXT NOT NULL,
    source_observation_id TEXT REFERENCES optimization_observations(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    similar_count INTEGER DEFAULT 0,
    promoted BOOLEAN DEFAULT FALSE,
    promoted_at TEXT
);

-- Vec0 table for candidate similarity
CREATE VIRTUAL TABLE IF NOT EXISTS optimization_candidates_vec
    USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[1024]);

-- Parameter experiments
CREATE TABLE IF NOT EXISTS optimization_experiments (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    param_key TEXT NOT NULL,          -- e.g. 'COMPRESSION_THRESHOLD'
    baseline_value TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    rationale TEXT,
    status TEXT DEFAULT 'proposed',   -- proposed | active | concluded
    control_obs_count INTEGER DEFAULT 0,
    experiment_obs_count INTEGER DEFAULT 0,
    control_mean_score REAL,
    experiment_mean_score REAL,
    p_value REAL,
    concluded_at TEXT,
    conclusion TEXT                    -- 'promoted' | 'rejected' | 'inconclusive'
);
```

### Embedding Pipeline

The critic currently uses its own FastEmbed encoder. Replace with the existing `EmbeddingEngine`:

```python
# Before (critic.py):
from fastembed import TextEmbedding
_encoder = TextEmbedding("BAAI/bge-small-en-v1.5")

# After:
# Reuse the worker's EmbeddingEngine instance (Voyage 4 Large, 1024-dim)
# Passed in from the worker at critique time
async def embed_candidate(text: str, engine: EmbeddingEngine) -> list[float]:
    return await engine.embed_query(text)
```

This means:
- **Same model** (Voyage 4 Large) as knowledge store and memory search
- **Same dimension** (1024) as all other vec0 tables
- **Same API key management** — no separate config
- **Better quality** — Voyage 4 Large significantly outperforms bge-small for semantic similarity

### Similarity Search via vec0

Replace the linear-scan cosine similarity in `critic.py` with vec0 queries:

```python
async def find_similar_candidates(
    embedding: list[float],
    threshold: float = 0.3,
    limit: int = 20,
    db: aiosqlite.Connection = None,
) -> list[dict]:
    """Find similar candidates using vec0 cosine search."""
    import json
    cursor = await db.execute(
        """
        SELECT c.id, c.lesson_text, c.similar_count, c.created_at,
               v.distance AS cosine_distance
        FROM optimization_candidates_vec v
        JOIN optimization_candidates c ON c.id = v.id
        WHERE v.embedding MATCH ?
          AND k = ?
        """,
        (json.dumps(embedding), limit),
    )
    rows = await cursor.fetchall()
    # vec0 returns cosine distance; convert to similarity
    return [
        {
            "id": r[0],
            "lesson_text": r[1],
            "similar_count": r[2],
            "created_at": r[3],
            "similarity": 1.0 - r[4],
        }
        for r in rows
        if (1.0 - r[4]) >= threshold
    ]
```

---

## 3. Analysis Engine

The analysis engine runs periodically (after every N turns, or on a cron-like schedule within the worker). It replaces the per-turn critic with a batch analysis that's more statistically robust.

### 3a. Prompt Lesson Analysis

Same spirit as doc 048's critic, but:

- Operates on **batches** of observations, not single turns
- Looks for **patterns across turns** with low outcome scores
- Uses vec0 to check candidate novelty (instead of linear scan)
- Embeds with Voyage 4 (not bge-small)

```python
async def analyze_batch(
    observations: list[dict],
    engine: EmbeddingEngine,
    db: aiosqlite.Connection,
):
    """Analyze a batch of recent low-scoring observations.

    Groups by failure mode, generates lesson candidates,
    checks novelty against existing candidates via vec0.
    """
    low_scoring = [o for o in observations if o["outcome_score"] < 0.6]
    if len(low_scoring) < 3:
        return  # Not enough signal

    # Cluster low-scoring observations by embedding similarity
    # to find common failure patterns
    embeddings = await engine.embed([o["user_message_preview"] for o in low_scoring])
    clusters = cluster_by_similarity(low_scoring, embeddings, threshold=0.5)

    for cluster in clusters:
        if len(cluster) < 2:
            continue
        # Generate a lesson candidate from the cluster
        lesson = await generate_lesson_from_cluster(cluster)
        if lesson:
            await store_candidate(lesson, engine, db)
```

### 3b. Parameter Analysis

This is new. The analysis engine identifies parameters that correlate with poor outcomes:

```python
# Tunable parameters and their valid ranges
TUNABLE_PARAMS = {
    "COMPRESSION_THRESHOLD": {"type": "int", "min": 4000, "max": 16000, "step": 1000},
    "VERBATIM_MESSAGE_COUNT": {"type": "int", "min": 2, "max": 8, "step": 1},
    "HISTORY_WINDOW_SIZE": {"type": "int", "min": 10, "max": 40, "step": 5},
    "SCORE_THRESHOLD": {"type": "float", "min": 0.2, "max": 0.7, "step": 0.05},
    "LOW_CONFIDENCE_THRESHOLD": {"type": "float", "min": 0.4, "max": 0.8, "step": 0.05},
    "REPETITION_THRESHOLD": {"type": "int", "min": 2, "max": 5, "step": 1},
    "CYCLE_WINDOW": {"type": "int", "min": 15, "max": 50, "step": 5},
    "CYCLE_REPEATS": {"type": "int", "min": 2, "max": 5, "step": 1},
    "DELEGATION_THRESHOLD": {"type": "int", "min": 4, "max": 15, "step": 1},
    "SUMMARY_MAX_WORDS": {"type": "int", "min": 50, "max": 200, "step": 25},
    "TOPIC_MAX_MESSAGES": {"type": "int", "min": 4, "max": 16, "step": 2},
    "TOKEN_TIER_0": {"type": "int", "min": 16384, "max": 65536, "step": 8192},
    "TOKEN_TIER_1": {"type": "int", "min": 32768, "max": 131072, "step": 16384},
    "SIMILARITY_THRESHOLD": {"type": "float", "min": 0.2, "max": 0.6, "step": 0.05},
    "PROMOTION_COUNT": {"type": "int", "min": 2, "max": 5, "step": 1},
}
```

The analysis looks for correlations:

```python
async def analyze_parameters(observations: list[dict], db: aiosqlite.Connection):
    """Find parameters that correlate with poor outcomes.

    For each parameter, compare outcome scores when the parameter
    was at different values (from config snapshots in observations).
    """
    for param, spec in TUNABLE_PARAMS.items():
        values_and_scores = [
            (o["config_snapshot"].get(param), o["outcome_score"])
            for o in observations
            if param in o.get("config_snapshot", {})
        ]
        if len(values_and_scores) < 20:
            continue  # Not enough data

        # Simple: compare mean score for above-median vs below-median param values
        median_val = sorted(v for v, _ in values_and_scores)[len(values_and_scores) // 2]
        low_group = [s for v, s in values_and_scores if v <= median_val]
        high_group = [s for v, s in values_and_scores if v > median_val]

        if not low_group or not high_group:
            continue

        low_mean = sum(low_group) / len(low_group)
        high_mean = sum(high_group) / len(high_group)

        # If there's a meaningful difference, propose an experiment
        if abs(high_mean - low_mean) > 0.1:
            better_direction = "higher" if high_mean > low_mean else "lower"
            proposed = suggest_value(param, spec, median_val, better_direction)
            await create_experiment(param, current_value=median_val, proposed_value=proposed, db=db)
```

---

## 4. A/B Cohort Runner

Parameter experiments use a simple cohort system. When an experiment is active, a percentage of turns use the proposed value while the rest use the baseline.

### Cohort Assignment

```python
import hashlib

def assign_cohort(
    conversation_id: str,
    experiment_id: str,
    experiment_pct: float = 0.2,
) -> str:
    """Deterministic cohort assignment based on conversation ID.

    Same conversation always gets the same cohort (no mid-conversation switches).
    """
    h = hashlib.sha256(f"{conversation_id}:{experiment_id}".encode()).hexdigest()
    bucket = int(h[:8], 16) / 0xFFFFFFFF
    return "experiment" if bucket < experiment_pct else "control"
```

### Parameter Override

At turn start, check for active experiments and apply overrides:

```python
async def apply_experiment_overrides(
    config: dict,
    conversation_id: str,
    db: aiosqlite.Connection,
) -> tuple[dict, str]:
    """Apply active experiment overrides to the config.

    Returns (modified_config, cohort_label).
    """
    active = await get_active_experiments(db)
    if not active:
        return config, "control"

    # Only one experiment active at a time (simplifies analysis)
    exp = active[0]
    cohort = assign_cohort(conversation_id, exp["id"])

    if cohort == "experiment":
        config = config.copy()
        config[exp["param_key"]] = exp["proposed_value"]

    return config, cohort
```

### Experiment Conclusion

After enough observations accumulate in both cohorts (minimum 30 per group), run a two-sample t-test:

```python
from scipy import stats

async def evaluate_experiment(exp_id: str, db: aiosqlite.Connection):
    control_scores = await get_cohort_scores(exp_id, "control", db)
    experiment_scores = await get_cohort_scores(exp_id, "experiment", db)

    if len(control_scores) < 30 or len(experiment_scores) < 30:
        return  # Not enough data yet

    t_stat, p_value = stats.ttest_ind(experiment_scores, control_scores)
    control_mean = sum(control_scores) / len(control_scores)
    experiment_mean = sum(experiment_scores) / len(experiment_scores)

    conclusion = "inconclusive"
    if p_value < 0.05:
        if experiment_mean > control_mean:
            conclusion = "promoted"
        else:
            conclusion = "rejected"

    await update_experiment(exp_id, {
        "status": "concluded",
        "control_mean_score": control_mean,
        "experiment_mean_score": experiment_mean,
        "p_value": p_value,
        "conclusion": conclusion,
        "concluded_at": datetime.utcnow().isoformat(),
    }, db)
```

---

## 5. Migration from Doc 048

Doc 048 is implemented and working. This doc extends it, not replaces it.

### What Changes

| Component | 048 (current) | 049 (new) |
|-----------|---------------|-----------|
| Candidate embeddings | FastEmbed bge-small-en-v1.5 (384-dim) | Voyage 4 Large via EmbeddingEngine (1024-dim) |
| Candidate storage | `candidates.jsonl` (flat file) | `optimization_candidates` + `optimization_candidates_vec` (sqlite-vec) |
| Similarity search | Linear scan with numpy | vec0 `MATCH` query |
| Scope | Prompt lessons only | Prompts + numeric parameters |
| Evaluation | None (assume lessons help) | Outcome scores + A/B testing |
| Critic trigger | Every turn (≥3 steps) | Batch analysis (every N turns or periodic) |
| Lesson promotion | Recurrence count ≥ 3 | Recurrence ≥ 3 **and** correlated with low outcome scores |

### What Stays

- File-based lessons in `prompts/_optimization/lessons/` (proposed → approved)
- Human-in-the-loop approval for promoted lessons
- Non-blocking background execution
- The critic concept (now batch-oriented instead of per-turn)

### Migration Steps

1. **Add outcome tracking to worker.py** — extend the existing audit event emission (~30 lines)
2. **Create sqlite tables** — add to `_AGENT_DB_SCHEMA` in worker.py
3. **Swap embedding calls in critic.py** — replace FastEmbed with `EmbeddingEngine.embed_query()`
4. **Replace JSONL with sqlite** — new functions in critic.py for vec0 queries
5. **Add parameter snapshot to observations** — serialize current thresholds at turn start
6. **Build analysis engine** — new file `backend/app/agent/optimizer.py`
7. **Add experiment runner** — cohort assignment + parameter override at turn start
8. **Migrate existing candidates** — one-time script to re-embed with Voyage 4 and insert into sqlite

---

## 6. File Layout

```
backend/app/agent/
    critic.py              # Modified — uses EmbeddingEngine + sqlite-vec
    optimizer.py           # New — analysis engine + parameter experiments
    outcome.py             # New — outcome signal collection + scoring

prompts/_optimization/
    lessons/
        proposed/          # Unchanged — individual lesson .md files
        approved/          # Unchanged — human-approved lessons
    # candidates.jsonl     # Removed — replaced by sqlite table
```

---

## 7. Feedback Loop Lifecycle

```
Turn executes
    │
    ├── Outcome signals collected (tool_calls, cost, loops, etc.)
    ├── Config snapshot recorded (all current param values)
    ├── Observation stored in sqlite + vec0
    │
    ▼
Every 50 turns (or daily, whichever comes first):
    │
    ├── Batch analysis runs on recent observations
    │   ├── Low-scoring turns clustered by semantic similarity
    │   ├── Lesson candidates generated from clusters
    │   ├── Candidates checked for novelty via vec0
    │   └── Recurring candidates promoted to proposed/
    │
    ├── Parameter analysis runs
    │   ├── Correlate param values with outcome scores
    │   ├── Propose experiments for promising changes
    │   └── Evaluate concluded experiments (t-test)
    │
    └── Report generated for human review
        ├── New proposed lessons
        ├── Experiment results (promoted/rejected/inconclusive)
        └── Outcome score trends
```

---

## 8. Category Baselines

To judge whether a turn was efficient, we need baselines per task category. These bootstrap from the first 100 observations per category and update as a rolling median:

```python
CATEGORY_BASELINES = {
    "coding": {"median_cost": 0.08, "median_iterations": 6, "median_tools": 8},
    "research": {"median_cost": 0.04, "median_iterations": 4, "median_tools": 5},
    "chat": {"median_cost": 0.01, "median_iterations": 1, "median_tools": 0},
    "file_ops": {"median_cost": 0.03, "median_iterations": 3, "median_tools": 4},
}
# Updated every 50 observations from actual data
```

---

## 9. Constraints

| Constraint | Value | Rationale |
|------------|-------|-----------|
| Min observations for analysis | 50 | Statistical minimum for meaningful patterns |
| Min observations per cohort | 30 | Minimum for t-test validity |
| Max active experiments | 1 | Isolate variable effects |
| Experiment traffic split | 20% experiment / 80% control | Limit blast radius |
| Analysis frequency | Every 50 turns or daily | Balance between freshness and cost |
| Experiment max duration | 14 days | Force conclusion even if inconclusive |
| Embedding model | Voyage 4 Large (1024-dim) | Matches existing infrastructure |
| Vec store | sqlite-vec (vec0) | Already deployed for knowledge/memory |
| p-value threshold | 0.05 | Standard statistical significance |

---

## 10. Cost

| Component | Cost | Frequency |
|-----------|------|-----------|
| Outcome collection | Zero (local computation) | Every turn |
| Voyage 4 embedding (candidates) | ~$0.0001 per candidate | Per candidate |
| Batch analysis (LLM call) | ~$0.05–0.10 per batch | Every 50 turns |
| Parameter analysis | Zero (statistical computation) | Every 50 turns |
| sqlite storage | Negligible | Continuous |

Total: roughly **$0.002 per turn** amortized (down from $0.02–0.05 per turn with the per-turn critic in doc 048).

---

## 11. Observability

### Dashboard Data (future frontend tab)

```sql
-- Outcome score trend (7-day rolling average)
SELECT date(created_at) AS day,
       AVG(outcome_score) AS avg_score,
       COUNT(*) AS turn_count
FROM optimization_observations
GROUP BY day ORDER BY day;

-- Active experiments
SELECT param_key, baseline_value, proposed_value,
       control_obs_count, experiment_obs_count,
       control_mean_score, experiment_mean_score
FROM optimization_experiments
WHERE status = 'active';

-- Promoted lessons (last 30 days)
SELECT lesson_text, created_at, similar_count
FROM optimization_candidates
WHERE promoted = TRUE AND created_at > datetime('now', '-30 days');
```

---

## 12. Future Extensions

- **DSPy integration (doc 023):** Once enough labeled observations accumulate, use them as the training set for MIPROv2 prompt optimization. The observations table _is_ the training data DSPy needs.
- **Multi-parameter experiments:** Run Bayesian optimization over the parameter space instead of one-at-a-time experiments.
- **Cross-agent learning:** Share promoted lessons between agents with similar configurations.
- **Automated fragment editing:** Instead of just generating lesson text, propose diffs to actual prompt fragment files.
- **Regression detection:** Alert when outcome scores drop significantly after a git push or config change.

---

## 13. Decisions

| Question | Decision |
|----------|----------|
| Replace FastEmbed in critic? | **Yes** — use Voyage 4 via existing EmbeddingEngine |
| Replace candidates.jsonl? | **Yes** — sqlite-vec tables |
| Keep per-turn critic? | **No** — batch analysis is cheaper and more robust |
| Parameter optimization scope? | All numeric thresholds in worker + context pipeline + fragment router |
| A/B testing approach? | Deterministic cohort by conversation ID, 80/20 split |
| Human approval still required? | **Yes** — for both lessons and parameter promotions |
| Dependency on doc 023 (DSPy)? | **No** — standalone, but observations feed DSPy later |
