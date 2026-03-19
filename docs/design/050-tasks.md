# Design Doc 050 — Implementation Task List

## Phase 1: Schema & Backend Infrastructure

### 1.1 Database Schema Additions
- [ ] Add `optimization_param_history` table to `_AGENT_DB_SCHEMA` in `worker.py`
- [ ] Add missing indexes from §9 (idx_obs_created_at, idx_obs_category_created, idx_obs_cohort, idx_obs_score, idx_experiments_status, idx_candidates_promoted)
- [ ] Create `prompts/_optimization/lessons/rejected/` directory with `.gitkeep`

### 1.2 Agent DB Access from Main Backend
- [ ] Create `backend/app/db/agent_db.py` — aiosqlite connection helper that resolves agent.db path from `BOND_WORKER_DATA_DIR` env var
- [ ] Connection pool: max 3 readers, 1 writer (WAL mode already enabled by worker)

### 1.3 API Router: `backend/app/api/v1/optimization.py`
- [ ] Create router with `prefix="/optimization"`, `tags=["optimization"]`
- [ ] Register router in `backend/app/main.py`
- [ ] Pydantic models for all request/response schemas
- [ ] Error response schema: `{detail, code, context}` pattern

### 1.4 Overview Endpoints
- [ ] `GET /api/v1/optimization/overview` — aggregate stats, failure signals, category counts
- [ ] `GET /api/v1/optimization/outcomes?days=30&category=all` — daily aggregated chart data
- [ ] Empty state handling when < 50 observations

### 1.5 Lessons Endpoints
- [ ] `GET /api/v1/optimization/lessons?status=all&page=1&per_page=50&q=` — list with pagination + search
- [ ] `POST /api/v1/optimization/lessons/{id}/approve` — move proposed→approved
- [ ] `POST /api/v1/optimization/lessons/{id}/reject` — move proposed→rejected with optional reason
- [ ] `POST /api/v1/optimization/lessons/{id}/revoke` — move approved→proposed
- [ ] `PUT /api/v1/optimization/lessons/{id}` — edit lesson content (with validation)
- [ ] Lesson ID validation: `^[a-zA-Z0-9_-]+$` (path traversal prevention)
- [ ] Content sanitization: reject executable code blocks

### 1.6 Parameters Endpoints
- [ ] `GET /api/v1/optimization/params` — all params with current values, metadata, latest experiment
- [ ] `PUT /api/v1/optimization/params/{key}` — update with range/step/type validation
- [ ] `POST /api/v1/optimization/params/{key}/rollback` — revert to previous or default
- [ ] `GET /api/v1/optimization/params/{key}/history` — change log
- [ ] `PARAM_DESCRIPTIONS` dict with human-readable descriptions

### 1.7 Experiments Endpoints
- [ ] `GET /api/v1/optimization/experiments?status=all&page=1&per_page=20` — list
- [ ] `POST /api/v1/optimization/experiments` — create (max 1 active, range/step validation, 30-day cooldown)
- [ ] `POST /api/v1/optimization/experiments/{id}/conclude` — force conclude with min 10 obs guard
- [ ] `POST /api/v1/optimization/experiments/{id}/cancel` — cancel with optional reason
- [ ] `GET /api/v1/optimization/experiments/{id}/scores` — raw score arrays + stats

### 1.8 Retention Endpoints
- [ ] `GET /api/v1/optimization/retention` — storage stats + current policy
- [ ] `PUT /api/v1/optimization/retention` — update retention policy

---

## Phase 2: Doc 049 Amendments (Backend)

### 2.1 Improved User Correction Detection (`outcome.py`)
- [ ] Replace naive keyword regex with pattern + anti-pattern approach from §10b
- [ ] CORRECTION_PATTERNS and CORRECTION_ANTI_PATTERNS

### 2.2 Task Category Classifier (`outcome.py`)
- [ ] Enhance `classify_task()` with tool-based priority + broader keyword matching per §10a

### 2.3 Embedding Failure Handling (`critic.py`)
- [ ] `embed_with_fallback()` — retry with exponential backoff, return None on failure
- [ ] `needs_embedding` flag for candidates stored without vectors
- [ ] Re-embedding on next analysis cycle

### 2.4 Data Retention (`optimizer.py`)
- [ ] `purge_stale_observations()` — age-based + row-count purge, preserve active experiment data
- [ ] Integrate purge into `run_analysis()` cycle
- [ ] Retention config storage (read from DB or config)

### 2.5 scipy Removal
- [ ] Verify `_welch_t_test()` in optimizer.py is already inline (confirmed — already done in existing code)
- [ ] No action needed — already implemented without scipy

---

## Phase 3: Frontend — Shared Components

### 3.1 Chart Components
- [ ] `charts/LineChart.tsx` — SVG polyline + area fill, hover tooltips, hidden data table for a11y
- [ ] `charts/BarChart.tsx` — SVG rects, hover tooltips, hidden data table
- [ ] `charts/ProgressBar.tsx` — CSS progress bar

### 3.2 Shared UI Components
- [ ] `components/ConfirmDialog.tsx` — modal with title, message, optional textarea, confirm/cancel
- [ ] `components/Pagination.tsx` — page controls (prev/next + page indicator)
- [ ] `components/EmptyState.tsx` — icon + message + optional CTA
- [ ] `components/ErrorBanner.tsx` — inline error with retry button
- [ ] `components/LoadingSkeleton.tsx` — shimmer animation matching card dimensions

---

## Phase 4: Frontend — Panels

### 4.1 OptimizationTab.tsx (Main Component)
- [ ] Sub-tab routing: Overview | Lessons | Parameters | Experiments | Retention
- [ ] URL hash persistence: `#optimization/lessons`
- [ ] Register in `TABS` array in `settings/page.tsx`
- [ ] WAI-ARIA Tabs pattern

### 4.2 OverviewPanel.tsx
- [ ] Stats cards: avg score, turns, pending lessons, active experiments
- [ ] Period selector: 7d / 30d / 90d
- [ ] Outcome score trend (LineChart)
- [ ] Cost per turn trend (LineChart)
- [ ] Failure signals breakdown
- [ ] Empty state when < 50 observations
- [ ] 60-second auto-refresh via setInterval

### 4.3 LessonsPanel.tsx
- [ ] Filter by status (proposed/approved/rejected/all)
- [ ] Free-text search
- [ ] Lesson cards with metadata (first seen, recurrences, correlated low-score turns)
- [ ] Approve / Edit / Reject actions
- [ ] Inline edit mode (textarea for title + content)
- [ ] Reject confirmation dialog with optional reason
- [ ] Revoke action on approved lessons
- [ ] Pagination
- [ ] Card animations (slide on approve, fade on reject)

### 4.4 ParametersPanel.tsx
- [ ] Parameter cards with description, range, step, default, current value
- [ ] `<input type="range">` slider with snap-to-step
- [ ] Live preview of new value (unsaved indicator)
- [ ] "Apply" button with confirmation dialog
- [ ] "Start Experiment" button
- [ ] Experiment recommendation badge (green) when concluded with "promoted"
- [ ] History display
- [ ] Rollback button
- [ ] Keyboard: arrows = one step, Home/End = min/max

### 4.5 ExperimentsPanel.tsx
- [ ] Filter by status (active/concluded/cancelled/all)
- [ ] Active experiment card: progress bar, obs counts, estimated completion
- [ ] Concluded experiment cards: result badge, stats, date range
- [ ] Cancelled experiment cards
- [ ] "Force Conclude" and "Cancel" actions with confirmation
- [ ] Pagination

### 4.6 RetentionPanel.tsx
- [ ] Storage stats display (obs count, oldest/newest, storage estimate)
- [ ] Candidate stats (total, promoted, storage)
- [ ] Retention policy editor (max days, max rows, auto-purge toggle)
- [ ] Last/next purge timestamps
- [ ] Save button

### 4.7 Responsive Layout
- [ ] ≥1024px: full layout as wireframed
- [ ] 768–1023px: stats cards 2×2, charts stacked
- [ ] <768px: single-column, sliders full-width, sub-tabs → dropdown

### 4.8 Styling
- [ ] Inline styles matching existing Settings conventions
- [ ] Color palette: #12121a (card), #1e1e2e (input), #6c8aff (accent), #6cffa0 (success), #ffcc44 (warning), #ff6c8a (error)
- [ ] Chart colors per §7
- [ ] Visible focus indicators (2px #6c8aff outline)
- [ ] 4.5:1 contrast ratio compliance

---

## Phase 5: Backend Tests

- [ ] `test_optimization_api.py`:
  - Overview aggregation (0, 1, 1000+ observations)
  - Outcomes daily aggregation with category filter
  - Lesson CRUD: approve, reject (with reason), revoke, edit
  - Lesson validation: empty content, oversized, code blocks
  - Parameter update: valid, out-of-range, wrong step, wrong type
  - Parameter rollback: with history, without history
  - Experiment creation: valid, duplicate active, same value, recent re-run
  - Experiment conclusion: sufficient data, insufficient data
  - Retention purge: age-based, row-count, active experiment preservation
- [ ] `test_outcome.py`:
  - Task classifier: tool-based, keyword-based, fallback
  - User correction: true positives, anti-pattern avoidance
- [ ] `test_optimizer.py`:
  - Welch's t-test: known values, edge cases

---

## Phase 6: Integration & Polish

- [ ] End-to-end verification: create observations → see overview → approve lesson → tune parameter
- [ ] Error state testing: DB unavailable, empty state, 404s
- [ ] Accessibility audit: tab navigation, screen reader, focus management
- [ ] Cross-panel consistency: fonts, spacing, colors match existing Settings tabs
