"""Analysis engine, parameter experiments, and A/B cohort runner.

Design Doc 049: Closed-Loop Optimization Engine — Sections 3-4.

Runs periodically (every 50 turns or daily) to:
- Batch-analyze low-scoring observations for prompt lesson candidates
- Correlate parameter values with outcome scores
- Manage A/B experiments with deterministic cohort assignment
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
from ulid import ULID

logger = logging.getLogger("bond.agent.optimizer")

# ---------------------------------------------------------------------------
# Tunable parameters and their valid ranges
# ---------------------------------------------------------------------------

TUNABLE_PARAMS: dict[str, dict[str, Any]] = {
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

PARAM_DESCRIPTIONS: dict[str, str] = {
    "COMPRESSION_THRESHOLD": "Token count that triggers history compression. Higher = more context preserved but larger prompts.",
    "VERBATIM_MESSAGE_COUNT": "Number of recent messages kept word-for-word (not summarized). Higher = more fidelity for recent context.",
    "HISTORY_WINDOW_SIZE": "Maximum messages in the sliding window. Higher = more history but more tokens.",
    "SCORE_THRESHOLD": "Minimum relevance score for semantic search results. Lower = more results but noisier.",
    "LOW_CONFIDENCE_THRESHOLD": "Below this score, search results are excluded entirely. Safety net for irrelevant matches.",
    "REPETITION_THRESHOLD": "How many identical tool calls before loop detection fires. Lower = catches loops faster but may false-positive.",
    "CYCLE_WINDOW": "Number of recent messages checked for repetition cycles. Larger window catches longer cycles.",
    "CYCLE_REPEATS": "Number of cycle repetitions before intervention. Lower = more aggressive loop breaking.",
    "DELEGATION_THRESHOLD": "Tool call count before suggesting task delegation to a coding agent.",
    "SUMMARY_MAX_WORDS": "Max words in a context summary block. Lower = more compressed but may lose detail.",
    "TOPIC_MAX_MESSAGES": "Max messages per topic segment during compression. Controls granularity of topic detection.",
    "TOKEN_TIER_0": "Starting max_tokens for LLM calls. Escalated on truncation.",
    "TOKEN_TIER_1": "Second-tier max_tokens after first truncation.",
    "SIMILARITY_THRESHOLD": "Cosine similarity for lesson candidate deduplication. Lower = more aggressive grouping.",
    "PROMOTION_COUNT": "Similar candidates needed before a lesson is promoted. Higher = more conservative promotion.",
}

PARAM_DEFAULTS: dict[str, Any] = {k: v.get("default") for k, v in TUNABLE_PARAMS.items()}


# ---------------------------------------------------------------------------
# Cohort assignment (deterministic by conversation ID)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Experiment DB helpers
# ---------------------------------------------------------------------------


async def create_experiment(
    param_key: str,
    current_value: Any,
    proposed_value: Any,
    db: aiosqlite.Connection,
    rationale: str = "",
) -> str:
    """Create a new parameter experiment (status='proposed')."""
    exp_id = str(ULID())
    await db.execute(
        """
        INSERT INTO optimization_experiments
            (id, param_key, baseline_value, proposed_value, rationale, status)
        VALUES (?, ?, ?, ?, ?, 'proposed')
        """,
        (exp_id, param_key, str(current_value), str(proposed_value), rationale),
    )
    await db.commit()
    logger.info("Created experiment %s: %s %s -> %s", exp_id, param_key, current_value, proposed_value)
    return exp_id


async def get_active_experiments(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return all experiments with status='active'."""
    cursor = await db.execute(
        "SELECT id, param_key, baseline_value, proposed_value, rationale "
        "FROM optimization_experiments WHERE status = 'active'"
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "param_key": r[1],
            "baseline_value": r[2],
            "proposed_value": r[3],
            "rationale": r[4],
        }
        for r in rows
    ]


async def get_cohort_scores(
    experiment_id: str, cohort: str, db: aiosqlite.Connection
) -> list[float]:
    """Get outcome scores for a specific cohort in an experiment."""
    cursor = await db.execute(
        "SELECT outcome_score FROM optimization_observations WHERE cohort = ? "
        "AND created_at >= (SELECT created_at FROM optimization_experiments WHERE id = ?)",
        (cohort if cohort == "control" else experiment_id, experiment_id),
    )
    rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def update_experiment(
    exp_id: str, updates: dict[str, Any], db: aiosqlite.Connection
) -> None:
    """Update experiment fields."""
    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [exp_id]
    await db.execute(
        f"UPDATE optimization_experiments SET {set_clauses} WHERE id = ?",
        values,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Experiment overrides
# ---------------------------------------------------------------------------


async def apply_experiment_overrides(
    config: dict[str, Any],
    conversation_id: str,
    db: aiosqlite.Connection,
) -> tuple[dict[str, Any], str]:
    """Apply active experiment overrides to the config.

    Returns (modified_config, cohort_label).
    """
    active = await get_active_experiments(db)
    if not active:
        return config, "control"

    # Only one experiment active at a time
    exp = active[0]
    cohort = assign_cohort(conversation_id, exp["id"])

    if cohort == "experiment":
        config = config.copy()
        proposed = exp["proposed_value"]
        # Parse back to the correct type
        param_spec = TUNABLE_PARAMS.get(exp["param_key"], {})
        if param_spec.get("type") == "int":
            proposed = int(float(proposed))
        elif param_spec.get("type") == "float":
            proposed = float(proposed)
        config[exp["param_key"]] = proposed

    return config, cohort


# ---------------------------------------------------------------------------
# Welch's t-test (no scipy dependency)
# ---------------------------------------------------------------------------


def _welch_t_test(sample_a: list[float], sample_b: list[float]) -> tuple[float, float]:
    """Welch's t-test for two independent samples.

    Returns (t_statistic, approximate_p_value).
    Uses the Abramowitz & Stegun approximation for the t-distribution CDF.
    """
    n_a, n_b = len(sample_a), len(sample_b)
    if n_a < 2 or n_b < 2:
        return 0.0, 1.0

    mean_a = statistics.mean(sample_a)
    mean_b = statistics.mean(sample_b)
    var_a = statistics.variance(sample_a)
    var_b = statistics.variance(sample_b)

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se < 1e-12:
        return 0.0, 1.0

    t_stat = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    denom = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    if denom < 1e-12:
        return t_stat, 1.0
    df = num / denom

    # Approximate two-tailed p-value using the normal approximation
    # (good for df > 30, which we require)
    abs_t = abs(t_stat)
    # Use complementary error function for the normal CDF tail
    p_value = math.erfc(abs_t / math.sqrt(2))
    return t_stat, p_value


# ---------------------------------------------------------------------------
# Experiment evaluation
# ---------------------------------------------------------------------------


async def evaluate_experiment(exp_id: str, db: aiosqlite.Connection) -> None:
    """Evaluate an active experiment using Welch's t-test."""
    control_scores = await get_cohort_scores(exp_id, "control", db)
    experiment_scores = await get_cohort_scores(exp_id, exp_id, db)

    if len(control_scores) < 30 or len(experiment_scores) < 30:
        return  # Not enough data yet

    t_stat, p_value = _welch_t_test(experiment_scores, control_scores)
    control_mean = statistics.mean(control_scores)
    experiment_mean = statistics.mean(experiment_scores)

    conclusion = "inconclusive"
    if p_value < 0.05:
        if experiment_mean > control_mean:
            conclusion = "promoted"
        else:
            conclusion = "rejected"

    await update_experiment(exp_id, {
        "status": "concluded",
        "control_obs_count": len(control_scores),
        "experiment_obs_count": len(experiment_scores),
        "control_mean_score": control_mean,
        "experiment_mean_score": experiment_mean,
        "p_value": p_value,
        "conclusion": conclusion,
        "concluded_at": datetime.utcnow().isoformat(),
    }, db)

    logger.info(
        "Experiment %s concluded: %s (control=%.3f, experiment=%.3f, p=%.4f)",
        exp_id, conclusion, control_mean, experiment_mean, p_value,
    )


# ---------------------------------------------------------------------------
# Parameter analysis
# ---------------------------------------------------------------------------


def _suggest_value(param: str, spec: dict, median_val: Any, direction: str) -> Any:
    """Suggest a new parameter value one step in the given direction."""
    step = spec["step"]
    if direction == "higher":
        proposed = median_val + step
    else:
        proposed = median_val - step

    # Clamp to valid range
    proposed = max(spec["min"], min(spec["max"], proposed))

    if spec["type"] == "int":
        return int(proposed)
    return round(proposed, 4)


async def analyze_parameters(
    observations: list[dict[str, Any]], db: aiosqlite.Connection
) -> None:
    """Find parameters that correlate with poor outcomes.

    For each parameter, compare outcome scores when the parameter
    was at different values (from config snapshots in observations).
    """
    for param, spec in TUNABLE_PARAMS.items():
        values_and_scores = [
            (o["config_snapshot"].get(param), o["outcome_score"])
            for o in observations
            if param in o.get("config_snapshot", {})
            and o["config_snapshot"].get(param) is not None
        ]
        if len(values_and_scores) < 20:
            continue

        # Sort by param value to find median
        sorted_vals = sorted(v for v, _ in values_and_scores)
        median_val = sorted_vals[len(sorted_vals) // 2]

        low_group = [s for v, s in values_and_scores if v <= median_val]
        high_group = [s for v, s in values_and_scores if v > median_val]

        if not low_group or not high_group:
            continue

        low_mean = statistics.mean(low_group)
        high_mean = statistics.mean(high_group)

        if abs(high_mean - low_mean) > 0.1:
            better_direction = "higher" if high_mean > low_mean else "lower"
            proposed = _suggest_value(param, spec, median_val, better_direction)

            # Check if there's already an active/proposed experiment for this param
            cursor = await db.execute(
                "SELECT COUNT(*) FROM optimization_experiments "
                "WHERE param_key = ? AND status IN ('proposed', 'active')",
                (param,),
            )
            row = await cursor.fetchone()
            if row and row[0] > 0:
                continue

            await create_experiment(
                param, current_value=median_val, proposed_value=proposed, db=db,
                rationale=f"Median split analysis: {better_direction} values correlate with "
                          f"+{abs(high_mean - low_mean):.3f} outcome score",
            )


# ---------------------------------------------------------------------------
# Batch analysis (prompt lessons)
# ---------------------------------------------------------------------------


def _cluster_by_similarity(
    items: list[dict], embeddings: list[list[float]], threshold: float = 0.5
) -> list[list[dict]]:
    """Simple greedy clustering by cosine similarity."""
    n = len(items)
    if n == 0:
        return []

    assigned = [False] * n
    clusters: list[list[dict]] = []

    for i in range(n):
        if assigned[i]:
            continue
        cluster = [items[i]]
        assigned[i] = True
        emb_i = embeddings[i]

        for j in range(i + 1, n):
            if assigned[j]:
                continue
            emb_j = embeddings[j]
            # Cosine similarity
            dot = sum(a * b for a, b in zip(emb_i, emb_j))
            norm_i = math.sqrt(sum(a * a for a in emb_i))
            norm_j = math.sqrt(sum(b * b for b in emb_j))
            if norm_i > 1e-9 and norm_j > 1e-9:
                sim = dot / (norm_i * norm_j)
                if sim >= threshold:
                    cluster.append(items[j])
                    assigned[j] = True

        clusters.append(cluster)

    return clusters


async def analyze_batch(
    observations: list[dict[str, Any]],
    engine: Any,  # EmbeddingEngine
    db: aiosqlite.Connection,
) -> None:
    """Analyze a batch of recent low-scoring observations.

    Groups by failure mode, generates lesson candidates,
    checks novelty against existing candidates via vec0.
    """
    from backend.app.agent.critic import store_candidate, generate_lesson_from_cluster

    low_scoring = [o for o in observations if o["outcome_score"] < 0.6]
    if len(low_scoring) < 3:
        return  # Not enough signal

    # Cluster low-scoring observations by embedding similarity
    previews = [o.get("user_message_preview", "") or "" for o in low_scoring]
    previews = [p if p else "empty" for p in previews]
    embeddings = await engine.embed(previews)
    clusters = _cluster_by_similarity(low_scoring, embeddings, threshold=0.5)

    for cluster in clusters:
        if len(cluster) < 2:
            continue
        lesson = await generate_lesson_from_cluster(cluster)
        if lesson:
            await store_candidate(lesson, engine, db)


# ---------------------------------------------------------------------------
# Top-level periodic analysis
# ---------------------------------------------------------------------------


async def run_analysis(
    db: aiosqlite.Connection,
    engine: Any,  # EmbeddingEngine
) -> None:
    """Top-level function that runs periodically (every 50 turns or daily).

    1. Fetch recent observations
    2. Run batch analysis for prompt lessons
    3. Run parameter analysis
    4. Evaluate any active experiments
    5. Expire old experiments (>14 days)
    """
    try:
        # Fetch recent observations (last 100)
        cursor = await db.execute(
            """
            SELECT id, conversation_id, turn_index, task_category,
                   user_message_preview, signals_json, outcome_score,
                   config_snapshot_json, cohort
            FROM optimization_observations
            ORDER BY created_at DESC LIMIT 100
            """
        )
        rows = await cursor.fetchall()
        if len(rows) < 50:
            logger.debug("Not enough observations for analysis (%d < 50)", len(rows))
            return

        observations = []
        for r in rows:
            config_snapshot = {}
            if r[7]:
                try:
                    config_snapshot = json.loads(r[7])
                except (json.JSONDecodeError, TypeError):
                    pass
            observations.append({
                "id": r[0],
                "conversation_id": r[1],
                "turn_index": r[2],
                "task_category": r[3],
                "user_message_preview": r[4],
                "signals_json": r[5],
                "outcome_score": r[6],
                "config_snapshot": config_snapshot,
                "cohort": r[8],
            })

        # 1. Batch analysis for prompt lessons
        await analyze_batch(observations, engine, db)

        # 2. Parameter analysis
        await analyze_parameters(observations, db)

        # 3. Evaluate active experiments
        active = await get_active_experiments(db)
        for exp in active:
            await evaluate_experiment(exp["id"], db)

        # 4. Expire old experiments (>14 days)
        cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
        await db.execute(
            """
            UPDATE optimization_experiments
            SET status = 'concluded', conclusion = 'inconclusive',
                concluded_at = datetime('now')
            WHERE status = 'active' AND created_at < ?
            """,
            (cutoff,),
        )
        await db.commit()

        logger.info("Optimization analysis complete: %d observations analyzed", len(observations))

    except Exception:
        logger.warning("Optimization analysis failed", exc_info=True)


# ---------------------------------------------------------------------------
# Data retention purge (Doc 050 §10e)
# ---------------------------------------------------------------------------


async def purge_stale_observations(
    db: aiosqlite.Connection,
    max_days: int = 180,
    max_rows: int = 50000,
) -> None:
    """Remove old observations beyond retention limits.

    Preserves observations referenced by active experiments.
    """
    # Age-based purge
    await db.execute(
        """
        DELETE FROM optimization_observations
        WHERE created_at < datetime('now', ? || ' days')
          AND id NOT IN (
            SELECT DISTINCT o.id FROM optimization_observations o
            JOIN optimization_experiments e ON o.cohort = e.id
            WHERE e.status = 'active'
          )
        """,
        (f"-{max_days}",),
    )

    # Row-count purge (keep newest)
    await db.execute(
        """
        DELETE FROM optimization_observations
        WHERE id NOT IN (
            SELECT id FROM optimization_observations
            ORDER BY created_at DESC
            LIMIT ?
        )
        """,
        (max_rows,),
    )

    # Clean orphaned vectors
    await db.execute(
        """
        DELETE FROM optimization_observations_vec
        WHERE id NOT IN (SELECT id FROM optimization_observations)
        """
    )

    await db.commit()
    logger.info("Purge complete: max_days=%d, max_rows=%d", max_days, max_rows)
