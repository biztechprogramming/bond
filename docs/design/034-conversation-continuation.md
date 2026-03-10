# Design Doc 034: Conversation Continuation Architecture

**Status:** Draft  
**Created:** 2026-03-10  
**Context:** Agent sessions hit iteration limits mid-task. Continuation ("say continue") fails because the new turn inherits a bloated context. We need a system that lets an agent resume work cleanly.

---

## Problem Statement

When an agent session hits the iteration limit (e.g., 100 tool calls), it stops mid-task and suggests the user say "continue." But:

1. **The context is bloated** — 375 tool calls produce ~1.6MB of transcript. The continuation immediately burns iterations re-reading and re-orienting.
2. **No state awareness** — The continuation doesn't know what's done vs. what's remaining. It may redo completed work.
3. **Scope creep compounds** — A session that drifted from 1 task to 8 tasks leaves a continuation with no clear focal point.

### Observed iteration waste (real session, design doc 033)

| Category | Iterations | % |
|---|---|---|
| Exploring/reading code | 135 | 36% |
| Debugging runtime issues | 102 | 27% |
| Actual code edits | 61 | 16% |
| Build/test/git | 30 | 8% |
| Process management | 24 | 6% |
| Other | 23 | 6% |

Only 16% of iterations produced code. The rest was orientation and debugging.

---

## Proposed Design: Plan-Aware Fresh Context

### Core Principle

> Process every message as a **new conversation**. History is available to draw upon, not injected into the prompt.

### How It Works

```
User says "continue updating 033"
          │
          ▼
┌─────────────────────────────┐
│   1. Start fresh session    │
│      (clean context)        │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│   2. Parse intent           │
│   "continue" → resume plan  │
│   "adjust X" → modify plan  │
│   new task → create plan    │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│   3. Load work plan         │
│   (source of truth for      │
│    what's done/remaining)   │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│   4. Determine current      │
│      position in plan       │
│   - Check completed items   │
│   - Verify via git/fs state │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│   5. Execute next item(s)   │
│      from plan              │
│   - History searchable      │
│     (not in prompt)         │
└─────────────────────────────┘
```

### Message Classification

When a message arrives, classify it before loading any history:

| Intent | Action |
|---|---|
| **"continue"** / **"keep going"** | Load work plan → find next incomplete item → execute |
| **"adjust X then continue"** | Load work plan → modify it → save → execute next item |
| **"do Y instead"** | Abandon current plan → create new plan → execute |
| **New task (no plan exists)** | Create work plan → execute first item |

### The Work Plan

A structured document (stored as markdown or JSON) that tracks:

```markdown
# Work Plan: Design Doc 033 — Discord & Slack Channels

## Status: in-progress
## Branch: feature/033-channels-discord-slack
## Started: 2026-03-10T11:00:00Z

## Items

### 1. [done] Discord adapter skeleton
- Created gateway/src/channels/discord.ts
- Implements ChannelAdapter interface
- Commit: abc1234

### 2. [done] Discord bot connection & auth
- Bot token validation, login, ready event
- Commit: def5678

### 3. [in-progress] Discord message routing
- Started: inbound messages working
- Remaining: outbound replies, thread support

### 4. [pending] Slack adapter skeleton
### 5. [pending] Slack Socket Mode connection
### 6. [pending] Slack message routing
### 7. [pending] Settings UI for Discord & Slack
### 8. [pending] Tests for both adapters

## Decisions
- Using discord.js v14 (WebSocket gateway, no public URL needed)
- Socket Mode for Slack (also no public URL)
- Allow-list auto-detects installer identity

## Constraints
- Must follow existing ChannelAdapter interface
- No public URLs — both use WebSocket connections
```

### How State Is Determined

When resuming, the agent doesn't trust the plan blindly. It **verifies**:

1. **Git state** — What commits exist on the branch? What files are modified/uncommitted?
2. **File existence** — Do the files mentioned in "done" items actually exist?
3. **Quick validation** — Does `tsc --noEmit` pass? Are there syntax errors?

This takes 3-5 iterations, not 135.

### History as Searchable Context (Not Injected)

The old session transcript stays on disk. If the agent needs to recall a specific decision or code pattern from the prior session, it can search it:

```
history_search(query="why did we use Socket Mode for Slack")
→ "Decision: Socket Mode avoids needing a public URL. Bond runs locally."
```

This is pull-based (agent asks when needed) vs. push-based (everything in the prompt). The agent only pays the token/iteration cost for history it actually needs.

### Fallback: No Work Plan

If there's no work plan (ad-hoc conversation, not a structured task):

1. Scan the old transcript in **reverse chronological order**
2. Extract: last assistant message, last user request, uncommitted changes
3. Build a lightweight checkpoint:
   - What was the user asking for?
   - What was the agent doing when it stopped?
   - What's the current git/file state?
4. Present this as context for the new session

This is ~500 tokens, not 100K.

### Iteration Budget Awareness

The agent should be aware of its iteration budget and manage scope:

- At **50% budget**: If working from a plan, checkpoint progress on current item
- At **80% budget**: Stop accepting new tangential work. Finish current item or save state cleanly.
- At **95% budget**: Write final checkpoint. Do not start new items.

The work plan makes this natural — each item is a discrete unit. The agent finishes the current item, marks it done, and stops. The next session picks up the next item.

---

## Comparison: Three Approaches

### 1. This Design — Plan-Aware Fresh Context

**Mechanism:** Start fresh. Use a work plan to know where you are. Pull history on demand.

**When compression happens:** Never — old context is discarded, not compressed. State lives in the plan.

**Token budget allocation:**
| Component | Tokens |
|---|---|
| System prompt + work plan | ~2-5K |
| Current item context (files, code) | ~10-30K |
| History lookups (on demand) | 0-5K |
| Working space | remainder |

### 2. Agent Zero — Progressive 3-Tier Compression

**Mechanism:** Keep everything in one conversation. Compress progressively as context grows.

**Tiers:**
| Tier | Budget | Content | Compression method |
|---|---|---|---|
| Current Topic | 50% | Active exchange | Truncate large messages → summarize attention window |
| History Topics | 30% | Prior completed topics | Summarize → promote to Bulk |
| Bulks | 20% | Oldest context | Merge bulks → re-summarize → evict oldest |

**When compression happens:** Continuously, whenever total tokens exceed `ctx_length × ctx_history`. Uses a utility LLM to generate summaries.

**Token budget allocation:**
| Component | Tokens |
|---|---|
| Current topic (full messages) | 50% of history budget |
| Summarized prior topics | 30% of history budget |
| Heavily summarized bulks | 20% of history budget |

### 3. OpenClaw — Structured Compaction with Preserved Turns

**Mechanism:** Same conversation, but when context overflows, summarize old messages into a structured summary. Preserve recent turns verbatim.

**Summary structure (required sections):**
- `## Decisions` — Key choices made
- `## Open TODOs` — Unfinished work
- `## Constraints/Rules` — Rules that must be followed
- `## Pending user asks` — Unanswered user requests
- `## Exact identifiers` — Literal values (IDs, URLs, ports, paths)

Plus: file operations list, tool failure log, workspace context (AGENTS.md).

**When compression happens:** When context window is exceeded. Summarizes in stages (chunked) using the session's model. Recent 3 turns preserved verbatim.

**Token budget allocation:**
| Component | Tokens |
|---|---|
| Structured summary of old messages | up to 50% of context |
| Preserved recent turns (last 3) | verbatim |
| New content (system prompt, tools, etc.) | remainder |

---

## Pros and Cons

### Plan-Aware Fresh Context (This Design)

| Pros | Cons |
|---|---|
| **Zero wasted tokens** — no compressed history eating budget | **Requires a work plan** — ad-hoc conversations need a fallback |
| **No summarization cost** — no LLM calls to compress | **Plan maintenance** — agent must keep the plan updated |
| **Deterministic state** — plan + git state = exact position | **Cold start** — agent has no "feel" for prior conversation tone/style |
| **Scope enforcement** — plan items are discrete, prevents drift | **Loses nuance** — subtle decisions not captured in plan may be lost |
| **Scales infinitely** — 100th continuation is as fast as 2nd | **History search quality** — depends on search implementation |
| **Simple to implement** — no compression algorithms needed | **Two systems** — plan-based vs. fallback checkpoint |

### Agent Zero — Progressive Compression

| Pros | Cons |
|---|---|
| **Seamless** — no session boundary, conversation just continues | **Lossy** — each compression loses detail |
| **No plan needed** — works for any conversation shape | **Cumulative degradation** — after many compressions, context is mostly summaries-of-summaries |
| **Preserves tone/style** — current topic keeps full messages | **LLM cost** — every compression requires a summarization call |
| **Graceful degradation** — oldest context fades naturally | **No scope control** — doesn't prevent drift, just manages the mess |
| **Battle-tested** — running in production agent-zero instances | **Hallucination risk** — LLM summaries can introduce errors |
| | **Doesn't solve iteration limits** — context fits, but iterations still exhausted |

### OpenClaw — Structured Compaction

| Pros | Cons |
|---|---|
| **Structured** — required sections ensure critical info survives | **Still one conversation** — context grows until compaction triggers |
| **Preserved recent turns** — last 3 turns are verbatim (no loss) | **Compaction is expensive** — multi-stage summarization with chunking |
| **File tracking** — knows what files were read/modified | **Compaction can fail** — falls back to cancellation if LLM errors |
| **Tool failure awareness** — surfaces repeated tool failures | **Summary bloat** — structured sections can grow large themselves |
| **Workspace context** — re-injects AGENTS.md constraints | **Doesn't solve iteration limits** — same as Agent Zero |
| **Exact identifiers** — preserves literal values that LLMs often mangle | **Single-pass** — no iterative refinement of summaries |

---

## Key Differentiator

The fundamental difference is **what serves as the source of truth**:

| Approach | Source of truth | Failure mode |
|---|---|---|
| **Plan-Aware** | Work plan (external document) | Plan gets stale or incomplete |
| **Agent Zero** | Conversation history (compressed) | Summaries lose critical details |
| **OpenClaw** | Structured summary (generated) | Summary misses context, identifiers mangled |

**Plan-Aware is the only approach that solves the iteration problem**, because it starts fresh and only does remaining work. The other two solve context window overflow but still burn iterations on redundant exploration within a single continuous session.

---

## Hybrid Recommendation

Use **Plan-Aware Fresh Context** as the primary strategy, borrowing specific strengths from the others:

1. **From OpenClaw:** Structured summary sections. If no work plan exists, generate a checkpoint with `## Decisions`, `## Open TODOs`, `## Exact identifiers` — not a raw dump.

2. **From Agent Zero:** Attention preservation. When the agent pulls history on demand, prioritize the first and last messages of each topic (the request and the resolution), not the middle (the fumbling).

3. **Novel:** Iteration budget awareness built into the plan execution loop. The agent doesn't just "know" its budget — the plan runner enforces it by not starting items that can't finish.

---

## Implementation Sketch

### Components Needed

1. **Intent classifier** — Determines if message is "continue", "adjust", or "new task"
2. **Work plan store** — CRUD for plans (could be a markdown file per branch, or DB rows)
3. **Plan position resolver** — Cross-references plan items with git state to determine true position
4. **History index** — Searchable index over old session transcripts (semantic search or keyword)
5. **Iteration budget tracker** — Tracks iteration count and signals the agent at thresholds

### What Already Exists

- **Antfarm stories table** — Already tracks stories with status (pending/done), could serve as work plan
- **OpenClaw memory_search** — Could index old transcripts
- **Session transcript files** — Already archived on reset, available for indexing
- **AGENTS.md + progress.txt** — Existing checkpoint mechanisms (partial)

---

## Open Questions

1. **Who creates the work plan?** The planner agent (structured workflow) or the developer agent itself (ad-hoc)?
2. **Where does the plan live?** File in repo? Antfarm DB? Both?
3. **How granular are plan items?** Per-file? Per-feature? Per-commit?
4. **What triggers a fresh session?** Automatic on iteration limit? User-initiated? Both?
5. **How is history searched?** Semantic embedding? Keyword? Read last N lines?
