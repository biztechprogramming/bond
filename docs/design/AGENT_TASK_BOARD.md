# Agent Task Board — Design Document

## Problem Statement

When a Bond agent works on a multi-step task (e.g., "implement these 3 changes to the context pipeline"), it operates as an opaque stream of tool calls. The user has no structured visibility into:

1. **What the agent plans to do** — The plan exists only in the LLM's reasoning
2. **What's in progress** — No way to see which step the agent is currently on
3. **What's done vs. remaining** — Tool call logs are noise, not signal
4. **What happened if it crashes** — On restart, the agent re-reads everything from scratch because there's no checkpoint. All prior work (reads, analysis, decisions) is lost.

This design introduces a **Task Board** — a lightweight, structured task tracking system that agents maintain as they work, users observe via a Kanban UI, and agents restore from on crash recovery.

---

## Core Concepts

### Work Plan
A **Work Plan** is created when an agent begins a non-trivial task. It represents the overall goal and contains ordered **Work Items**.

| Field | Type | Description |
|-------|------|-------------|
| id | ULID | Primary key |
| agent_id | TEXT | Agent that owns this plan |
| conversation_id | TEXT | Conversation that spawned this plan |
| title | TEXT | Short description of the overall goal |
| status | ENUM | `active`, `paused`, `completed`, `failed`, `cancelled` |
| created_at | TIMESTAMP | When the plan was created |
| updated_at | TIMESTAMP | Last status change |
| completed_at | TIMESTAMP | When the plan finished (any terminal status) |

### Work Item
A **Work Item** is a single discrete unit of work within a plan. Items are created lightweight (just a title) and enriched as the agent works on them.

| Field | Type | Description |
|-------|------|-------------|
| id | ULID | Primary key |
| plan_id | TEXT | FK to work_plans |
| title | TEXT | Short description ("Add line_start/line_end to file_read") |
| status | ENUM | See status lifecycle below |
| ordinal | INTEGER | Sort order within the plan |
| context_snapshot | TEXT | JSON — agent's working context at time of last update (see Context Checkpointing) |
| notes | TEXT | Agent's running notes — findings, decisions, blockers |
| files_changed | JSON | Array of file paths modified during this item |
| started_at | TIMESTAMP | When status moved to `in_progress` |
| completed_at | TIMESTAMP | When status moved to a terminal state |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

### Work Item Status Lifecycle

```
new → in_progress → done → in_review → approved → in_test → tested → complete
                  ↘ blocked
                  ↘ failed
```

| Status | Meaning |
|--------|---------|
| `new` | Created but not started |
| `in_progress` | Agent is actively working on this |
| `done` | Agent finished implementation |
| `in_review` | Awaiting human or agent review |
| `approved` | Review passed |
| `in_test` | Tests are being run |
| `tested` | Tests passed |
| `complete` | Fully done — implementation, review, and test |
| `blocked` | Cannot proceed — dependency or user input needed |
| `failed` | Attempted and failed |

**Minimum viable flow:** `new → in_progress → done → complete`
**Full flow:** `new → in_progress → done → in_review → approved → in_test → tested → complete`

Agents use the minimum flow by default. The full flow is available for workflows that require explicit review/test gates (e.g., PR-based development).

### Work Item Notes
Notes are appended (never overwritten) as the agent works. Each note entry is timestamped:

```json
[
  {"at": "2026-02-27T14:30:00Z", "text": "Read worker.py outline — _select_relevant_fragments at line 290"},
  {"at": "2026-02-27T14:30:15Z", "text": "Function takes fragments list + user_message + history. Need to add cache_key param."},
  {"at": "2026-02-27T14:31:02Z", "text": "Edit applied. Added _fragment_cache_key and _fragment_cache_result to WorkerState."},
  {"at": "2026-02-27T14:31:30Z", "text": "Tests passing. Moving to done."}
]
```

---

## Context Checkpointing

This is the key innovation. When an agent updates a work item, it snapshots its working context into `context_snapshot`. This is NOT the full conversation history — it's a structured summary of what the agent knows and where it is.

### Context Snapshot Schema

```json
{
  "version": 1,
  "plan_summary": "Implementing 3 context efficiency changes to worker.py",
  "current_item_id": "01ABC...",
  "files_read": {
    "/bond/backend/app/worker.py": {
      "total_lines": 1594,
      "sections_read": [[1, 30], [187, 220], [510, 570], [1070, 1100]],
      "key_findings": [
        "WorkerState class at line 187 — needs _fragment_cache_key field",
        "_select_relevant_fragments at line 290 — utility model call every turn",
        "_compress_history at line 533 — may re-summarize sliding window output"
      ]
    },
    "/bond/backend/app/agent/context_decay.py": {
      "total_lines": 299,
      "outline": "apply_progressive_decay (line 15), _decay_tier (line 80), ..."
    }
  },
  "decisions_made": [
    "Cache key = hash of user_message + last 2 history messages",
    "Store cache on _state object, not as module global"
  ],
  "edits_applied": [
    {"file": "/bond/backend/app/worker.py", "description": "Added _fragment_cache_key to WorkerState.__init__"},
    {"file": "/bond/backend/app/worker.py", "description": "Added cache check at top of _select_relevant_fragments"}
  ],
  "remaining_work": [
    "Change 2: Dedup sliding window + compression summarization",
    "Change 3: Skip progressive decay on to-be-summarized messages"
  ],
  "blocked_on": null,
  "environment": {
    "branch": "feature/sprint-1-skeleton",
    "last_commit": "abc123",
    "python_version": "3.14",
    "test_command": "cd /bond && pip install pytest && python -m pytest backend/tests/ -v"
  }
}
```

### When Context is Saved

The agent updates `context_snapshot` on the current work item:
- When moving an item to `in_progress` (initial context)
- After each significant finding or edit (incremental update)
- When moving to `done` (final state)
- On **max iterations hit** — the agent MUST save context before stopping
- On **error/crash recovery** — the worker catches exceptions and saves what it has

### Crash Recovery Flow

1. Agent starts a new turn (user sends a message or agent restarts)
2. Worker checks for `active` work plans for this agent
3. If found, loads the plan + items + latest `context_snapshot`
4. Injects the snapshot into the system prompt as structured context:
   ```
   [Resuming work plan: "Implement 3 context efficiency changes"]
   
   Completed:
   - ✅ Change 1: Cache fragment selection (done, tested)
   
   In Progress:
   - 🔄 Change 2: Dedup sliding window + compression
     Context: Read _compress_history at lines 533-570. The function checks for
     cached_summary in context_summaries table. Need to detect the sliding window
     summary message and pass it through instead of re-summarizing.
     Files read: worker.py (lines 510-570), already have the relevant code.
     Decision: Check if compressible[0] starts with "[Previous conversation summary]"
   
   Remaining:
   - ⬜ Change 3: Skip progressive decay on to-be-summarized msgs
   ```
5. Agent continues from where it left off — no re-reading, no re-analyzing

---

## Database Schema

### Migration: `000021_work_plans.up.sql`

```sql
CREATE TABLE work_plans (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    conversation_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'completed', 'failed', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE INDEX idx_wp_agent_status ON work_plans(agent_id, status);
CREATE INDEX idx_wp_conversation ON work_plans(conversation_id);

CREATE TRIGGER work_plans_updated_at
    AFTER UPDATE ON work_plans FOR EACH ROW
BEGIN
    UPDATE work_plans SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE work_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES work_plans(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'in_progress', 'done', 'in_review', 'approved',
                         'in_test', 'tested', 'complete', 'blocked', 'failed')),
    ordinal INTEGER NOT NULL DEFAULT 0,
    context_snapshot JSON,
    notes JSON DEFAULT '[]',
    files_changed JSON DEFAULT '[]',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE INDEX idx_wi_plan_status ON work_items(plan_id, status);
CREATE INDEX idx_wi_plan_ordinal ON work_items(plan_id, ordinal);

CREATE TRIGGER work_items_updated_at
    AFTER UPDATE ON work_items FOR EACH ROW
BEGIN
    UPDATE work_items SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

---

## Agent Tools

Two new tools, registered in the native tool registry:

### `work_plan` — Manage work plans and items

```json
{
  "name": "work_plan",
  "description": "Create and manage work plans with trackable items. Use at the start of multi-step tasks.",
  "parameters": {
    "action": "create_plan | add_item | update_item | complete_plan | get_plan",
    "plan_id": "ID of existing plan (for add_item, update_item, complete_plan, get_plan)",
    "title": "Title for plan or item (for create_plan, add_item)",
    "item_id": "ID of item to update (for update_item)",
    "status": "New status (for update_item, complete_plan)",
    "notes": "Note to append (for update_item)",
    "context_snapshot": "JSON context to save (for update_item)",
    "files_changed": "Array of file paths (for update_item)"
  }
}
```

### Tool Selection Integration

Add `work_plan` to `tool_selection.py` keywords:
- Always include when the agent has an active work plan
- Include on keywords: "implement", "build", "create", "fix", "refactor", "change", "update", "migrate"

---

## API Endpoints

### `GET /api/v1/plans`
List work plans. Query params: `agent_id`, `status`, `conversation_id`, `limit`.

### `GET /api/v1/plans/{plan_id}`
Get a plan with all its items.

### `PATCH /api/v1/plans/{plan_id}/items/{item_id}`
Update an item's status (for user-driven status changes from the UI, e.g., moving from `done` to `approved`).

### `DELETE /api/v1/plans/{plan_id}`
Cancel a plan.

### SSE Events
The existing SSE stream for agent turns emits new event types:
- `plan_created` — `{plan_id, title}`
- `item_created` — `{plan_id, item_id, title, ordinal}`
- `item_updated` — `{plan_id, item_id, status, notes}`
- `plan_completed` — `{plan_id, status}`

These drive real-time updates to the Kanban UI.

---

## Frontend: Kanban Board

### Location
New page: `/plans` (or `/board`)
Also accessible as a panel within the chat view (split pane).

### Columns
Configurable per view, default:

| New | In Progress | Done | In Review | Complete |
|-----|-------------|------|-----------|----------|

Each card shows:
- Item title
- Agent name (avatar)
- Time in current status
- Note count indicator
- Files changed count

### Card Detail (click to expand)
- Full notes timeline
- Files changed list (clickable → opens diff view if available)
- Context snapshot (collapsible, for debugging)
- Status transition buttons (user can manually advance: done → approved, etc.)

### Real-time Updates
WebSocket subscription to plan SSE events. Cards animate between columns on status change.

---

## Worker Integration

### Plan Creation (in `_run_agent_loop`)

When the agent's first response includes a structured plan (e.g., "Here's my plan: 1. ... 2. ... 3. ..."), or when the agent calls `work_plan(action="create_plan")`:

1. Create the `work_plans` row
2. Create `work_items` rows for each step
3. Emit SSE events
4. Store `plan_id` on the loop state so subsequent tool calls can reference it

### Auto-Plan Prompt Fragment

New fragment `auto-planning`:
```
## Work Planning
When given a task with multiple steps:
1. Call `work_plan(action="create_plan", title="...")` to create a plan
2. Call `work_plan(action="add_item", ...)` for each step
3. Before starting each step, update it to `in_progress` with initial context
4. After completing each step, update it to `done` with final context and notes
5. Save context_snapshot with your findings, decisions, and remaining work
6. If you hit max iterations or an error, save your current context before stopping
```

### Crash Recovery (in `_run_agent_loop` startup)

Before the main loop:
```python
# Check for active work plans
active_plan = await _load_active_plan(agent_id)
if active_plan:
    # Find the in_progress item
    current_item = next((i for i in active_plan.items if i.status == 'in_progress'), None)
    if current_item and current_item.context_snapshot:
        # Inject recovery context into the system prompt
        recovery_context = _format_recovery_context(active_plan, current_item)
        # Prepend to messages as a system-level context block
```

### Max Iterations Safety Net

In the existing max-iterations handler (end of `_run_agent_loop`):
```python
# Before saving the max-iterations memory, also checkpoint the work plan
if _active_plan_id:
    await _checkpoint_work_plan(_active_plan_id, messages, tool_calls_made)
```

---

## Implementation Phases

### Phase 1: Schema + Tools + Basic Recovery (MVP)
- Migration 000021: `work_plans` and `work_items` tables
- `work_plan` tool implementation in `native.py`
- Register in tool registry and selection
- Auto-planning prompt fragment
- Crash recovery: load active plan → inject context
- API: `GET /plans`, `GET /plans/{id}`
- **No UI** — plans visible via API only

### Phase 2: SSE + Kanban UI
- SSE events for plan/item changes
- Frontend Kanban board page
- Real-time WebSocket updates
- Card detail view with notes timeline

### Phase 3: User Interaction + Advanced Flows
- User can change item status from UI (approve, reject, reorder)
- Full status lifecycle (review → approved → test → tested → complete)
- Plan templates (predefined item sets for common workflows)
- Cross-agent plans (one plan, multiple agents working items)

### Phase 4: Analytics + History
- Plan completion metrics (time per item, total duration, crash recovery count)
- Historical plan browser
- Plan diff view (what changed between agent sessions)

---

## Token Impact

### Cost
- `work_plan` tool definition: ~200 tokens (included only when relevant via tool selection)
- Recovery context injection: ~300-800 tokens depending on plan size
- `context_snapshot` saves: ~500 tokens per save (via tool call output)

### Savings
- Crash recovery avoids re-reading files: **5,000-20,000 tokens saved per recovery**
- Agent doesn't re-explore what it already found: **3,000-10,000 tokens saved**
- Structured plan reduces scope drift: **prevents 10-30 unnecessary tool calls**
- Net: **significant token reduction on any multi-step task that would otherwise fail or restart**

---

## Open Questions

1. **Should plans persist across conversations?** Current design ties plans to a conversation. Cross-conversation plans would require a different lookup mechanism.
2. **Plan granularity threshold** — Should the agent create a plan for every task, or only when it estimates > N steps? Recommend: create for anything estimated > 3 steps.
3. **Context snapshot size limit** — How large can `context_snapshot` get before it hurts more than it helps? Recommend: cap at 4000 tokens, summarize if larger.
4. **Multi-agent plans** — Phase 3 scope. Needs a claim/lock mechanism so two agents don't work the same item.
