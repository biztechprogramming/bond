"""Conversation continuation — Plan-Aware Fresh Context (Design Doc 034).

When a user says "continue", this module provides the machinery to:
1. Classify intent (continue / adjust / new task)
2. Resolve current position in the work plan against real state (git, filesystem)
3. Build a minimal recovery context instead of replaying bloated history

The goal: a continuation turn starts fresh with ~2-5K tokens of plan context
instead of ~100K+ of compressed history. Only 3-5 iterations spent orienting
instead of 135.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("bond.agent.continuation")


# ---------------------------------------------------------------------------
# Intent Classification
# ---------------------------------------------------------------------------

class ContinuationIntent(Enum):
    """What the user wants when they send a message."""
    CONTINUE = "continue"        # Resume where we left off
    ADJUST = "adjust"            # Modify the plan, then continue
    NEW_TASK = "new_task"        # Abandon current plan, do something new
    NORMAL = "normal"            # No continuation semantics — regular message


# Patterns that indicate a continuation intent.
# Ordered by specificity — first match wins.
_CONTINUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(please\s+)?continue\b", re.IGNORECASE),
    re.compile(r"^keep\s+going\b", re.IGNORECASE),
    re.compile(r"^carry\s+on\b", re.IGNORECASE),
    re.compile(r"^go\s+ahead\b", re.IGNORECASE),
    re.compile(r"^resume\b", re.IGNORECASE),
    re.compile(r"^pick\s+up\s+where\b", re.IGNORECASE),
    re.compile(r"^finish\s+(what|the|this|that)\b", re.IGNORECASE),
    re.compile(r"^next\s+(item|task|step)\b", re.IGNORECASE),
]

_ADJUST_PATTERNS: list[re.Pattern[str]] = [
    # "change X then continue", "adjust X and keep going", "skip item 3"
    re.compile(r"^(change|adjust|modify|update|skip)\s+.+\b(then|and)\s+(continue|keep going|carry on|resume)", re.IGNORECASE),
    re.compile(r"^skip\s+(item|step|task)\s+", re.IGNORECASE),
    re.compile(r"^(but\s+)?(first|also|instead)\s+", re.IGNORECASE),
]

_NEW_TASK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(do|start|work\s+on|implement|build|create|fix|add)\s+.+\binstead\b", re.IGNORECASE),
    re.compile(r"^(forget|abandon|drop|cancel)\s+(the\s+)?(current\s+)?(plan|task|work)", re.IGNORECASE),
    re.compile(r"^(new|different)\s+(task|plan|project)\b", re.IGNORECASE),
]


def classify_intent(message: str, has_active_plan: bool) -> ContinuationIntent:
    """Classify a user message into a continuation intent.

    Args:
        message: The user's message text.
        has_active_plan: Whether there's an active work plan for this conversation.

    Returns:
        The classified intent.
    """
    text = message.strip()
    if not text:
        return ContinuationIntent.NORMAL

    # Without an active plan, continuation/adjust don't make sense
    if not has_active_plan:
        return ContinuationIntent.NORMAL

    # Check adjust first (more specific — contains continuation + modification)
    for pattern in _ADJUST_PATTERNS:
        if pattern.search(text):
            logger.info("Intent classified as ADJUST: %r", text[:80])
            return ContinuationIntent.ADJUST

    # Check continue
    for pattern in _CONTINUE_PATTERNS:
        if pattern.search(text):
            logger.info("Intent classified as CONTINUE: %r", text[:80])
            return ContinuationIntent.CONTINUE

    # Check new task (explicit abandonment)
    for pattern in _NEW_TASK_PATTERNS:
        if pattern.search(text):
            logger.info("Intent classified as NEW_TASK: %r", text[:80])
            return ContinuationIntent.NEW_TASK

    return ContinuationIntent.NORMAL


# ---------------------------------------------------------------------------
# Plan Position Resolver
# ---------------------------------------------------------------------------

@dataclass
class PlanPosition:
    """Where we are in the work plan, verified against real state."""
    plan_id: str
    plan_title: str
    completed_items: list[dict] = field(default_factory=list)
    in_progress_items: list[dict] = field(default_factory=list)
    pending_items: list[dict] = field(default_factory=list)
    failed_items: list[dict] = field(default_factory=list)

    # Verification results
    verified_files: dict[str, bool] = field(default_factory=dict)  # path -> exists
    git_branch: str | None = None
    git_has_uncommitted: bool = False
    git_recent_commits: list[str] = field(default_factory=list)

    # Computed
    next_item: dict | None = None

    @property
    def total_items(self) -> int:
        return (len(self.completed_items) + len(self.in_progress_items)
                + len(self.pending_items) + len(self.failed_items))

    @property
    def progress_pct(self) -> float:
        if self.total_items == 0:
            return 0.0
        return len(self.completed_items) / self.total_items * 100


async def resolve_plan_position(
    plan: dict[str, Any],
    workspace_dir: str | None = None,
) -> PlanPosition:
    """Cross-reference a work plan with actual git/filesystem state.

    This is the "trust but verify" step. Instead of blindly trusting
    the plan's status fields, we check:
    1. Do files mentioned in completed items actually exist?
    2. What's the current git branch and uncommitted changes?
    3. What recent commits exist?

    Args:
        plan: The work plan dict from the API (with items).
        workspace_dir: Path to the workspace/repo directory.

    Returns:
        PlanPosition with verified state.
    """
    import asyncio
    import os
    import subprocess

    items = plan.get("items", [])

    position = PlanPosition(
        plan_id=plan["id"],
        plan_title=plan.get("title", ""),
    )

    # Categorize items
    for item in sorted(items, key=lambda i: i.get("ordinal", 0)):
        status = item.get("status", "new")
        if status in ("done", "complete", "tested", "approved"):
            position.completed_items.append(item)
        elif status == "in_progress":
            position.in_progress_items.append(item)
        elif status in ("failed",):
            position.failed_items.append(item)
        else:  # new, blocked
            position.pending_items.append(item)

    # Determine next item to work on
    if position.in_progress_items:
        position.next_item = position.in_progress_items[0]
    elif position.pending_items:
        position.next_item = position.pending_items[0]

    # Verify filesystem state
    if workspace_dir and os.path.isdir(workspace_dir):
        # Check files from completed items
        all_files: set[str] = set()
        for item in position.completed_items + position.in_progress_items:
            files = item.get("files_changed", [])
            if isinstance(files, list):
                all_files.update(files)

        for fpath in all_files:
            full = os.path.join(workspace_dir, fpath)
            position.verified_files[fpath] = os.path.exists(full)

        # Check git state
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if result.returncode == 0:
                position.git_branch = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if result.returncode == 0:
                position.git_has_uncommitted = bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if result.returncode == 0:
                position.git_recent_commits = [
                    line.strip() for line in result.stdout.strip().split("\n")
                    if line.strip()
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return position


# ---------------------------------------------------------------------------
# Continuation Context Builder
# ---------------------------------------------------------------------------

def build_continuation_context(
    position: PlanPosition,
    plan: dict[str, Any],
    adjustment: str | None = None,
) -> str:
    """Build a minimal, focused context message for a continuation turn.

    This replaces injecting the entire conversation history. The agent
    gets exactly what it needs to resume work:
    - What's done
    - What's next
    - Current state verification
    - Any adjustment instructions

    Target: ~500-2000 tokens instead of 100K+.
    """
    lines: list[str] = []

    lines.append(f"# Resuming: {position.plan_title}")
    lines.append(f"Plan ID: {position.plan_id}")
    lines.append(f"Progress: {len(position.completed_items)}/{position.total_items} items complete ({position.progress_pct:.0f}%)")
    lines.append("")

    if adjustment:
        lines.append(f"## Adjustment Requested")
        lines.append(adjustment)
        lines.append("")

    # Completed items (brief)
    if position.completed_items:
        lines.append("## Completed")
        for item in position.completed_items:
            lines.append(f"- ✅ {item['title']}")
            if item.get("files_changed"):
                files = item["files_changed"]
                if isinstance(files, list) and files:
                    lines.append(f"  Files: {', '.join(files[:5])}")
        lines.append("")

    # In-progress items (detailed)
    if position.in_progress_items:
        lines.append("## In Progress (resume here)")
        for item in position.in_progress_items:
            lines.append(f"- 🔄 **{item['title']}** (item_id: {item['id']})")
            if item.get("description"):
                lines.append(f"  Context: {item['description'][:500]}")
            if item.get("notes") and isinstance(item["notes"], list):
                recent = item["notes"][-3:]
                for note in recent:
                    if isinstance(note, dict):
                        lines.append(f"  Note: {note.get('text', '')[:200]}")
            if item.get("files_changed"):
                files = item["files_changed"]
                if isinstance(files, list):
                    lines.append(f"  Files changed: {', '.join(files[:10])}")
        lines.append("")

    # Pending items (titles only)
    if position.pending_items:
        lines.append("## Remaining")
        for item in position.pending_items:
            lines.append(f"- ⬜ {item['title']} (item_id: {item['id']})")
        lines.append("")

    # Verification results
    if position.git_branch or position.verified_files:
        lines.append("## State Verification")
        if position.git_branch:
            lines.append(f"- Branch: `{position.git_branch}`")
        if position.git_has_uncommitted:
            lines.append("- ⚠️ Uncommitted changes detected")
        if position.git_recent_commits:
            lines.append("- Recent commits:")
            for commit in position.git_recent_commits[:3]:
                lines.append(f"  - {commit}")
        if position.verified_files:
            missing = [f for f, exists in position.verified_files.items() if not exists]
            if missing:
                lines.append(f"- ⚠️ Expected files not found: {', '.join(missing[:5])}")
            else:
                lines.append(f"- ✅ All {len(position.verified_files)} referenced files verified")
        lines.append("")

    # Instructions
    lines.append("## Instructions")
    if position.next_item:
        lines.append(f"Continue with: **{position.next_item['title']}**")
        lines.append(f"Update its status to `in_progress` with work_plan tool, then implement it.")
    else:
        lines.append("All items appear complete. Verify the plan and mark it done if appropriate.")
    lines.append("")
    lines.append("Do NOT re-read files that were already processed in completed items unless you need to verify something specific.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fallback: No Work Plan — Lightweight Checkpoint
# ---------------------------------------------------------------------------

@dataclass
class LightweightCheckpoint:
    """Minimal state for continuation without a work plan."""
    last_user_request: str = ""
    last_assistant_action: str = ""
    uncommitted_changes: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_todos: list[str] = field(default_factory=list)
    exact_identifiers: dict[str, str] = field(default_factory=dict)


def build_checkpoint_from_history(
    history: list[dict[str, Any]],
    workspace_dir: str | None = None,
) -> LightweightCheckpoint:
    """Build a lightweight checkpoint from conversation history.

    Scans history in reverse to extract the essential state.
    Inspired by OpenClaw's structured compaction sections.

    Args:
        history: The conversation message history.
        workspace_dir: Path to check git status.

    Returns:
        LightweightCheckpoint with ~500 tokens of context.
    """
    import os
    import subprocess

    checkpoint = LightweightCheckpoint()

    # Scan in reverse for last user request and assistant action
    for msg in reversed(history):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content or not isinstance(content, str):
            continue

        if role == "user" and not checkpoint.last_user_request:
            checkpoint.last_user_request = content[:500]
        elif role == "assistant" and not checkpoint.last_assistant_action:
            checkpoint.last_assistant_action = content[:500]

        if checkpoint.last_user_request and checkpoint.last_assistant_action:
            break

    # Check git state for uncommitted changes
    if workspace_dir and os.path.isdir(workspace_dir):
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                checkpoint.uncommitted_changes = [
                    f.strip() for f in result.stdout.strip().split("\n") if f.strip()
                ][:10]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return checkpoint


def format_checkpoint_context(checkpoint: LightweightCheckpoint) -> str:
    """Format a lightweight checkpoint as context for a continuation turn.

    Target: ~500 tokens.
    """
    lines = ["# Resuming Previous Work", ""]

    if checkpoint.last_user_request:
        lines.append("## Last Request")
        lines.append(checkpoint.last_user_request)
        lines.append("")

    if checkpoint.last_assistant_action:
        lines.append("## Last Action")
        lines.append(checkpoint.last_assistant_action)
        lines.append("")

    if checkpoint.uncommitted_changes:
        lines.append("## Uncommitted Changes")
        for f in checkpoint.uncommitted_changes:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.decisions:
        lines.append("## Decisions Made")
        for d in checkpoint.decisions:
            lines.append(f"- {d}")
        lines.append("")

    if checkpoint.open_todos:
        lines.append("## Open TODOs")
        for t in checkpoint.open_todos:
            lines.append(f"- {t}")
        lines.append("")

    if checkpoint.exact_identifiers:
        lines.append("## Exact Identifiers")
        for key, val in checkpoint.exact_identifiers.items():
            lines.append(f"- {key}: `{val}`")
        lines.append("")

    lines.append("## Instructions")
    lines.append("Pick up where you left off. Check the current file/git state before making changes.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Iteration Budget Tracker
# ---------------------------------------------------------------------------

@dataclass
class IterationBudget:
    """Tracks iteration usage and enforces budget thresholds."""
    total: int
    used: int = 0

    # Threshold percentages
    CHECKPOINT_PCT: float = 0.50
    WRAP_UP_PCT: float = 0.80
    STOP_PCT: float = 0.95

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    @property
    def pct_used(self) -> float:
        if self.total == 0:
            return 1.0
        return self.used / self.total

    @property
    def should_checkpoint(self) -> bool:
        """At 50%: checkpoint current progress."""
        return self.pct_used >= self.CHECKPOINT_PCT

    @property
    def should_wrap_up(self) -> bool:
        """At 80%: stop accepting new work, finish current item."""
        return self.pct_used >= self.WRAP_UP_PCT

    @property
    def should_stop(self) -> bool:
        """At 95%: write final checkpoint, do not start new items."""
        return self.pct_used >= self.STOP_PCT

    def tick(self) -> None:
        """Record one iteration used."""
        self.used += 1

    def get_budget_message(self) -> str | None:
        """Get a budget-awareness message if at a threshold, or None."""
        if self.should_stop:
            return (
                f"BUDGET CRITICAL: {self.used}/{self.total} iterations used ({self.pct_used:.0%}). "
                f"Do NOT start new work items. Save your checkpoint and wrap up."
            )
        if self.should_wrap_up:
            return (
                f"BUDGET WARNING: {self.used}/{self.total} iterations used ({self.pct_used:.0%}). "
                f"Finish your current item and checkpoint. Do not start new items."
            )
        if self.should_checkpoint:
            return (
                f"BUDGET NOTE: {self.used}/{self.total} iterations used ({self.pct_used:.0%}). "
                f"Consider checkpointing your current progress."
            )
        return None
