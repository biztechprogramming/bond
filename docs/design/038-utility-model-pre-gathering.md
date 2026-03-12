# 038 — Utility Model Pre-Gathering Phase

## Status: Proposed
## Author: Bond AI
## Date: 2026-03-11

---

## 1. Problem

Bond's agent loop runs every iteration on the primary model (Opus at $15/$75 per M tokens). A typical turn makes 15-23 LLM calls, most of which are information gathering — reading files, grepping, searching. Each call resends the entire growing context. The cost scales as a triangle:

```
Iteration  0:   5K input tokens     $0.02
Iteration  5:  40K input tokens     $0.08
Iteration 10:  80K input tokens     $0.15
Iteration 15: 120K input tokens     $0.22
Iteration 20: 160K input tokens     $0.30
                                   ──────
                          Total:   ~$2.00+ per turn
```

Most of this spend is Opus reading files one at a time. This is commodity work — any model can do it. Opus is only needed for judgment, planning, and complex edits.

### Evidence from Langfuse

Trace `01KKFHE3WKK93MWWF4J60WH0N1`: agent-turn-xxx-iter-8 contains 23 `litellm-acompletion` calls, all Opus, each triggering tool calls. The majority are single-tool information-gathering iterations — exactly the pattern described above.

### Prior art: Agent Zero

Agent Zero solves this with a two-model architecture:

```python
# Agent Zero's model split:
chat_model    = expensive (Opus/GPT-4)     # agent loop only
utility_model = cheap (Sonnet/Flash)       # everything else

# Utility model handles:
# - Memory recall (search query generation)
# - Memory filtering (relevance scoring)  
# - History compression
# - Solution memorization
# - Document querying
# - Chat renaming

# Chat model handles:
# - The actual agent loop (thinking + tool calls + responses)
```

The key insight: information gathering happens *before* the expensive model sees anything. The cheap model prepares a context bundle, then the expensive model acts on it.

### Bond's current state

Bond *has* a utility model (`config.utility_model`, defaults to `claude-sonnet-4-6`). It's used for:
- Tool result filtering (post-read compression)
- Parallel worker pool execution

It is **never used for information gathering**. All discovery happens inside the main loop on the primary model.

---

## 2. Design: Three-Phase Agent Turn

Replace the current single-loop architecture with three distinct phases:

```
┌─────────────────────────────────────────────────────────┐
│                    CURRENT ARCHITECTURE                  │
│                                                         │
│  User message → [Opus loop × 15-23 iterations] → Reply │
│                  (gathering + thinking + acting)         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                   PROPOSED ARCHITECTURE                  │
│                                                         │
│  Phase 1: PLAN          Opus × 1 call (no tools)        │
│  Phase 2: GATHER        Utility × 1-3 calls (tools)    │
│  Phase 3: ACT           Opus × 2-8 iterations (tools)  │
└─────────────────────────────────────────────────────────┘
```

### Phase 1: Plan (Opus, 1 call, no tools)

A single Opus call with `tool_choice: "none"` (or tools omitted entirely). Opus sees the user message and conversation history, thinks about what it needs, and outputs a structured plan.

```python
plan_prompt = """
Analyze the user's request. Before taking any action, output a JSON plan:

{
  "complexity": "simple" | "moderate" | "complex",
  "approach": "brief description of how you'll handle this",
  "information_needed": [
    {"type": "file_read", "path": "backend/app/worker.py", "reason": "need to see agent loop"},
    {"type": "file_read", "path": "backend/app/agent/tools/coding_agent.py", "reason": "..."},
    {"type": "grep", "pattern": "utility_model", "directory": "backend/", "reason": "..."},
    {"type": "project_search", "query": "system events", "reason": "..."},
    {"type": "memory_search", "query": "prior decisions about model routing", "reason": "..."}
  ],
  "delegate_to_coding_agent": true | false,
  "estimated_iterations": 3
}

Rules:
- For simple questions (greetings, factual answers), set complexity to "simple" 
  and information_needed to []. You'll answer directly in Phase 3.
- For code tasks, list ALL files you expect to need. Over-estimate — it's cheap 
  to read files you don't end up using.
- If this should be delegated to a coding agent, say so upfront. Don't spend 
  15 iterations gathering context only to delegate at the end.
```

**Cost:** ~$0.03-0.05 (one Opus call with conversation history, no tool results in context)

**Why Opus for planning:** This is the judgment call — figuring out *what* to read and *how* to approach the task. This is where the expensive model earns its cost. A cheap model might miss relevant files or misjudge the approach.

### Phase 2: Gather (Utility model, parallel tool execution)

Take the plan from Phase 1 and execute all information-gathering tool calls using the utility model. No LLM loop needed — just execute the tool calls directly and collect results.

```python
async def gather_phase(plan: dict, tool_context: ToolContext) -> str:
    """Execute all info-gathering from the plan using the utility model.
    
    Returns a formatted context bundle string to inject into Phase 3.
    """
    results = []
    
    for need in plan["information_needed"]:
        if need["type"] == "file_read":
            content = await tools.file_read(need["path"], outline=need.get("outline"))
            results.append(f"### {need['path']}\n```\n{content}\n```")
            
        elif need["type"] == "grep":
            output = await tools.shell_grep(need["pattern"], need["directory"])
            results.append(f"### grep '{need['pattern']}' {need['directory']}\n```\n{output}\n```")
            
        elif need["type"] == "project_search":
            hits = await tools.project_search(need["query"])
            results.append(f"### project_search('{need['query']}')\n{hits}")
            
        elif need["type"] == "memory_search":
            memories = await tools.search_memory(need["query"])
            results.append(f"### memory_search('{need['query']}')\n{memories}")
    
    # Optional: use utility model to summarize/compress if results are huge
    context_bundle = "\n\n".join(results)
    
    if estimate_tokens(context_bundle) > COMPRESSION_THRESHOLD:
        context_bundle = await compress_with_utility_model(
            context_bundle, plan["approach"], utility_model
        )
    
    return context_bundle
```

**Cost:** ~$0.01-0.02 for the tool executions (free — local) + optional utility model compression

**Key design decisions:**
- **No LLM in the loop.** The plan says what to read; we just read it. No model decides "hmm, I should also read X" — that was Phase 1's job.
- **Parallel execution.** All reads/greps/searches run concurrently. This is already supported by `ParallelWorkerPool`.
- **Compression gate.** If the gathered context exceeds a threshold (e.g., 50K tokens), use the utility model to compress before handing to Opus. Cheaper than Opus processing bloated context for 8 iterations.

### Phase 3: Act (Opus, with pre-built context)

The existing agent loop, but now Opus starts with all the information it needs:

```python
# Inject gathered context as the first message after the user message
messages = [
    system_prompt,
    *history,
    {"role": "user", "content": user_message},
    {"role": "user", "content": f"[System: Pre-gathered context for this task]\n\n{context_bundle}"},
]

# Run the normal agent loop — but now Opus rarely needs to read files
for _iteration in range(max_iterations):
    response = opus(messages, tools)
    # ... existing tool handling ...
```

**Cost:** ~$0.10-0.30 (Opus with pre-built context, 2-8 iterations instead of 15-23)

**Why this reduces iterations:**
- Opus already has the files it needs — no more "read file A... ok now read file B" one at a time
- It can go straight to planning edits or writing a coding agent spec
- The context is front-loaded, so each iteration is productive (editing, not exploring)

---

## 3. Handling Edge Cases

### Simple questions (no gathering needed)

Phase 1 returns `complexity: "simple"` and `information_needed: []`. Skip Phase 2 entirely. Phase 3 runs with just the conversation history — same as today but explicit.

```python
if plan["complexity"] == "simple":
    # Skip Phase 2, go straight to Phase 3 with no pre-gathered context
    pass
```

### Coding agent delegation

Phase 1 returns `delegate_to_coding_agent: true`. Phase 2 gathers just enough context for Opus to write a good task spec. Phase 3 is a short loop (1-3 iterations) where Opus writes the spec and spawns the agent.

```python
if plan["delegate_to_coding_agent"]:
    # Phase 2 still runs — gather the files Opus needs to write a good spec
    # Phase 3 uses a reduced iteration budget (max 5)
    max_iterations = 5
```

### Plan misses files

Opus's plan won't always list every file it needs. That's fine — Phase 3 still has full tool access. If Opus discovers it needs another file mid-loop, it reads it normally. The goal isn't to eliminate all in-loop reads, just to eliminate the *predictable* ones (the 10 files it reads every time before starting real work).

### Very large context

If Phase 2 gathers 200K tokens of file content, that's counterproductive. The compression gate handles this:

```python
GATHER_TOKEN_BUDGET = 80_000  # max tokens for pre-gathered context

if estimate_tokens(context_bundle) > GATHER_TOKEN_BUDGET:
    # Use utility model to compress: keep only the parts relevant to the plan
    context_bundle = await utility_model.call(
        system="Compress the following code/content to only the parts relevant "
               "to this task: {plan.approach}. Keep function signatures, key logic, "
               "and structure. Remove boilerplate, imports, and unrelated code.",
        user=context_bundle,
    )
```

### Prompt caching interaction

Anthropic's prompt caching works by caching prefix matches. The pre-gathered context goes *after* the system prompt and history (which are cached), so it doesn't break caching. On subsequent iterations within Phase 3, the pre-gathered context is part of the cached prefix.

---

## 4. Cost Comparison

### Current: 23-iteration Opus loop

| Phase | Model | Calls | Avg Input | Cost |
|---|---|---|---|---|
| Everything | Opus | 23 | ~80K avg | ~$2.15 |

### Proposed: Plan → Gather → Act

| Phase | Model | Calls | Avg Input | Cost |
|---|---|---|---|---|
| Plan | Opus | 1 | ~20K | $0.04 |
| Gather | None (direct tool exec) | 0 | 0 | $0.00 |
| Gather (compression) | Sonnet | 0-1 | ~50K | $0.00-0.02 |
| Act | Opus | 5 | ~60K avg | $0.55 |
| **Total** | | **6-7** | | **~$0.60** |

**Estimated savings: 70-75% per turn.** The main savings come from:
1. Fewer Opus iterations (5 vs 23)
2. Smaller context growth (files loaded upfront, not accumulated over iterations)
3. No single-tool iterations (Phase 2 reads everything in parallel)

---

## 5. Implementation Plan

### Step 1: Plan phase (low risk, high signal)

Add plan extraction before the main loop in `worker.py`:

```python
# Before the main loop:
if user_message and not _is_continuation:
    plan_response = await _cancellable_llm_call(
        _state.interrupt_event,
        model=model,
        messages=[*messages],  # same context as iteration 0
        tools=None,            # no tools — just think
        temperature=0.3,       # lower temp for structured output
        max_tokens=2000,
        response_format={"type": "json_object"},  # if supported
    )
    plan = parse_plan(plan_response)
```

**Effort:** ~0.5 days
**Risk:** Low — it's one extra Opus call before the loop. If plan parsing fails, fall through to existing behavior.

### Step 2: Gather phase (medium risk, biggest cost impact)

Execute the plan's `information_needed` directly, without an LLM loop:

```python
if plan and plan.get("information_needed"):
    context_bundle = await gather_phase(plan, tool_context)
    if context_bundle:
        messages.append({
            "role": "user", 
            "content": f"[Pre-gathered context]\n\n{context_bundle}"
        })
```

**Effort:** ~1 day
**Risk:** Medium — need to handle tool execution outside the normal loop, respect permissions, handle errors gracefully.

### Step 3: Adaptive budget from plan

Use the plan's complexity and delegation signal to set the iteration budget before the loop:

```python
if plan.get("delegate_to_coding_agent"):
    _adaptive_budget = min(max_iterations, 5)
elif plan.get("complexity") == "simple":
    _adaptive_budget = min(max_iterations, 3)
elif plan.get("complexity") == "moderate":
    _adaptive_budget = min(max_iterations, 12)
else:
    _adaptive_budget = min(max_iterations, 20)
```

**Effort:** ~0.5 days
**Risk:** Low — replaces the current heuristic budget (which already exists) with plan-informed budget.

### Step 4: Langfuse instrumentation

Add Phase 1/2/3 markers to Langfuse traces so you can see the impact:

```python
_langfuse_meta["trace_metadata"]["plan_complexity"] = plan.get("complexity")
_langfuse_meta["trace_metadata"]["plan_files_requested"] = len(plan.get("information_needed", []))
_langfuse_meta["trace_metadata"]["gather_tokens"] = estimate_tokens(context_bundle)
_langfuse_meta["trace_metadata"]["phase"] = "plan" | "gather" | "act"
```

**Effort:** ~0.25 days
**Risk:** None — observability only.

### Step 5: Compression gate (optional optimization)

If gathered context exceeds budget, use utility model to compress:

**Effort:** ~0.5 days
**Risk:** Low — the utility model compression already exists for history; extend it to pre-gathered context.

---

## 6. What NOT to change

- **`worker.py` core loop structure** — the `for _iteration in range(max_iterations)` loop stays. We're adding phases *before* it, not replacing it.
- **Tool execution inside the loop** — Opus can still call tools in Phase 3. Pre-gathering reduces but doesn't eliminate in-loop reads.
- **System prompt fragments** — the prompt architecture stays. `tool-efficiency.md` still applies; it just matters less because the loop is shorter.
- **Prompt caching** — the existing Anthropic cache breakpoint logic stays. Pre-gathered context becomes part of the cacheable prefix.

---

## 7. Interaction with Other Features

### Coding agent completion loop (038-system-events)

The completion loop triggers a new agent turn when a coding agent finishes. That turn should also use the three-phase approach — but Phase 1 will see the completion context and likely output `complexity: "simple"` (just summarize, no gathering needed).

### Plan-aware continuation (034)

The existing plan-aware continuation already classifies intent. Phase 1's plan overlaps with this — they should be merged. The plan phase replaces the current heuristic intent classification with an explicit structured plan.

### Parallel worker pool

Phase 2 can use the existing `ParallelWorkerPool` for concurrent tool execution. The pool already handles utility model routing and timeout management.

### Tool result filtering

Phase 2's results can be pre-filtered before injection. The existing `filter_tool_result` function works here — run each gathered result through the filter before building the context bundle.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Plan phase adds latency for simple questions | Skip plan for single-sentence inputs or greetings. Detect with a cheap heuristic (message length, presence of code/file references). |
| Plan misidentifies complexity | Phase 3 still has full tool access. A bad plan means a slightly less efficient Phase 3, not a failure. |
| Structured output parsing fails | Fall through to existing behavior (no plan, full iteration budget). Log the failure for debugging. |
| Pre-gathered context is too large | Compression gate with utility model. Hard cap at 80K tokens. |
| Plan adds extra Opus call that's wasted on simple tasks | Gate on message complexity. If the user says "hi" or "thanks," skip all three phases and just respond. |

---

## 9. Success Criteria

- [ ] Average Opus calls per turn drops from 15-23 to 5-8
- [ ] Average turn cost drops by 50%+ (measurable in Langfuse)
- [ ] No regression in response quality (human evaluation)
- [ ] Simple questions (< 3 iterations today) are not slower
- [ ] Coding agent delegation happens within 3 iterations, not 15+
- [ ] Langfuse traces show clear Plan/Gather/Act phase boundaries

---

## 10. Open Questions

1. **Should Phase 1 use the utility model instead of Opus?** Planning is judgment-heavy, which favors Opus. But Sonnet-4 is good enough for "list the files you need" — especially since Phase 3 can course-correct. Worth A/B testing.

2. **Should we cache plans?** If the same user asks follow-up questions about the same codebase area, the plan's `information_needed` is similar. Could skip Phase 2 on cache hit. Probably premature optimization.

3. **How does this interact with streaming?** Phase 1 and 2 happen before any response streams to the user. The user sees nothing during planning/gathering (maybe 2-5 seconds). Should we stream a "Planning..." status? The frontend already handles `agentStatus: "thinking"`.

4. **What about multi-turn context?** If the user asks "now add tests for that," Opus already has the files from the previous turn in history. Phase 2 should be smart enough to skip re-reading files already in context. The plan can express this: `"information_needed": []` because "I already have everything from the last turn."
