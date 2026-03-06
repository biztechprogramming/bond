# Design Doc: Parallel Information Gathering & Utility Model Optimization

## 1. Goal
Optimize the agent's information-gathering phase by enforcing parallel tool execution and leveraging a "Utility Model" approach. This ensures the agent gathers all necessary context (memory, files, git status, environment) in the fewest possible turns.

## 2. Current Limitations
- **Sequential Exploration:** The agent often reads one file, then another, then searches memory, leading to high latency and token usage.
- **Incomplete Context:** Initial tool calls often miss relevant metadata (git status, directory structure) that would inform better subsequent calls.
- **Underutilized Parallelism:** While the underlying LLM supports multiple tool calls per turn, the system prompts do not explicitly mandate "batching" for discovery.
- **Sequential Loop Execution:** Even when the LLM emits multiple tool calls in a single turn, the agent loop executes them sequentially (`for tool_call ... await`), negating the latency benefit.

## 3. Codebase Audit (Pre-Implementation)

### Already Implemented
- **`file_read` batch mode:** Supports a `paths` parameter to read multiple files via `asyncio.gather` in a single tool call.
- **`parallel_orchestrate` tool:** Accepts batches of tool calls with dependency ordering.
- **Prompt guidance:** `tool-efficiency.md` and `proactive-workflow.md` already encourage batching and upfront discovery.

### Gaps Identified
1. **Agent loop (`loop.py` line ~337):** Tool calls from a single LLM turn are executed sequentially. Independent calls (e.g., `file_read` + `search_memory` + `code_execute`) should run concurrently via `asyncio.gather`.
2. **No explicit "Discovery Phase" mandate:** Prompts encourage batching but don't define a structured first-turn discovery protocol.
3. **No auto-context injection on `work_plan`:** The agent must spend a tool call to get git status; this could be injected automatically.

## 4. Proposed Changes

### A. Loop Optimization — Parallel Tool Execution
**File:** `backend/app/agent/loop.py`

Classify tool calls in each turn into two groups:
- **Independent (parallelizable):** `file_read`, `search_memory`, `code_execute`, `web_search`, `web_read` — these have no side effects on each other.
- **Sequential (side-effecting):** `file_write`, `file_edit`, `memory_save`, `respond`, `work_plan` — these must run in order.

Execute independent calls concurrently with `asyncio.gather`, then run sequential calls in order. If a `respond` (terminal) call is in the batch, execute all non-terminal calls first, then handle the terminal one.

### B. Prompt Engineering — Discovery Phase Mandate
**File:** `prompts/universal/tool-efficiency.md`

Add a "Discovery Phase" section:
- **Mandatory First Turn:** For non-trivial tasks, the first turn *must* batch discovery tools:
  - `search_memory`: Check for past context.
  - `code_execute`: `git status`, `git log --oneline -5`, directory structure.
  - `file_read` (with `outline: true`): Map the project structure.
- **Parallelism Constraint:** Never call a single "read" or "search" tool if 2+ related information needs can be identified.

### C. Auto-Context on Work Plan Creation (Future)
When `work_plan(action="create_plan")` is called, the system could auto-inject a lightweight context snapshot (current branch, last 3 commits, working directory). This is deferred to a follow-up PR to keep scope manageable.

## 5. Implementation Strategy
1. **Parallel loop execution** — modify `loop.py` to use `asyncio.gather` for independent tool calls.
2. **Discovery phase prompt** — update `tool-efficiency.md` with explicit first-turn batching mandate.
3. **Design doc update** — this document, updated with audit findings and implementation status.

## 6. Expected Impact
- **Reduced Latency:** 30-50% reduction in wall-clock time for turns with multiple tool calls.
- **Better Accuracy:** Agents start with a holistic view of the codebase rather than a fragmented one.
- **Lower Cost:** Fewer round-trips to the LLM due to better first-turn context gathering.

## 7. Risks & Mitigations
| Risk | Mitigation |
|------|-----------|
| Parallel execution of side-effecting tools causes race conditions | Strict classification: only pure-read tools run in parallel |
| LLM doesn't emit multiple tool calls despite prompt changes | `parallel_orchestrate` tool remains as explicit fallback |
| Error in one parallel call masks errors in others | Use `return_exceptions=True` in `asyncio.gather` and report all results |
