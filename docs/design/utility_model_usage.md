# Design Doc: Enabling Utility Model Usage

## Problem Statement
The current implementation of the Bond agent primarily uses the `primary_model` for all LLM interactions, including those that are better suited for a smaller, faster, and cheaper `utility_model`. Although the `utility_model` is defined and passed into the worker context, it is rarely utilized.

## Proposed Changes

### 1. `bond/backend/app/worker.py`
- Modify the main loop to distinguish between tasks that require the reasoning capabilities of the `primary_model` and those that can be handled by the `utility_model`.
- Currently, the worker seems to default to `model = config["model"]`. We should introduce logic to switch to `utility_model` for:
    - Tool call result filtering (already partially implemented in `tool_result_filter.py`).
    - Simple state updates or summarization tasks.
    - Preliminary analysis of long contexts.

### 2. LLM Request Logic
- Update the `llm.generate` calls (or equivalent) to accept an optional `model` override, or implement a heuristic within `worker.py` to decide which model to use for the next turn.

### 3. Agent Configuration
- Ensure all agent configurations explicitly define a `utility_model`.

## Implementation Plan
1.  Identify specific LLM calls in `worker.py` and associated modules that can be offloaded to the utility model.
2.  Update the `Worker` class to maintain a reference to both models and toggle based on the current task type.
3.  Add logging to track which model is being used for each request to verify the change.
