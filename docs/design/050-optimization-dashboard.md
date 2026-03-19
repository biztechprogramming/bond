# 050 — Optimization Dashboard

**Status:** Draft  
**Depends on:** [049 — Closed-Loop Optimization Engine](049-closed-loop-optimization.md)  
**Last updated:** 2026-03-19

---

## Problem

Doc 049 built a closed-loop optimization engine: outcome tracking, lesson candidates, parameter experiments, A/B testing. All the data lives in sqlite tables and filesystem lesson files — but there's no way to interact with it without raw SQL or file browsing. The human-in-the-loop can't actually be in the loop.

Additionally, doc 049 has gaps that surface when you build the UI on top of it:

1. **No data retention policy.** Observations accumulate forever. At ~1 KB/row plus 1024-dim embeddings, a busy agent generating 200 turns/day hits ~100 MB in six months. Need a purge strategy.
2. **No database indexes.** The `optimization_observations` table is queried by `created_at`, `cohort`, `task_category`, and `outcome_score` — but no indexes are defined beyond the primary key.
3. **No rollback mechanism.** Parameter changes via experiment promotion have no undo. If a promoted value degrades performance after the experiment concludes, there's no first-class rollback.
4. **Task category classifier is unspecified.** Doc 049 references `task_category` in signals but doesn't define the classifier — it just says "Classifier" as the source.
5. **User correction heuristic is fragile.** Keyword matching ("no", "wrong", "try again") will false-positive on legitimate responses ("no problem", "nothing wrong").
6. **No error handling for embedding failures.** Voyage 4 API downtime would silently break candidate storage and similarity search.
7. **scipy dependency.** The t-test introduces `scipy` as a new dependency without noting it in requirements.

This doc addresses all of the above alongside the dashboard UI.

---

## Goal

A new **Optimization** tab in Settings that makes it dead simple to:

1. **See how the agent is performing** — outcome score trends, cost trends, failure patterns
2. **Review and approve lessons** — one-click approve/reject/edit on proposed lessons
3. **Tune parameters** — see every tunable parameter, its current value, and whether experiments suggest changes
4. **Monitor experiments** — see active A/B tests, their progress, and results

Every decision the UI presents should be obvious. No guessing, no context-switching to terminals.

---

## Design Principles

- **Decisions, not data.** Every chart and table answers a question: "Should I approve this lesson?" "Should I change this threshold?" "Is the agent getting better or worse?"
- **One-click actions.** Approve a lesson, start an experiment, promote a parameter change — all single actions.
- **Progressive disclosure.** Overview first, details on demand.
- **Match existing patterns.** Same tab system, inline styles, REST API conventions as existing Settings tabs.
- **Defensive by default.** Destructive actions require confirmation. All state changes are reversible or auditable.

---

## Architecture

### API Endpoints

New router: `backend/app/api/v1/optimization.py`

All endpoints are authenticated via the existing session middleware (same as `/api/v1/settings`, `/api/v1/prompts`, etc.). All read from the agent's local sqlite DB.

```
GET    /api/v1/optimization/overview
GET    /api/v1/optimization/outcomes?days=30&category=all
GET    /api/v1/optimization/lessons?status=proposed&page=1&per_page=50
POST   /api/v1/optimization/lessons/{id}/approve
POST   /api/v1/optimization/lessons/{id}/reject
POST   /api/v1/optimization/lessons/{id}/revoke
PUT    /api/v1/optimization/lessons/{id}
GET    /api/v1/optimization/params
PUT    /api/v1/optimization/params/{key}
POST   /api/v1/optimization/params/{key}/rollback
GET    /api/v1/optimization/params/{key}/history
GET    /api/v1/optimization/experiments?status=all&page=1&per_page=20
POST   /api/v1/optimization/experiments
POST   /api/v1/optimization/experiments/{id}/conclude
POST   /api/v1/optimization/experiments/{id}/cancel
GET    /api/v1/optimization/experiments/{id}/scores
GET    /api/v1/optimization/retention
PUT    /api/v1/optimization/retention
```

### Error Response Schema

All error responses follow a consistent structure:

```json
{
  "detail": "Human-readable error message",
  "code": "EXPERIMENT_ALREADY_ACTIVE",
  "context": { "active_experiment_id": "exp_01HXYZ" }
}
```

Standard HTTP status codes:
- `400` — validation failure (out-of-range param, invalid step, malformed body)
- `404` — lesson/experiment/param not found
- `409` — conflict (experiment already active, lesson already approved)
- `422` — unprocessable entity (Pydantic validation)
- `500` — internal error (DB unavailable, filesystem error)
- `503` — embedding service unavailable (Voyage 4 API down)

### Frontend

New tab component: `frontend/src/app/settings/optimization/OptimizationTab.tsx`

Added to `TABS` array in `settings/page.tsx`.

---

## 1. Overview Panel

The first thing you see. Answers: "Is the agent improving?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Optimization Overview                           [7d ▾][30d]│
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Avg Score│  │ Turns    │  │ Lessons  │  │ Experiments│   │
│  │  0.74 ↑  │  │  1,247   │  │  3 pending│ │  1 active │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Outcome Score Trend (30 days)                        │   │
│  │  ████████████████████████████████████████             │   │
│  │  0.6 ─────────────────── 0.74 ─── 0.78              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Cost per Turn (30 days)                              │   │
│  │  ████████████████████████████████████████             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Failure Signals                                      │   │
│  │  Loop interventions: 12 (3.2%)                        │   │
│  │  User corrections:    8 (2.1%)                        │   │
│  │  Continuations:      23 (6.1%)                        │   │
│  │  Compressions:       89 (23.6%)                       │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ⚠ Empty State (when < 50 observations)               │   │
│  │  "Collecting data... 23/50 observations recorded.     │   │
│  │   Charts will appear once enough data is available."   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### API: `GET /api/v1/optimization/overview`

Query params: `days` (7 | 30 | 90, default 30), `category` (optional filter).

```json
{
  "period_days": 30,
  "total_observations": 1247,
  "avg_score_7d": 0.74,
  "avg_score_30d": 0.71,
  "avg_score_prev_30d": 0.68,
  "score_trend": "improving",
  "pending_lessons": 3,
  "approved_lessons": 8,
  "active_experiments": 1,
  "concluded_experiments": 4,
  "failure_signals": {
    "loop_interventions": { "count": 12, "pct": 3.2 },
    "user_corrections": { "count": 8, "pct": 2.1 },
    "continuations": { "count": 23, "pct": 6.1 },
    "compressions": { "count": 89, "pct": 23.6 }
  },
  "categories": {
    "coding": 520,
    "chat": 410,
    "research": 200,
    "file_ops": 117
  }
}
```

### API: `GET /api/v1/optimization/outcomes?days=30&category=all`

Returns daily aggregated data for charts. `category` filters to a single task category or `all`.

```json
{
  "days": [
    {
      "date": "2026-03-18",
      "avg_score": 0.78,
      "turn_count": 42,
      "avg_cost": 0.034,
      "avg_tool_calls": 4.2,
      "avg_iterations": 3.1,
      "loop_interventions": 1,
      "user_corrections": 0,
      "categories": { "coding": 18, "chat": 15, "research": 9 }
    }
  ]
}
```

### Charts

Rendered with **pure SVG** — no chart library. Data is simple time-series (≤90 daily points). Lightweight `<svg>` with `<polyline>` and `<rect>` elements.

Components:
- `LineChart` — outcome score trend, cost trend (SVG polyline + area fill)
- `BarChart` — turns per day, category breakdown (SVG rects)

Both accept `data: {x: string, y: number}[]` and render at a fixed aspect ratio inside a flex container.

**Interactivity:**
- Hover shows tooltip with exact value and date
- Click a data point to see that day's breakdown (drilldown future extension)

**Accessibility:**
- Each chart includes a `<title>` and `<desc>` element for screen readers
- A visually hidden `<table>` with the raw data is rendered alongside each chart for assistive technology
- Tooltip content is announced via `aria-live="polite"` region

---

## 2. Lessons Panel

Answers: "What has the system learned, and should I approve it?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Lessons                        [Search 🔍]  [Proposed ▾]   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ⏳ Verify file exists before attempting to read it    │   │
│  │    First seen: 2026-03-15 · Recurrences: 4           │   │
│  │    Correlated with 12 low-scoring turns               │   │
│  │                                                       │   │
│  │    [✅ Approve]  [✏️ Edit]  [🗑 Reject]              │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ── Approved ──────────────────────────────────────────────  │
│  ✅ Always confirm file paths are absolute (2026-03-10)      │
│  │   [↩ Revoke]                                              │
│  ...                                                         │
│                                                              │
│  ── Rejected ──────────────────────────────────────────────  │
│  ❌ Use try/except for all file reads (2026-03-12)           │
│  │   Rejected 2026-03-13 · Reason: "Too broad"              │
│  ...                                                         │
│                                                              │
│  Page 1 of 3  [← Prev] [Next →]                             │
└─────────────────────────────────────────────────────────────┘
```

### API: `GET /api/v1/optimization/lessons?status=proposed&page=1&per_page=50&q=file`

Query params:
- `status`: `proposed` | `approved` | `rejected` | `all` (default: `all`)
- `page`, `per_page`: pagination (default 1, 50)
- `q`: free-text search over lesson title/content

```json
{
  "items": [
    {
      "id": "2026-03-15-verify-file-exists",
      "filename": "2026-03-15-verify-file-exists.md",
      "title": "Verify file exists before reading",
      "content": "Verify a file exists before attempting to read it...",
      "status": "proposed",
      "first_observed": "2026-03-15",
      "recurrences": 4,
      "correlated_low_score_turns": 12,
      "created_at": "2026-03-15T14:22:00Z",
      "updated_at": "2026-03-15T14:22:00Z"
    }
  ],
  "total": 11,
  "page": 1,
  "per_page": 50,
  "pages": 1
}
```

Implementation: reads the filesystem (`prompts/_optimization/lessons/{proposed,approved,rejected}/*.md`), parses front-matter metadata from each file.

### API: `POST /api/v1/optimization/lessons/{id}/approve`

Moves the `.md` file from `proposed/` to `approved/`. Returns the updated lesson.

### API: `POST /api/v1/optimization/lessons/{id}/reject`

Body (optional): `{ "reason": "Too broad — needs specificity" }`

Moves the `.md` file from `proposed/` to `rejected/` (not deleted). Reason is appended to front-matter so the analysis engine can learn from rejected patterns.

**Rationale for reject → move (not delete):** Preserves audit trail. The analysis engine can also use rejected lessons as negative examples to avoid re-proposing similar candidates.

### API: `POST /api/v1/optimization/lessons/{id}/revoke`

Moves an approved lesson back to `proposed/`. Removes it from the active system prompt on next turn. Records revocation timestamp in front-matter.

### API: `PUT /api/v1/optimization/lessons/{id}`

Updates the content of a lesson file. Body: `{ "title": "...", "content": "..." }`.

Validation:
- `title` required, 5–200 characters
- `content` required, 10–2000 characters
- No executable code blocks (basic sanitization — reject if contains triple-backtick blocks with `python`, `bash`, `sh`, `js`, `javascript`)

### Inline Edit

Clicking "Edit" turns the lesson card into a textarea with title and content fields. Save commits the edit. Cancel discards. Unsaved changes show a dot indicator on the card.

### Confirmation Dialog

Reject shows a confirmation modal: "Reject this lesson? It will be archived, not deleted." with optional reason textarea.

---

## 3. Parameters Panel

Answers: "What knobs exist, what are they set to, and should I change any of them?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Parameters                                    [Filter ▾]    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ COMPRESSION_THRESHOLD                          8000   │   │
│  │ When to trigger history compression (tokens)          │   │
│  │ Range: 4000–16000  Step: 1000  Default: 8000          │   │
│  │                                                       │   │
│  │ ◀━━━━━━━━━━●━━━━━━━━━━━━━━━▶                         │   │
│  │ 4000              8000            16000               │   │
│  │                                                       │   │
│  │ 💡 Experiment suggests 10000 (p=0.03, +4% score)     │   │
│  │    Control: 45 obs, avg 0.71                          │   │
│  │    Treatment: 12 obs, avg 0.75                        │   │
│  │ [Apply 10000]  [Start New Experiment]                 │   │
│  │                                                       │   │
│  │ History: 8000 → 10000 (exp) → 8000 (rollback) → ...  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### API: `GET /api/v1/optimization/params`

Returns all tunable parameters with current values, defaults, metadata, and latest experiment results.

```json
{
  "params": [
    {
      "key": "COMPRESSION_THRESHOLD",
      "description": "Token count that triggers history compression. Higher = more context preserved but larger prompts.",
      "type": "int",
      "min": 4000,
      "max": 16000,
      "step": 1000,
      "default_value": 8000,
      "current_value": 8000,
      "last_changed_at": "2026-03-10T08:00:00Z",
      "last_changed_by": "experiment_promoted",
      "experiment": {
        "id": "exp_01HXYZ",
        "status": "concluded",
        "proposed_value": 10000,
        "control_mean_score": 0.71,
        "experiment_mean_score": 0.75,
        "p_value": 0.03,
        "conclusion": "promoted",
        "control_obs_count": 45,
        "experiment_obs_count": 12
      }
    }
  ]
}
```

Source: merges `TUNABLE_PARAMS` from `optimizer.py` with current config values and the latest experiment per parameter from `optimization_experiments` table.

### API: `PUT /api/v1/optimization/params/{key}`

Updates a parameter value. Body: `{ "value": 10000 }`.

Validation:
- `key` must exist in `TUNABLE_PARAMS`
- `value` must be within `[min, max]`
- `value` must land on a valid step: `(value - min) % step == 0`
- Type must match (`int` vs `float`)

Side effects:
- Stores previous value in `optimization_param_history` table (see schema additions below)
- Records `changed_by`: `"manual"`, `"experiment_promoted"`, or `"rollback"`

### API: `POST /api/v1/optimization/params/{key}/rollback`

Reverts the parameter to its previous value (from `optimization_param_history`). If no history exists, reverts to `default_value` from `TUNABLE_PARAMS`.

Returns the restored value and the rolled-back value.

### API: `GET /api/v1/optimization/params/{key}/history`

Returns the change history for a parameter:

```json
{
  "key": "COMPRESSION_THRESHOLD",
  "history": [
    {
      "value": 8000,
      "changed_at": "2026-03-01T00:00:00Z",
      "changed_by": "default"
    },
    {
      "value": 10000,
      "changed_at": "2026-03-10T08:00:00Z",
      "changed_by": "experiment_promoted",
      "experiment_id": "exp_01HXYZ"
    },
    {
      "value": 8000,
      "changed_at": "2026-03-15T12:00:00Z",
      "changed_by": "rollback"
    }
  ]
}
```

### API: `POST /api/v1/optimization/experiments`

Starts a new experiment. Body: `{ "param_key": "COMPRESSION_THRESHOLD", "proposed_value": 10000 }`.

Validation:
- No other experiment is currently active (max 1) — returns `409` with active experiment ID
- The proposed value is within range and on-step
- The parameter isn't already at the proposed value
- The same param+value combination hasn't been tested in the last 30 days (avoid re-running inconclusive experiments without new data)

### Slider

Each parameter gets an `<input type="range">` with correct min/max/step. Changing the slider shows the new value in a live preview but **does not save** until you click "Apply" or "Start Experiment".

The slider thumb snaps to valid steps. Current value is shown as a distinct marker. Default value is shown as a subtle tick mark.

**Keyboard:** Arrow keys move by one step. Home/End jump to min/max.

### Experiment Recommendations

When an experiment concludes with `conclusion: "promoted"`, the parameter card shows a green badge:

> 💡 Experiment suggests 10000 (p=0.03, +4% score, n=45/12)

One click to apply. The badge also shows sample sizes so the user can judge confidence.

---

## 4. Experiments Panel

Answers: "What's being tested right now, and what happened with past tests?"

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Experiments                              [Active ▾] [All]   │
│                                                              │
│  ── Active ────────────────────────────────────────────────  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 🧪 COMPRESSION_THRESHOLD: 8000 → 10000               │   │
│  │    Started: 2026-03-14  ·  Split: 80/20               │   │
│  │    Control: 45 obs (avg 0.71)                         │   │
│  │    Treatment: 12 obs (avg 0.75)                       │   │
│  │    Progress: ████████░░░░░░ 12/30 min obs             │   │
│  │    Est. completion: ~4 days (based on current rate)    │   │
│  │                                                       │   │
│  │    [Force Conclude]  [Cancel]                         │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ── Concluded ─────────────────────────────────────────────  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ✅ CYCLE_WINDOW: 20 → 30  ·  Promoted                │   │
│  │    p=0.02  ·  Control 0.68 (n=42) → Treatment 0.76 (n=35) │
│  │    Ran 2026-03-01 to 2026-03-10  ·  Applied: Yes     │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ❌ TOKEN_TIER_0: 16384 → 32768  ·  Rejected          │   │
│  │    p=0.41  ·  No significant difference               │   │
│  │    Ran 2026-02-20 to 2026-03-05                       │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ⚪ SIMILARITY_THRESHOLD: 0.3 → 0.25  ·  Cancelled    │   │
│  │    Cancelled 2026-03-08  ·  Reason: "Superseded"      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Page 1 of 2  [← Prev] [Next →]                             │
└─────────────────────────────────────────────────────────────┘
```

### API: `GET /api/v1/optimization/experiments?status=all&page=1&per_page=20`

Query params:
- `status`: `active` | `concluded` | `cancelled` | `all`
- `page`, `per_page`: pagination

```json
{
  "items": [
    {
      "id": "exp_01HXYZ",
      "param_key": "COMPRESSION_THRESHOLD",
      "baseline_value": "8000",
      "proposed_value": "10000",
      "rationale": "Higher threshold reduces compression frequency...",
      "status": "active",
      "created_at": "2026-03-14T10:00:00Z",
      "control_obs_count": 45,
      "experiment_obs_count": 12,
      "control_mean_score": 0.71,
      "experiment_mean_score": 0.75,
      "min_obs_per_cohort": 30,
      "max_duration_days": 14,
      "expires_at": "2026-03-28T10:00:00Z",
      "estimated_completion": "2026-03-22T00:00:00Z"
    }
  ],
  "total": 5,
  "page": 1,
  "per_page": 20,
  "pages": 1
}
```

### API: `POST /api/v1/optimization/experiments/{id}/conclude`

Force-conclude an experiment early. Runs the t-test on whatever data exists.

If either cohort has < 10 observations, the conclusion is automatically `"inconclusive"` with a warning: `"Insufficient data for statistical significance"`.

### API: `POST /api/v1/optimization/experiments/{id}/cancel`

Cancels an active experiment. Body (optional): `{ "reason": "Superseded by newer hypothesis" }`.

Observations already collected remain in the database for future analysis, but no new observations are assigned to the experiment cohort.

### API: `GET /api/v1/optimization/experiments/{id}/scores`

Returns raw score arrays for both cohorts — used for detail view or distribution chart.

```json
{
  "control": [0.8, 0.6, 0.9, 0.7],
  "experiment": [0.9, 0.8, 0.7, 0.85],
  "control_stats": {
    "mean": 0.71, "median": 0.70, "std": 0.12,
    "min": 0.2, "max": 1.0, "count": 45
  },
  "experiment_stats": {
    "mean": 0.75, "median": 0.77, "std": 0.10,
    "min": 0.3, "max": 1.0, "count": 12
  }
}
```

---

## 5. Data Retention Panel

Answers: "How much data is the optimization engine storing, and what's the purge policy?"

### API: `GET /api/v1/optimization/retention`

```json
{
  "observations": {
    "total_count": 4821,
    "oldest": "2025-12-01T00:00:00Z",
    "newest": "2026-03-19T11:00:00Z",
    "storage_estimate_mb": 18.2
  },
  "candidates": {
    "total_count": 47,
    "promoted_count": 11,
    "storage_estimate_mb": 0.8
  },
  "retention_policy": {
    "observations_max_days": 180,
    "observations_max_rows": 50000,
    "candidates_keep_promoted": true,
    "auto_purge_enabled": true,
    "last_purge_at": "2026-03-15T02:00:00Z",
    "next_purge_at": "2026-03-22T02:00:00Z"
  }
}
```

### API: `PUT /api/v1/optimization/retention`

Updates retention policy. Body: `{ "observations_max_days": 180, "observations_max_rows": 50000, "auto_purge_enabled": true }`.

The purge runs weekly (or on the next analysis cycle after the policy is updated). It deletes observations older than `max_days` and trims to `max_rows` (keeping newest). Promoted candidates are never purged.

---

## 6. Sub-Tab Navigation

The Optimization tab uses internal sub-tabs:

```
[Overview]  [Lessons]  [Parameters]  [Experiments]  [Retention]
```

Default view: Overview. Rendered as a horizontal pill bar within the tab content area, matching the Prompts tab pattern (which has "Fragments" and "Templates").

Sub-tab state is persisted in the URL hash: `#optimization/lessons`.

---

## 7. Frontend Components

### File Layout

```
frontend/src/app/settings/optimization/
    OptimizationTab.tsx        # Main component, sub-tab routing
    OverviewPanel.tsx          # Stats cards + charts
    LessonsPanel.tsx           # Proposed/approved/rejected lesson management
    ParametersPanel.tsx        # Parameter sliders + experiment recommendations
    ExperimentsPanel.tsx       # Active/concluded/cancelled experiments
    RetentionPanel.tsx         # Data retention policy + storage stats
    charts/
        LineChart.tsx          # Pure SVG line/area chart
        BarChart.tsx           # Pure SVG bar chart
        ProgressBar.tsx        # Simple CSS progress bar
    components/
        ConfirmDialog.tsx      # Reusable confirmation modal
        Pagination.tsx         # Page controls
        EmptyState.tsx         # "No data yet" placeholder
        ErrorBanner.tsx        # Inline error display
        LoadingSkeleton.tsx    # Shimmer placeholder during fetch
```

### Styling

Inline styles matching existing Settings tab conventions. Same color palette:
- Background: `#12121a` (card), `#1e1e2e` (input/detail)
- Accent: `#6c8aff` (blue), `#6cffa0` (green/success), `#ffcc44` (warning), `#ff6c8a` (error/reject)
- Text: `#e0e0e8` (primary), `#8888a0` (secondary), `#5a5a6e` (tertiary)

### Chart Colors

- Outcome score line: `#6cffa0` (green) with `#6cffa020` area fill
- Cost line: `#ffcc44` (amber) with `#ffcc4420` area fill
- Bar chart: `#6c8aff` (blue) bars
- Experiment control: `#8888a0` (gray)
- Experiment treatment: `#6c8aff` (blue)

### No External Dependencies

Charts are pure SVG. No recharts, d3, or chart.js. The data is simple enough (≤90 daily points, ≤20 parameters, ≤10 experiments) that SVG polylines and rects are adequate. Keeps the frontend dependency-free and fast.

### Loading & Error States

Every panel has three states:
1. **Loading** — `LoadingSkeleton` with shimmer animation matching card dimensions
2. **Error** — `ErrorBanner` with retry button and error detail (non-blocking; other panels still render)
3. **Empty** — `EmptyState` with contextual message ("No observations yet — the engine starts recording after your next conversation")

### Accessibility

- All interactive elements have visible focus indicators (2px `#6c8aff` outline)
- Tab navigation follows [WAI-ARIA Tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)
- Charts include hidden data tables as described in §1
- Color is never the only indicator — icons and text labels accompany all status states
- Minimum contrast ratio 4.5:1 for all text (verified against the color palette above)

### Responsive Behavior

- **≥1024px:** Full layout as wireframed
- **768–1023px:** Stats cards wrap to 2×2 grid; charts stack vertically
- **<768px:** Single-column layout; parameter sliders go full-width; sub-tabs become a dropdown

---

## 8. Backend Router

### File: `backend/app/api/v1/optimization.py`

```python
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/optimization", tags=["optimization"])
```

Registered in the FastAPI app alongside existing routers (same pattern as `prompts.py`, `memory.py`, etc.).

### DB Access

The agent's sqlite DB path comes from the worker's data directory. The API reads from it using `aiosqlite`. For lesson file operations, it reads/writes the filesystem at `prompts/_optimization/lessons/`.

### Concurrency

The sqlite DB is accessed via `aiosqlite` with WAL mode (already enabled for the agent DB). The API uses a connection pool (max 3 readers, 1 writer) to avoid contention. File operations (lesson approve/reject/revoke) use `os.rename()` which is atomic on POSIX.

### Parameter Descriptions

Human-readable descriptions for each parameter, alongside `TUNABLE_PARAMS`:

```python
PARAM_DESCRIPTIONS = {
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

PARAM_DEFAULTS = {k: v.get("default") for k, v in TUNABLE_PARAMS.items()}
```

---

## 9. Schema Additions (Doc 049 Amendments)

These tables extend the schema defined in doc 049.

### Parameter Change History

```sql
CREATE TABLE IF NOT EXISTS optimization_param_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    param_key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_by TEXT NOT NULL,           -- 'manual' | 'experiment_promoted' | 'rollback' | 'default'
    experiment_id TEXT,                  -- FK to optimization_experiments if applicable
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_param_history_key
    ON optimization_param_history(param_key, changed_at DESC);
```

### Rejected Lessons Directory

Add `prompts/_optimization/lessons/rejected/` alongside `proposed/` and `approved/`. Rejected lessons include front-matter with rejection reason and timestamp:

```yaml
---
title: Use try/except for all file reads
rejected_at: 2026-03-13T10:00:00Z
rejected_reason: Too broad — needs specificity
originally_proposed: 2026-03-12
recurrences: 2
---
```

### Additional Indexes for Doc 049 Tables

```sql
-- Performance indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_obs_created_at
    ON optimization_observations(created_at);
CREATE INDEX IF NOT EXISTS idx_obs_category_created
    ON optimization_observations(task_category, created_at);
CREATE INDEX IF NOT EXISTS idx_obs_cohort
    ON optimization_observations(cohort);
CREATE INDEX IF NOT EXISTS idx_obs_score
    ON optimization_observations(outcome_score);
CREATE INDEX IF NOT EXISTS idx_experiments_status
    ON optimization_experiments(status);
CREATE INDEX IF NOT EXISTS idx_candidates_promoted
    ON optimization_candidates(promoted);
```

---

## 10. Doc 049 Amendments

These issues were identified during dashboard design and should be addressed in the 049 implementation.

### 10a. Task Category Classifier

Doc 049 references `task_category` but doesn't define the classifier. Implementation:

```python
CATEGORY_KEYWORDS = {
    "coding": {"code", "function", "bug", "error", "compile", "test", "refactor",
               "implement", "class", "variable", "debug", "fix", "pr", "commit"},
    "research": {"search", "find", "look up", "what is", "explain", "compare",
                 "summarize", "article", "paper", "documentation"},
    "file_ops": {"file", "read", "write", "create", "delete", "move", "copy",
                 "rename", "directory", "folder", "path"},
    "chat": set(),  # Default fallback
}

def classify_task(user_message: str, tool_names: list[str]) -> str:
    """Classify a turn's task category from message content and tools used.

    Priority: tool-based signal > keyword matching > default.
    """
    msg_lower = user_message.lower()

    # Tool-based classification (strongest signal)
    coding_tools = {"edit", "write", "exec", "repo_pr"}
    if coding_tools & set(tool_names):
        return "coding"

    # Keyword scoring
    scores = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        if not keywords:
            continue
        scores[category] = sum(1 for kw in keywords if kw in msg_lower)

    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best

    return "chat"
```

### 10b. Improved User Correction Detection

The naive keyword heuristic in 049 will false-positive on "no problem", "nothing wrong", etc. Improved version:

```python
CORRECTION_PATTERNS = [
    r"\bthat'?s?\s+(not|wrong|incorrect)\b",
    r"\b(try|do)\s+(it\s+)?again\b",
    r"^no[,.]?\s",                          # "No, I meant..."
    r"\bi\s+(said|meant|asked|wanted)\b",
    r"\bactually[,]?\s",                     # "Actually, ..."
    r"\bstop\b",
    r"\bwrong\s+(file|path|dir|answer|approach)\b",
    r"\bundo\b",
    r"\brevert\b",
]

CORRECTION_ANTI_PATTERNS = [
    r"\bno\s+(problem|worries|rush|issue|need)\b",
    r"\bnothing\s+wrong\b",
    r"\bnot\s+bad\b",
    r"\bno\s+thanks?\b",
]

def detect_user_correction(message: str) -> bool:
    msg_lower = message.lower().strip()
    if len(msg_lower) < 3:
        return False
    for anti in CORRECTION_ANTI_PATTERNS:
        if re.search(anti, msg_lower):
            return False
    for pattern in CORRECTION_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
    return False
```

### 10c. Embedding Failure Handling

Add retry + graceful degradation for Voyage 4 API failures:

```python
async def embed_with_fallback(
    text: str,
    engine: EmbeddingEngine,
    max_retries: int = 2,
) -> list[float] | None:
    """Embed text with retry. Returns None on persistent failure.

    When embedding fails, the candidate is stored without a vector
    and flagged for re-embedding on the next successful cycle.
    """
    for attempt in range(max_retries + 1):
        try:
            return await engine.embed_query(text)
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.warning(f"Embedding failed after {max_retries + 1} attempts: {e}")
            return None
```

Candidates stored without embeddings get a `needs_embedding = TRUE` flag and are retried on the next analysis cycle.

### 10d. scipy Dependency

Add `scipy` to `requirements.txt` / `pyproject.toml`. Alternatively, implement the t-test inline to avoid the dependency (it's ~15 lines of math with just the `math` stdlib module):

```python
import math

def welch_ttest(a: list[float], b: list[float]) -> tuple[float, float]:
    """Welch's t-test (unequal variance). Returns (t_stat, p_value).

    Uses the Welch-Satterthwaite approximation for degrees of freedom
    and a t-distribution approximation for the p-value.
    """
    n_a, n_b = len(a), len(b)
    if n_a < 2 or n_b < 2:
        return 0.0, 1.0

    mean_a = sum(a) / n_a
    mean_b = sum(b) / n_b
    var_a = sum((x - mean_a) ** 2 for x in a) / (n_a - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n_b - 1)

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return 0.0, 1.0

    t = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    den = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = num / den if den > 0 else 1.0

    # Approximate p-value using regularized incomplete beta function
    # For large df, use normal approximation
    if df > 100:
        from math import erfc
        p = erfc(abs(t) / math.sqrt(2))
    else:
        # Beta function approximation (good enough for df > 2)
        x = df / (df + t * t)
        p = _regularized_incomplete_beta(df / 2, 0.5, x)

    return t, p
```

**Recommendation:** Use the inline implementation. It avoids pulling in a 30 MB dependency for one function.

### 10e. Data Retention

Add a purge function to `optimizer.py`:

```python
async def purge_stale_observations(
    db: aiosqlite.Connection,
    max_days: int = 180,
    max_rows: int = 50000,
):
    """Remove old observations beyond retention limits.

    Preserves observations referenced by active experiments.
    """
    # Age-based purge
    await db.execute("""
        DELETE FROM optimization_observations
        WHERE created_at < datetime('now', ? || ' days')
          AND id NOT IN (
            SELECT DISTINCT o.id FROM optimization_observations o
            JOIN optimization_experiments e ON o.cohort = e.id
            WHERE e.status = 'active'
          )
    """, (f"-{max_days}",))

    # Row-count purge (keep newest)
    await db.execute("""
        DELETE FROM optimization_observations
        WHERE id NOT IN (
            SELECT id FROM optimization_observations
            ORDER BY created_at DESC
            LIMIT ?
        )
    """, (max_rows,))

    # Clean orphaned vectors
    await db.execute("""
        DELETE FROM optimization_observations_vec
        WHERE id NOT IN (SELECT id FROM optimization_observations)
    """)

    await db.commit()
```

---

## 11. Interaction Flows

### Approve a Lesson

1. User opens Optimization → Lessons
2. Sees proposed lesson card with recurrence count and correlated low-scoring turns
3. Optionally clicks Edit to refine wording → textarea appears inline
4. Clicks Approve
5. API moves file from `proposed/` to `approved/`
6. Card animates to Approved section (300ms slide)
7. Toast: "Lesson approved — active on next turn"

### Reject a Lesson

1. User clicks Reject on a proposed lesson
2. Confirmation dialog appears: "Archive this lesson? You can optionally add a reason."
3. User types reason (optional) and confirms
4. API moves file to `rejected/` with reason in front-matter
5. Card removed from proposed list with fade animation

### Revoke an Approved Lesson

1. User clicks Revoke on an approved lesson
2. Confirmation: "Revoke this lesson? It will be moved back to Proposed."
3. API moves file from `approved/` back to `proposed/`
4. Toast: "Lesson revoked — removed from system prompt on next turn"

### Tune a Parameter Manually

1. User opens Optimization → Parameters
2. Adjusts slider for a parameter
3. Live preview shows new value; card border changes to indicate unsaved change
4. Clicks "Apply"
5. Confirmation: "Change COMPRESSION_THRESHOLD from 8000 to 10000?"
6. API updates the parameter and records history
7. Toast: "Parameter updated"

### Tune a Parameter via Experiment

1. User adjusts slider to a new value
2. Clicks "Start Experiment" instead of "Apply"
3. API creates experiment; card shows "Experiment running" with progress bar
4. Over time, observations accumulate in both cohorts
5. When min observations are met (or 14 days pass), experiment auto-concludes
6. Result badge appears on the parameter card
7. If promoted: user clicks "Apply" to set the value

### Rollback a Parameter

1. User opens Optimization → Parameters
2. Notices a parameter was recently changed and performance degraded
3. Clicks the history link → sees change log
4. Clicks "Rollback" → parameter reverts to previous value
5. Toast: "Rolled back to 8000"

---

## 12. Files Changed

| File | Change |
|------|--------|
| `backend/app/api/v1/optimization.py` | **New** — all optimization API endpoints |
| `backend/app/api/v1/__init__.py` (or app registration) | Register optimization router |
| `backend/app/agent/optimizer.py` | **Modified** — add retention purge, param history writes |
| `backend/app/agent/critic.py` | **Modified** — add rejected lesson directory handling |
| `backend/app/agent/outcome.py` | **Modified** — improved correction detection, task classifier |
| `frontend/src/app/settings/optimization/OptimizationTab.tsx` | **New** — main tab component |
| `frontend/src/app/settings/optimization/OverviewPanel.tsx` | **New** — stats + charts |
| `frontend/src/app/settings/optimization/LessonsPanel.tsx` | **New** — lesson management |
| `frontend/src/app/settings/optimization/ParametersPanel.tsx` | **New** — parameter tuning |
| `frontend/src/app/settings/optimization/ExperimentsPanel.tsx` | **New** — experiment monitoring |
| `frontend/src/app/settings/optimization/RetentionPanel.tsx` | **New** — data retention config |
| `frontend/src/app/settings/optimization/charts/LineChart.tsx` | **New** — SVG line chart |
| `frontend/src/app/settings/optimization/charts/BarChart.tsx` | **New** — SVG bar chart |
| `frontend/src/app/settings/optimization/charts/ProgressBar.tsx` | **New** — CSS progress bar |
| `frontend/src/app/settings/optimization/components/ConfirmDialog.tsx` | **New** — confirmation modal |
| `frontend/src/app/settings/optimization/components/Pagination.tsx` | **New** — page controls |
| `frontend/src/app/settings/optimization/components/EmptyState.tsx` | **New** — empty state placeholder |
| `frontend/src/app/settings/optimization/components/ErrorBanner.tsx` | **New** — error display |
| `frontend/src/app/settings/optimization/components/LoadingSkeleton.tsx` | **New** — loading shimmer |
| `frontend/src/app/settings/page.tsx` | Add "Optimization" to TABS array + import |
| `prompts/_optimization/lessons/rejected/` | **New** directory |

---

## 13. Test Plan

### Backend Unit Tests

| Test | File | Coverage |
|------|------|----------|
| Overview aggregation with 0, 1, 1000+ observations | `test_optimization_api.py` | Overview endpoint |
| Outcomes daily aggregation with category filter | `test_optimization_api.py` | Outcomes endpoint |
| Lesson CRUD: approve, reject (with reason), revoke, edit | `test_optimization_api.py` | Lessons endpoints |
| Lesson validation: reject empty content, oversized content | `test_optimization_api.py` | Input validation |
| Parameter update: valid, out-of-range, wrong step, wrong type | `test_optimization_api.py` | Params endpoints |
| Parameter rollback: with history, without history (→ default) | `test_optimization_api.py` | Rollback endpoint |
| Experiment creation: valid, duplicate active, same value, recent re-run | `test_optimization_api.py` | Experiments endpoints |
| Experiment conclusion: sufficient data, insufficient data | `test_optimization_api.py` | Conclude endpoint |
| Retention purge: age-based, row-count, preserves active experiment data | `test_optimization_api.py` | Retention |
| Task classifier: tool-based, keyword-based, fallback | `test_outcome.py` | Category classification |
| User correction detection: true positives, false positive avoidance | `test_outcome.py` | Correction heuristic |
| Welch's t-test: known values, edge cases (identical groups, n=2) | `test_optimizer.py` | Statistics |

### Frontend Tests

| Test | Coverage |
|------|----------|
| Sub-tab routing and URL hash persistence | Navigation |
| Loading → loaded → error state transitions for each panel | State management |
| Lesson approve/reject flows with confirmation | Lessons panel |
| Parameter slider snap-to-step behavior | Parameters panel |
| Chart renders with 0, 1, 30, 90 data points | Chart components |
| Keyboard navigation through tabs and sliders | Accessibility |
| Responsive layout breakpoints | Responsive design |

---

## 14. Rollout Plan

### Phase 1: Backend API (1–2 days)

1. Add database indexes (§9) to migration
2. Add `optimization_param_history` table
3. Create `rejected/` lessons directory
4. Implement all API endpoints with Pydantic validation
5. Write backend unit tests

### Phase 2: Frontend Core (2–3 days)

1. Add Optimization tab to settings page
2. Build Overview panel with SVG charts
3. Build Lessons panel with CRUD
4. Build Parameters panel with sliders

### Phase 3: Experiments + Polish (1–2 days)

1. Build Experiments panel
2. Build Retention panel
3. Add confirmation dialogs, error/loading/empty states
4. Responsive breakpoints
5. Accessibility audit

### Phase 4: Doc 049 Amendments (1 day)

1. Implement improved task classifier
2. Implement improved correction detection
3. Replace scipy with inline t-test
4. Add embedding failure handling
5. Add retention purge to analysis cycle

---

## 15. Performance Considerations

| Query | Expected Latency | Mitigation |
|-------|-------------------|------------|
| Overview (30-day aggregation) | 5–50ms for 10K rows | Indexes on `created_at`; consider materialized daily summary table if >100K rows |
| Outcomes (daily aggregates) | 5–50ms | Same indexes; GROUP BY date is efficient with sorted index |
| Lessons list | <5ms | Filesystem read; ≤50 files typical |
| Params list | <5ms | In-memory merge of config + DB query |
| Experiments list | <5ms | Small table (≤100 rows total) |
| Experiment scores | 10–100ms | Raw score arrays; paginate if >1000 per cohort |

If observation counts exceed 100K, add a `optimization_daily_summary` materialized table that the analysis engine updates during each cycle. The overview and outcomes endpoints read from the summary table instead of aggregating raw observations.

---

## 16. Security Considerations

- **Authentication:** All endpoints require the same auth as existing Settings API (session cookie / token)
- **Authorization:** No multi-tenant concerns (single agent per instance), but endpoints validate that the requesting user has admin-level access
- **Input sanitization:** Lesson content is sanitized against executable code injection (see §2 validation rules)
- **File path traversal:** Lesson IDs are validated against `^[a-zA-Z0-9_-]+$` before constructing filesystem paths — no directory traversal possible
- **Rate limiting:** Standard FastAPI rate limiting applies (same as other endpoints)
- **CSRF:** Mutation endpoints (POST/PUT) require the standard CSRF token (inherited from existing middleware)

---

## 17. Cost

Zero runtime cost. All data comes from sqlite tables already populated by the doc 049 engine. The API endpoints are read-heavy with occasional writes. No LLM calls, no embeddings, no external API calls from the dashboard itself.

The doc 049 amendments (improved classifier, correction detection, inline t-test) also add zero API cost — they replace external dependencies with local computation.

---

## 18. Open Questions

| # | Question | Proposed Answer |
|---|----------|-----------------|
| 1 | Should the dashboard auto-refresh? | Yes — poll every 60s on the Overview tab via `setInterval`. Other tabs refresh on navigation. No WebSocket/SSE needed for v1. |
| 2 | Should experiments auto-promote on conclusion? | No — always require human click. The whole point is human-in-the-loop. |
| 3 | Should we support bulk lesson actions? | Defer to v2. Single-action is fine for the expected volume (≤10 pending at a time). |
| 4 | Should rejected lessons be permanently deletable? | Defer. Keeping them for negative-example training is more valuable. Add a "Purge rejected" button in v2 if storage is a concern. |
| 5 | Should the overview show per-category breakdowns? | v1: category counts only. v2: add category-filtered charts. |

---

## 19. Future Extensions

- **Diff view for lessons:** show original proposal vs. human-edited version before approval
- **Observation drilldown:** click a chart data point to see individual turns from that day
- **Export:** download observations as CSV for external analysis
- **Notifications:** push a message to chat when a new lesson is proposed or experiment concludes (SSE events)
- **Category-filtered charts:** filter all views by task category
- **Lesson lineage:** show which observations triggered a lesson, with links to conversation turns
- **Multi-parameter experiments:** Bayesian optimization over the parameter space
- **Regression detection:** alert when scores drop after a deploy or config change

---

## 20. Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Chart library? | Pure SVG | Data is ≤90 points; no reason to add a dependency |
| Reject = delete or archive? | Archive to `rejected/` | Audit trail + negative examples for future analysis |
| Inline t-test or scipy? | Inline (Welch's t-test) | Avoids 30 MB dependency for one function |
| Parameter rollback scope? | One step back (or to default) | Simple, predictable; full undo stack is overkill for ≤15 params |
| Retention default? | 180 days / 50K rows | ~6 months of data for a busy agent; configurable |
| Auto-refresh? | 60s polling on Overview | Simple; SSE in v2 if needed |
| Mobile support? | Responsive breakpoints | Settings page is already desktop-primary but shouldn't break on tablet |
