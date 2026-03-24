# Design Doc 068: Parallel Pre-Gathering

**Status:** Draft  
**Date:** 2026-03-23  
**Updated:** 2026-03-24  
**Depends on:** 019 (Parallel Orchestration), 038 (Utility Model Pre-Gathering)

---

## 1. Problem

Bond's `pre_gather.py` runs gathering tasks **sequentially** before the main agent turn. For tasks that need multiple pieces of context — reading several files, checking git status, scanning for related issues — each gathering operation blocks the next.

A typical pre-gathering phase:

```
git status          →  200ms
read file A         →  150ms
read file B         →  150ms
grep for references →  300ms
read file C         →  150ms
                    ─────────
Total:                 950ms
```

These operations are independent — none depends on the output of another. They could all run in parallel:

```
git status     ─┐
read file A    ─┤
read file B    ─┼→  300ms (limited by slowest: grep)
grep for refs  ─┤
read file C    ─┘
```

That's a **3x speedup** on the pre-gathering phase alone. For web fetches (which can take 1-3 seconds each), parallelization is even more impactful.

**Latency is what users feel.** Cost optimization (docs 062-067) is important for operators, but latency is what makes the agent feel fast or slow. This is the highest-impact user-facing improvement in this batch.

## 2. What Bond Gets

1. **40-70% reduction in pre-gathering latency** depending on the number and type of gathering tasks
2. **Uses existing infrastructure** — Bond already has `parallel_worker.py` from doc 019
3. **No quality impact** — same information gathered, just faster
4. **Better gathering utilization** — with parallelism, the token budget from doc 067 is consumed more efficiently (all high-priority tasks start immediately rather than waiting in a queue)

## 3. Current Architecture

```
pre_gather.py
     │
     ▼
  plan_tasks()     → ordered list of GatherTask
     │
     ▼
  for task in tasks:    ← SEQUENTIAL
     execute(task)
     append result
     │
     ▼
  return results
```

### Current `pre_gather.py` Flow (simplified)

```python
def pre_gather(agent_config: dict, context: dict) -> list[GatherResult]:
    tasks = plan_gathering_tasks(agent_config, context)
    results = []
    for task in tasks:
        result = execute_gather_task(task)
        results.append(result)
    return results
```

## 4. New Architecture

```
pre_gather.py
     │
     ▼
  plan_tasks()     → ordered list of GatherTask
     │
     ▼
  partition into:
    ├── independent tasks  → run in parallel via asyncio.gather
    └── dependent tasks    → run sequentially after dependencies resolve
     │
     ▼
  collect results (ordered by original priority)
     │
     ▼
  return results
```

### Task Dependency Model

Most gathering tasks are independent. The few that have dependencies:

| Task | Depends On | Reason |
|------|-----------|--------|
| `read_file(path)` | Nothing | Independent |
| `git_status()` | Nothing | Independent |
| `git_diff(file)` | Nothing | Independent |
| `grep(pattern, dir)` | Nothing | Independent |
| `web_fetch(url)` | Nothing | Independent |
| `read_imports(file)` | `read_file(file)` | Needs file content to extract imports |
| `read_related(file)` | `grep(file_refs)` | Needs grep results to know what's related |

In practice, **>80% of gathering tasks are independent** and can run fully in parallel. The dependent tasks form a short chain (depth 1-2 at most).

## 5. Implementation

### Phase 1: Async Gathering + Thread Safety Audit (~3 days)

**Files changed:** `backend/app/agent/pre_gather.py`

#### Thread Safety Requirements

Moving `execute_gather_task` from sequential to a `ThreadPoolExecutor` requires verifying thread safety. The following invariants **must** hold:

1. **`context` is read-only during gathering.** Each task receives a frozen snapshot of context. Tasks must not mutate the shared `context` dict. If any task needs to modify context, it must operate on a deep copy.
2. **`execute_gather_task` has no shared mutable state.** Each invocation must be self-contained — no module-level caches, counters, or buffers that are written without locks.
3. **Logging is thread-safe.** Python's `logging` module is thread-safe by default (handlers acquire locks). Custom formatters or handlers must be verified.
4. **File system operations are read-only.** Gathering tasks only read files; mutations happen during agent execution, not pre-gathering.

> **Action item:** Before merging Phase 1, audit every code path reachable from `execute_gather_task` for shared mutable state. Document findings in a checklist in the PR.

#### Implementation

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

# Max concurrent gathering tasks
MAX_PARALLEL_TASKS = 8

# Lazy-initialized thread pool — created on first use, cleaned up at shutdown
_gather_pool: ThreadPoolExecutor | None = None

def _get_gather_pool() -> ThreadPoolExecutor:
    """Lazy-initialize the thread pool. Avoids allocating threads when gathering is disabled."""
    global _gather_pool
    if _gather_pool is None:
        _gather_pool = ThreadPoolExecutor(
            max_workers=MAX_PARALLEL_TASKS,
            thread_name_prefix="gather",
        )
    return _gather_pool

def shutdown_gather_pool() -> None:
    """Call during application shutdown to clean up the thread pool."""
    global _gather_pool
    if _gather_pool is not None:
        _gather_pool.shutdown(wait=False)
        _gather_pool = None

async def pre_gather_parallel(
    agent_config: dict, 
    context: dict,
    budget: int | None = None,  # from doc 067
    cancellation_event: asyncio.Event | None = None,  # for interrupt support
) -> list[GatherResult]:
    """Pre-gather information in parallel where possible."""
    tasks = plan_gathering_tasks(agent_config, context)
    
    if budget is not None:
        tasks = apply_budget_filter(tasks, budget)  # doc 067 integration
    
    # Partition into dependency tiers
    independent, dependent = partition_by_dependencies(tasks)
    
    # Freeze context snapshot for thread safety
    frozen_context = deepcopy(context)
    
    # Run independent tasks in parallel
    pool = _get_gather_pool()
    loop = asyncio.get_running_loop()
    independent_results = await asyncio.gather(*[
        _execute_with_timeout(pool, task, frozen_context)
        for task in independent
    ])
    
    # Build result map for dependency resolution
    # Only include successful results — failed tasks should NOT be passed to dependents
    result_map = {
        task.name: result 
        for task, result in zip(independent, independent_results)
        if not result.error
    }
    
    # Run dependent tasks (may also parallelize within a tier)
    dependent_results = await _execute_dependent_tasks(
        dependent, result_map, frozen_context, cancellation_event
    )
    
    # Merge and sort by original priority
    all_results = list(independent_results) + dependent_results
    all_results.sort(key=lambda r: r.priority, reverse=True)
    
    return all_results

def partition_by_dependencies(
    tasks: list[GatherTask],
) -> tuple[list[GatherTask], list[GatherTask]]:
    """Split tasks into independent and dependent groups."""
    independent = []
    dependent = []
    
    for task in tasks:
        if not task.depends_on:
            independent.append(task)
        else:
            dependent.append(task)
    
    return independent, dependent

async def _execute_dependent_tasks(
    tasks: list[GatherTask], 
    result_map: dict[str, GatherResult],
    frozen_context: dict,
    cancellation_event: asyncio.Event | None = None,
) -> list[GatherResult]:
    """Execute dependent tasks, resolving dependencies from result_map.
    
    Tasks whose dependencies failed (not present in result_map) are skipped
    with a warning. Only successful results are added to result_map for 
    downstream dependents.
    """
    pool = _get_gather_pool()
    loop = asyncio.get_running_loop()
    results = []
    
    # Group by dependency depth (simple topological sort)
    remaining = list(tasks)
    while remaining:
        # Check for cancellation between tiers
        if cancellation_event and cancellation_event.is_set():
            logger.info("Gathering cancelled — skipping %d remaining dependent tasks", len(remaining))
            for task in remaining:
                results.append(GatherResult(
                    task_name=task.name,
                    content="[Gathering cancelled]",
                    tokens=0,
                    error=True,
                ))
            break
        
        # Find tasks whose dependencies are all resolved (successfully)
        ready = [t for t in remaining if all(d in result_map for d in t.depends_on)]
        
        # Find tasks whose dependencies failed — skip them
        skipped = [
            t for t in remaining 
            if t not in ready and any(d not in result_map for d in t.depends_on)
            and not any(d in [r.name for r in remaining if r not in ready and r != t] for d in t.depends_on)
        ]
        for task in skipped:
            missing = [d for d in task.depends_on if d not in result_map]
            logger.warning(
                "Skipping task %s — dependencies failed or timed out: %s",
                task.name, missing,
            )
            results.append(GatherResult(
                task_name=task.name,
                content=f"[Skipped: dependencies unavailable: {', '.join(missing)}]",
                tokens=0,
                error=True,
            ))
        
        if not ready and not skipped:
            # Circular dependency or unresolvable — skip remaining
            logger.warning("Unresolvable dependencies: %s", [t.name for t in remaining])
            for task in remaining:
                results.append(GatherResult(
                    task_name=task.name,
                    content="[Skipped: unresolvable dependency cycle]",
                    tokens=0,
                    error=True,
                ))
            break
        
        # Execute ready tasks in parallel
        if ready:
            ready_results = await asyncio.gather(*[
                _execute_with_timeout(
                    pool,
                    task,
                    frozen_context,
                    dep_results={d: result_map[d] for d in task.depends_on},
                )
                for task in ready
            ])
            
            for task, result in zip(ready, ready_results):
                # Only add successful results so downstream dependents are skipped on failure
                if not result.error:
                    result_map[task.name] = result
                results.append(result)
        
        remaining = [t for t in remaining if t not in ready and t not in skipped]
    
    return results
```

### Phase 2: Task Dependency Declarations + Domain Serialization (~1.5 days)

**Files changed:** `backend/app/agent/pre_gather.py` (task planning)

Add dependency declarations to `GatherTask`:

```python
@dataclass
class GatherTask:
    name: str
    task_type: str          # "file_read", "git_status", "grep", "web_fetch", etc.
    args: dict
    priority: float         # 0.0-1.0, higher = more important
    estimated_tokens: int   # for budget integration (doc 067)
    depends_on: list[str] = field(default_factory=list)  # names of prerequisite tasks
    domain: str | None = None  # for web fetches: the domain for rate-limit grouping
```

Task planning already knows what tasks to create — this just adds explicit dependency edges.

#### Per-Domain Web Fetch Serialization

Web fetches to the same domain must be serialized to avoid rate limiting. This is implemented as a per-domain semaphore:

```python
from collections import defaultdict

# Per-domain concurrency limits for web fetches
_domain_semaphores: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))

async def _execute_with_domain_limit(
    pool: ThreadPoolExecutor,
    task: GatherTask,
    frozen_context: dict,
    dep_results: dict | None = None,
) -> GatherResult:
    """Wrap execution with per-domain concurrency limiting for web fetches."""
    if task.domain:
        async with _domain_semaphores[task.domain]:
            return await _execute_with_timeout(pool, task, frozen_context, dep_results)
    else:
        return await _execute_with_timeout(pool, task, frozen_context, dep_results)
```

### Phase 3: Timeout, Error Handling, and Cancellation (~1.5 days)

Individual gathering tasks can hang (slow web fetch, NFS stall). Add per-task timeouts:

```python
async def _execute_with_timeout(
    executor: ThreadPoolExecutor,
    task: GatherTask,
    frozen_context: dict,
    dep_results: dict[str, GatherResult] | None = None,
    timeout: float | None = None,
) -> GatherResult:
    """Execute a gather task with timeout."""
    if timeout is None:
        timeout = PARALLEL_WEB_TIMEOUT if task.task_type == "web_fetch" else PARALLEL_TASK_TIMEOUT
    
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(executor, execute_gather_task, task, frozen_context, dep_results),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("Gather task %s timed out after %.1fs", task.name, timeout)
        return GatherResult(
            task_name=task.name,
            content=f"[Gathering timed out after {timeout}s]",
            tokens=0,
            error=True,
        )
    except Exception as e:
        logger.error("Gather task %s failed: %s", task.name, e, exc_info=True)
        return GatherResult(
            task_name=task.name,
            content=f"[Gathering failed: {e}]",
            tokens=0,
            error=True,
        )
```

A failed or timed-out task doesn't block other tasks. Dependent tasks whose prerequisites failed are **skipped with a warning** — they are not executed with broken dependency results.

#### Cancellation Strategy

When the user sends a new message (interrupt) while pre-gathering is in progress, the system must handle in-flight tasks gracefully. Since `ThreadPoolExecutor` futures cannot be cancelled once running, we use **cooperative cancellation**:

1. **`cancellation_event`**: An `asyncio.Event` passed into `pre_gather_parallel`. The message queue handler (doc 005) sets this event when an interrupt arrives.
2. **Between tiers**: Before starting each tier of dependent tasks, we check the event. If set, remaining tasks are skipped and marked as cancelled.
3. **In-flight tasks run to completion but results are discarded**: Thread pool tasks that are already running will finish (unavoidable with threads), but their results won't be used if the gathering phase has been cancelled.
4. **Async tasks use `asyncio.wait_for`**: The timeout mechanism already provides a natural upper bound on how long any single task can run.

> **Note:** Full preemptive cancellation would require moving to `asyncio`-native tasks (no thread pool). This is a future optimization if cancel latency becomes an issue.

### Phase 4: Observability (~0.5 days)

Log parallel gathering performance and emit metrics to Langfuse:

```python
@dataclass
class GatherMetrics:
    total_tasks: int
    parallel_tasks: int
    sequential_tasks: int
    wall_clock_ms: float
    sequential_equivalent_ms: float  # how long it would have taken sequentially
    speedup: float                   # sequential_equivalent / wall_clock
    tasks_timed_out: int
    tasks_failed: int
    tasks_skipped: int               # due to dependency failures
    tasks_cancelled: int             # due to user interrupt
```

#### Metrics Sink

`GatherMetrics` are emitted as **Langfuse span attributes** on the pre-gathering trace. This integrates with Bond's existing observability infrastructure (`docker-compose.langfuse.yml`):

```python
from langfuse import get_current_trace

def emit_gather_metrics(metrics: GatherMetrics) -> None:
    """Attach gathering metrics to the current Langfuse trace."""
    trace = get_current_trace()
    if trace:
        trace.span(
            name="pre_gather_parallel",
            metadata={
                "total_tasks": metrics.total_tasks,
                "parallel_tasks": metrics.parallel_tasks,
                "sequential_tasks": metrics.sequential_tasks,
                "wall_clock_ms": metrics.wall_clock_ms,
                "sequential_equivalent_ms": metrics.sequential_equivalent_ms,
                "speedup": metrics.speedup,
                "tasks_timed_out": metrics.tasks_timed_out,
                "tasks_failed": metrics.tasks_failed,
                "tasks_skipped": metrics.tasks_skipped,
                "tasks_cancelled": metrics.tasks_cancelled,
            },
        )
    
    # Also log structured for local debugging
    logger.info(
        "Pre-gathering complete",
        extra={
            "gather_total_tasks": metrics.total_tasks,
            "gather_speedup": round(metrics.speedup, 2),
            "gather_wall_clock_ms": round(metrics.wall_clock_ms, 1),
            "gather_failures": metrics.tasks_failed + metrics.tasks_timed_out,
        },
    )
```

## 6. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PARALLEL_GATHERING_ENABLED` | `true` | Master switch |
| `PARALLEL_MAX_TASKS` | `8` | Max concurrent gathering tasks |
| `PARALLEL_TASK_TIMEOUT` | `10.0` | Per-task timeout (seconds) |
| `PARALLEL_WEB_TIMEOUT` | `15.0` | Per-task timeout for web fetches (longer) |
| `PARALLEL_GATHERING_ROLLOUT_PCT` | `100` | Percentage of requests using parallel gathering (for gradual rollout) |

## 7. Integration with Doc 067 (Adaptive Token Budgeting)

Parallel gathering + budget awareness work together. However, since parallelism means all tasks complete in roughly the same wall-clock time, we can adopt an **eager-gather, lazy-filter** strategy:

1. Budget is calculated (doc 067)
2. Tasks are planned and sorted by priority
3. **All tasks run in parallel** (regardless of budget)
4. After gathering completes, results are filtered by budget — highest-priority results are kept, lower-priority results that would exceed the budget are discarded

This gives the agent more information at no additional latency cost. The only argument against is token-counting overhead, which is negligible compared to the gathering I/O itself. If a task is truly expensive to *execute* (not just to *count*), it can be filtered pre-execution as a special case.

## 8. Rollback and Gradual Rollout

### Rollback Criteria

The `PARALLEL_GATHERING_ENABLED` master switch can be toggled off. Automatic rollback should be triggered if:

- **Task timeout rate exceeds 15%** (sustained over 1 hour) — indicates thread contention or resource exhaustion
- **Quality eval score regresses >2%** — indicates gathering order or dependency issues are affecting results
- **P95 turn latency increases** (rather than decreases) — indicates the overhead of parallelism outweighs the benefit

### Gradual Rollout Plan

1. **Week 1:** `PARALLEL_GATHERING_ROLLOUT_PCT=10` — enable for 10% of requests. Compare `GatherMetrics.speedup` and quality eval scores against the sequential baseline.
2. **Week 2:** If metrics are healthy, ramp to 50%.
3. **Week 3:** Ramp to 100% if no regressions detected.
4. **Ongoing:** Monitor `GatherMetrics` dashboards in Langfuse. Alert on timeout rate >10%.

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Race condition in file system (reading file while another task writes) | Low | Gathering tasks are read-only. File mutations happen during agent execution, not pre-gathering. |
| Thread pool exhaustion under concurrent users | Medium | Capped at `MAX_PARALLEL_TASKS` per request. Consider a shared semaphore across requests if load testing shows contention (see Backpressure below). |
| Memory spike from parallel results | Low | Results are strings (tool output text). Even 8 concurrent large file reads is ~200KB — trivial. |
| Error in one task affects others | Low | Each task is independent with its own try/except. Failures are isolated. Dependent tasks whose prerequisites failed are **skipped** (not executed with broken inputs). |
| Web fetch parallelism triggers rate limiting | Medium | Per-domain concurrency limit of 1 via `asyncio.Semaphore` (see Phase 2). Same-domain fetches are serialized; cross-domain fetches run in parallel. |
| Thread safety violations in `execute_gather_task` | High | **Mandatory audit before merge** — see Phase 1 thread safety requirements. Context is deep-copied. All shared state must be read-only or protected by locks. |
| User interrupt during gathering leaves orphan threads | Low | Cooperative cancellation via `asyncio.Event`. In-flight threads run to completion (unavoidable) but results are discarded. Timeout provides upper bound. |

### Backpressure / Adaptive Concurrency

`MAX_PARALLEL_TASKS = 8` is a static per-request cap. Under high concurrency (many simultaneous users), this could mean 8 threads × N users competing for I/O. Future improvement: use a **shared semaphore across requests** or make `MAX_PARALLEL_TASKS` adaptive based on system load (e.g., halve it when CPU usage >80%).

This is not required for Phase 1 but should be revisited after load testing with concurrent users.

## 10. Testing Strategy

### Unit Tests

| Test | What It Verifies |
|------|-----------------|
| `test_partition_by_dependencies` | Pure logic: tasks with no `depends_on` go to independent, others to dependent |
| `test_partition_all_independent` | Edge case: all tasks independent → dependent list is empty |
| `test_partition_all_dependent` | Edge case: all tasks have dependencies → independent list is empty |
| `test_dependency_resolution_order` | Topological sort: tier-1 deps resolve before tier-2 tasks execute |
| `test_failed_dependency_skips_downstream` | If task A fails, task B (depends on A) is skipped with error, not executed |
| `test_circular_dependency_detected` | Circular deps → remaining tasks skipped with warning, no infinite loop |
| `test_timeout_returns_error_result` | Task exceeding timeout returns `GatherResult(error=True)` |
| `test_cancellation_skips_remaining` | Setting `cancellation_event` skips unstarted tasks |
| `test_domain_serialization` | Two web fetches to same domain run sequentially; different domains run in parallel |

### Integration Tests

| Test | What It Verifies |
|------|-----------------|
| `test_parallel_matches_sequential` | Run the same task set both sequentially and in parallel — results must be identical (order-independent comparison) |
| `test_parallel_is_faster` | Wall-clock time for parallel execution < sequential for ≥3 independent tasks |
| `test_budget_filter_integration` | Budget filtering + parallel execution produces correct subset of results |

### Load / Stress Tests

| Test | What It Verifies |
|------|-----------------|
| `test_thread_pool_under_load` | 50 concurrent gathering requests don't deadlock or exhaust threads |
| `test_timeout_under_contention` | Tasks still respect timeouts when the thread pool is saturated |

## 11. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| Pre-gathering wall clock time | `GatherMetrics.wall_clock_ms` | -50% vs sequential |
| Speedup factor | `GatherMetrics.speedup` | >2x average |
| Total turn latency | End-to-end from user message to first response token | -20% (pre-gathering is ~30% of turn time) |
| Task timeout rate | `GatherMetrics.tasks_timed_out / total_tasks` | <5% |
| Task skip rate | `GatherMetrics.tasks_skipped / total_tasks` | <2% |
| Quality | Eval suite | No regression |

## 12. Estimated Effort

| Phase | Effort | Notes |
|-------|--------|-------|
| Phase 1: Async gathering + thread safety audit | 3 days | Includes audit of `execute_gather_task` call tree |
| Phase 2: Dependency declarations + domain serialization | 1.5 days | |
| Phase 3: Timeout, error handling, cancellation | 1.5 days | |
| Phase 4: Observability + Langfuse integration | 0.5 days | |
| Testing (unit + integration + load) | 1.5 days | |
| **Total** | **8 days** | |

> **Note:** The original estimate of 4.5 days did not account for the thread safety audit, testing, cancellation design, or domain serialization. The revised 8-day estimate reflects a production-ready implementation.

## 13. Relationship to Prior Docs

- **Doc 005 (Message Queue and Interrupts):** The cancellation strategy integrates with doc 005's interrupt handling. When a user interrupt arrives, the message queue handler sets the `cancellation_event` that pre-gathering checks between dependency tiers.
- **Doc 019 (Parallel Orchestration):** This doc uses the same pattern — fan out independent work, collect results. `parallel_worker.py` provides the infrastructure; this doc applies it to pre-gathering specifically.
- **Doc 038 (Utility Model Pre-Gathering):** Pre-gathering was introduced here. This doc optimizes its execution without changing what gets gathered.
- **Doc 067 (Adaptive Token Budgeting):** Parallel gathering + budget awareness are complementary. Budget decides *what* to keep; parallelism decides *how fast* to gather. With the eager-gather-lazy-filter approach, all tasks run in parallel and budget filtering happens post-completion.
- **Doc 062 (Headroom):** If Headroom is enabled, gathered results that exceed the tool result filter threshold get compressed. This happens after gathering completes — parallelism doesn't affect it.
