# Design Doc 082: Goal Hierarchy & Context Propagation

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-29  
**Depends on:** 004 (Conversation Persistence), 011 (Multi-Agent Conversations)  
**Inspired by:** Paperclip's mission → goal → project → task hierarchy with automatic context inheritance

---

## 1. Problem Statement

Bond's work plans are **flat** — a plan has items, and items have statuses. There is no concept of *why* a task exists, what larger objective it serves, or how multiple work plans relate to each other. This causes several problems:

- **No prioritization signal** — When the agent has multiple pending tasks, it cannot reason about which is more important because there's no hierarchy connecting tasks to goals.
- **Lost context across conversations** — A user might discuss a goal in one conversation, create tasks in another, and work on them in a third. The agent has no structured way to carry the "why" forward.
- **No progress tracking at the goal level** — Users can't see "I'm 60% done with my API migration" because there's nothing aggregating task completion upward.
- **No alignment check** — The agent can't verify that a proposed action serves the user's stated objectives.

Paperclip solves this with a strict hierarchy: Company Mission → Goals → Projects → Tasks, where every task inherits context from its parent chain. Bond needs a lighter-weight version of this.

---

## 2. Goals

1. **Goal entity** — Users can define high-level goals with descriptions, deadlines, and priority.
2. **Project grouping** — Goals contain projects, projects contain work plans. A work plan always belongs to exactly one project (or a default "ungrouped" project).
3. **Context inheritance** — When the agent works on a task, it automatically receives the goal and project context in its system prompt, so it knows *why* it's doing the work.
4. **Progress rollup** — Goal and project completion percentages are computed from child task statuses.
5. **Alignment checking** — Before creating a work plan, the agent can check whether the proposed work aligns with any active goal.

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = goal, public)]
pub struct Goal {
    #[primary_key]
    pub id: String,
    pub agent_id: String,
    pub title: String,
    pub description: String,
    pub priority: u8,           // 1 (highest) to 5 (lowest)
    pub status: String,         // "active" | "completed" | "paused" | "abandoned"
    pub deadline: Option<Timestamp>,
    pub created_at: Timestamp,
    pub updated_at: Timestamp,
}

#[table(name = project, public)]
pub struct Project {
    #[primary_key]
    pub id: String,
    pub goal_id: String,
    pub title: String,
    pub description: String,
    pub status: String,         // "active" | "completed" | "paused"
    pub created_at: Timestamp,
    pub updated_at: Timestamp,
}
```

The existing `WorkPlan` table gains a new field:

```rust
pub project_id: Option<String>,  // FK to Project. None = ungrouped.
```

### 3.2 Reducers

- `create_goal {id, agentId, title, description, priority, deadline?}` 
- `update_goal {id, title?, description?, priority?, status?, deadline?}`
- `create_project {id, goalId, title, description}`
- `update_project {id, title?, description?, status?}`
- `assign_work_plan_to_project {workPlanId, projectId}`
- `unassign_work_plan_from_project {workPlanId}`

### 3.3 Computed Views

Progress rollup is computed client-side (or via a scheduled reducer):

```
Project.completion_pct = count(work_items WHERE status="done") / count(work_items) across all plans in project
Goal.completion_pct = avg(project.completion_pct) for all projects in goal
```

---

## 4. Context Propagation

### 4.1 Automatic Context Injection

When the agent starts working on a task that belongs to a project → goal chain, the system prompt includes:

```
## Active Context
**Goal:** {goal.title} — {goal.description}
**Project:** {project.title} — {project.description}  
**Priority:** {goal.priority}/5 | **Deadline:** {goal.deadline or "None"}
**Progress:** Goal {goal.completion_pct}% | Project {project.completion_pct}%

You are working on this task in service of the above goal. Keep your work aligned with the goal's intent.
```

This is injected as a prompt fragment with high priority, using the existing fragment system (010, 021).

### 4.2 Alignment Check Tool

A new tool available to the agent:

```python
def check_alignment(proposed_action: str) -> AlignmentResult:
    """Given a proposed action description, returns which active goals 
    it aligns with (if any) and a confidence score."""
```

This uses semantic similarity between the proposed action and active goal descriptions. It's advisory — the agent can proceed regardless, but it surfaces misalignment early.

---

## 5. Frontend Integration

### 5.1 Goals View

A new top-level view showing:

- **Goal cards** with title, description, priority badge, deadline, and progress bar.
- **Expand** to see child projects, each with their own progress bar.
- **Expand further** to see work plans within each project.
- **Drag-and-drop** to reorder goals by priority or move work plans between projects.

### 5.2 Conversation Integration

When viewing a conversation, a sidebar panel shows which goal/project the current work relates to, with a link to navigate to the goals view.

---

## 6. Migration Path

1. **Phase 1**: Add `Goal` and `Project` tables + reducers. Add `project_id` to `WorkPlan`. All existing work plans start with `project_id = None`.
2. **Phase 2**: Context propagation — inject goal/project context into system prompts when working on assigned tasks.
3. **Phase 3**: Frontend goals view with progress rollup.
4. **Phase 4**: Alignment check tool (requires semantic similarity, may depend on embedding infrastructure).

---

## 7. Open Questions

- Should goals be per-agent or global? If a user has multiple agents, do they share goals?
- How deep should the hierarchy go? Paperclip has 4 levels (mission → goal → project → task). Two levels (goal → project → work plan) may be enough for a personal assistant.
- Should the agent be able to create goals autonomously, or only the user?
- How do we handle goal conflicts (two goals with contradictory requirements)?
