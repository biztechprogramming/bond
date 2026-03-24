# Design Doc 068: Parallel Pre-Gathering

**Status:** Draft  
**Date:** 2026-03-23  
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

### Phase 1: Async Gathering (~2 days)

**Files changed:** `backend/app/agent/pre_gather.py`

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Max concurrent gathering tasks
MAX_PARALLEL_TASKS = 8

# Thread pool for IO-bound tasks (file reads, web fetches)
_gather_pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_TASKS)


async def pre_gather_parallel(
    agent_config: dict, 
    context: dict,
    budget: int | None = None,  # from doc 067
) -> list[GatherResult]:
    """Pre-gather information in parallel where possible."""
    tasks = plan_gathering_tasks(agent_config, context)
    
    if budget is not None:
        tasks = apply_budget_filter(tasks, budget)  # doc 067 integration
    
    # Partition into dependency tiers
    independent, dependent = partition_by_dependencies(tasks)
    
    # Run independent tasks in parallel
    loop = asyncio.get_event_loop()
    independent_results = await asyncio.gather(*[
        loop.run_in_executor(_gather_pool, execute_gather_task, task)
        for task in independent
    ])
    
    # Build result map for dependency resolution
    result_map = {
        task.name: result 
        for task, result in zip(independent, independent_results)
    }
    
    # Run dependent tasks (may also parallelize within a tier)
    dependent_results = await _execute_dependent_tasks(dependent, result_map)
    
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
) -> list[GatherResult]:
    """Execute dependent tasks, resolving dependencies from result_map."""
    loop = asyncio.get_event_loop()
    results = []
    
    # Group by dependency depth (simple topological sort)
    remaining = list(tasks)
    while remaining:
        # Find tasks whose dependencies are all resolved
        ready = [t for t in remaining if all(d in result_map for d in t.depends_on)]
        if not ready:
            # Circular dependency or unresolvable — skip remaining
            logger.warning("Unresolvable dependencies: %s", [t.name for t in remaining])
            break
        
        # Execute ready tasks in parallel
        ready_results = await asyncio.gather(*[
            loop.run_in_executor(
                _gather_pool, 
                execute_gather_task, 
                task, 
                {d: result_map[d] for d in task.depends_on},
            )
            for task in ready
        ])
        
        for task, result in zip(ready, ready_results):
            result_map[task.name] = result
            results.append(result)
        
        remaining = [t for t in remaining if t not in ready]
    
    return results
```

### Phase 2: Task Dependency Declarations (~1 day)

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
    depends_on: list[str] = field(default_factory=list)  # NEW: names of prerequisite tasks
```

Task planning already knows what tasks to create — this just adds explicit dependency edges.

### Phase 3: Timeout and Error Handling (~1 day)

Individual gathering tasks can hang (slow web fetch, NFS stall). Add per-task timeouts:

```python
async def _execute_with_timeout(
    executor: ThreadPoolExecutor,
    task: GatherTask,
    timeout: float = 10.0,  # seconds
) -> GatherResult:
    """Execute a gather task with timeout."""
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(executor, execute_gather_task, task),
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
        logger.warning("Gather task %s failed: %s", task.name, e)
        return GatherResult(
            task_name=task.name,
            content=f"[Gathering failed: {e}]",
            tokens=0,
            error=True,
        )
```

A failed or timed-out task doesn't block other tasks. Dependent tasks that needed the failed result are skipped with a warning.

### Phase 4: Observability (~0.5 days)

Log parallel gathering performance:

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
```

## 6. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PARALLEL_GATHERING_ENABLED` | `true` | Master switch |
| `PARALLEL_MAX_TASKS` | `8` | Max concurrent gathering tasks |
| `PARALLEL_TASK_TIMEOUT` | `10.0` | Per-task timeout (seconds) |
| `PARALLEL_WEB_TIMEOUT` | `15.0` | Per-task timeout for web fetches (longer) |

## 7. Integration with Doc 067 (Adaptive Token Budgeting)

Parallel gathering + budget awareness work together:

1. Budget is calculated (doc 067)
2. Tasks are planned and sorted by priority
3. Tasks that would exceed the budget are filtered out
4. Remaining tasks run in parallel

This means the gathering phase is both *faster* (parallel) and *smarter* (budget-aware). The agent gets the highest-priority information first, within the time it takes for the slowest individual task.

## 8. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Race condition in file system (reading file while another task writes) | Low | Gathering tasks are read-only. File mutations happen during agent execution, not pre-gathering. |
| Thread pool exhaustion | Low | Capped at `MAX_PARALLEL_TASKS`. Tasks beyond the cap queue in the executor. |
| Memory spike from parallel results | Low | Results are strings (tool output text). Even 8 concurrent large file reads is ~200KB — trivial. |
| Error in one task affects others | Medium | Each task is independent with its own try/except. Failures are isolated. Dependent tasks skip gracefully. |
| Web fetch parallelism triggers rate limiting | Medium | Web fetches are typically to different domains. Same-domain fetches should be serialized. Add a per-domain concurrency limit of 1. |

## 9. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| Pre-gathering wall clock time | `GatherMetrics.wall_clock_ms` | -50% vs sequential |
| Speedup factor | `GatherMetrics.speedup` | >2x average |
| Total turn latency | End-to-end from user message to first response token | -20% (pre-gathering is ~30% of turn time) |
| Task timeout rate | `GatherMetrics.tasks_timed_out / total_tasks` | <5% |
| Quality | Eval suite | No regression |

## 10. Relationship to Prior Docs

- **Doc 019 (Parallel Orchestration):** This doc uses the same pattern — fan out independent work, collect results. `parallel_worker.py` provides the infrastructure; this doc applies it to pre-gathering specifically.
- **Doc 038 (Utility Model Pre-Gathering):** Pre-gathering was introduced here. This doc optimizes its execution without changing what gets gathered.
- **Doc 067 (Adaptive Token Budgeting):** Parallel gathering + budget awareness are complementary. Budget decides *what* to gather; parallelism decides *how fast*.
- **Doc 062 (Headroom):** If Headroom is enabled, gathered results that exceed the tool result filter threshold get compressed. This happens after gathering completes — parallelism doesn't affect it.
