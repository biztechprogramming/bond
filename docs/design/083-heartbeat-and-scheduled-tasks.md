# Design Doc 083: Heartbeat & Scheduled Task System

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-29  
**Depends on:** 008 (Containerized Agent Runtime), 004 (Conversation Persistence), 081 (Cost Tracking)  
**Inspired by:** Paperclip's agent heartbeat system where agents wake on a schedule, check for work, and act autonomously

---

## 1. Problem Statement

Bond is **purely reactive** — the agent does nothing until the user sends a message. This means:

- **No proactive work** — The agent can't check on long-running processes, send daily summaries, or monitor systems without being asked.
- **No background task processing** — If a user creates a work plan with 10 items, the agent waits for the user to ask about each one instead of working through them autonomously.
- **No scheduled operations** — Common workflows like "review PRs every morning", "check CI status every hour", or "summarize my emails at 6 PM" require the user to remember to ask.
- **No autonomous follow-up** — After completing a task, the agent can't check back later to verify the deployment succeeded or the test suite stayed green.

Paperclip agents have a heartbeat loop: they wake on a configurable schedule, check their task queue, and execute pending work. Bond needs a similar mechanism.

---

## 2. Goals

1. **Scheduled triggers** — Users can define recurring schedules (cron-style) that wake the agent to perform specific actions.
2. **Task queue processing** — The agent can autonomously work through pending work plan items without user prompting.
3. **One-shot delayed tasks** — "Remind me in 2 hours" or "Check if the deploy succeeded in 30 minutes."
4. **Heartbeat monitoring** — A lightweight periodic check-in where the agent evaluates whether any proactive action is needed.
5. **User control** — All autonomous behavior is opt-in, pausable, and bounded by budget controls (081).

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = scheduled_task, public)]
pub struct ScheduledTask {
    #[primary_key]
    pub id: String,
    pub agent_id: String,
    pub title: String,
    pub description: String,
    pub task_type: String,          // "cron" | "one_shot" | "heartbeat"
    pub cron_expression: Option<String>,  // "0 9 * * *" for daily at 9am
    pub run_at: Option<Timestamp>,  // For one_shot tasks
    pub action: String,             // What to do: "process_queue" | "run_prompt" | "check_url" | "custom"
    pub action_config: String,      // JSON config for the action (prompt text, URL, etc.)
    pub conversation_id: Option<String>,  // Which conversation to post results in (None = create new)
    pub max_cost_usd: Option<f64>,  // Per-execution budget cap
    pub enabled: bool,
    pub last_run_at: Option<Timestamp>,
    pub next_run_at: Option<Timestamp>,
    pub created_at: Timestamp,
    pub updated_at: Timestamp,
}

#[table(name = scheduled_task_run, public)]
pub struct ScheduledTaskRun {
    #[primary_key]
    pub id: String,
    pub task_id: String,
    pub started_at: Timestamp,
    pub completed_at: Option<Timestamp>,
    pub status: String,             // "running" | "completed" | "failed" | "budget_exceeded"
    pub cost_usd: f64,
    pub conversation_id: String,    // The conversation where results were posted
    pub summary: Option<String>,    // Brief outcome summary
}
```

### 3.2 Reducers

- `create_scheduled_task {id, agentId, title, description, taskType, cronExpression?, runAt?, action, actionConfig, conversationId?, maxCostUsd?, enabled}`
- `update_scheduled_task {id, ...fields}`
- `delete_scheduled_task {id}`
- `pause_scheduled_task {id}` / `resume_scheduled_task {id}`
- `record_task_run {id, taskId, startedAt, completedAt?, status, costUsd, conversationId, summary?}`

---

## 4. Architecture

### 4.1 Scheduler Service

A new lightweight process (or thread within the Gateway) that:

1. Queries `scheduled_task` for tasks where `next_run_at <= now() AND enabled = true`.
2. For each due task, dispatches it to the appropriate agent worker.
3. Updates `last_run_at` and computes `next_run_at` from the cron expression.

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│  Scheduler  │────▶│   Gateway    │────▶│   Worker   │
│  (cron loop)│     │  (dispatch)  │     │  (execute) │
└─────────────┘     └──────────────┘     └────────────┘
       │                                       │
       │         ┌──────────────┐              │
       └────────▶│ SpacetimeDB  │◀─────────────┘
                 │ (task state)  │
                 └──────────────┘
```

### 4.2 Task Execution

When a scheduled task fires, the scheduler creates a synthetic user message in the target conversation (or a new one):

```python
async def execute_scheduled_task(task: ScheduledTask):
    conversation_id = task.conversation_id or create_new_conversation(
        title=f"Scheduled: {task.title}",
        channel="scheduled"
    )
    
    # Construct the prompt based on action type
    if task.action == "run_prompt":
        prompt = json.loads(task.action_config)["prompt"]
    elif task.action == "process_queue":
        prompt = "Review your pending work items and work on the highest priority one."
    elif task.action == "check_url":
        url = json.loads(task.action_config)["url"]
        prompt = f"Check the status of {url} and report any issues."
    
    # Dispatch as a normal agent turn with budget cap
    await dispatch_agent_turn(
        agent_id=task.agent_id,
        conversation_id=conversation_id,
        message=prompt,
        max_cost_usd=task.max_cost_usd or 1.00,
    )
```

### 4.3 Heartbeat Mode

A special task type that runs every N minutes with a minimal prompt:

```
Check your task queue, recent conversations, and any monitored systems.
If there's pending work or something that needs attention, handle it.
If everything is fine, respond with a brief status and stop.
Keep this check under $0.10.
```

The heartbeat is budget-constrained by default to prevent runaway autonomous spending.

---

## 5. Built-in Task Templates

| Template | Cron | Action | Description |
|----------|------|--------|-------------|
| Daily Summary | `0 18 * * *` | `run_prompt` | "Summarize what we accomplished today and what's pending for tomorrow." |
| PR Review | `0 9 * * 1-5` | `run_prompt` | "Check for open PRs in my repos and review any that need attention." |
| CI Monitor | `*/30 * * * *` | `check_url` | Check CI dashboard URL and alert on failures. |
| Work Queue | `0 10 * * 1-5` | `process_queue` | Work through pending work plan items autonomously. |
| Heartbeat | `*/15 * * * *` | `heartbeat` | Lightweight check-in for anything needing attention. |

---

## 6. Safety & Controls

- **Budget integration (081)** — Every scheduled execution is subject to both per-execution and global budget limits. If the monthly budget is exhausted, all scheduled tasks pause automatically.
- **Quiet hours** — Users can define hours when no scheduled tasks run (e.g., 10 PM – 7 AM).
- **Kill switch** — A single toggle to pause ALL scheduled tasks immediately.
- **Execution log** — Every run is recorded with cost, duration, and outcome for auditability.
- **Concurrency limit** — At most N scheduled tasks can run simultaneously (default: 1) to prevent resource contention.

---

## 7. Frontend Integration

### 7.1 Scheduled Tasks View

- **Task list** with name, schedule, last run time, next run time, status, and cost.
- **Create/edit** modal with cron builder (visual, not raw cron syntax).
- **Run history** table with drill-down to the conversation where results were posted.
- **Global pause** toggle in the header.

### 7.2 Conversation Markers

Conversations initiated by scheduled tasks are tagged with a ⏰ icon and the task name, distinguishing them from user-initiated conversations.

---

## 8. Migration Path

1. **Phase 1**: Schema + reducers. Scheduler service with basic cron support. One-shot delayed tasks ("remind me in X").
2. **Phase 2**: Task templates, heartbeat mode, work queue processing.
3. **Phase 3**: Frontend task manager with cron builder and execution history.
4. **Phase 4**: Quiet hours, concurrency controls, advanced scheduling (dependencies between tasks).

---

## 9. Open Questions

- Should the scheduler run inside the Gateway process or as a separate service? Separate is cleaner but adds operational complexity.
- How do we handle timezone? Store cron in UTC and convert for display, or let users specify their timezone?
- What happens if a scheduled task is still running when its next execution is due? Skip? Queue? Run in parallel?
- Should the agent be able to create its own scheduled tasks ("I'll check back on this in an hour") or only the user?
