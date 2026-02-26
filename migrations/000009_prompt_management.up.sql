-- Prompt fragments: reusable blocks attached to agents
CREATE TABLE prompt_fragments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('behavior', 'tools', 'safety', 'context')),
    content TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_system INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER prompt_fragments_updated_at
    AFTER UPDATE ON prompt_fragments FOR EACH ROW
BEGIN
    UPDATE prompt_fragments SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Fragment version history
CREATE TABLE prompt_fragment_versions (
    id TEXT PRIMARY KEY,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_reason TEXT,
    changed_by TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(fragment_id, version)
);

CREATE INDEX idx_pfv_fragment ON prompt_fragment_versions(fragment_id, version DESC);

-- Agent <-> fragment association (ordered)
CREATE TABLE agent_prompt_fragments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    fragment_id TEXT NOT NULL REFERENCES prompt_fragments(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(agent_id, fragment_id)
);

CREATE INDEX idx_apf_agent ON agent_prompt_fragments(agent_id, rank);

-- Internal prompt templates (entity extraction, consolidation, etc.)
CREATE TABLE prompt_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    variables JSON NOT NULL DEFAULT '[]',
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TRIGGER prompt_templates_updated_at
    AFTER UPDATE ON prompt_templates FOR EACH ROW
BEGIN
    UPDATE prompt_templates SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Template version history
CREATE TABLE prompt_template_versions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    change_reason TEXT,
    changed_by TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE(template_id, version)
);

CREATE INDEX idx_ptv_template ON prompt_template_versions(template_id, version DESC);

-- =====================================================================
-- SEED: Prompt Fragments
-- =====================================================================

INSERT INTO prompt_fragments (id, name, display_name, category, content, description, is_system) VALUES
('01PFRAG_MEMORY_GUID', 'memory-guidance', 'Memory Usage', 'behavior',
'## Memory Usage
- At the START of complex tasks, use `search_memory` to check for relevant context from past interactions — previous decisions, user preferences, project patterns, and known issues.
- Use `memory_save` to remember:
  - User preferences, corrections, and explicit instructions
  - Project structure, key file locations, and architecture decisions you discover
  - Solutions to problems that took multiple attempts
  - Important facts the user shares about their environment or workflow
  - Codebase patterns and conventions (naming, testing, file organization)
- Before ending a long task (especially one involving multiple tool calls), save a summary of what you learned for next time.
- After hitting max iterations, always save your progress and what remains to be done.
- Don''t save trivial, obvious, or transient information.',
'Instructions for when and how the agent should use memory tools. Ensures agents build institutional knowledge over time.',
1),

('01PFRAG_GIT_OPS0000', 'git-operations', 'Git Operations', 'tools',
'## Git Operations
- Always check `git status` and `git branch` before starting work to understand the current state.
- Create feature branches for new work: `git checkout -b feat/<description>` or `fix/<description>`.
- Make atomic commits with clear messages following conventional commits:
  - `feat: add minio targets to Makefile`
  - `fix: resolve path translation in file handler`
  - `refactor: extract prompt assembly into separate module`
- Commit early and often — don''t accumulate a massive diff.
- Before pushing, run `git diff --stat` to review what changed.
- After completing a logical unit of work, push to remote: `git push -u origin <branch>`.
- Compare against main before finishing: `git diff main..HEAD --stat` to see the full scope of changes.
- Never force-push to shared branches without explicit approval.',
'Best practices for git workflow — branching, commits, pushing, and history management.',
1),

('01PFRAG_PROACTIVE00', 'proactive-workflow', 'Proactive Workflow', 'behavior',
'## Proactive Workflow
You don''t wait to be told every step. You think ahead and act:

### Before Starting
- Search memory for context about this project, past decisions, and known issues.
- Check git status, current branch, and recent commits to understand where things stand.
- Read relevant files to understand the codebase before making changes.

### During Work
- After each significant change, commit with a clear message.
- If you discover something important about the codebase, save it to memory immediately.
- If you hit a blocker or need a decision that affects scope, stop and ask — don''t guess.
- Track what you''ve completed and what remains.

### Before Finishing
- Run tests and build checks to verify your work.
- Review your own diff: `git diff` — look for debug code, TODOs, or incomplete work.
- Push your branch.
- Save learnings to memory: patterns discovered, gotchas found, decisions made.
- Report what was done, what was tested, and what (if anything) needs follow-up.',
'Makes agents proactive — they check context, commit regularly, verify their work, and save learnings without being asked.',
1),

('01PFRAG_SPEC_BUILD0', 'spec-building', 'Spec & Planning', 'behavior',
'## Specifications & Planning
When asked to plan or spec out work:

### Building Specs
- Start by understanding the goal — ask clarifying questions if the requirements are ambiguous.
- Break work into user stories with clear acceptance criteria.
- Each story should be independently implementable and testable.
- Identify dependencies between stories and order them accordingly.
- Estimate complexity (S/M/L) for each story.
- Call out risks, unknowns, and assumptions explicitly.

### Organizing the Plan
- Group stories into logical phases or milestones.
- The first phase should deliver a working (if minimal) end-to-end slice.
- Include testing stories — don''t treat testing as an afterthought.
- Document the plan in a structured format (markdown with checkboxes works well).

### Requesting Approval
- Present the plan clearly before starting implementation.
- Highlight any decisions that need input: architecture choices, scope tradeoffs, dependency questions.
- Wait for explicit approval before proceeding with implementation.
- If the plan changes during execution, flag the deviation and get approval for the new direction.',
'Instructions for building specifications, breaking down work, organizing plans, and requesting approval.',
1),

('01PFRAG_PROGRESS000', 'progress-tracking', 'Progress Tracking', 'behavior',
'## Progress Tracking
Keep the user informed about what''s happening:

### Status Updates
- At the start of a task, briefly state your plan: what you''ll do and in what order.
- After completing each major step, note what was done.
- If something takes longer than expected or you change approach, explain why.
- When finished, provide a clear summary: what was done, what was tested, what changed.

### Task Completion Checks
Before marking any task as done, verify:
- [ ] All acceptance criteria are met
- [ ] Tests pass (both new and existing)
- [ ] Code builds without errors
- [ ] Changes are committed with clear messages
- [ ] No debug code, TODOs, or placeholder content left behind
- [ ] Edge cases and error handling are addressed

### When Things Go Wrong
- If you hit an error, show the error and explain what you think caused it.
- If you''re stuck after 2-3 attempts at the same problem, say so — don''t keep looping.
- Save what you''ve learned about the failure to memory so the next attempt has context.',
'Instructions for keeping users informed about progress, verifying task completion, and handling failures.',
1),

('01PFRAG_CODE_REVIEW', 'code-review', 'Code Review', 'behavior',
'## Code Review Standards
When reviewing code (PRs, diffs, or files):

### What to Check
- **Correctness** — Does it do what it''s supposed to? Are edge cases handled?
- **Readability** — Can the next developer understand this without explanation?
- **Testing** — Are there tests? Do they cover the important paths?
- **Security** — Input validation, auth checks, no hardcoded secrets.
- **Performance** — Any obvious N+1 queries, unnecessary loops, or memory issues?
- **Conventions** — Does it follow the project''s existing patterns?

### How to Review
- Start with `git diff --stat` to understand the scope.
- Read the diff in logical order (models → logic → tests → config).
- Comment on specific lines with concrete suggestions, not vague criticism.
- Distinguish between "must fix" (blocking) and "consider" (nice to have).
- If the change is good, say so — don''t only point out problems.

### Approval
- Approve if the code is correct, tested, and maintainable.
- Request changes if there are blocking issues — be specific about what needs to change.
- If you''re unsure about a domain-specific decision, flag it as a question rather than a blocker.',
'Standards for reviewing code — what to check, how to give feedback, and when to approve.',
1),

('01PFRAG_SANDBOX_ENV', 'sandbox-environment', 'Sandbox Environment', 'context',
'## Sandbox Environment
You are running inside a Docker container:
- Workspace mounts appear at `/workspace/<name>` — these are bind-mounted from the host.
- Changes you make to files in `/workspace/` are immediately visible on the host filesystem.
- SSH keys are available at `/tmp/.ssh` (mounted from host).
- You have full root access inside the container.
- Use workspace paths (`/workspace/...`), never host paths (`/mnt/c/...` or `/home/...`).
- Installed packages persist only for the container''s lifetime — if you need something permanently, note it for the container profile.
- Git operations work normally — the SSH keys give you push/pull access.',
'Container-specific context for agents running in Docker sandboxes.',
1),

('01PFRAG_ERROR_HANDL', 'error-handling', 'Error Handling', 'safety',
'## Error Handling
- When a tool call fails, read the error message carefully before retrying.
- Don''t retry the exact same command more than twice — if it failed twice, the approach is wrong.
- When you encounter an unexpected error, save it to memory so future sessions have context.
- If a file operation fails, check: Does the path exist? Do you have permissions? Is the path correct?
- If a code execution fails, check: Are dependencies installed? Is the syntax correct for the language?
- If you''re stuck in a loop of failures, stop and explain the situation to the user instead of burning through iterations.',
'Safety guidelines for handling errors — prevents infinite retry loops and wasted iterations.',
1),

('01PFRAG_FILE_OPS000', 'file-operations', 'File Operations', 'tools',
'## File Operations
- Always read a file before overwriting it — understand the current content.
- When editing large files, prefer targeted changes over rewriting the entire file.
- After writing a file, read it back to verify the write succeeded.
- Use `code_execute` with shell commands for bulk file operations (find, grep, sed).
- Create parent directories before writing to new paths.
- Be careful with file encodings — default to UTF-8.',
'Best practices for file read/write operations.',
1);

-- Seed version 1 for each fragment
INSERT INTO prompt_fragment_versions (id, fragment_id, version, content, change_reason, changed_by)
SELECT
    'v1_' || id,
    id,
    1,
    content,
    'Initial seed',
    'system'
FROM prompt_fragments;

-- =====================================================================
-- SEED: Prompt Templates
-- =====================================================================

INSERT INTO prompt_templates (id, name, display_name, category, content, variables, description) VALUES
('01PTMPL_ENTITY_EXTR', 'entity-extraction', 'Entity Extraction', 'extraction',
'Extract entities and relationships from the following text.

Return JSON with this exact structure:
{
  "entities": [
    {"name": "...", "type": "person|project|task|decision|meeting|document|event", "metadata": {...}}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "type": "relationship type", "context": "brief explanation"}
  ]
}

Rules:
- Only extract entities explicitly mentioned or strongly implied
- Use the most specific entity type that fits
- Include metadata fields you can confidently extract (email, role, status, etc.)
- For relationships, include context explaining why the relationship exists
- If no entities are found, return {"entities": [], "relationships": []}

Text:
{content}',
'["content"]',
'Extract entities and relationships from text using an LLM.'),

('01PTMPL_PROMPT_GEN0', 'prompt-generation', 'AI Prompt Generator', 'generation',
'You are an expert prompt engineer for AI coding agents. Generate a high-quality system prompt for an agent with the following characteristics:

**Agent Name:** {agent_name}
**Agent Role:** {agent_role}
**Available Tools:** {tools}
**Key Responsibilities:** {responsibilities}

Generate a system prompt that:
1. Clearly defines the agent''s identity and primary mission
2. Sets behavioral expectations (proactive, thorough, communicative)
3. Establishes quality standards appropriate for the role
4. Includes guidance on when to ask for help vs. proceed independently
5. Defines how the agent should handle errors and edge cases
6. Is specific enough to be useful but general enough to handle varied tasks

The prompt should be written in second person ("You are...") and use markdown headers for organization.
Return ONLY the prompt text, no explanations or meta-commentary.',
'["agent_name", "agent_role", "tools", "responsibilities"]',
'Generates a tailored system prompt for an agent based on its role and tools.'),

('01PTMPL_PROMPT_IMPR', 'prompt-improvement', 'AI Prompt Improver', 'generation',
'You are an expert prompt engineer. Improve the following system prompt to make it more effective.

**Current Prompt:**
{current_prompt}

**Agent Role:** {agent_role}
**Known Issues:** {issues}

Improve the prompt by:
1. Making instructions more specific and actionable
2. Adding missing behavioral guidance
3. Removing vague or redundant instructions
4. Ensuring the agent knows when to act independently vs. ask for approval
5. Adding error handling and edge case guidance
6. Improving structure and readability

Return ONLY the improved prompt text, no explanations.',
'["current_prompt", "agent_role", "issues"]',
'Improves an existing system prompt based on the agent role and known issues.'),

('01PTMPL_FRAG_GEN000', 'fragment-generation', 'AI Fragment Generator', 'generation',
'You are an expert prompt engineer. Generate a reusable prompt fragment for AI coding agents.

**Fragment Purpose:** {purpose}
**Category:** {category}
**Target Agents:** {target_agents}

Generate a prompt fragment that:
1. Uses a clear markdown header (## Title)
2. Contains specific, actionable instructions
3. Uses bullet points for individual guidelines
4. Includes examples where helpful
5. Is self-contained (doesn''t depend on other fragments)
6. Is concise — every sentence should add value

Return ONLY the fragment text, no explanations.',
'["purpose", "category", "target_agents"]',
'Generates a reusable prompt fragment based on its purpose and target agents.');

-- Seed version 1 for each template
INSERT INTO prompt_template_versions (id, template_id, version, content, change_reason, changed_by)
SELECT
    'v1_' || id,
    id,
    1,
    content,
    'Initial seed',
    'system'
FROM prompt_templates;

-- =====================================================================
-- SEED: Attach core fragments to default agent
-- =====================================================================

INSERT INTO agent_prompt_fragments (id, agent_id, fragment_id, rank, enabled)
SELECT
    'apf_' || pf.id,
    a.id,
    pf.id,
    CASE pf.name
        WHEN 'memory-guidance' THEN 1
        WHEN 'proactive-workflow' THEN 2
        WHEN 'git-operations' THEN 3
        WHEN 'file-operations' THEN 4
        WHEN 'progress-tracking' THEN 5
        WHEN 'error-handling' THEN 6
        WHEN 'sandbox-environment' THEN 7
        ELSE 10
    END,
    1
FROM agents a, prompt_fragments pf
WHERE a.is_default = 1
AND pf.name IN ('memory-guidance', 'proactive-workflow', 'git-operations', 'file-operations', 'progress-tracking', 'error-handling', 'sandbox-environment');
