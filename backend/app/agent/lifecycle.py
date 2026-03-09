"""Lifecycle-triggered prompt injection (Doc 024, Phase 2 of Doc 027).

Detects the agent's current work phase from observable tool call signals
and loads the corresponding Tier 2 fragments from the manifest.

Phases are detected heuristically — no LLM call needed because tool calls
are unambiguous (e.g. ``git commit`` is always a commit).

Usage in the worker turn loop::

    state = LifecycleState(
        turn_number=turn_number,
        last_tool_calls=tool_call_strings_this_turn,
    )
    phase = detect_phase(state)
    if phase != Phase.IDLE:
        fragments = load_lifecycle_fragments(phase, prompts_dir)
        # inject into next turn's system prompt
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from backend.app.agent.manifest import FragmentMeta, get_tier2_fragments, load_manifest

logger = logging.getLogger("bond.agent.lifecycle")


# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------


class Phase(Enum):
    """Lifecycle phases the agent can be in during a work session."""

    PLANNING = auto()
    IMPLEMENTING = auto()
    COMMITTING = auto()
    PUSHING = auto()
    REVIEWING = auto()
    IDLE = auto()


# Phase name → manifest phase string (manifest uses lowercase names)
_PHASE_TO_MANIFEST: dict[Phase, str] = {
    Phase.PLANNING: "planning",
    Phase.IMPLEMENTING: "implementing",
    Phase.COMMITTING: "committing",
    Phase.PUSHING: "pushing",
    Phase.REVIEWING: "reviewing",
}


# ---------------------------------------------------------------------------
# Lifecycle state
# ---------------------------------------------------------------------------


@dataclass
class LifecycleState:
    """Observable signals used to detect the current lifecycle phase."""

    turn_number: int = 0
    last_tool_calls: list[str] = field(default_factory=list)
    has_work_plan: bool = False
    work_plan_status: str | None = None  # "in_progress", "done", etc.


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------


def detect_phase(state: LifecycleState) -> Phase:
    """Detect the current lifecycle phase from observable tool call signals.

    Priority order matters — committing overrides implementing, because if
    the agent is committing it's done implementing for now. We check the
    most specific (highest-priority) phases first.
    """
    last_tools = state.last_tool_calls

    # Check most specific phases first — scan ALL tool calls for each phase
    # in priority order (reviewing > pushing > committing) so that a PR
    # creation in the second tool call isn't masked by a push in the first.
    if any(_is_pr_create(tc) for tc in last_tools):
        return Phase.REVIEWING
    if any(_is_git_push(tc) for tc in last_tools):
        return Phase.PUSHING
    if any(_is_git_commit(tc) for tc in last_tools):
        return Phase.COMMITTING

    # Implementation phase: agent is editing files or executing code
    for tool_call in last_tools:
        if _is_implementation(tool_call):
            return Phase.IMPLEMENTING

    # Planning phase: early in conversation or creating/updating plans
    if state.turn_number <= 3 and not last_tools:
        return Phase.PLANNING
    if any(_is_planning(tc) for tc in last_tools):
        return Phase.PLANNING

    return Phase.IDLE


# ---------------------------------------------------------------------------
# Signal matchers
# ---------------------------------------------------------------------------

# Signals for git commit detection
_COMMIT_SIGNALS = ("git commit", "git add", "git stage")

# Signals for git push detection
_PUSH_SIGNALS = ("git push",)

# Signals for PR creation / review detection
_PR_SIGNALS = ("gh pr create", "gh pr review", "gh pr merge")

# Signals for implementation tool detection
_IMPL_TOOLS = ("file_edit", "code_execute", "file_write")

# Signals for planning tool detection
_PLAN_TOOLS = ("work_plan",)


def _is_git_commit(tool_call: str) -> bool:
    """Check if a tool call involves git commit/add/stage."""
    lower = tool_call.lower()
    # Must be a shell execution tool, not just any tool that mentions git
    if "code_execute" not in lower and "shell" not in lower:
        return False
    return any(s in lower for s in _COMMIT_SIGNALS)


def _is_git_push(tool_call: str) -> bool:
    """Check if a tool call involves git push."""
    lower = tool_call.lower()
    if "code_execute" not in lower and "shell" not in lower:
        return False
    return any(s in lower for s in _PUSH_SIGNALS)


def _is_pr_create(tool_call: str) -> bool:
    """Check if a tool call involves PR creation or review."""
    lower = tool_call.lower()
    return any(s in lower for s in _PR_SIGNALS)


def _is_implementation(tool_call: str) -> bool:
    """Check if a tool call is an implementation action (file edit, code exec)."""
    return any(t in tool_call for t in _IMPL_TOOLS)


def _is_planning(tool_call: str) -> bool:
    """Check if a tool call is a planning action."""
    return any(t in tool_call for t in _PLAN_TOOLS)


# ---------------------------------------------------------------------------
# Pre-commit check (for pre-execution injection)
# ---------------------------------------------------------------------------


def is_git_commit_command(tool_name: str, tool_args: dict) -> bool:
    """Check if a specific tool call is about to execute a git commit.

    This is used for the pre-commit hook — injecting git guidance right
    before the commit executes, not just between turns.

    Args:
        tool_name: The tool being called (e.g. 'code_execute')
        tool_args: The tool arguments dict

    Returns:
        True if this tool call will execute a git commit
    """
    if tool_name != "code_execute":
        return False
    code = tool_args.get("code", "")
    lower = code.lower()
    return "git commit" in lower or ("git add" in lower and "git commit" in lower)


def is_git_push_command(tool_name: str, tool_args: dict) -> bool:
    """Check if a specific tool call is about to execute a git push."""
    if tool_name != "code_execute":
        return False
    code = tool_args.get("code", "")
    return "git push" in code.lower()


def is_pr_create_command(tool_name: str, tool_args: dict) -> bool:
    """Check if a specific tool call is about to create a PR."""
    if tool_name != "code_execute":
        return False
    code = tool_args.get("code", "")
    return "gh pr create" in code.lower()


# ---------------------------------------------------------------------------
# Fragment loading
# ---------------------------------------------------------------------------


def load_lifecycle_fragments(
    phase: Phase, prompts_dir: Path
) -> list[FragmentMeta]:
    """Load Tier 2 fragments for a given lifecycle phase from the manifest.

    Reads from prompts/manifest.yaml — fragments are files on disk, not
    database rows. The manifest maps each file to its lifecycle phase.

    Args:
        phase: Current detected lifecycle phase
        prompts_dir: Path to the prompts directory (e.g. ~/bond/prompts/)

    Returns:
        List of FragmentMeta with content loaded from disk. Empty list for IDLE.
    """
    if phase == Phase.IDLE:
        return []

    manifest_phase = _PHASE_TO_MANIFEST.get(phase)
    if not manifest_phase:
        return []

    manifest = load_manifest(prompts_dir)
    fragments = get_tier2_fragments(manifest, manifest_phase)

    if fragments:
        logger.info(
            "Lifecycle fragments for %s: %s (%d total tokens)",
            phase.name,
            [f.path for f in fragments],
            sum(f.token_estimate for f in fragments),
        )

    return fragments


def format_lifecycle_injection(phase: Phase, fragments: list[FragmentMeta]) -> str:
    """Format lifecycle fragments for injection into the system prompt.

    Returns a formatted string ready to append to the system prompt, or
    an empty string if no fragments are provided.
    """
    if not fragments:
        return ""

    content_parts = [f.content for f in fragments if f.content]
    if not content_parts:
        return ""

    joined = "\n\n---\n\n".join(content_parts)
    return f"\n\n## Current Phase: {phase.name}\n{joined}"


def format_precommit_injection(fragments: list[FragmentMeta]) -> str:
    """Format fragments for pre-commit injection (injected as a system message
    right before a git commit executes).

    Returns a formatted string, or empty string if no fragments.
    """
    if not fragments:
        return ""

    content_parts = [f.content for f in fragments if f.content]
    if not content_parts:
        return ""

    joined = "\n\n---\n\n".join(content_parts)
    return (
        "## Before You Commit\n"
        f"{joined}\n\n"
        "Review your staged changes with `git diff --cached` before committing.\n"
        "Ensure your commit follows the format above."
    )
