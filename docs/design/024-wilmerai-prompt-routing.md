# Design Doc 024: Lifecycle-Triggered Prompt Routing

**Status:** Draft (Revised 2026-03-09)  
**Date:** 2026-03-09  
**Depends on:** 010 (Prompt Management), 021 (Prompt Hierarchy), 028 (Checkbox Removal)  
**Reference:** [SomeOddCodeGuy/WilmerAI](https://github.com/SomeOddCodeGuy/WilmerAI) (⭐ 804)

---

## 1. Scope

This document covers **Tier 2: Lifecycle-Triggered fragments** — rules that should be injected based on what phase of work the agent is in, not what the user asked about.

| Fragment | When It Fires | Why Not Semantic Router |
|---|---|---|
| `git-workflow` | Agent is about to commit/push | User never says "please use git best practices" |
| `commit-messages` | Agent is writing a commit message | Fires on agent behavior, not user input |
| `python-testing` | Agent is implementing code | Testing rules apply during implementation, always |
| `code-review` | Agent is reviewing a PR or creating one | Fires at review phase, not when user mentions "review" |
| `progress-tracking` | Agent is working on a multi-step task | Should fire based on task complexity, not keywords |

These fragments aren't "sometimes relevant based on topic." They're **always relevant at a specific point in the work lifecycle** — regardless of whether the task is about databases, frontend, or infrastructure.

### The Three Tiers (Context)

```
Tier 1: ALWAYS ON → System prompt (doc 028)
  Work plan, safety, error handling, core behavior

Tier 2: LIFECYCLE-TRIGGERED → This doc
  Git rules when committing, testing rules when implementing,
  review rules when creating PRs

Tier 3: CONTEXT-DEPENDENT → Semantic Router (doc 022)
  Domain-specific knowledge matched by user intent
```

---

## 2. What WilmerAI Teaches

WilmerAI's key insight: **categorize the situation first, then load the right prompt bundle**. The categorization isn't about the topic — it's about the *type of work* being done.

WilmerAI does this with a dedicated categorizer step (small LLM call or embedding match) that classifies the message, then routes to a workflow with pre-bundled system prompts and context injections defined in JSON config.

Bond's version of this is simpler because **the agent already knows what phase it's in**. The work plan tracks items with statuses (`new`, `in_progress`, `done`). The agent calls `git commit`. The agent calls `work_plan(action="add_item")`. These are observable signals that don't require classification.

---

## 3. Lifecycle Phases and Their Fragments

### 3.1 Phase Definitions

| Phase | Signal (how we detect it) | Fragments to inject |
|---|---|---|
| **Planning** | First 2-3 tool calls of a conversation, or `work_plan(action="create_plan")` | `progress-tracking` |
| **Implementing** | `file_edit` or `code_execute` tool calls, work plan item status = `in_progress` | `python-testing` |
| **Committing** | `code_execute` with `git add` or `git commit` in command | `git-workflow`, `commit-messages` |
| **Pushing** | `code_execute` with `git push` in command | `git-workflow` |
| **Reviewing** | `code_execute` with `gh pr create` or conversation mentions PR review | `code-review` |

### 3.2 Why Not Just Put These in the System Prompt?

Because they'd bloat it. The system prompt already contains Tier 1 rules (~1500 tokens). Adding all lifecycle fragments would add another ~1100 tokens — present on every turn even when irrelevant.

A commit message format guide is noise when the agent is reading files. Testing requirements are noise when the agent is writing a PR description. Lifecycle injection keeps the context window focused.

### 3.3 Why Not Use Semantic Router?

Semantic router matches user messages against fragment utterances. But lifecycle fragments fire on **agent actions**, not user intent:

- The user says: "Add a feature to handle webhook events"
- The agent implements for 15 tool calls, then commits
- At commit time, `git-workflow` and `commit-messages` should inject
- The user message is about webhooks, not git — semantic router would never match

The lifecycle hook system observes **what the agent is doing** (which tools it's calling, what phase the work plan is in) and injects the right fragments at the right moment.

---

## 4. Implementation

### 4.1 Lifecycle Detector

```python
# backend/app/agent/lifecycle.py

from dataclasses import dataclass
from enum import Enum, auto

class Phase(Enum):
    PLANNING = auto()
    IMPLEMENTING = auto()
    COMMITTING = auto()
    PUSHING = auto()
    REVIEWING = auto()
    IDLE = auto()

# Phase → fragment names to inject
LIFECYCLE_FRAGMENTS: dict[Phase, list[str]] = {
    Phase.PLANNING: ["progress-tracking"],
    Phase.IMPLEMENTING: ["python-testing"],
    Phase.COMMITTING: ["git-workflow", "commit-messages"],
    Phase.PUSHING: ["git-workflow"],
    Phase.REVIEWING: ["code-review"],
    Phase.IDLE: [],
}

@dataclass
class LifecycleState:
    current_phase: Phase
    turn_number: int
    has_work_plan: bool
    last_tool_calls: list[str]
    work_plan_status: str | None  # "in_progress", "done", etc.


def detect_phase(state: LifecycleState) -> Phase:
    """Detect the current lifecycle phase from observable signals.
    
    Priority order matters — committing overrides implementing,
    because if the agent is committing, it's done implementing.
    """
    last_tools = state.last_tool_calls
    
    # Check most specific phases first
    for tool_call in last_tools:
        if _is_git_push(tool_call):
            return Phase.PUSHING
        if _is_git_commit(tool_call):
            return Phase.COMMITTING
        if _is_pr_create(tool_call):
            return Phase.REVIEWING
    
    # Implementation phase: agent is editing files or executing code
    for tool_call in last_tools:
        if _is_implementation(tool_call):
            return Phase.IMPLEMENTING
    
    # Planning phase: early in conversation or creating plans
    if state.turn_number <= 3 or any(_is_planning(tc) for tc in last_tools):
        return Phase.PLANNING
    
    return Phase.IDLE


def _is_git_commit(tool_call: str) -> bool:
    """Check if a tool call involves git commit."""
    if "code_execute" not in tool_call and "shell" not in tool_call:
        return False
    commit_signals = ["git commit", "git add", "git stage"]
    return any(s in tool_call.lower() for s in commit_signals)


def _is_git_push(tool_call: str) -> bool:
    push_signals = ["git push"]
    return any(s in tool_call.lower() for s in push_signals)


def _is_pr_create(tool_call: str) -> bool:
    pr_signals = ["gh pr create", "gh pr", "pull request"]
    return any(s in tool_call.lower() for s in pr_signals)


def _is_implementation(tool_call: str) -> bool:
    impl_tools = ["file_edit", "code_execute", "file_write"]
    return any(t in tool_call for t in impl_tools)


def _is_planning(tool_call: str) -> bool:
    return "work_plan" in tool_call
```

### 4.2 Fragment Loader

```python
# backend/app/agent/lifecycle.py (continued)

from pathlib import Path
from .manifest import load_manifest, get_tier2_fragments, FragmentMeta

def load_lifecycle_fragments(phase: Phase, prompts_dir: Path) -> list[FragmentMeta]:
    """Load Tier 2 fragments for a given lifecycle phase.
    
    Reads from prompts/manifest.yaml — fragments are files on disk,
    not database rows. The manifest maps each file to its phase.
    
    Args:
        phase: Current detected phase
        prompts_dir: Path to ~/bond/prompts/
    
    Returns:
        List of FragmentMeta with content loaded from disk
    """
    manifest = load_manifest(prompts_dir)  # Cached after first call
    
    phase_name = phase.name.lower()
    return get_tier2_fragments(manifest, phase_name)
```

### 4.3 Integration Point in Worker

The lifecycle hook runs **between turns**, not during fragment selection. It observes the previous turn's tool calls and injects fragments into the next turn's system prompt:

```python
# In worker.py — inside the turn loop

# After each turn completes, update lifecycle state
lifecycle_state = LifecycleState(
    current_phase=Phase.IDLE,
    turn_number=turn_number,
    has_work_plan=bool(plan_id),
    last_tool_calls=[tc["name"] + ":" + str(tc.get("arguments", {})) for tc in tool_calls_this_turn],
    work_plan_status=current_plan_status,
)

detected_phase = detect_phase(lifecycle_state)

if detected_phase != Phase.IDLE:
    lifecycle_frags = load_lifecycle_fragments(detected_phase, fragment_store)
    if lifecycle_frags:
        # Inject into the NEXT turn's system prompt
        lifecycle_content = "\n\n".join(f["content"] for f in lifecycle_frags)
        # Append as a section in the system prompt
        messages[0]["content"] += f"\n\n## Current Phase: {detected_phase.name}\n{lifecycle_content}"
        
        logger.info(
            "Lifecycle injection: phase=%s, fragments=%s",
            detected_phase.name,
            [f.get("name") for f in lifecycle_frags],
        )
```

### 4.4 Pre-Commit Hook (Critical Path)

The most important lifecycle hook is the commit phase. When the agent is about to commit, git rules **must** be present. This is the specific case you asked about — "how do we always include git best practices when it's time to commit?"

```python
# In the tool execution handler, BEFORE executing git commands

async def handle_code_execute(command: str, **kwargs):
    """Execute code with lifecycle-aware fragment injection."""
    
    # Pre-commit hook: if this is a git commit, ensure git fragments are loaded
    if _is_git_commit(f"code_execute:{command}"):
        git_fragments = load_lifecycle_fragments(Phase.COMMITTING, fragment_store)
        
        if git_fragments:
            # Inject git guidance as a system message RIGHT BEFORE the commit
            guidance = "\n\n".join(f["content"] for f in git_fragments)
            inject_system_message(f"""
## Before You Commit
{guidance}

Review your staged changes with `git diff --cached` before committing.
Ensure your commit follows the format above.
""")
    
    # Then execute the actual command
    return await _execute_in_sandbox(command, **kwargs)
```

This guarantees that no matter what the user asked about, the agent sees git best practices right when it needs them.

---

## 5. Phase Detection: LLM vs. Heuristic

WilmerAI uses an LLM categorizer. Bond doesn't need one for lifecycle detection because **the signals are explicit tool calls**, not ambiguous natural language.

| Approach | Pros | Cons |
|---|---|---|
| **Heuristic (proposed)** | Zero latency, deterministic, no API cost | Might miss edge cases |
| **LLM categorizer** | Handles ambiguous cases | 100ms+ latency, API cost on every turn |

**Decision: Heuristic.** Tool calls are unambiguous — `git commit` is always a commit. If edge cases emerge, add more heuristic rules before adding an LLM call.

### Edge Cases the Heuristic Handles

| Scenario | Detection |
|---|---|
| Agent runs `git commit -m "..."` | `_is_git_commit` matches "git commit" |
| Agent runs `git add . && git commit` | Same — "git commit" substring |
| Agent discusses commits but doesn't run one | No `code_execute` tool call → doesn't fire |
| Agent runs `git log --oneline` | No match — "git log" is not in commit signals |
| Agent creates a PR via `gh pr create` | `_is_pr_create` matches → review phase |

---

## 6. Fragment Content for Each Phase

### Committing Phase

Injects `git-workflow` + `commit-messages` from `prompts/engineering/git/`:

```markdown
# Git Best Practices
- Atomic commits: one logical change per commit
- Branching: use feature branches, never commit to main directly
- Review staged changes with `git diff --cached` before committing
- Only push when a task or sub-task is complete and tested

# Commit Messages
- Format: <type>(<scope>): <description>
- Types: feat, fix, docs, refactor, test, chore
- Subject: 50 chars max, imperative mood
- Run `git diff --cached` to verify what's being committed
```

### Implementing Phase

Injects `python-testing`:

```markdown
# Testing Requirements
- Write tests for every change
- Cover main functionality and key edge cases
- Run existing tests to check for regressions
- Run your new tests to confirm they pass
```

### Review Phase

Injects `code-review`:

```markdown
# Code Review / PR Creation
- Clear title summarizing the change
- Description: what you did, why, what was tested
- Review your own diff before submitting
```

---

## 7. Workflow Presets (Optional Extension)

If the heuristic phase detection proves insufficient, WilmerAI's preset concept can be layered on:

```python
WORKFLOW_PRESETS = {
    "feature-development": {
        "phases": [Phase.PLANNING, Phase.IMPLEMENTING, Phase.COMMITTING, Phase.PUSHING, Phase.REVIEWING],
        "description": "Full feature development lifecycle",
    },
    "bugfix": {
        "phases": [Phase.IMPLEMENTING, Phase.COMMITTING, Phase.PUSHING],
        "description": "Quick bugfix — skip planning and review",
    },
    "review-only": {
        "phases": [Phase.REVIEWING],
        "description": "PR review — no implementation",
    },
}
```

But this is not needed for v1. The heuristic phase detector handles the common cases.

---

## 8. Migration Path

| Step | Work | Risk |
|------|------|------|
| 1 | Classify fragments into tiers (done — see table in doc 022 §4.2) | Design only |
| 2 | Implement `lifecycle.py` with `detect_phase()` and `load_lifecycle_fragments()` | New code |
| 3 | Wire lifecycle detection into worker turn loop | Integration |
| 4 | Add pre-commit hook in tool execution handler | Integration |
| 5 | Move git, testing, and review fragments out of DB selection pipeline | Migration |
| 6 | Verify: commit without mentioning git still gets git guidance | Test |
| 7 | Verify: implement without mentioning tests still gets testing rules | Test |

**Critical test case:** User says "Add a webhook handler for Stripe events." Agent implements, commits, pushes. At no point does the user mention git, testing, or PRs. Verify that:
- During implementation → `python-testing` is injected
- At commit time → `git-workflow` + `commit-messages` are injected
- At push time → `git-workflow` is injected
- At PR creation → `code-review` is injected

---

## 9. What This Doesn't Solve

- **Topic-based selection** — "Which database fragment is relevant?" That's semantic router (doc 022).
- **Always-on rules** — Safety, work planning. That's the system prompt (doc 028).
- **Custom lifecycle phases** — If a user wants a custom workflow (e.g., "always run linting before commit"), that requires phase configuration in the UI. Future work.
- **Multi-agent lifecycle** — In multi-agent conversations (doc 011), each agent may be in a different phase. The lifecycle detector runs per-agent.

---

## 10. Decisions

| Question | Decision |
|----------|----------|
| How to detect phases? | **Heuristic on tool calls** — deterministic, zero latency |
| When to inject? | **Between turns** — observe previous turn, inject into next |
| Pre-commit hook? | **Yes** — inject git guidance right before `git commit` executes |
| LLM categorizer? | **No** — tool call heuristics are unambiguous enough |
| Workflow presets? | **Not in v1** — add if heuristic proves insufficient |
| Where do lifecycle fragments live? | **Filesystem** at `~/bond/prompts/`, tagged as `tier: 2` in `manifest.yaml` |
