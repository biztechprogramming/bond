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
import subprocess
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

    # Context index stats (Design Doc 075)
    index_stats: dict[str, int] = field(default_factory=dict)

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
    checkpoint: LightweightCheckpoint | None = None,
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
    # When both a checkpoint and plan position exist, merge them
    if checkpoint is not None:
        _merge_plan_into_checkpoint(checkpoint, position)
        # Include checkpoint details in the plan context below
        # (the merged checkpoint's open_todos and progress_summary are used)

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
    # Context index stats (Design Doc 075)
    if position.index_stats:
        lines.append("## Indexed Content")
        sources = position.index_stats.get("sources", 0)
        chunks = position.index_stats.get("chunks", 0)
        if sources > 0:
            lines.append(
                f"Previous session indexed {sources} tool output{'s' if sources != 1 else ''} "
                f"({chunks} search chunks available)."
            )
            lines.append("Use ctx_search to find specific details from prior work.")
        lines.append("")

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
class ToolCallRecord:
    """Record of a single tool call for checkpoint tracking."""
    tool_name: str
    success: bool
    output_summary: str  # Truncated to ~200 chars
    timestamp: str = ""  # ISO 8601
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output_summary": self.output_summary,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCallRecord":
        return cls(
            tool_name=data.get("tool_name", ""),
            success=data.get("success", False),
            output_summary=data.get("output_summary", ""),
            timestamp=data.get("timestamp", ""),
            duration_ms=data.get("duration_ms", 0),
        )


@dataclass
class LightweightCheckpoint:
    """Minimal state for continuation without a work plan."""
    last_user_request: str = ""
    last_assistant_action: str = ""
    uncommitted_changes: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    open_todos: list[str] = field(default_factory=list)
    exact_identifiers: dict[str, str] = field(default_factory=dict)

    # Progress checkpointing fields (Design Doc 096)
    completed_actions: list[ToolCallRecord] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    failed_approaches: list[str] = field(default_factory=list)
    progress_summary: str = ""
    stop_reason: str = ""
    turn_number: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    git_branch: str = ""
    git_head_sha: str = ""

    def capture_git_state(self, workspace_dir: str) -> None:
        """Capture current git branch and HEAD SHA."""
        import subprocess
        try:
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if branch.returncode == 0:
                self.git_branch = branch.stdout.strip()
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if head.returncode == 0:
                self.git_head_sha = head.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def to_dict(self) -> dict:
        """Serialize to dict for JSON persistence."""
        return {
            "last_user_request": self.last_user_request,
            "last_assistant_action": self.last_assistant_action,
            "uncommitted_changes": self.uncommitted_changes,
            "decisions": self.decisions,
            "open_todos": self.open_todos,
            "completed_actions": [a.to_dict() for a in self.completed_actions],
            "files_modified": self.files_modified,
            "failed_approaches": self.failed_approaches,
            "progress_summary": self.progress_summary,
            "stop_reason": self.stop_reason,
            "turn_number": self.turn_number,
            "successful_tool_calls": self.successful_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "git_branch": self.git_branch,
            "git_head_sha": self.git_head_sha,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LightweightCheckpoint":
        """Deserialize from dict."""
        cp = cls()
        for key in ["last_user_request", "last_assistant_action", "progress_summary",
                     "stop_reason", "git_branch", "git_head_sha"]:
            if key in data:
                setattr(cp, key, data[key])
        for key in ["uncommitted_changes", "decisions", "open_todos",
                     "files_modified", "failed_approaches"]:
            if key in data:
                setattr(cp, key, data[key])
        for key in ["turn_number", "successful_tool_calls", "failed_tool_calls"]:
            if key in data:
                setattr(cp, key, data[key])
        if "completed_actions" in data:
            cp.completed_actions = [ToolCallRecord.from_dict(a) for a in data["completed_actions"]]
        return cp


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


def format_checkpoint_context(
    checkpoint: LightweightCheckpoint,
    max_tokens: int = 2000,
) -> str:
    """Format a lightweight checkpoint as context for a continuation turn.

    Token-budget-aware with truncation tiers (Design Doc 096):
    - >=1500: full checkpoint
    - 800-1500: medium (summary, failed approaches, TODOs, files)
    - 300-800: minimal (summary, failed approaches, TODOs)
    - <300: ultra-minimal (one-line summary + failed approaches)
    """
    if max_tokens < 300:
        # Ultra-minimal
        lines = ["# Resuming Previous Work"]
        if checkpoint.progress_summary:
            lines.append(checkpoint.progress_summary)
        if checkpoint.failed_approaches:
            lines.append("Failed: " + "; ".join(checkpoint.failed_approaches[:3]))
        return "\n".join(lines)

    if max_tokens < 800:
        # Minimal
        lines = ["# Resuming Previous Work", ""]
        if checkpoint.progress_summary:
            lines.append(f"**Progress:** {checkpoint.progress_summary}")
            lines.append("")
        if checkpoint.failed_approaches:
            lines.append("## Failed Approaches")
            for f in checkpoint.failed_approaches[:5]:
                lines.append(f"- {f}")
            lines.append("")
        if checkpoint.open_todos:
            lines.append("## Open TODOs")
            for t in checkpoint.open_todos[:5]:
                lines.append(f"- {t}")
            lines.append("")
        lines.append("Pick up where you left off.")
        return "\n".join(lines)

    if max_tokens < 1500:
        # Medium
        lines = ["# Resuming Previous Work", ""]
        if checkpoint.progress_summary:
            lines.append(f"**Progress:** {checkpoint.progress_summary}")
            lines.append("")
        if checkpoint.failed_approaches:
            lines.append("## Failed Approaches")
            for f in checkpoint.failed_approaches[:5]:
                lines.append(f"- {f}")
            lines.append("")
        if checkpoint.open_todos:
            lines.append("## Open TODOs")
            for t in checkpoint.open_todos[:10]:
                lines.append(f"- {t}")
            lines.append("")
        if checkpoint.files_modified:
            lines.append("## Files Modified")
            for f in checkpoint.files_modified[:10]:
                lines.append(f"- {f}")
            lines.append("")
        if checkpoint.git_branch:
            lines.append(f"Branch: `{checkpoint.git_branch}`")
        lines.append("")
        lines.append("Pick up where you left off. Check current file/git state before making changes.")
        return "\n".join(lines)

    # Full checkpoint (>=1500 tokens budget)
    lines = ["# Resuming Previous Work", ""]

    if checkpoint.progress_summary:
        lines.append(f"**Progress:** {checkpoint.progress_summary}")
        lines.append(f"**Stop reason:** {checkpoint.stop_reason}")
        lines.append("")

    if checkpoint.last_user_request:
        lines.append("## Last Request")
        lines.append(checkpoint.last_user_request)
        lines.append("")

    if checkpoint.last_assistant_action:
        lines.append("## Last Action")
        lines.append(checkpoint.last_assistant_action)
        lines.append("")

    if checkpoint.completed_actions:
        lines.append("## Recent Actions")
        for a in checkpoint.completed_actions[-10:]:
            status = "ok" if a.success else "FAILED"
            lines.append(f"- [{status}] {a.tool_name}: {a.output_summary}")
        lines.append("")

    if checkpoint.files_modified:
        lines.append("## Files Modified")
        for f in checkpoint.files_modified:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.uncommitted_changes:
        lines.append("## Uncommitted Changes")
        for f in checkpoint.uncommitted_changes:
            lines.append(f"- {f}")
        lines.append("")

    if checkpoint.failed_approaches:
        lines.append("## Failed Approaches")
        for f in checkpoint.failed_approaches:
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

    if checkpoint.git_branch or checkpoint.git_head_sha:
        lines.append("## Git State")
        if checkpoint.git_branch:
            lines.append(f"- Branch: `{checkpoint.git_branch}`")
        if checkpoint.git_head_sha:
            lines.append(f"- HEAD: `{checkpoint.git_head_sha[:12]}`")
        lines.append("")

    lines.append("## Instructions")
    lines.append("Pick up where you left off. Check the current file/git state before making changes.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checkpoint Persistence (Design Doc 096)
# ---------------------------------------------------------------------------


async def save_checkpoint(
    conversation_id: str,
    agent_id: str,
    checkpoint: LightweightCheckpoint,
) -> None:
    """Persist checkpoint to SpacetimeDB via PersistenceClient."""
    from backend.app.agent.persistence_client import PersistenceClient
    import json
    from datetime import datetime, timezone, timedelta

    client = PersistenceClient(conversation_id=conversation_id)
    await client.init()
    try:
        now = datetime.now(timezone.utc)
        await client.upsert_checkpoint(
            checkpoint_id=conversation_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            data=json.dumps(checkpoint.to_dict()),
            stop_reason=checkpoint.stop_reason,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
        )
    finally:
        await client.close()


async def load_checkpoint(conversation_id: str) -> LightweightCheckpoint | None:
    """Load checkpoint from SpacetimeDB."""
    from backend.app.agent.persistence_client import PersistenceClient
    import json

    client = PersistenceClient(conversation_id=conversation_id)
    await client.init()
    try:
        data = await client.get_checkpoint(conversation_id)
        if data and "data" in data:
            return LightweightCheckpoint.from_dict(json.loads(data["data"]))
        return None
    finally:
        await client.close()


def _check_git_divergence(
    checkpoint: LightweightCheckpoint,
    workspace_dir: str,
) -> bool:
    """Return True if git state has diverged enough to invalidate the checkpoint."""
    if not checkpoint.git_branch and not checkpoint.git_head_sha:
        return False  # No git state recorded — can't check
    try:
        # Check branch
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=workspace_dir, timeout=5,
        )
        if branch.returncode == 0 and checkpoint.git_branch:
            if branch.stdout.strip() != checkpoint.git_branch:
                return True  # Different branch
        # Check HEAD distance
        if checkpoint.git_head_sha:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=workspace_dir, timeout=5,
            )
            if head.returncode == 0 and head.stdout.strip() != checkpoint.git_head_sha:
                # Check how many commits apart
                distance = subprocess.run(
                    ["git", "rev-list", "--count", f"{checkpoint.git_head_sha}..HEAD"],
                    capture_output=True, text=True, cwd=workspace_dir, timeout=5,
                )
                if distance.returncode == 0 and int(distance.stdout.strip()) > 5:
                    return True  # More than 5 commits apart
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return False


def _merge_plan_into_checkpoint(
    checkpoint: LightweightCheckpoint,
    plan_position: PlanPosition,
) -> LightweightCheckpoint:
    """Merge work plan progress into checkpoint for unified resumption context.

    The plan provides structure (what items exist, which are done).
    The checkpoint provides details (what was tried, what failed, what files changed).
    """
    # Add plan items as TODOs (replacing checkpoint's open_todos with plan-aware ones)
    plan_todos: list[str] = []
    for item in plan_position.pending_items:
        plan_todos.append(f"[Plan item {item.get('ordinal', '?')}] {item.get('title', '')}")
    if plan_position.in_progress_items:
        current = plan_position.in_progress_items[0]
        plan_todos.insert(0, f"[IN PROGRESS] {current.get('title', '')}")

    # Plan TODOs take precedence over checkpoint TODOs (more structured)
    if plan_todos:
        checkpoint.open_todos = plan_todos

    # Add plan progress to summary
    completed_count = len(plan_position.completed_items)
    plan_summary = (
        f"Work plan: {completed_count}/{plan_position.total_items} items complete "
        f"({plan_position.progress_pct:.0f}%)"
    )
    if checkpoint.progress_summary:
        checkpoint.progress_summary = f"{plan_summary}\n{checkpoint.progress_summary}"
    else:
        checkpoint.progress_summary = plan_summary

    return checkpoint


async def delete_checkpoint(conversation_id: str) -> None:
    """Delete checkpoint after successful completion."""
    from backend.app.agent.persistence_client import PersistenceClient

    client = PersistenceClient(conversation_id=conversation_id)
    await client.init()
    try:
        await client.delete_checkpoint(conversation_id)
    finally:
        await client.close()


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
    NUDGE_PCT: float = 0.65
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
    def should_nudge(self) -> bool:
        """At 65%: gentle reminder to start wrapping up."""
        return self.pct_used >= self.NUDGE_PCT

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
                f"URGENT — You are at {self.used}/{self.total} iterations ({self.pct_used:.0%}) and approaching your limit. "
                f"You MUST act NOW to avoid termination.\n\n"
                f"If you have remaining work that requires code changes, you MUST delegate it to a coding_agent RIGHT NOW. To do this:\n"
                f"1. Summarize everything you've learned and accomplished so far\n"
                f"2. Write a detailed, self-contained task description for the coding_agent that includes: "
                f"what files to modify, what changes to make, what the expected behavior should be, and any context/patterns you've discovered\n"
                f"3. Call the coding_agent tool with this task description\n"
                f"4. Then use the respond tool to tell the user what you accomplished directly and what you delegated\n\n"
                f"If no code changes remain, use the respond tool NOW to give the user a complete answer with everything you've found.\n\n"
                f"DO NOT continue exploring or reading more files. DO NOT make any more tool calls except coding_agent or respond."
            )
        if self.should_wrap_up:
            return (
                f"URGENT — You are at {self.used}/{self.total} iterations ({self.pct_used:.0%}) and approaching your limit. "
                f"You MUST act NOW to avoid termination.\n\n"
                f"If you have remaining work that requires code changes, you MUST delegate it to a coding_agent RIGHT NOW. To do this:\n"
                f"1. Summarize everything you've learned and accomplished so far\n"
                f"2. Write a detailed, self-contained task description for the coding_agent that includes: "
                f"what files to modify, what changes to make, what the expected behavior should be, and any context/patterns you've discovered\n"
                f"3. Call the coding_agent tool with this task description\n"
                f"4. Then use the respond tool to tell the user what you accomplished directly and what you delegated\n\n"
                f"If no code changes remain, use the respond tool NOW to give the user a complete answer with everything you've found.\n\n"
                f"DO NOT continue exploring or reading more files. DO NOT make any more tool calls except coding_agent or respond."
            )
        if self.should_nudge:
            return (
                f"You've used {self.pct_used:.0%} of your iteration budget ({self.used}/{self.total}). "
                f"Start wrapping up. If the remaining work involves code changes you haven't started yet, "
                f"consider delegating to a coding_agent rather than trying to do everything yourself."
            )
        if self.should_checkpoint:
            return (
                f"BUDGET NOTE: {self.used}/{self.total} iterations used ({self.pct_used:.0%}). "
                f"Consider checkpointing your current progress."
            )
        return None
