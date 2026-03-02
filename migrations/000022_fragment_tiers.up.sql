-- Add tiered fragment selection columns to prompt_fragments
ALTER TABLE prompt_fragments ADD COLUMN summary TEXT NOT NULL DEFAULT '';
ALTER TABLE prompt_fragments ADD COLUMN tier TEXT NOT NULL DEFAULT 'standard'
    CHECK(tier IN ('core', 'standard', 'specialized'));
ALTER TABLE prompt_fragments ADD COLUMN task_triggers TEXT NOT NULL DEFAULT '[]';
ALTER TABLE prompt_fragments ADD COLUMN token_estimate INTEGER NOT NULL DEFAULT 0;

-- Populate token_estimate from content length
UPDATE prompt_fragments SET token_estimate = CAST(LENGTH(content) / 4 AS INTEGER);

-- Tier assignments
UPDATE prompt_fragments SET tier = 'core' WHERE name IN ('tool-efficiency', 'error-handling');
UPDATE prompt_fragments SET tier = 'specialized' WHERE name IN ('spec-building', 'work-planning', 'mediatr-pipeline');
-- All others remain 'standard' (default)

-- Summaries (~50-100 chars each)
UPDATE prompt_fragments SET summary = 'Batch tool calls, outline-first reads, avoid re-reads'
    WHERE name = 'tool-efficiency';
UPDATE prompt_fragments SET summary = 'Error recovery: no retry loops, read-only FS workaround, install missing deps'
    WHERE name = 'error-handling';
UPDATE prompt_fragments SET summary = 'file_read outline mode, large chunks, file_edit for surgical edits'
    WHERE name = 'file-operations';
UPDATE prompt_fragments SET summary = 'Git branching, atomic commits, conventional commit messages'
    WHERE name = 'git-operations';
UPDATE prompt_fragments SET summary = 'When/how to use search_memory and memory_save tools'
    WHERE name = 'memory-guidance';
UPDATE prompt_fragments SET summary = 'Check context first, commit often, verify work, save learnings'
    WHERE name = 'proactive-workflow';
UPDATE prompt_fragments SET summary = 'Scope control, match effort to task, status updates'
    WHERE name = 'progress-tracking';
UPDATE prompt_fragments SET summary = 'Docker container context, workspace mounts, writable paths'
    WHERE name = 'sandbox-environment';
UPDATE prompt_fragments SET summary = 'Zero warnings/errors enforcement, build + lint verification'
    WHERE name = 'must-compile';
UPDATE prompt_fragments SET summary = 'Break work into stories, acceptance criteria, phased plan'
    WHERE name = 'spec-building';
UPDATE prompt_fragments SET summary = 'Multi-step planning, create plan immediately, checkpoint strategy'
    WHERE name = 'work-planning';
UPDATE prompt_fragments SET summary = 'MediatR pipeline pattern for .NET request/response handling'
    WHERE name = 'mediatr-pipeline';
UPDATE prompt_fragments SET summary = 'Code correctness, readability, testing, security, conventions'
    WHERE name = 'code-review';
UPDATE prompt_fragments SET summary = 'Reproduce first, isolate root cause, test the fix, no new bugs'
    WHERE name = 'bugfix-discipline';

-- Task triggers (JSON arrays of keyword patterns for auto-include)
-- IMPORTANT: Triggers must be specific phrases, not common single words like "plan", "fix", "build"
UPDATE prompt_fragments SET task_triggers = '["git ", "commit message", "git branch", "git push", "merge branch", "rebase", "pull request"]'
    WHERE name = 'git-operations';
UPDATE prompt_fragments SET task_triggers = '["create file", "write file", "edit file", "read file", "modify file", "new file"]'
    WHERE name = 'file-operations';
UPDATE prompt_fragments SET task_triggers = '["must compile", "zero warnings", "build and lint", "type check", "typecheck", "ruff check", "mypy", "tsc"]'
    WHERE name = 'must-compile';
UPDATE prompt_fragments SET task_triggers = '["write a spec", "build a spec", "specification", "design doc", "architecture doc", "write an rfc"]'
    WHERE name = 'spec-building';
UPDATE prompt_fragments SET task_triggers = '["create a plan", "make a plan", "break down into", "roadmap", "multi-step plan", "phased approach"]'
    WHERE name = 'work-planning';
UPDATE prompt_fragments SET task_triggers = '["search_memory", "memory_save", "remember from", "recall from", "past conversation"]'
    WHERE name = 'memory-guidance';
UPDATE prompt_fragments SET task_triggers = '["mediatr", "pipeline behavior", "dotnet", "csharp", "c# "]'
    WHERE name = 'mediatr-pipeline';
UPDATE prompt_fragments SET task_triggers = '["code review", "review this pr", "review the code", "pull request review"]'
    WHERE name = 'code-review';
UPDATE prompt_fragments SET task_triggers = '["fix this bug", "bugfix", "debug this", "broken", "regression", "failing test"]'
    WHERE name = 'bugfix-discipline';
