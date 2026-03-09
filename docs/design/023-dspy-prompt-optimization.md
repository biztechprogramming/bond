# Design Doc 023: DSPy Prompt Optimization Integration

**Status:** Draft  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy)  
**Reference:** [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) (⭐ 32,654)

---

## 1. What DSPy Does

DSPy replaces hand-written prompts with *programmatic* prompt construction. Instead of writing prompt text, you define **signatures** (typed input→output contracts) and **modules** (composable processing steps). DSPy's optimizers then automatically discover the best prompt strategy — including which few-shot examples to include, what instructions to generate, and in what order — by evaluating candidates against a metric function.

Key concepts:

- **Signature**: A typed contract like `"question -> answer"` or `"context, question -> reasoning, answer"`
- **Module**: A composable unit (`dspy.Predict`, `dspy.ChainOfThought`, `dspy.ReAct`) that implements a signature
- **Optimizer (Teleprompter)**: Searches the space of possible prompts and selects the best one
  - `BootstrapFewShot`: Generates and filters few-shot examples automatically
  - `MIPROv2`: Uses a meta-LLM to propose instructions + examples, evaluates them
  - `BootstrapFinetune`: Compiles optimized prompts into fine-tuning data
- **Metric**: A function that scores how good an output is (accuracy, relevance, format compliance)

```python
import dspy

# Define what the module does (not how)
class FragmentSelector(dspy.Module):
    def __init__(self):
        self.select = dspy.ChainOfThought(
            "user_message, available_fragments -> selected_fragment_ids, reasoning"
        )
    
    def forward(self, user_message, available_fragments):
        return self.select(
            user_message=user_message,
            available_fragments=available_fragments,
        )

# Define what "good" means
def selection_metric(example, prediction, trace=None):
    # Did the optimizer pick the same fragments a human would?
    expected = set(example.selected_fragment_ids)
    predicted = set(prediction.selected_fragment_ids)
    return len(expected & predicted) / max(len(expected), 1)

# Optimize — DSPy finds the best prompt automatically
optimizer = dspy.MIPROv2(metric=selection_metric, num_candidates=20)
optimized_selector = optimizer.compile(
    FragmentSelector(),
    trainset=labeled_examples,
)

# Use it — the optimized prompt is baked in
result = optimized_selector(
    user_message="optimize my PostgreSQL query",
    available_fragments=fragment_catalog,
)
```

---

## 2. What's Broken in Bond Today

Bond's LLM-based fragment selection (`_utility_model_select` in `context_pipeline.py`) has a hand-written prompt:

```python
selection_prompt = f"""You are a prompt fragment selector. Pick ONLY fragments 
directly useful for this task. Do NOT include fragments "just in case".

Available fragments (with token costs):
{catalog}

Token budget remaining: ~{budget_remaining} tokens
...
JSON array only:"""
```

**Problems:**

| Issue | Detail |
|-------|--------|
| **Hand-tuned prompt** | The selection prompt was written once by a human. No systematic evaluation of whether it actually picks the right fragments. |
| **No evaluation loop** | No way to know if selection quality degrades over time as fragments are added/changed. |
| **No few-shot examples** | The utility model gets zero examples of correct selections. It's guessing based on instructions alone. |
| **Brittle JSON parsing** | The response is parsed with `json.loads` after stripping markdown fences. No structured output enforcement. |
| **No optimization** | The prompt never improves. Every failure is a one-off fix rather than data for a systematic optimizer. |

---

## 3. How DSPy Fixes This

DSPy replaces the hand-written selection prompt with an optimized one, discovered automatically by evaluating candidates against labeled data.

### 3.1 The Key Insight

Bond's fragment selection is a **classification problem**: given a user message and a catalog of fragments, select the relevant subset. DSPy excels at exactly this — it can optimize the classifier prompt using a small labeled dataset.

### 3.2 Building the Training Set

The labeled dataset comes from Bond's own audit log. Every fragment selection already records `_selection_reason` (core, keyword_trigger, llm_selected). Over time, we can label which selections led to good outcomes:

```python
# Training examples — can be generated from audit logs + human review
trainset = [
    dspy.Example(
        user_message="write a SQL migration to add a column",
        available_fragments="[{id: PF001, name: 'Database Safety'}, {id: PF002, name: 'React Hooks'}, ...]",
        selected_fragment_ids=["PF001", "PF005"],  # Human-verified correct
    ).with_inputs("user_message", "available_fragments"),
    # ... 50-100 examples is usually enough
]
```

### 3.3 Architecture

```
CURRENT:
  Hand-written prompt → utility model → JSON parse → fragment list

WITH DSPy:
  DSPy-optimized prompt (auto-generated instructions + few-shot examples)
    → utility model → structured output → fragment list
  
  Offline optimization loop:
    Labeled examples → MIPROv2 optimizer → better prompt → re-evaluate → ship
```

### 3.4 Implementation

```python
# backend/app/agent/fragment_optimizer.py

import dspy
from pathlib import Path
import json

class FragmentSelector(dspy.Module):
    """DSPy module for selecting relevant prompt fragments."""
    
    def __init__(self):
        self.select = dspy.ChainOfThought(
            dspy.Signature(
                "user_message: str, fragment_catalog: str, token_budget: int "
                "-> selected_ids: list[str], reasoning: str"
            )
        )
    
    def forward(self, user_message: str, fragment_catalog: str, token_budget: int = 2500):
        result = self.select(
            user_message=user_message,
            fragment_catalog=fragment_catalog,
            token_budget=token_budget,
        )
        return result


def selection_metric(example, prediction, trace=None):
    """Score fragment selection quality.
    
    Precision-recall F1 against human-labeled selections.
    """
    try:
        expected = set(example.selected_ids)
        predicted = set(prediction.selected_ids)
    except (AttributeError, TypeError):
        return 0.0
    
    if not expected and not predicted:
        return 1.0
    if not expected or not predicted:
        return 0.0
    
    precision = len(expected & predicted) / len(predicted)
    recall = len(expected & predicted) / len(expected)
    
    if precision + recall == 0:
        return 0.0
    return 2 * (precision * recall) / (precision + recall)


def optimize_selector(
    trainset: list,
    model: str = "claude-sonnet-4-6",
    num_candidates: int = 20,
    save_path: Path = Path("optimized_selector.json"),
) -> FragmentSelector:
    """Run DSPy optimization offline. Save the optimized prompt."""
    
    dspy.configure(lm=dspy.LM(model))
    
    selector = FragmentSelector()
    
    optimizer = dspy.MIPROv2(
        metric=selection_metric,
        num_candidates=num_candidates,
        max_bootstrapped_demos=5,
        max_labeled_demos=10,
    )
    
    optimized = optimizer.compile(selector, trainset=trainset)
    optimized.save(str(save_path))
    
    return optimized


def load_optimized_selector(path: Path = Path("optimized_selector.json")) -> FragmentSelector:
    """Load a previously optimized selector for runtime use."""
    selector = FragmentSelector()
    selector.load(str(path))
    return selector
```

### 3.5 Integration with Existing Pipeline

```python
# In context_pipeline.py — replace _utility_model_select

from .fragment_optimizer import load_optimized_selector

_dspy_selector = None

def _get_selector():
    global _dspy_selector
    if _dspy_selector is None:
        optimized_path = Path(__file__).parent / "optimized_selector.json"
        if optimized_path.exists():
            _dspy_selector = load_optimized_selector(optimized_path)
        else:
            # Fall back to unoptimized selector
            _dspy_selector = FragmentSelector()
    return _dspy_selector

async def _utility_model_select(candidates, user_message, history, budget_remaining, config, extra_kwargs):
    selector = _get_selector()
    
    catalog = "\n".join(
        f"- ID: {f.get('id')} | {f.get('display_name', f.get('name'))} (~{f.get('token_estimate', 0)} tokens): {f.get('summary', f.get('description', ''))}"
        for f in candidates
    )
    
    result = selector(
        user_message=user_message,
        fragment_catalog=catalog,
        token_budget=budget_remaining,
    )
    
    selected_ids = result.selected_ids
    return [f for f in candidates if f.get("id") in selected_ids]
```

---

## 4. Optimization Workflow

DSPy optimization runs **offline**, not at request time. The workflow:

```
1. Collect fragment selection audit logs (already tracked in Bond)
2. Human reviews a batch: "were these the right fragments for this message?"
   → Labels 50-100 examples
3. Run optimize_selector() — takes 5-20 minutes, costs ~$2-5 in API calls
4. Evaluate optimized prompt on held-out test set
5. If better → deploy optimized_selector.json
6. Repeat monthly or when fragment catalog changes significantly
```

### Bootstrap from Existing Data

Bond already logs `_selection_reason` for every fragment selection. We can bootstrap the training set:

```python
# Generate initial training set from audit logs
async def bootstrap_training_set(db) -> list:
    """Pull fragment selection decisions from audit log.
    
    Uses selections where the agent completed the task successfully
    (no retries, no user corrections) as positive examples.
    """
    rows = await db.fetch_all("""
        SELECT user_message, selected_fragment_ids, task_outcome
        FROM fragment_audit_log
        WHERE task_outcome = 'success'
        ORDER BY created_at DESC
        LIMIT 200
    """)
    
    return [
        dspy.Example(
            user_message=row["user_message"],
            available_fragments=row["available_fragment_catalog"],
            selected_ids=json.loads(row["selected_fragment_ids"]),
        ).with_inputs("user_message", "available_fragments")
        for row in rows
    ]
```

---

## 5. Migration Path

| Step | Work | Risk |
|------|------|------|
| 1 | `uv add dspy` | Dependency only |
| 2 | Add `fragment_audit_log` table to track selection outcomes | Schema addition |
| 3 | Run for 2 weeks collecting selection + outcome data | No code change — just logging |
| 4 | Label 50-100 examples from audit log (human review or heuristic) | Manual effort, ~2 hours |
| 5 | Run offline optimization, generate `optimized_selector.json` | Offline script |
| 6 | A/B test: optimized selector vs. current hand-written prompt | Feature flag |
| 7 | If improvement confirmed, deploy optimized selector as default | Config change |

---

## 6. What This Doesn't Solve

- **Latency** — DSPy-optimized prompts still make an LLM call. They're better prompts, not faster ones. For latency, use semantic router (doc 022) as the fast path.
- **Real-time adaptation** — The optimizer runs offline. It doesn't learn from the current conversation in real-time.
- **Fragment quality** — Optimizing selection doesn't fix poorly written fragments.
- **Cold start** — Needs labeled data to optimize. Before you have data, it's just an unoptimized DSPy module (roughly equivalent to the current hand-written prompt).

---

## 7. Complementary Use with Semantic Router (Doc 022)

The ideal pipeline combines both:

```
User message arrives
  │
  ├── Semantic Router (doc 022): fast embedding match → high-confidence picks
  │     Cost: 0 (local embeddings)
  │     Latency: ~5ms
  │
  └── DSPy-optimized LLM selector (this doc): handles ambiguous cases
        Cost: ~0.001/call (utility model)
        Latency: ~200ms
        Only fires when semantic confidence < threshold

Result: fast path handles 70-80% of requests, 
        optimized LLM handles the rest
```

---

## 8. Decisions

| Question | Decision |
|----------|----------|
| Replace hand-written prompt? | **Yes**, with DSPy-optimized version after sufficient labeled data |
| Optimization frequency? | **Monthly**, or when >10 fragments are added/changed |
| Training set size? | **50-100 labeled examples** minimum |
| Optimizer? | **MIPROv2** — best quality for classification tasks |
| Runtime model? | Same utility model already configured (Sonnet) |
| Structured output? | DSPy handles this — replaces manual JSON parsing |
