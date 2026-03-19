"""Outcome signal collection and composite scoring.

Design Doc 049: Closed-Loop Optimization Engine — Section 1.

Collects structured outcome signals from each agent turn and computes
a composite quality score (0.0–1.0).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("bond.agent.outcome")

# ---------------------------------------------------------------------------
# Category baselines (bootstrap values — updated from actual data later)
# ---------------------------------------------------------------------------

CATEGORY_BASELINES: dict[str, dict[str, float]] = {
    "coding": {"median_cost": 0.08, "median_iterations": 6, "median_tools": 8},
    "research": {"median_cost": 0.04, "median_iterations": 4, "median_tools": 5},
    "chat": {"median_cost": 0.01, "median_iterations": 1, "median_tools": 0},
    "file_ops": {"median_cost": 0.03, "median_iterations": 3, "median_tools": 4},
}

# ---------------------------------------------------------------------------
# User correction detection
# ---------------------------------------------------------------------------

_CORRECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bthat'?s?\s+(not|wrong|incorrect)\b",
        r"\b(try|do)\s+(it\s+)?again\b",
        r"^no[,.]?\s",
        r"\bi\s+(said|meant|asked|wanted)\b",
        r"\bactually[,]?\s",
        r"\bstop\b",
        r"\bwrong\s+(file|path|dir|answer|approach)\b",
        r"\bundo\b",
        r"\brevert\b",
    ]
]

_CORRECTION_ANTI_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bno\s+(problem|worries|rush|issue|need)\b",
        r"\bnothing\s+wrong\b",
        r"\bnot\s+bad\b",
        r"\bno\s+thanks?\b",
    ]
]


def detect_user_correction(user_message: str) -> bool:
    """Heuristic: does a user message indicate correction of the prior turn?"""
    if not user_message or len(user_message) < 3:
        return False
    # Only check the first 500 chars — corrections are usually at the start
    snippet = user_message[:500]
    # Anti-patterns override correction signals
    for ap in _CORRECTION_ANTI_PATTERNS:
        if ap.search(snippet):
            return False
    for p in _CORRECTION_PATTERNS:
        if p.search(snippet):
            return True
    return False


# ---------------------------------------------------------------------------
# Task category classification (simple heuristic)
# ---------------------------------------------------------------------------


CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "coding": {
        "code", "function", "bug", "error", "compile", "test", "refactor",
        "implement", "class", "variable", "debug", "fix", "pr", "commit",
    },
    "research": {
        "search", "find", "look up", "what is", "explain", "compare",
        "summarize", "article", "paper", "documentation",
    },
    "file_ops": {
        "file", "read", "write", "create", "delete", "move", "copy",
        "rename", "directory", "folder", "path",
    },
    "chat": set(),
}


def classify_task(user_message: str, tool_names: list[str]) -> str:
    """Classify the turn's task category from the user message and tools used."""
    msg_lower = (user_message or "").lower()

    # Tool-based classification is the strongest signal
    code_tools = {"code_execute", "file_write", "file_edit", "coding_agent"}
    file_tools = {"file_read", "file_write", "file_edit", "file_list"}
    research_tools = {"web_search", "web_fetch", "memory_search"}

    tool_set = set(tool_names)

    if tool_set & code_tools:
        return "coding"
    if tool_set & research_tools:
        return "research"
    if tool_set & file_tools and not (tool_set & code_tools):
        return "file_ops"

    # Keyword scoring fallback
    best_category = "chat"
    best_score = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        if not keywords:
            continue
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > best_score:
            best_score = score
            best_category = category

    return best_category


# ---------------------------------------------------------------------------
# Composite outcome score
# ---------------------------------------------------------------------------


def compute_outcome_score(signals: dict[str, Any]) -> float:
    """Compute a composite quality score (0.0–1.0) from outcome signals.

    Lower cost, fewer loops, no user corrections = higher score.
    """
    score = 1.0

    # Penalties
    if signals.get("had_loop_intervention"):
        score -= 0.3
    if signals.get("user_correction"):
        score -= 0.4
    if signals.get("had_continuation"):
        score -= 0.1

    # Efficiency bonus/penalty relative to task category baseline
    category = signals.get("task_category", "chat")
    baseline = CATEGORY_BASELINES.get(category, {})
    if baseline:
        median_cost = max(baseline.get("median_cost", 0.001), 0.001)
        cost_ratio = signals.get("total_cost", 0.0) / median_cost
        if cost_ratio > 2.0:
            score -= 0.2  # 2x more expensive than typical
        elif cost_ratio < 0.5:
            score += 0.1  # Notably efficient

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Signal collection helper
# ---------------------------------------------------------------------------


def collect_signals(
    *,
    tool_calls: int = 0,
    iterations: int = 0,
    total_cost: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    wall_time_ms: int = 0,
    had_loop_intervention: bool = False,
    had_continuation: bool = False,
    had_compression: bool = False,
    fragments_selected: int = 0,
    fragment_names: list[str] | None = None,
    user_correction: bool = False,
    task_category: str = "chat",
    user_message_preview: str = "",
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """Package all outcome signals into a structured dict.

    Also computes the composite outcome_score.
    """
    signals: dict[str, Any] = {
        "tool_calls": tool_calls,
        "iterations": iterations,
        "total_cost": total_cost,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "wall_time_ms": wall_time_ms,
        "had_loop_intervention": had_loop_intervention,
        "had_continuation": had_continuation,
        "had_compression": had_compression,
        "fragments_selected": fragments_selected,
        "fragment_names": fragment_names or [],
        "user_correction": user_correction,
        "task_category": task_category,
        "user_message_preview": user_message_preview,
        "tool_names": tool_names or [],
    }
    signals["outcome_score"] = compute_outcome_score(signals)
    return signals
