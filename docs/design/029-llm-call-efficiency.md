# Design Doc 029: LLM Call Efficiency

**Status:** Draft  
**Date:** 2026-03-09  
**Depends on:** 027 (fragment selection), 024 (lifecycle hooks)

---

## Problem Statement

A single user message to Bond can trigger **48 LLM calls** costing **$0.52** for what amounts to "read some files and tell me what you think." This is unacceptable for a system meant to run continuously.

### Evidence: Langfuse Trace Analysis

Session `01KKA1M7T065ZT2RTBVP32VM6Z` — two user messages:

1. "Can you evaluate how well design doc 27 has been implemented so far through phase 3?"
2. "Try again. You should have access now."

| Metric | Value |
|--------|-------|
| Total LLM calls | 48 |
| Opus calls (primary) | 9 ($0.49) |
| Gemini Flash Lite calls (utility) | 39 ($0.03) |
| Total cost | $0.52 |
| Agentic loop iterations for message 2 | 43 |
| Turn clusters (>10s gap) | 9 |

Message 2 alone triggered **43 LLM calls** across 7 turn clusters. The agent read files one at a time, each read spawning a new utility model call with the full system prompt and growing conversation history.

### Where the calls come from

Bond's worker has **four sources** of LLM calls per user message:

| Source | Model | When | Current call count |
|--------|-------|------|--------------------|
| **Primary agent turn** | Opus | First iteration + consequential actions | 1-3 per user message |
| **Speculative utility iterations** | Flash Lite | Every iteration after info-gathering-only tools | 3-30+ per user message |
| **Speculative replay** | Opus | When utility model proposes consequential action | 1-5 per user message |
| **Tool result filter** | Flash Lite | Per tool result > 1,500 chars that isn't rule-pruned | 0-10+ per user message |
| **History compression** | Flash Lite | When history exceeds compression threshold | 0-2 per user message |

The speculative utility model pattern is the biggest multiplier. It was designed to save money by routing info-gathering iterations to a cheaper model. In practice, it **increases total call count 4-5x** while saving only marginal cost, because:

- The utility model gets the full system prompt (~5,000+ tokens) every call
- The utility model doesn't batch tool calls well (Flash Lite ignores the batching instruction)
- Every utility call that produces a final response or consequential action triggers a **replay** on the primary model — so you pay for both
- The conversation history grows with every iteration, so later calls are more expensive

---

## Root Causes

### 1. No tool call batching enforcement

The system prompt says "batch independent tool calls." The primary model (Opus) sometimes does. The utility model (Flash Lite) almost never does. Result: `file_read` → LLM call → `file_read` → LLM call → `file_read` → LLM call, when all three could have been one iteration.

### 2. Speculative utility creates more calls than it saves

The pattern: if the previous iteration only called info-gathering tools, route the next iteration to the cheap model. Sounds good. But:

- The cheap model makes worse decisions, leading to more iterations
- Every "consequential" action triggers a replay (2 calls instead of 1)
- Every "final response" triggers a replay (2 calls instead of 1)
- The cheap model loops more often, triggering replay-on-repeat detection

Net effect: **you spend less per call but make 4x more calls.** In the traced session, the 39 Flash Lite calls cost only $0.03 total, but the 9 Opus calls ($0.49) include replays that wouldn't have existed without the speculative pattern.

**Note on model assumptions:** The traced session used Opus (primary) and Gemini Flash Lite (utility). But both models are configurable per agent. The utility model defaults to `claude-sonnet-4-6` (Anthropic), and the primary could be any provider. The call count problem is model-agnostic — it exists regardless of which models are in play.

### 3. Full context replay every iteration

Every iteration of the agentic loop sends the complete message array to the LLM. By iteration 30, that array includes the system prompt, all prior tool calls, all tool results, and all assistant messages. Anthropic models benefit from prompt caching on repeated prefixes, but non-Anthropic models pay full price every iteration. Since both the primary and utility models are configurable (and may or may not be Anthropic), the context replay cost depends entirely on the agent's configuration.

### 4. Tool result filter is an LLM call per result

When `rule_based_prune` doesn't catch a large tool result, `filter_tool_result` makes a full LLM call to the utility model. This is a good idea for huge results (10K+ tokens), but the 1,500 char threshold is too aggressive — most file reads are 2-5K chars and don't need LLM filtering.

### 5. No iteration budget awareness

`max_iterations` defaults to 25 (configurable per agent). There's a budget note injected into tool results (`[Turn X/25 | ~N tokens | M tool calls]`), but there's no actual enforcement of "this task should take ~5 iterations, not 40."

---

## Proposed Changes

### Phase 1: Reduce calls per turn (Week 1) — HIGH IMPACT

#### 1A. Remove speculative utility model routing

**Delete the speculative utility pattern entirely.** Route all agentic loop iterations to the primary model.

Why:
- The primary model typically batches tool calls better than a cheaper utility model → fewer iterations
- No replay overhead → no double-calls for consequential actions
- When the primary model is Anthropic, prompt caching makes subsequent iterations cheap (cache hits on the shared prefix). When it's not Anthropic, you still benefit from fewer total calls.
- Net cost reduction despite higher per-call price, because iteration count drops 3-4x

The utility model should only be used for **auxiliary tasks** (history compression, tool result filtering) — never for the main agent loop.

**Model-agnostic note:** This change benefits all model configurations. The speculative routing adds overhead regardless of whether primary/utility are the same provider or different providers.

**Changes:**
- `worker.py`: Remove `_use_utility`, `INFO_GATHERING_TOOLS` routing logic, and replay mechanism (~80 lines)
- `worker.py`: Remove the `_needs_replay` block and associated bookkeeping
- Keep `utility_model` config — it's still used for compression and filtering

**Risk:** Low. The speculative pattern is an optimization that demonstrably makes things worse. Removing it simplifies the code and reduces calls.

**Expected impact:** 48 → ~15 calls for the traced session. The 39 utility calls and their associated replays disappear. The primary model handles all iterations directly — if it's Anthropic, prompt caching keeps subsequent iterations cheap; if it's another provider, you still save by eliminating the 4x call multiplier.

#### 1B. Enforce tool call batching in the agent loop

Add a **batching hint** to the system prompt that's stronger than the current instruction, and add a **post-hoc batching check** that detects single-tool-call iterations and injects a nudge.

**Changes:**
- Add to system prompt (Tier 1): "When you need to read multiple files or gather multiple pieces of information, call ALL the tools in a single response. The system executes them in parallel. Single-tool iterations waste time and money."
- `worker.py`: After each iteration, if the model made exactly 1 info-gathering tool call and the response contains no text content, inject a system message: `"You made a single info-gathering call. If you need more information, batch multiple tool calls in your next response."`
- Track consecutive single-tool iterations. After 3 in a row, inject a stronger nudge.

**Expected impact:** Reduces iterations by 30-50% for exploration-heavy tasks.

#### 1C. Raise tool result filter threshold

**Changes:**
- `tool_result_filter.py`: Raise `FILTER_THRESHOLD` from 1,500 to 6,000 chars (~1,500 tokens)
- Expand `rule_based_prune` to handle the common cases that currently fall through to LLM filtering:
  - `file_read` results: truncate to first/last 50 lines if > 200 lines (rule-based, no LLM)
  - `code_execute` results: strip ANSI codes, truncate stdout > 4K chars to first/last 1K
  - `shell_grep` results: cap at 30 matches
- Add `SKIP_TOOLS` entries for all shell utility tools (`shell_find`, `shell_ls`, `shell_grep`, `shell_tree`, `shell_head`, `shell_wc`, `git_info`) — their results are always small and structured

**Expected impact:** Eliminates 80%+ of tool result filter LLM calls.

---

### Phase 2: Reduce iterations per task (Week 2) — MEDIUM IMPACT

#### 2A. Adaptive iteration budgets

Instead of a flat `max_iterations=25`, classify tasks and set proportional budgets:

| Task type | Detection | Budget |
|-----------|-----------|--------|
| Simple Q&A | No tool calls in first response | 1-2 |
| File lookup | First tool call is file_read/grep | 5-8 |
| Analysis/review | Multiple reads + no edits | 10-15 |
| Implementation | file_edit/file_write in plan | 20-30 |
| Complex multi-file | work_plan with 5+ items | 30-50 |

**Changes:**
- `worker.py`: After the first iteration, classify the task based on tool calls made and adjust `max_iterations` downward if appropriate
- Add a hard cap warning at 15 iterations (already in system prompt as guidance — make it a real check)
- At 80% of budget, inject: `"You're approaching your iteration limit. Wrap up or synthesize what you have."`

#### 2B. Early termination for read-only tasks

If the agent has made 10+ iterations and has never called a consequential tool (file_write, file_edit, code_execute), it's in a pure analysis loop. Inject a termination nudge:

`"You've gathered substantial context. Synthesize your findings and respond to the user now. Do not read more files."`

**Changes:**
- `worker.py`: Track `_has_made_consequential_call` flag
- After iteration 10, if flag is False, inject the nudge as a system message
- After iteration 15, if still no consequential calls, force a respond-only tool set

---

### Phase 3: Smarter context management (Week 3-4) — MEDIUM IMPACT

#### 3A. Incremental context instead of full replay

Currently every iteration replays the entire message array. For iterations 5+, the system prompt and early history are identical — only the last tool result is new.

**Changes:**
- Implement a **context fingerprint**: hash the messages array up to the last tool result. If the fingerprint matches the previous iteration, ensure prompt caching is used aggressively for Anthropic models (already partially done via `_advance_cache_breakpoint`, but can be more aggressive)
- For non-Anthropic primary models: implement a **context delta** mode where iterations 2+ send a compressed system prompt (just the Tier 1 rules, no Tier 2/3 fragments) since the model has already seen them
- Provider detection already exists in worker.py (`_resolve_provider`) — use it to apply the right strategy per model

#### 3B. In-loop tool result decay (improve existing)

The existing `_decay_in_loop_tool_results` runs every 3 iterations. Make it more aggressive:

**Changes:**
- Run every 2 iterations instead of 3
- After iteration 8, decay ALL tool results older than the last 3 to one-line summaries
- After iteration 15, decay everything older than the last 2 tool results

#### 3C. Streaming tool results for large outputs

For tools that produce large results (`code_execute` with build output, `file_read` of large files), stream the result through `rule_based_prune` BEFORE adding it to the message array. Currently the full result is added first, then decayed later.

**Changes:**
- `worker.py`: Apply `rule_based_prune` immediately after tool execution, before appending to messages
- Move the prune/filter decision BEFORE the message append, not after

**Note:** This is partially already happening (the prune/filter runs before append). The change is to make the rule-based path cover more cases so the LLM filter path is rarely hit (see 1C).

---

### Phase 4: Observability improvements (Week 4) — LOW IMPACT, HIGH VALUE

#### 4A. Distinguish LLM call types in Langfuse

Currently all 48 calls are named `agent-turn-{agent_id}`. Impossible to distinguish primary from utility from filter from compression without reading the input payload.

**Changes:**
- Primary agent iterations: `agent-turn-{agent_id}-iter-{N}`
- Speculative utility iterations (if kept): `agent-utility-{agent_id}-iter-{N}`
- Tool result filter: `tool-filter-{agent_id}-{tool_name}`
- History compression: `context-compression-{agent_id}`
- Sliding window summary: `context-window-{agent_id}`

Add a `call_type` tag to each trace: `primary`, `utility`, `filter`, `compression`.

#### 4B. Per-session cost tracking

Emit a summary trace at the end of each agent turn with:
- Total LLM calls (by type)
- Total tokens (input/output by model)
- Total cost
- Iterations used vs budget
- Cache hit rate

#### 4C. Cost alerting

If a single user message exceeds $0.25 or 20 iterations, log a warning and tag the trace with `cost:high`. Make the threshold configurable via agent settings.

---

## Expected Results

| Metric | Before | After Phase 1 | After Phase 2 | After All |
|--------|--------|---------------|---------------|-----------|
| LLM calls per message (analysis task) | 48 | ~15 | ~10 | ~8 |
| Cost per message (analysis task) | $0.52 | ~$0.25 | ~$0.18 | ~$0.15 |
| Utility model calls | 39 | 0 (agent loop) / 0-2 (filter) | Same | Same |
| Replay overhead | 5+ calls | 0 | 0 | 0 |
| Avg iterations for read-only tasks | 40+ | ~15 | ~10 | ~8 |
| Observability | All calls look identical | Tagged by type | + budget tracking | + cost alerts |

### Cost projection at scale

At 50 messages/day:
- **Before:** $26/day, $780/month
- **After Phase 1:** ~$12.50/day, $375/month
- **After all phases:** ~$7.50/day, $225/month

---

## Implementation Order

1. **Phase 1A** (remove speculative utility) — biggest single win, cleanest change, lowest risk
2. **Phase 1C** (raise filter threshold) — quick win, reduces auxiliary calls
3. **Phase 4A** (Langfuse labeling) — do this early so you can measure the impact of subsequent changes
4. **Phase 1B** (batching enforcement) — moderate win, depends on model behavior
5. **Phase 2A/2B** (iteration budgets) — requires tuning, benefits from 4A observability
6. **Phase 3** (context management) — optimization layer, do after the big wins are in

---

## What NOT to do

- **Don't add more LLM calls to reduce LLM calls.** No "planning model" that decides how many iterations to allow. No "routing model" that picks the right model per iteration. Every auxiliary LLM call is overhead.
- **Don't cache at the HTTP layer.** The same user message can have completely different context depending on conversation history. Cache at the prompt level (Anthropic prompt caching, or provider-equivalent) not the request level.
- **Don't reduce `max_iterations` as a blunt fix.** A hard cap of 10 would have prevented the 48-call session, but it would also break legitimate complex tasks. The fix is smarter budgeting, not lower caps.

---

## Open Questions

1. **Should the utility model be used for the agent loop at all?** This doc recommends removing it entirely from the loop. An alternative is to keep it but only for iterations 2-4 (early exploration), then switch to primary. Needs A/B testing.

2. **Is the tool result filter worth keeping?** With expanded `rule_based_prune` coverage (Phase 1C), the LLM filter path may fire so rarely that the code complexity isn't justified. Could simplify to rule-based-only and accept slightly larger tool results in the context.

3. **What's the right iteration budget for analysis tasks?** The proposal says 10-15. Needs calibration against real usage patterns. Langfuse data after Phase 4A will inform this.
