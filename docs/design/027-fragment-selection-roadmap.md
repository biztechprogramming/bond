# Design Doc 027: Fragment Selection Roadmap

**Status:** Draft (Revised 2026-03-09)  
**Date:** 2026-03-09  
**Depends on:** 022, 023, 024, 025, 026, 028

---

## Overview

This document sequences the fragment selection redesign. The core change: replace static checkbox attachment with a three-tier system where the right prompts reach the LLM at the right time, automatically.

All prompts live on the filesystem at `~/bond/prompts/` (64 files), versioned in git. A `prompts/manifest.yaml` file provides tier classification, lifecycle phase mappings, and semantic router utterances. No database storage for prompt content or metadata.

---

## The Three Tiers

| Tier | What | How Selected | Doc |
|------|------|---|---|
| **Tier 1: Always-On** | Safety, work planning, error handling, must-compile | System prompt — concatenated at runtime from disk files | 028 |
| **Tier 2: Lifecycle-Triggered** | Git best practices, testing requirements, PR creation rules | Phase detection — fires based on what the agent is doing (committing, implementing, reviewing) | 024 |
| **Tier 3: Context-Dependent** | PostgreSQL tips, React patterns, SpacetimeDB reducers | Semantic router — embedding similarity against user message | 022 |

---

## Sequencing

### Phase 1: Checkbox Removal + Tier 1 + Manifest (Week 1)
**Docs 028 + manifest creation.**

Remove the checkbox attachment model. Create `prompts/manifest.yaml` with tier/phase/utterance metadata for all 64 prompt files. Move always-on rules into the system prompt via runtime concatenation from disk.

**Why first:** Everything else depends on this. Can't implement dynamic selection while checkboxes are in place. Low risk — the checkbox model is currently a no-op anyway (empty fragment list).

**Deliverables:**
- Create `prompts/manifest.yaml` — classify all 64 files into Tier 1/2/3
- Implement `manifest.py` — load manifest, read files from disk, cache
- Remove checkbox UI from `AgentsTab.tsx`
- Remove attachment API endpoints from `prompts.py`
- Drop `agent_prompt_fragments`, `prompt_fragments`, `prompt_fragment_versions` tables
- Replace `load_universal_fragments()` with manifest-based Tier 1 loading
- Remove `config.get("prompt_fragments")` path — fragments come from disk, not agent config

### Phase 2: Lifecycle Hooks (Week 2-3)
**Doc 024.** Implement Tier 2 phase-triggered injection. This ensures git best practices fire at commit time, testing rules fire during implementation, etc.

**Why before semantic router:** Tier 2 handles the most critical operational rules (git workflow, testing). Getting these right is more important than optimizing Tier 3 topic matching.

**Deliverables:**
- `lifecycle.py` with `detect_phase()` heuristic on tool calls
- Phase detection in worker turn loop — reads Tier 2 entries from manifest
- Pre-commit hook in tool execution handler
- **Test:** User says "add a Stripe webhook handler" → agent implements, commits, pushes → git guidance is present at commit time despite user never mentioning git

### Phase 3: Semantic Router (Week 3-4)
**Doc 022.** Implement Tier 3 dynamic selection via embedding similarity. Utterances come from `manifest.yaml`, fragment content from disk files.

**Deliverables:**
- `uv add semantic-router sentence-transformers`
- `fragment_router.py` — builds route layer from manifest Tier 3 entries
- Replace `_select_relevant_fragments` and `context_pipeline.py` keyword/LLM selection with `select_fragments_by_similarity`
- Remove `_matches_triggers`, `_utility_model_select`, `_build_search_text`
- **Result:** 70-80% of turns skip the utility model call entirely

### Phase 4: Pipeline Refactor (Week 5-6)
**Doc 026.** Extract the selection logic into composable pipeline components.

**Why now, not earlier:** The components need to exist before you can extract them. Phases 1-3 build the components. Phase 4 gives them a clean architecture.

**Deliverables:**
- `PipelineComponent` protocol, `FragmentContext` dataclass
- Concrete components: `Tier1Loader` → `LifecycleInjector` → `SemanticMatcher` → `BudgetEnforcer`
- `FragmentPipeline` orchestrator

### Phase 5: DSPy Optimization (Month 2+)
**Doc 023.** After 4+ weeks of audit data, optimize the LLM fallback prompt (for the minority of turns where semantic confidence is low).

**Deliverables:**
- Fragment selection audit logging
- 50-100 labeled examples from audit data
- DSPy-optimized selection prompt
- A/B test vs. hand-written prompt

### Phase 6: Trained Classifier (Month 3+)
**Doc 025.** After 8+ weeks of audit data, train a classifier to handle common patterns.

**Deliverables:**
- Training script on accumulated audit data
- ONNX-exported classifier
- Monthly retraining pipeline
- 85%+ of requests handled at ~1ms

---

## Pipeline Evolution

```
Today (broken):
  Empty fragment list → selection runs on nothing → no fragments injected
  Universal fragments loaded separately but not coordinated with selection

After Phase 1:
  Tier 1 in system prompt from disk (always on)
  manifest.yaml classifies all 64 files
  No checkboxes, no database fragment tables

After Phase 2:
  Tier 1: system prompt (always)
  Tier 2: lifecycle hooks inject at commit/implement/review time from disk
  Tier 3: not yet implemented (no domain-specific injection)

After Phase 3:
  Tier 1: system prompt (always)
  Tier 2: lifecycle hooks from disk
  Tier 3: semantic router selects from disk via manifest utterances

After Phase 4:
  Same as above, composable pipeline architecture

After Phase 5-6:
  Tier 3: classifier → semantic router → DSPy-optimized LLM → budget
  (95%+ of requests under 10ms)
```

---

## Answering the Critical Questions

**How do we enforce the work plan?**  
Tier 1 — `universal/work-planning.md` content is in the system prompt, every turn. The agent always sees "your FIRST tool call on any non-trivial task MUST be `work_plan(action='create_plan')`." No selection needed. It's a file on disk, versioned in git.

**How do we always include git best practices when committing?**  
Tier 2 — lifecycle hook detects `git commit` in tool calls, reads `engineering/git/git.md` and `engineering/git/commits/commits.md` from disk, injects their content. The user doesn't need to mention git. The manifest maps these files to `phase: committing`.

**How do we tell the agent to commit to a new branch and push for review?**  
Tier 1 (system prompt) for the rule: "Never commit to main. Use feature branches." — already in `agent.system.main.md`.  
Tier 2 (lifecycle) for the how: commit message format, branch naming, push workflow — injected from disk files when the agent is actually committing.

---

## Success Metrics

| Metric | Today | After Phase 3 |
|--------|-------|--------|
| Fragments injected per turn | 0 (broken) | 3-6 (working) |
| Tier 1 presence | Partial (universals only, ad-hoc) | 100% (all always-on rules, from manifest) |
| Git guidance at commit time | Never (no lifecycle hooks) | 100% (lifecycle detection) |
| Utility model calls for selection | 1.0 per turn (wasted on empty list) | < 0.3 per turn |
| Selection latency (p50) | ~300ms (wasted LLM call) | < 10ms (local embeddings) |
| Prompt version history | Database versions (unused) | `git log prompts/` |
