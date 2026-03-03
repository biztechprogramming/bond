# Design Doc 016: Instructor Integration (Submodule)

## Goal
Integrate the [Instructor](https://github.com/jxnl/instructor) library as a git submodule to provide strictly validated structured outputs for agent tool calling, planning, and entity extraction.

## Context
Bond currently relies on free-form JSON generation from LLMs for tool arguments and planning. This occasionally leads to malformed JSON, missing fields, or "hallucinated" tool arguments, causing redundant agent turns and increased token costs.

By integrating Instructor, we can:
1.  Enforce Pydantic schemas at the LLM generation level.
2.  Enable automatic retries with validation error feedback.
3.  Simplify tool implementation by receiving validated Pydantic objects instead of raw dicts.

## Why a Submodule?
While Instructor is available via `pip`, integrating it as a submodule at `vendor/instructor` allows Bond to:
1.  **Strict Version Pinning:** Ensure the agentic core is locked to a specific commit for stability across environments.
2.  **Custom Extensions:** Modify the Instructor-LiteLLM bridge if needed to support Bond's specific SSE streaming or context distillation requirements.
3.  **Local-First Resilience:** Ensure the core logic is present in the repository without external registry dependencies during build-time for air-gapped or containerized environments.

## Proposed Changes

### 1. Repository Structure
- Add submodule: `git submodule add https://github.com/jxnl/instructor.git vendor/instructor`
- Update `PYTHONPATH` in Docker and dev environments to include `vendor/instructor/python`.

### 2. Agent Loop Integration (`backend/app/agent/loop.py`)
- Wrap the `litellm` client with `instructor.from_litellm()`.
- Refactor the tool-calling loop to use `response_model` for structured actions.

### 3. Tool Definitions (`backend/app/agent/tools/definitions.py`)
- Standardize all tool arguments as Pydantic models.
- Use Instructor's validation features to verify `plan_id` ULIDs and path allowlists before the tool even executes.

### 4. Planning Optimization
- Use Instructor to generate the initial `WorkPlan` object, ensuring every plan has a valid title, agent_id, and at least one item.

## Success Criteria
- [ ] 0% malformed JSON errors in tool-calling logs.
- [ ] Reduced average turns per task by eliminating "fix the JSON" agent steps.
- [ ] Successful validation of existing `pytest` suite with the new patched loop.
- [ ] Submodule correctly initialized and importable in `bond-agent-worker` container.
