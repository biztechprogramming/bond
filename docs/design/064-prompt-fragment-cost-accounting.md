# Design Doc 064: Prompt Fragment Cost Accounting

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 021 (Prompt Hierarchy), 049 (Closed-Loop Optimization)

---

## 1. Problem

Bond loads prompt fragments via `fragment_router.py` every turn. The manifest system (`manifest.py`) selects which fragments to include based on agent configuration and task context. But there is **limited visibility** into the per-fragment token cost and no way to identify which fragments are worth their token budget.

This means:

- Nobody knows which fragments are expensive vs cheap in dollar terms
- Nobody knows which fragments are rarely relevant but always loaded
- Prompt optimization (docs 048, 049) can't target the highest-cost fragments because it can't rank them
- There's no mechanism to detect "dead" fragments that consume tokens without influencing behavior

You can't optimize what you can't measure.

## 2. Existing Infrastructure

Bond already has significant observability plumbing in place:

- **LiteLLM** handles all LLM calls and provides `completion_cost()` for accurate per-model USD pricing (`cost_tracker.py`)
- **Langfuse** receives every trace with fragment metadata — `worker.py` (lines 816-849) already builds `_audit_fragments` containing fragment names and token estimates, passed as Langfuse tags and metadata
- **`fragment_router.py`** already tracks `token_estimate` on `FragmentMeta` and exposes it via `get_tier3_meta()`
- **`context_pipeline.py`** logs `fragment_stats` (selected count, total count) to the `context_pipeline_log` table

What's missing is not data collection — it's **cost attribution**, **queryable per-fragment metrics**, and **dead fragment detection**.

## 3. What Bond Gets

1. **Per-fragment cost attribution** — every turn, attribute a share of the actual USD input cost to each loaded fragment
2. **First-class Langfuse scores** — fragment costs as individual named scores, queryable and aggregatable in the Langfuse dashboard
3. **Fragment cost ranking** — rank fragments by total USD spend so operators can see where prompt dollars are going and target optimization efforts
4. **Optimization targets** — feed fragment cost rankings into the optimizer dashboard (doc 050)

## 4. Architecture

No new tables or logging infrastructure. All data flows through existing LiteLLM cost calculation and Langfuse trace instrumentation.

```
    worker.py (existing)
           │
           ├── _audit_fragments[] ← already built with names + token estimates
           │
           ▼
    ┌────────────────────────────┐
    │  Emit Langfuse scores      │  ← NEW: one score per fragment per trace
    │  (name, tokens, usd_cost)  │
    └──────────┬─────────────────┘
               │
               ▼
    ┌────────────────────────────┐
    │  After LLM call:           │
    │  Attribute input cost      │  ← NEW: (frag_tokens / total_frag_tokens) * (frag_share * input_cost)
    │  proportionally            │
    └──────────┬─────────────────┘
               │
               ▼
    ┌────────────────────────────┐
    │  Langfuse dashboard        │  ← Existing: now has per-fragment cost data
    │  + cost report script      │  ← NEW: periodic script
    └────────────────────────────┘
```

## 5. Implementation

### Phase 1: Per-Fragment Langfuse Scores (~0.5 days)

**File changed:** `backend/app/worker.py`

After building `_audit_fragments` (existing code, ~line 816), emit each fragment as a named Langfuse score instead of only stuffing them into a metadata blob:

```python
# After _audit_fragments is built and _langfuse_meta is populated:
# Note: use the existing module-level Langfuse client (see langfuse_client.py),
# not a per-call Langfuse() instance.
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    for frag in _audit_fragments:
        frag_name = frag.get("name", "unknown")
        frag_tokens = frag.get("tokens", frag.get("tokenEstimate", 0))
        _langfuse.score(
            trace_id=_langfuse_meta.get("trace_id"),
            name=f"fragment_token_est:{frag_name}",
            value=frag_tokens,
            comment=frag.get("path", ""),
            metadata={"model": model, "session_id": session_id},
        )
```

This makes fragment token estimates first-class in Langfuse's analytics — filterable, sortable, and aggregatable across traces. Note: values are `char/4` estimates, not exact tokenizer counts. The proportional cost attribution in Phase 2 corrects for this.

### Phase 2: Dollar-Cost Attribution (~0.5 days)

**File changed:** `backend/app/agent/cost_tracker.py`

After a primary LLM call completes, we know the real USD cost from `calc_call_cost()` and the actual input token count from the response usage. Attribute the input portion proportionally to each fragment:

```python
def attribute_fragment_costs(
    self,
    response: Any,
    model: str,
    fragments: list[dict],
) -> list[dict]:
    """Attribute input cost proportionally to each loaded fragment.

    Returns enriched fragment dicts with 'usd_cost' added.
    """
    total_cost = self.calc_call_cost(response, model)
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0

    if not input_tokens or not fragments:
        return fragments

    # Estimate input share of total cost (input is typically cheaper per token)
    # Use litellm's model cost info if available, else assume 1:3 input:output ratio
    try:
        from litellm import model_cost
        info = model_cost.get(model, {})
        input_price = info.get("input_cost_per_token", 0)
        output_price = info.get("output_cost_per_token", 0)
        input_total = input_tokens * input_price
        output_total = output_tokens * output_price
        input_share = input_total / (input_total + output_total) if (input_total + output_total) > 0 else 0.5
    except Exception:
        input_share = 0.5

    input_cost_all = total_cost * input_share
    fragment_token_total = sum(
        f.get("tokens", f.get("tokenEstimate", 0)) for f in fragments
    )

    if fragment_token_total == 0:
        return fragments

    # Scale down to only the portion of input cost attributable to fragments
    # (input also includes system prompt, user message, history, tool results, etc.)
    # Clamp to 1.0: char/4 estimates can overshoot real token counts.
    fragment_share = min(fragment_token_total / input_tokens, 1.0) if input_tokens > 0 else 0
    if fragment_share > 0.95:
        logger.warning(
            "fragment_share=%.2f — char/4 estimates may be badly calibrated "
            "(fragment_token_total=%d, input_tokens=%d)",
            fragment_share, fragment_token_total, input_tokens,
        )
    input_cost = input_cost_all * fragment_share

    for frag in fragments:
        frag_tokens = frag.get("tokens", frag.get("tokenEstimate", 0))
        frag["usd_cost"] = (frag_tokens / fragment_token_total) * input_cost

    return fragments
```

The enriched fragment list (now with `usd_cost`) is then emitted as Langfuse scores alongside the token estimates from Phase 1:

```python
_langfuse.score(
    trace_id=_langfuse_meta.get("trace_id"),
    name=f"fragment_cost:{frag_name}",
    value=frag["usd_cost"],
    comment=f"{frag_tokens} tokens @ ${frag['usd_cost']:.6f}",
    metadata={"model": model, "session_id": session_id},
)
```

### Phase 3: Fragment Cost Report (~0.5 days)

**New file:** `scripts/fragment_cost_report.py`

A standalone script (run periodically or on-demand) that queries Langfuse for per-fragment cost data and produces a ranked table:

```python
"""Rank prompt fragments by total USD cost.

Queries Langfuse scores to produce a cost-ranked table of all loaded
fragments. Operators use this to decide what to optimize, consolidate,
or promote/demote between tiers.

Run: python scripts/fragment_cost_report.py --days 30 --min-loads 50
"""

from datetime import datetime, timedelta, timezone

from langfuse import Langfuse


def _fetch_all_scores(
    lf: Langfuse,
    prefix: str,
    from_timestamp: datetime | None = None,
) -> list:
    """Paginate through all Langfuse scores matching a prefix."""
    all_scores = []
    page = 1
    while True:
        kwargs: dict = {"name_starts_with": prefix, "page": page, "limit": 1000}
        if from_timestamp:
            kwargs["from_timestamp"] = from_timestamp
        batch = lf.get_scores(**kwargs)
        all_scores.extend(batch.data)
        if len(batch.data) < 1000:
            break
        page += 1
    return all_scores


def get_fragment_stats(days: int = 30) -> list[dict]:
    lf = Langfuse()
    from_ts = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch all token estimate scores
    scores = _fetch_all_scores(lf, "fragment_token_est:", from_timestamp=from_ts)

    # Aggregate by fragment name
    stats: dict[str, dict] = {}
    for score in scores:
        name = score.name.replace("fragment_token_est:", "")
        if name not in stats:
            stats[name] = {"loads": 0, "total_tokens": 0, "total_cost": 0.0}
        stats[name]["loads"] += 1
        stats[name]["total_tokens"] += score.value

    # Fetch corresponding cost scores
    cost_scores = _fetch_all_scores(lf, "fragment_cost:", from_timestamp=from_ts)
    for score in cost_scores:
        name = score.name.replace("fragment_cost:", "")
        if name in stats:
            stats[name]["total_cost"] += score.value

    # Sort by total cost descending
    ranked = sorted(stats.items(), key=lambda x: x[1]["total_cost"], reverse=True)

    return [{"name": name, **data} for name, data in ranked]
```

Output is a ranked table of fragments by total cost over the period. Operators use this to decide what to optimize, consolidate, or promote/demote between tiers.

### Future Work: Dead Fragment Detection via LLM-as-Judge Relevance

The cost report (Phase 3) ranks fragments by spend but cannot distinguish expensive-and-essential from expensive-and-useless. True dead fragment detection requires relevance data.

A future addition could use **Langfuse's evaluator feature** to sample traces (5-10%) and ask an LLM: "Which of these fragments demonstrably influenced the response? Score each 0-1." Relevance scores written back to Langfuse would enable filtering the cost report to surface fragments that are both expensive and irrelevant — actual dead fragments.

Keyword/heuristic approaches won't work here: style, safety, and governance fragments have zero keyword overlap with responses but are the most important to keep. An LLM judge is the honest approach. See doc 049 for the broader closed-loop optimization pipeline this would feed into.

## 6. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `FRAGMENT_COST_SCORES` | `true` | Emit per-fragment Langfuse scores |

## 7. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Langfuse score volume increases | Low | One score per fragment per trace. With ~15 fragments/turn, this is ~15 additional scores — well within Langfuse's capacity. |
| Cost attribution is approximate | Low | Fragment token estimates (char/4) are rough. The proportional split now scales by `fragment_tokens / actual_input_tokens` to avoid inflating costs, but individual estimates may still be off by 10-20% vs real tokenizer counts. Good enough for ranking — not for invoicing. |
| Prompt caching skews per-fragment costs | Low | Anthropic and OpenAI prompt caching can reduce real marginal cost of stable fragments by ~90%. The proportional attribution uses total `completion_cost()` (which reflects cached pricing) but splits it evenly — overcharging cached fragments, undercharging dynamic ones. Acceptable for v1 ranking. If cache hit metadata is available in the response (`cache_read_input_tokens`), a future refinement could weight the split accordingly. |

## 8. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| Visibility | Per-fragment cost data in Langfuse dashboard | 100% of turns emit fragment scores |
| Cost ranking | Fragments sortable by USD cost | Top-10 most expensive fragments identifiable within first week |
| Optimization savings | Before/after total fragment tokens per turn (after human pruning decisions) | -15-30% of fragment token budget |
| Agent quality after pruning | Eval suite pass rate | No regression |

## 9. Relationship to Prior Docs

- **Doc 021 (Prompt Hierarchy):** This doc instruments the hierarchy. Doesn't change the structure — just measures it.
- **Doc 048 (Self-Optimizing Prompts):** Fragment cost data is an input signal for prompt optimization. Know which fragments to optimize first.
- **Doc 049 (Closed-Loop Optimization):** Fragment relevance scores (see Future Work) would feed into the optimizer's observation pipeline.
- **Doc 050 (Optimization Dashboard):** Fragment cost and relevance data should be exposed here — sourced from Langfuse scores, not a separate table.

## 10. What This Doc Does NOT Do

- **Auto-remove fragments.** All pruning decisions are human-only per AGENTS.md governance rules.
- **Replace Langfuse.** No new tables or parallel logging. All data lives in Langfuse.
- **Detect dead fragments.** This doc measures cost, not relevance. The cost report ranks fragments by spend — it cannot tell you which fragments are useless. See Future Work for the LLM-as-judge approach needed to answer that question.
- **Score fragment interactions.** Fragments may depend on each other (Fragment A defines a concept that Fragment B references). This doc scores fragments individually. Interaction effects are out of scope — operators should use the cost report as a starting point for investigation, not a kill list.
