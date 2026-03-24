# Design Doc 066: Step Complexity Classification for Model Selection

**Status:** Draft  
**Date:** 2026-03-23  
**Depends on:** 025 (RouteLLM Classifier-Based Routing)

---

## 1. Problem

Bond uses a single model per agent — every loop iteration, whether it's a trivial "list files" tool call or a complex multi-step reasoning chain, goes to the same model at the same cost.

Doc 063 (CascadeFlow) tried to solve this with speculative execution: try a cheap model first, validate quality, escalate if needed. That design has a fundamental flaw — **speculative execution of tool calls with side effects**. If the cheap model produces a `file_write` or `shell_exec` that gets executed and then fails quality validation, you can't undo it.

This doc takes a different approach: **classify the next step's complexity *before* execution and route to the appropriate model once**. No speculative execution. No double calls. No validation step. One model selection per step.

## 2. What Bond Gets

1. **40-60% cost reduction** on routine steps (tool result processing, simple file operations) by routing to cheaper models
2. **No quality regression** on complex steps (multi-step planning, debugging, code architecture) by keeping the expensive model
3. **No speculative execution** — each step runs once on the selected model
4. **No external dependencies** — pure Bond implementation using signals already available in `loop_state.py`
5. **Graceful degradation** — if the classifier is wrong, worst case is one suboptimal step, not a failed-then-retried tool call

## 3. Step Complexity Signals

Bond already has these signals available at the start of each loop iteration:

### From `loop_state.py`

| Signal | Indicates | Available |
|--------|-----------|-----------|
| `pending_tool_results` | Agent is processing tool output (usually simple) | ✅ Yes |
| `last_step_failed` | Previous step hit an error (needs stronger reasoning) | ✅ Yes |
| `iteration_count` | How deep into the task (early = planning, late = wrapping up) | ✅ Yes |
| `tools_available` | Number of tools in context (more = harder routing decisions) | ✅ Yes |
| `consecutive_tool_calls` | In a tool-calling streak (mechanical execution) | ✅ Yes |

### From conversation context

| Signal | Indicates | Available |
|--------|-----------|-----------|
| `last_message_role` | Whether we're responding to a tool result or user message | ✅ Yes |
| `user_message_complexity` | Length/structure of the user's request | ✅ Derivable |
| `active_tool_names` | Which tools were just called (some are harder to process) | ✅ Yes |

### From agent configuration

| Signal | Indicates | Available |
|--------|-----------|-----------|
| `agent_type` | Coding agent vs general assistant vs researcher | ✅ Yes |
| `task_domain` | Code, writing, research, etc. | ✅ Partial (from fragment_router) |

## 4. Complexity Tiers

| Tier | Model | When | Examples |
|------|-------|------|----------|
| **ROUTINE** | Cheap (e.g., `llama-3.3-70b`, `gemini-flash`) | Processing tool results, simple tool calls, status checks | "Read this file" → reads file → processes content. Formatting output. Acknowledging user input. |
| **STANDARD** | Mid-tier (e.g., `claude-sonnet`, `gpt-4o`) | Normal task execution, single-step reasoning, code edits | Writing a function. Debugging a specific error. Explaining a concept. |
| **COMPLEX** | Expensive (e.g., `claude-opus`, `o3`) | Multi-step planning, architectural decisions, novel problem-solving | "Redesign this module." First response to a complex user request. Recovery after multiple failures. |

The default (no cascading) maps everything to STANDARD — the agent's configured model. Enabling step classification is opt-in.

## 5. Classifier Design

### v1: Rule-Based (~3 days to implement)

```python
from enum import Enum

class StepComplexity(Enum):
    ROUTINE = "routine"
    STANDARD = "standard"  
    COMPLEX = "complex"


def classify_step(loop_state: LoopState, messages: list[dict]) -> StepComplexity:
    """Classify the next step's complexity based on available signals.
    
    Rules are ordered by priority — first match wins.
    """
    
    # COMPLEX: Recovery from failure needs strong reasoning
    if loop_state.consecutive_failures >= 2:
        return StepComplexity.COMPLEX
    
    # COMPLEX: First response to user (planning phase)
    if loop_state.iteration_count == 0:
        last_user_msg = _get_last_user_message(messages)
        if last_user_msg and len(last_user_msg) > 200:
            return StepComplexity.COMPLEX
    
    # COMPLEX: Agent explicitly requested thinking/planning in its last message
    if _last_response_indicates_planning(messages):
        return StepComplexity.COMPLEX
    
    # ROUTINE: Processing a tool result (most common case)
    if loop_state.pending_tool_results:
        tool_names = [r["tool_name"] for r in loop_state.pending_tool_results]
        
        # Unless the tool result is an error — that might need stronger reasoning
        if any(r.get("is_error") for r in loop_state.pending_tool_results):
            return StepComplexity.STANDARD
        
        # Simple tool results: file_read, shell output, web_fetch
        simple_tools = {"file_read", "web_fetch", "web_search", "shell_grep"}
        if all(t in simple_tools for t in tool_names):
            return StepComplexity.ROUTINE
    
    # ROUTINE: In a consecutive tool-calling streak (mechanical execution)
    if loop_state.consecutive_tool_calls >= 3:
        return StepComplexity.ROUTINE
    
    # STANDARD: Everything else
    return StepComplexity.STANDARD
```

### v2: Learned Classifier (future, connects to doc 025)

Once Bond has enough step-level data (from doc 064's instrumentation and the optimizer's observation pipeline), train a small classifier:

- **Input features:** The signals from section 3, encoded as a fixed-size vector
- **Training signal:** Was the step successful? Did the agent need to retry? Did the user intervene?
- **Model:** Logistic regression or small decision tree — must be <1ms inference

This replaces the rule-based classifier. The rules from v1 become the fallback if the learned classifier isn't available.

## 6. Integration

**Files changed:** `backend/app/agent/llm.py`, `backend/app/agent/loop.py`

### In `loop.py`, before the LLM call:

```python
if agent_config.get("step_classification_enabled"):
    complexity = classify_step(loop_state, messages)
    model = model_for_complexity(complexity, agent_config)
    logger.info("Step %d classified as %s → using %s", 
                loop_state.iteration_count, complexity.value, model)
else:
    model = agent_config["model"]  # existing behavior
```

### Model mapping in agent config:

```python
def model_for_complexity(complexity: StepComplexity, config: dict) -> str:
    """Map complexity tier to model."""
    model_map = config.get("step_models", {})
    return model_map.get(complexity.value, config["model"])
```

### Default model map (configurable per agent):

```yaml
step_models:
  routine: "groq/llama-3.3-70b"
  standard: "anthropic/claude-sonnet-4-20250514"  # agent's configured model
  complex: "anthropic/claude-opus-4-6"
```

## 7. Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `STEP_CLASSIFICATION_ENABLED` | `false` | Master switch (opt-in per agent) |
| `STEP_MODEL_ROUTINE` | (none — uses agent's model) | Model for ROUTINE steps |
| `STEP_MODEL_STANDARD` | (agent's configured model) | Model for STANDARD steps |
| `STEP_MODEL_COMPLEX` | (agent's configured model) | Model for COMPLEX steps |

Per-agent overrides via SpacetimeDB agent config (same as doc 063 proposed, but simpler — just a model map, no cascade config).

## 8. Why This Doesn't Have the Problems of Doc 063

| Doc 063 Problem | This Doc's Approach |
|-----------------|-------------------|
| Speculative execution with side effects | No speculation. One model selected, one execution. |
| Undefined quality validator | No validation needed. Classifier decides up front. |
| Double tool execution risk | Tools execute once. |
| Streaming compatibility | Normal streaming. No buffering-then-replacing. |
| Multi-turn context contamination | Still present (different models in history) but less severe — the cheap model only handles simple steps where style mismatch matters less. |
| External library dependency | Zero. Pure Bond code. |
| Latency from validation step | No validation step. Classifier is <1ms. |

## 9. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Classifier routes a hard step to a cheap model | Medium | The cheap model attempts it; if it fails, the next iteration gets classified as STANDARD or COMPLEX (consecutive_failures signal). Self-correcting within 1-2 turns. |
| Cheap model produces lower-quality tool calls | Medium | Start conservative: only route ROUTINE for tool-result-processing steps where the model is basically formatting/summarizing, not deciding. |
| Mixed-model conversation history | Low | Tag each message with which model produced it (metadata only, not in content). Monitor quality metrics for degradation. |
| Provider rate limits (hitting two providers per session) | Low | ROUTINE model is typically a fast-inference provider (Groq, Together) with generous rate limits. Monitor 429s. |
| User confusion about which model is active | Low | Don't surface model selection to users unless they opt in. It's an optimization detail. |

## 10. Success Metrics

| Metric | How to measure | Target |
|--------|---------------|--------|
| % steps classified ROUTINE | Classifier output distribution | 40-60% |
| Cost per session | Before/after comparison | -30-50% |
| Latency per turn (ROUTINE steps) | p50/p95 latency by tier | -40% (faster models) |
| Quality regression | Eval suite, especially multi-step tasks | ±2% tolerance |
| Self-correction rate | How often ROUTINE misclassification → failure → STANDARD | <15% |

## 11. Relationship to Prior Docs

- **Doc 024 (WilmerAI):** External proxy routing — superseded by this in-process approach.
- **Doc 025 (RouteLLM):** Complementary. RouteLLM's trained classifier is the v2 path for this doc's rule-based v1. The data collection pipeline this doc produces (step complexity labels + outcomes) is exactly what RouteLLM needs for training.
- **Doc 063 (CascadeFlow):** Same goal, different mechanism. This doc avoids speculation entirely. If CascadeFlow matures and solves the side-effect problem, it could replace this — but this works now without external dependencies.
- **Doc 049 (Optimizer):** Step classification outcomes feed into the optimizer. The optimizer can tune classification thresholds over time.
