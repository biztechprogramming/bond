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
│  Phase 0: REPO MAP      git ls-files → JSON (~6K tok)  │
│  Phase 1: PLAN           Opus × 1 call (no tools,      │
│                          sees repo map + user request)  │
│  Phase 2: GATHER         Direct tool exec (no LLM) +   │
│                          optional utility compression   │
│  Phase 3: ACT            Opus × 2-8 iterations (tools) │
└─────────────────────────────────────────────────────────┘
```

### Phase 0: Repo Map (generated, no LLM call)

Before Phase 1, generate a compact minified JSON tree of the entire repository using `git ls-files`. This gives Opus a complete map of what exists — no guessing, no hallucinating file paths.

**Target: ~3,900 tokens** for a 800-file repo (Bond measured at 15.7KB).

The format is an indented tree — no JSON, no braces, no quotes, no commas, no file sizes. Just filenames and indentation for nesting. Directories end with `/`.

```python
import os, subprocess

# Extensions to exclude (binary, generated, locks)
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".map", ".tsbuildinfo", ".lock", ".wasm", ".pyc",
}

# Specific filenames to exclude
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "bun.lock", "uv.lock"}

# Directories whose individual files are auto-generated — collapse to a summary.
COLLAPSE_DIRS = {
    "gateway/src/spacetimedb/",
    "frontend/src/lib/spacetimedb/",
}


async def build_repo_map(repo_root: str) -> str:
    """Build a compact indented tree of all tracked files.
    
    Uses git ls-files (respects .gitignore, ~50ms). Output rules:
    - Directories use indentation for nesting, names end with /
    - Files are bare filenames, one per line
    - Empty files (0 bytes) are dropped
    - Auto-generated directories are collapsed to [generated: N files]
    - No JSON syntax — no braces, quotes, commas, or colons
    
    Returns: indented tree string (~3,900 tokens for a 800-file repo)
    """
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=repo_root
    )
    files = result.stdout.strip().split("\n")
    
    tree: dict = {}
    for filepath in sorted(files):
        name = os.path.basename(filepath)
        ext = os.path.splitext(name)[1].lower()
        if ext in SKIP_EXTS or name in SKIP_NAMES:
            continue
        
        try:
            size = os.path.getsize(os.path.join(repo_root, filepath))
        except OSError:
            continue
        
        # Drop empty files
        if size == 0:
            continue
        
        parts = filepath.split("/")
        node = tree
        for part in parts[:-1]:
            key = part + "/"
            node = node.setdefault(key, {})
        
        node[name] = True  # leaf node — just marks existence
    
    # Collapse auto-generated directories
    tree = _collapse_generated(tree)
    
    # Render as indented text
    return "\n".join(_render_tree(tree))


def _collapse_generated(tree: dict, path: str = "") -> dict:
    """Replace auto-generated directories with a file count summary."""
    result = {}
    for key, value in tree.items():
        full_path = path + key
        if isinstance(value, dict):
            if full_path in COLLAPSE_DIRS:
                file_count = sum(1 for v in value.values() if not isinstance(v, dict))
                result[key] = f"[generated: {file_count} files]"
            else:
                collapsed = _collapse_generated(value, full_path)
                if collapsed:
                    result[key] = collapsed
        else:
            result[key] = value
    return result


def _render_tree(tree: dict, indent: int = 0) -> list[str]:
    """Render tree as indented lines. Directories show as 'name/', files as 'name'."""
    lines = []
    prefix = " " * indent
    for key, value in tree.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}")
            lines.extend(_render_tree(value, indent + 1))
        elif isinstance(value, str):
            # Collapsed directory summary
            lines.append(f"{prefix}{key} {value}")
        else:
            lines.append(f"{prefix}{key}")
    return lines
```

**Example output** (~3,900 tokens for Bond's 628 entries):

```
AGENTS.md
Makefile
backend/
 app/
  worker.py
  agent/
   tools/
    coding_agent.py
    definitions.py
    native.py
   loop.py
   context_pipeline.py
  api/
   v1/
    agent.py
    conversations.py
gateway/
 src/
  server.ts
  spacetimedb/ [generated: 42 files]
  completion/
   handler.ts
```

**Token savings vs original JSON approach:**
| Optimization | Tokens saved |
|---|---|
| No braces `{ }` | ~278 |
| No commas | ~628 |
| No quotes | ~1,396 |
| No colons | ~631 |
| No file sizes | ~631 |
| Drop empty files | ~160 |
| Collapse generated dirs | ~800 |
| **Total savings vs naive JSON** | **~4,500 tokens** |

Opus can see every file and its location. No more guessing paths. The format is trivially parseable — directories end with `/`, files don't, nesting is by indentation.

See `docs/design/038-repo-map-sample.txt` for the full output.

### Phase 1: Plan (Opus, 1 call, no tools)

A single Opus call with `tool_choice: "none"` (or tools omitted entirely). Opus sees the user message, conversation history, **and the repo map**, then outputs a structured plan.

```python
plan_system = """
You are about to handle a task. Before taking any action, analyze what you need.

Here is the complete repository file tree (with sizes):
{repo_map}

Output a JSON plan:
{
  "complexity": "simple" | "moderate" | "complex",
  "approach": "brief description of how you'll handle this",
  "files_to_read": [
    "backend/app/worker.py",
    "backend/app/agent/tools/coding_agent.py",
    "gateway/src/server.ts"
  ],
  "grep_patterns": [
    {"pattern": "utility_model", "directory": "backend/"}
  ],
  "delegate_to_coding_agent": true | false,
  "estimated_iterations": 3
}

Rules:
- For simple questions (greetings, factual answers), set complexity to "simple"
  and files_to_read to []. You'll answer directly.
- For code tasks, list ALL files you expect to need. Pick from the tree above.
  Over-estimate — it's cheap to read extra files.
- If this should be delegated to a coding agent, say so upfront. Don't spend
  iterations gathering context only to delegate at the end.
- You can see file sizes in the tree. Prefer reading smaller files over huge ones
  when both contain what you need.
"""
```

**Cost:** ~$0.05-0.08 (one Opus call with conversation history + ~6K token repo map, no tool results)

**Why include the repo map:** Without it, Opus has to *remember* or *guess* what files exist. With it, Opus picks from a concrete list. No wasted iterations discovering that a file doesn't exist or that the file it needs is actually named something slightly different.

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

### Coding agent delegation (two distinct cases)

There are two ways a task ends up at a coding agent. They need different handling.

#### Case A: Planned delegation (known upfront)

Phase 1 returns `delegate_to_coding_agent: true`. The user said "build X" or "refactor Y" — Opus knows immediately this is coding agent work.

In this case, **the entire loop is about writing a good task prompt**, not about understanding the code. Opus doesn't need to understand every line — it needs to understand *enough* to write a spec that Claude Code can execute.

```python
if plan["delegate_to_coding_agent"]:
    # Phase 2: Gather, but LESS aggressively
    # Read only the files needed to understand the architecture and interfaces.
    # Don't read implementation details — Claude Code will do that itself.
    # The plan should request outlines, not full file contents.
    
    # Phase 3: Different system prompt — focus on spec writing
    delegation_system_prompt = """
    Your job is to write a detailed task specification for a coding agent.
    You have pre-gathered context about the relevant files and architecture.
    
    Write a task prompt that includes:
    1. What to build/change (specific, concrete)
    2. Which files to modify (exact paths from the repo map)
    3. Which files to reference for patterns/context (exact paths)
    4. Constraints and edge cases
    5. How to verify the work (tests, type checking, etc.)
    
    Do NOT:
    - Read more files to understand implementation details
    - Try to solve the problem yourself
    - Write code in your response
    
    Spawn the coding_agent tool with your spec when ready.
    """
    
    # Tight budget — spec writing shouldn't take more than 3 iterations
    _adaptive_budget = min(max_iterations, 3)
```

**Expected flow:**
```
Phase 0: Repo map                                    (~0ms, 0 tokens)
Phase 1: Plan → "delegate, need to understand X"     (1 Opus call, $0.05)
Phase 2: Gather outlines of 3-5 key files             (no LLM, $0.00)
Phase 3: 
  iter-0: Opus writes detailed task spec              ($0.08)
  iter-1: Opus spawns coding_agent with spec          ($0.08)
  [done — 2 iterations, total ~$0.21]
```

Compare to today: 15 iterations of Opus reading files, *then* spawning a coding agent with a vague prompt. ~$2.00.

#### Case B: Emergency handoff (mid-loop budget escalation)

The task started as direct work — Opus was reading files, maybe editing — but it's burning through the iteration budget. The existing `_budget_threshold` at 80% kicks in and injects a "hand off to coding_agent" message. This already exists in `worker.py`.

The problem with the current handoff: **Opus has spent 15 iterations gathering context that it now has to re-summarize into a coding agent prompt.** The coding agent gets a half-baked spec because Opus is rushing to hand off before the budget runs out.

**Fix: structured handoff context.**

When the budget escalation fires, instead of just telling Opus to "hand off," we build a structured handoff package from what Opus has already done:

```python
if _iteration >= _budget_threshold and _iteration > 2:
    # Build handoff context from the conversation so far
    handoff_context = _build_handoff_context(messages)
    
    messages.append({"role": "user", "content": f"""
SYSTEM: You are at iteration {_iteration + 1}/{_adaptive_budget}.
Budget escalation — hand off to coding_agent NOW.

Here is a summary of what you've gathered so far:

**Files you've read:**
{handoff_context['files_read']}

**Changes you've made (if any):**
{handoff_context['edits_made']}

**What's left to do:**
Based on your work so far, write a coding_agent task prompt that covers
the remaining work. Include the file paths you've already identified.
The coding agent has access to the same repo — reference files by path,
don't paste their contents.

Spawn coding_agent in your next response. Do not read more files.
"""})


def _build_handoff_context(messages: list[dict]) -> dict:
    """Extract what the agent has done so far from the message history.
    
    Scans tool calls and results to build:
    - List of files read (with paths)
    - List of edits made (file paths + brief description)
    - List of grep/search results
    """
    files_read = []
    edits_made = []
    
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = json.loads(fn.get("arguments", "{}"))
            
            if name == "file_read":
                files_read.append(args.get("path", "unknown"))
            elif name in ("file_edit", "file_write"):
                edits_made.append(args.get("path", "unknown"))
    
    return {
        "files_read": "\n".join(f"- {f}" for f in files_read) or "None",
        "edits_made": "\n".join(f"- {f}" for f in edits_made) or "None",
    }
```

**Expected flow:**
```
Phase 0-2: Normal plan/gather/act                    
Phase 3:
  iter-0 to iter-6: Opus working on the task directly
  iter-7: Budget at 80%, handoff fires
  iter-7: Opus sees structured handoff context         ($0.15)
  iter-8: Opus spawns coding_agent with detailed spec  ($0.15)
  [coding agent picks up where Opus left off]
```

The key difference from today: the handoff message includes **what files were already read and what edits were already made**, so Opus can write a spec that says "continue from here" instead of starting from scratch.

#### Summary: Two delegation paths

| | Case A: Planned | Case B: Emergency |
|---|---|---|
| **When decided** | Phase 1 (before any work) | Mid-loop (budget threshold) |
| **Info gathering** | Minimal — outlines only | Already done (it's why budget is depleted) |
| **Loop purpose** | Write task spec | Finish urgent work, then hand off |
| **Budget** | 3 iterations max | Remaining budget (usually 2-3) |
| **Spec quality** | High — focused from the start | Medium — compressed from partial work |
| **System prompt** | Spec-writing mode | Normal + handoff injection |
| **Cost** | ~$0.21 | ~$1.50 (existing work) + $0.30 (handoff) |

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

### Proposed: Repo Map → Plan → Gather → Act

| Phase | Model | Calls | Avg Input | Cost |
|---|---|---|---|---|
| Repo Map | None (git ls-files) | 0 | 0 | $0.00 |
| Plan | Opus | 1 | ~26K (history + 6K map) | $0.05 |
| Gather | None (direct tool exec) | 0 | 0 | $0.00 |
| Gather (compression) | Sonnet | 0-1 | ~50K | $0.00-0.02 |
| Act | Opus | 5 | ~60K avg | $0.55 |
| **Total** | | **6-7** | | **~$0.62** |

**Estimated savings: 70-75% per turn.** The main savings come from:
1. Fewer Opus iterations (5 vs 23)
2. Smaller context growth (files loaded upfront, not accumulated over iterations)
3. No single-tool iterations (Phase 2 reads everything in parallel)

---

## 5. Implementation Plan

### Step 1: Repo map generation (zero risk)

Build the `build_repo_map()` function. Run it once at turn start, cache it for the duration of the turn. Uses `git ls-files` — fast (~50ms), respects `.gitignore`, produces ~6K tokens.

```python
# At turn start, before any LLM calls:
repo_map = await build_repo_map(working_directory)
```

**Effort:** ~0.25 days
**Risk:** Zero — it's a subprocess call that produces a string. No LLM involved.

### Step 2: Plan phase (low risk, high signal)

Add plan extraction before the main loop in `worker.py`. Inject the repo map into the plan prompt:

```python
# Before the main loop:
if user_message and not _is_continuation:
    plan_messages = [
        {"role": "system", "content": plan_system.format(repo_map=repo_map)},
        *windowed_history,
        {"role": "user", "content": user_message},
    ]
    plan_response = await _cancellable_llm_call(
        _state.interrupt_event,
        model=model,
        messages=plan_messages,
        tools=None,            # no tools — just think
        temperature=0.3,       # lower temp for structured output
        max_tokens=2000,
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
