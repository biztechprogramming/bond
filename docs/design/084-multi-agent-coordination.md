# Design Doc 084: Multi-Agent Coordination

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-30  
**Depends on:** 011 (Multi-Agent Conversations), 037 (Coding Agent Skill), 082 (Goal Hierarchy)  
**Inspired by:** Paperclip's org-chart-based agent delegation and capability registry

---

## 1. Problem Statement

Bond currently operates as a single agent with the ability to spawn coding sub-agents (Design Doc 037). However, this delegation is ad-hoc — the main agent decides when to spawn a coder based on heuristics, and there is no structured way to:

- Register agents with **declared capabilities** (e.g., "I can write Python," "I can search the web," "I can review PRs").
- **Route tasks** to the best-suited agent based on capability matching and availability.
- **Coordinate multiple agents** working on related sub-tasks of a larger goal.
- **Aggregate results** from parallel agent work into a coherent response.
- Track **delegation chains** (who asked whom to do what, and what was the result).

Paperclip models this as a corporate org chart — agents have roles, report to managers, and tasks flow up and down the hierarchy. Bond needs a lighter-weight version: a capability registry, structured task handoff, and result aggregation — without the overhead of simulating a company.

---

## 2. Goals

1. **Capability registry** — Each agent type declares what it can do, what tools it has access to, and what models it prefers.
2. **Task routing** — Given a task description, automatically select the best agent(s) based on capability match, cost, and availability.
3. **Structured delegation** — A parent agent can delegate sub-tasks with clear inputs, expected outputs, and deadlines.
4. **Result aggregation** — When multiple agents work on sub-tasks, their results are collected, validated, and synthesized.
5. **Delegation visibility** — Users can see the full delegation tree: who is working on what, current status, and cost so far.

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = agent_capability, public)]
pub struct AgentCapability {
    #[primary_key]
    pub id: String,
    pub agent_type: String,         // "claude", "codex", "pi", "browser", "search"
    pub capability: String,         // "code_python", "code_typescript", "web_search", "file_edit", "code_review"
    pub proficiency: u8,            // 1-10 self-assessed or measured quality
    pub cost_tier: String,          // "low", "medium", "high"
    pub max_concurrent: u8,         // how many parallel instances allowed
    pub tools_available: String,    // JSON array of tool names
    pub created_at: Timestamp,
}

#[table(name = delegated_task, public)]
pub struct DelegatedTask {
    #[primary_key]
    pub id: String,
    pub parent_task_id: Option<String>,     // null for root tasks
    pub conversation_id: String,
    pub work_plan_id: Option<String>,       // link to 082 goal hierarchy
    pub delegating_agent: String,           // who assigned this
    pub assigned_agent: String,             // who is doing it
    pub title: String,
    pub description: String,
    pub input_context: String,              // JSON: files read, decisions made, constraints
    pub expected_output: String,            // what the parent expects back
    pub status: String,                     // "pending", "in_progress", "completed", "failed", "cancelled"
    pub result_summary: Option<String>,     // agent's summary of what it did
    pub result_artifacts: Option<String>,   // JSON: files changed, PRs created, etc.
    pub cost_usd: f64,
    pub started_at: Option<Timestamp>,
    pub completed_at: Option<Timestamp>,
    pub created_at: Timestamp,
}

#[table(name = agent_instance, public)]
pub struct AgentInstance {
    #[primary_key]
    pub id: String,
    pub agent_type: String,
    pub task_id: String,
    pub status: String,             // "idle", "working", "blocked", "done"
    pub last_heartbeat: Timestamp,
    pub created_at: Timestamp,
}
```

### 3.2 Reducers

- `register_capability {id, agentType, capability, proficiency, costTier, maxConcurrent, toolsAvailable}` — Declare what an agent type can do.
- `create_delegated_task {id, parentTaskId, conversationId, workPlanId, delegatingAgent, assignedAgent, title, description, inputContext, expectedOutput}` — Parent agent creates a sub-task.
- `update_task_status {id, status, resultSummary, resultArtifacts, costUsd}` — Agent reports progress or completion.
- `cancel_task {id, reason}` — Parent or user cancels a delegated task.

---

## 4. Architecture

### 4.1 Capability Registry

At startup, each agent type registers its capabilities. The registry is queryable:

```python
async def find_best_agent(task_description: str, required_capabilities: list[str]) -> str:
    """Match task requirements to agent capabilities.
    
    Returns the agent_type with the highest aggregate proficiency
    for the required capabilities, weighted by cost tier preference.
    """
    candidates = await get_agents_with_capabilities(required_capabilities)
    if not candidates:
        raise NoCapableAgentError(f"No agent has capabilities: {required_capabilities}")
    
    scored = []
    for agent in candidates:
        score = sum(c.proficiency for c in agent.capabilities if c.capability in required_capabilities)
        cost_weight = {"low": 1.2, "medium": 1.0, "high": 0.8}[agent.cost_tier]
        availability = await get_available_slots(agent.agent_type)
        if availability > 0:
            scored.append((agent.agent_type, score * cost_weight))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0] if scored else raise NoAvailableAgentError("All capable agents are at capacity")
```

### 4.2 Delegation Flow

```
User Request
    │
    ▼
┌─────────────────┐
│  Orchestrator    │  ← Primary Bond agent
│  (Claude)        │
└────────┬────────┘
         │ Analyzes task, identifies sub-tasks
         │
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│ Codex  │ │ Claude │ │ Claude │
│ Agent  │ │ Agent  │ │ Agent  │
│(coding)│ │(review)│ │(docs)  │
└───┬────┘ └───┬────┘ └───┬────┘
    │          │          │
    ▼          ▼          ▼
  Results aggregated by Orchestrator
    │
    ▼
  Synthesized response to user
```

### 4.3 Task Decomposition

The orchestrator uses a structured prompt to break complex requests into delegatable sub-tasks:

```python
DECOMPOSITION_PROMPT = """
Given this user request, identify sub-tasks that can be delegated to specialized agents.

Available agent capabilities: {capabilities}

For each sub-task, specify:
- title: short description
- required_capabilities: list of capability keys needed
- input_context: what the agent needs to know
- expected_output: what should come back
- dependencies: which other sub-tasks must complete first

User request: {request}
"""
```

### 4.4 Result Aggregation

When all sub-tasks complete, the orchestrator synthesizes:

```python
async def aggregate_results(parent_task_id: str) -> str:
    """Collect all sub-task results and synthesize a response."""
    sub_tasks = await get_sub_tasks(parent_task_id)
    
    failed = [t for t in sub_tasks if t.status == "failed"]
    if failed:
        # Report partial failure — never silently ignore
        failure_summary = "\n".join(f"- {t.title}: {t.result_summary}" for t in failed)
        logger.warning("Sub-tasks failed for parent %s: %s", parent_task_id, failure_summary)
    
    completed = [t for t in sub_tasks if t.status == "completed"]
    context = "\n\n".join(
        f"## {t.title}\n{t.result_summary}\nArtifacts: {t.result_artifacts}"
        for t in completed
    )
    
    return await synthesize_response(context, failed_tasks=failed)
```

---

## 5. Interaction with Existing Systems

| System | Integration |
|--------|------------|
| Coding agent (037) | Becomes one registered agent type with capabilities `["code_python", "code_typescript", "file_edit", "code_review"]` |
| Multi-agent conversations (011) | Conversation context shared across delegated agents via `input_context` |
| Goal hierarchy (082) | Delegated tasks link to work plan items; cost rolls up to project/goal level |
| Cost tracking (081) | Each agent instance reports cost; parent task aggregates sub-task costs |
| Circuit breakers (070) | Per-agent cost limits enforced independently; parent task has aggregate limit |

---

## 6. Migration Path

1. **Phase 1**: Capability registry — register the existing agent types (claude, codex, pi) with their capabilities. No behavioral change yet.
2. **Phase 2**: Structured delegation — replace the ad-hoc coding agent spawning with `DelegatedTask` creation. Orchestrator decomposes tasks and routes to best agent.
3. **Phase 3**: Parallel execution — allow multiple sub-agents to work concurrently on independent sub-tasks. Add result aggregation.
4. **Phase 4**: Dynamic scaling — agent instances can be spawned on demand based on workload, with `max_concurrent` limits enforced.

---

## 7. Open Questions

- How much context should be passed to sub-agents? Full conversation history is expensive; minimal context risks the agent missing important details. Should we use the context distillation pipeline (012) to compress?
- Should agents be able to re-delegate? (Agent A delegates to Agent B, who delegates part of it to Agent C.) If so, how deep can the chain go before it becomes unmanageable?
- How do we handle conflicting results from parallel agents? (e.g., two agents edit the same file differently.) Merge? Pick one? Ask the user?
- Should the user be able to override agent routing? ("Use Codex for this, not Claude.")
