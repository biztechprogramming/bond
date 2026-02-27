-- Work Plans: structured task tracking for agents
CREATE TABLE work_plans (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    conversation_id TEXT,
    parent_plan_id TEXT REFERENCES work_plans(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'paused', 'completed', 'failed', 'cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at TIMESTAMP
);

CREATE INDEX idx_wp_agent_status ON work_plans(agent_id, status);
CREATE INDEX idx_wp_conversation ON work_plans(conversation_id);
CREATE INDEX idx_wp_parent ON work_plans(parent_plan_id);

CREATE TRIGGER work_plans_updated_at
    AFTER UPDATE ON work_plans FOR EACH ROW
BEGIN
    UPDATE work_plans SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Work Items: individual steps within a plan
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

-- Auto-planning prompt fragment
INSERT OR IGNORE INTO prompt_fragments (id, name, display_name, category, content, description, is_system)
VALUES (
'01PFRAG_WORK_PLAN0', 'work-planning', 'Work Planning', 'behavior',
'## Work Planning
When given a task with multiple steps (3+):
1. Call `work_plan(action="create_plan", title="...")` to create a plan
2. Call `work_plan(action="add_item", ...)` for each step — keep titles short and clear
3. Before starting each step, update it to `in_progress` with a brief context note
4. As you work, append notes with findings, decisions, and important context
5. After completing each step, update to `done` with final context_snapshot including:
   - Files you read and key findings
   - Decisions you made and why
   - Edits you applied
   - What remains to be done
6. If you hit max iterations or an error, save your current context before stopping
7. Complete the plan when all items are done

For simple tasks (1-2 steps), skip the plan — just do the work.',
'Instructions for creating and maintaining work plans during multi-step tasks.',
1);

-- Attach to default agent
INSERT OR IGNORE INTO agent_prompt_fragments (id, agent_id, fragment_id, rank, enabled)
SELECT 'apf_01PFRAG_WORK_PLAN0', a.id, '01PFRAG_WORK_PLAN0', 9, 1
FROM agents a WHERE a.is_default = 1;

-- Version entry
INSERT INTO prompt_fragment_versions (id, fragment_id, version, content, change_reason, changed_by)
VALUES ('v1_01PFRAG_WORK_PLAN0', '01PFRAG_WORK_PLAN0', 1,
(SELECT content FROM prompt_fragments WHERE id = '01PFRAG_WORK_PLAN0'),
'Initial version', 'system');
