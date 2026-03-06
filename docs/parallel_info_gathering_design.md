# Design Doc: Parallel Information Gathering & Utility Model Optimization

## 1. Goal
Optimize the agent's information-gathering phase by enforcing parallel tool execution and leveraging a "Utility Model" approach. This ensures the agent gathers all necessary context (memory, files, git status, environment) in the fewest possible turns.

## 2. Current Limitations
- **Sequential Exploration:** The agent often reads one file, then another, then searches memory, leading to high latency and token usage.
- **Incomplete Context:** Initial tool calls often miss relevant metadata (git status, directory structure) that would inform better subsequent calls.
- **Underutilized Parallelism:** While the underlying LLM supports multiple tool calls per turn, the system prompts do not explicitly mandate "batching" for discovery.

## 3. Proposed Changes

### A. Prompt Engineering (The "Utility Model" Instruction)
Update the System Prompt to include a "Discovery Phase" mandate:
- **Mandatory First Turn:** If a task is non-trivial, the first turn *must* include a batch of discovery tools:
  - `search_memory`: To check for past context.
  - `code_execute`: To check `git status` and environment.
  - `file_read` (with `outline: true`): To map out the project structure.
- **Parallelism Constraint:** Instruct the agent to never call a single "read" or "search" tool if it can identify 2+ related information needs.

### B. Tool Logic Enhancements
- **Enhanced `file_read`:** Ensure the `outline` mode is highly efficient, providing a tree-like structure of the workspace to prevent blind `ls -R` calls.
- **Batching Utility:** Introduce or emphasize a "Batch Read" capability where multiple file paths can be requested in a single tool call (already supported by many LLMs, but needs explicit prompt backing).
- **Auto-Discovery:** When `work_plan` is called, the system could optionally inject a "Context Snapshot" (git branch, last 3 commits, current directory) to save the agent a tool call.

### C. Implementation Strategy
1. **System Prompt Update:** Add a section on "Tool Efficiency" and "Parallel Discovery".
2. **Loop Optimization:** Ensure `bond/backend/app/agent/loop.py` correctly handles and executes multiple tool calls in parallel where dependencies allow (e.g., all `file_read` calls in one turn).
3. **Utility Model Integration:** Define a "Utility" set of tools that are always considered "low-cost" and should be used aggressively at the start of a task.

## 4. Expected Impact
- **Reduced Latency:** 30-50% reduction in turns-to-completion for complex tasks.
- **Better Accuracy:** Agents start with a holistic view of the codebase rather than a fragmented one.
- **Lower Cost:** Fewer round-trips to the LLM.
