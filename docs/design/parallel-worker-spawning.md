# Design Doc: Parallel File Processing with Utility/Primary Model Architecture

## Status: Proposed
## Author: Bond AI
## Date: 2025-01-XX

---

## 1. Problem Statement

Currently, the agent loop in `worker.py` processes tool calls **sequentially within each iteration** — when the LLM requests multiple file reads, edits, or other independent operations, they execute one at a time. The existing `handle_parallel_orchestrate` in `native.py` provides batch execution of tool calls but:

1. **Doesn't spawn separate utility model workers** — it just runs tool handlers concurrently
2. **Doesn't use the utility model for information gathering** — all LLM calls go through the same model routing
3. **Doesn't have a gather→review pipeline** — no mechanism to have utility workers collect info and then have the primary model synthesize

## 2. Goals

- **Always parallelize information-gathering tool calls** (file_read, web_search, web_read, search_memory, code_execute with read-only intent)
- **Spawn utility model workers** for each independent information-gathering task
- **Aggregate results** from all workers and present them to the primary model for review/synthesis
- **Minimize primary model token usage** by offloading exploration to the cheaper utility model
- **Maintain correctness** — consequential actions (file_write, file_edit, code_execute with mutations) still go through the primary model

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    PRIMARY MODEL                         │
│  (claude-sonnet-4-20250514 / user-configured)           │
│                                                          │
│  Responsibilities:                                       │
│  - Decision making (what to do)                         │
│  - Consequential actions (writes, edits, responses)     │
│  - Review & synthesis of gathered information            │
│  - Final response generation                             │
└────────────┬────────────────────────┬───────────────────┘
             │ Spawns workers         │ Reviews results
             ▼                        ▲
┌────────────────────────────────────────────────────────┐
│              PARALLEL WORKER POOL                       │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ Worker 1 │  │ Worker 2 │  │ Worker 3 │  ...        │
│  │ (Utility)│  │ (Utility)│  │ (Utility)│             │
│  │          │  │          │  │          │             │
│  │ file_read│  │ web_read │  │ grep     │             │
│  │ + analyze│  │ + extract│  │ + parse  │             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
│       │              │              │                   │
│       ▼              ▼              ▼                   │
│  ┌─────────────────────────────────────────┐           │
│  │         RESULT AGGREGATOR               │           │
│  │  Collects, deduplicates, summarizes     │           │
│  └─────────────────────────────────────────┘           │
└────────────────────────────────────────────────────────┘
```

## 4. Detailed Design

### 4.1 Tool Classification

Tools are classified into two categories that determine execution strategy:

```python
# Information-gathering tools — safe to parallelize with utility model
PARALLEL_SAFE_TOOLS = {
    "file_read",
    "web_search", 
    "web_read",
    "search_memory",
    "code_execute",    # When read-only (grep, find, cat, ls, git log, etc.)
    "load_context",
}

# Consequential tools — must use primary model, execute sequentially
CONSEQUENTIAL_TOOLS = {
    "file_write",
    "file_edit", 
    "code_execute",    # When mutating (pip install, git commit, rm, etc.)
    "memory_save",
    "memory_update",
    "memory_delete",
    "respond",
    "work_plan",
    "repo_pr",
}
```

### 4.2 Worker Spawning Mechanism

When the LLM returns multiple tool calls in a single response, the worker loop will:

1. **Classify each tool call** as parallel-safe or consequential
2. **Group parallel-safe calls** into a batch
3. **Spawn a utility model worker coroutine** for each parallel-safe call
4. **Execute all workers concurrently** via `asyncio.gather()`
5. **Execute consequential calls sequentially** with the primary model (after parallel batch completes)
6. **Aggregate all results** and feed back to the primary model

```python
async def _spawn_utility_worker(
    tool_name: str,
    arguments: dict,
    context: dict,
    registry: ToolRegistry,
    utility_model: str,
    utility_kwargs: dict,
) -> dict:
    """Execute a single tool call as a utility model worker.
    
    For simple tool calls (file_read, web_search), just execute the tool directly.
    For complex gathering tasks, optionally use the utility model to analyze the result.
    """
    # Execute the tool
    result = await registry.execute(tool_name, arguments, context)
    
    # For large results, use utility model to extract relevant info
    if _should_summarize_result(tool_name, result):
        summary = await _utility_summarize(result, utility_model, utility_kwargs)
        result["_worker_summary"] = summary
    
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "result": result,
        "worker_model": utility_model,
    }
```

### 4.3 Enhanced Worker Loop Flow

The main change is in the tool execution section of `worker.py`'s `_run_turn()`:

```
For each LLM iteration:
  1. LLM returns tool_calls[]
  2. Classify tool_calls into parallel_batch[] and sequential_batch[]
  3. IF parallel_batch is not empty:
     a. Spawn utility workers for each call in parallel_batch
     b. await asyncio.gather(*workers)
     c. Collect results
  4. IF sequential_batch is not empty:
     a. Execute each sequentially (primary model for decisions)
  5. Aggregate all results into messages[]
  6. Feed back to LLM for next iteration
```

### 4.4 code_execute Intent Detection

Since `code_execute` can be either read-only or mutating, we need heuristic detection:

```python
# Read-only command patterns (safe to parallelize)
READ_ONLY_PATTERNS = [
    r"^(cat|head|tail|less|more)\s",
    r"^(ls|dir|find|locate)\s",
    r"^(grep|rg|ag|ack)\s",
    r"^(wc|du|df|stat)\s",
    r"^(git\s+(log|status|diff|show|branch))",
    r"^(echo|printf)\s",
    r"^(pwd|whoami|hostname|uname)",
    r"^(python|node|ruby)\s+-c\s+['\"].*['\"]",  # One-liner scripts
    r"^(sed|awk)\s+-n\s",  # sed/awk in print-only mode
]

# Mutating command patterns (must be sequential)
MUTATING_PATTERNS = [
    r"^(rm|mv|cp|mkdir|touch|chmod|chown)\s",
    r"^(pip|npm|yarn|cargo|apt|brew)\s+install",
    r"^(git\s+(commit|push|pull|merge|rebase|checkout|reset))",
    r"^(docker|kubectl)\s",
    r"^(echo|cat|tee)\s+.*[>|]",  # Redirects/pipes to files
]

def _is_read_only_command(code: str) -> bool:
    """Heuristically determine if a code_execute call is read-only."""
    first_line = code.strip().split('\n')[0].strip()
    
    for pattern in READ_ONLY_PATTERNS:
        if re.match(pattern, first_line):
            return True
    
    for pattern in MUTATING_PATTERNS:
        if re.match(pattern, first_line):
            return False
    
    # Default: treat as mutating (conservative)
    return False
```

### 4.5 Result Aggregation Strategy

After all parallel workers complete, results are aggregated before feeding back to the primary model:

```python
async def _aggregate_worker_results(
    worker_results: list[dict],
    utility_model: str,
    utility_kwargs: dict,
) -> list[dict]:
    """Aggregate results from parallel utility workers.
    
    Returns a list of tool result messages ready to be appended to the
    conversation history.
    """
    tool_messages = []
    
    for wr in worker_results:
        result = wr["result"]
        
        # If the worker produced a summary, include it
        summary = result.pop("_worker_summary", None)
        
        content = json.dumps(result, default=str)
        
        # If content is very large and we have a summary, use the summary
        # with a note that full content is available
        if summary and len(content) > 4000:
            content = json.dumps({
                "summary": summary,
                "full_result_truncated": True,
                "result_size": len(content),
                "key_data": _extract_key_data(result),
            })
        
        tool_messages.append({
            "role": "tool",
            "tool_call_id": wr.get("tool_call_id"),
            "content": content,
        })
    
    return tool_messages
```

### 4.6 Primary Model Review Phase

After gathering, the primary model reviews all collected information:

```
[System] You have received results from {N} parallel information-gathering 
operations. Review the collected data and decide on next steps.

[Tool Results] ... aggregated results from utility workers ...

[Primary Model] → Synthesizes information → Decides on consequential actions
```

This happens naturally in the existing loop — the tool results are appended as messages, and the next LLM call processes them. The key insight is that the **primary model only sees the aggregated results**, not the individual worker interactions.

## 5. Implementation Plan

### Phase 1: Core Infrastructure (This PR)

**File: `backend/app/agent/parallel_worker.py`** (NEW)
- `ParallelWorkerPool` class
- `_spawn_utility_worker()` 
- `_classify_tool_call()`
- `_is_read_only_command()`
- `_aggregate_worker_results()`

**File: `backend/app/worker.py`** (MODIFIED)
- Modify tool execution loop to use `ParallelWorkerPool`
- Add parallel batch detection before sequential execution
- Wire up utility model kwargs to worker pool

**File: `backend/app/agent/tools/native.py`** (MODIFIED)
- Enhance `handle_parallel_orchestrate` to use utility model workers
- Add result summarization support

**File: `backend/app/agent/tools/definitions.py`** (MODIFIED)
- Update `parallel_orchestrate` tool definition with worker model support

### Phase 2: Utility Model Summarization
- Add optional utility model summarization for large tool results
- Implement token-aware result truncation
- Add caching for repeated file reads

### Phase 3: Smart Batching
- Auto-detect independent tool calls that the LLM didn't explicitly parallelize
- Dependency graph analysis for tool call ordering
- Speculative pre-fetching based on conversation context

## 6. Configuration

```python
# In agent config (stored in DB)
{
    "utility_model": "anthropic/claude-sonnet-4-6",  # Already exists
    "parallel_config": {
        "enabled": True,
        "max_workers": 10,           # Max concurrent utility workers
        "summarize_threshold": 4000, # Summarize results larger than this (chars)
        "timeout_per_worker": 30,    # Seconds before worker timeout
    }
}
```

## 7. Metrics & Observability

- Log parallel batch size and execution time
- Track utility vs primary model token usage separately  
- Monitor worker success/failure rates
- Emit SSE events for parallel execution progress

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Rate limiting from parallel API calls | Semaphore-based concurrency limit (max_workers) |
| code_execute misclassified as read-only | Conservative default (treat unknown as mutating) |
| Large memory usage from concurrent results | Stream results, summarize large ones |
| Utility model hallucination in summaries | Primary model always reviews raw data for consequential decisions |
| Race conditions in shared state | Workers only read shared context, never write |

## 9. Testing Strategy

- Unit tests for tool classification (`_classify_tool_call`, `_is_read_only_command`)
- Integration tests for `ParallelWorkerPool` with mock registry
- End-to-end test: multi-file read task verifying parallel execution
- Performance benchmark: sequential vs parallel execution time
