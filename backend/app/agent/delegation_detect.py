"""Detect explicit user requests to delegate work to a coding agent.

When a user mid-conversation says something like "delegate this to a coding agent"
or "can you send this to Claude Code", we should honor that immediately rather
than burning iteration budget on investigation.
"""

from __future__ import annotations

import re

# Patterns that indicate the user wants immediate delegation.
# These are intentionally specific — we don't want false positives.
_DELEGATION_PATTERNS: list[re.Pattern[str]] = [
    # Direct delegation requests
    re.compile(r"delegate\s+.{0,30}(to|for)\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)", re.IGNORECASE),
    re.compile(r"send\s+(this|it)?\s*(to|over\s+to)\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)", re.IGNORECASE),
    re.compile(r"hand\s+(this|it)?\s*(off|over)\s*(to\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex))?", re.IGNORECASE),
    re.compile(r"(use|spawn|start|run|launch)\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)", re.IGNORECASE),
    re.compile(r"have\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)\s+(do|handle|take|work|finish|implement|fix)", re.IGNORECASE),
    re.compile(r"let\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)\s+(do|handle|take|work|finish|implement|fix)", re.IGNORECASE),
    re.compile(r"(can|could|would)\s+you\s+delegate", re.IGNORECASE),
    re.compile(r"pass\s+(this|it)\s+(to|along\s+to)\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)", re.IGNORECASE),
    # "give this to a coding agent"
    re.compile(r"give\s+(this|it)\s+to\s+(a\s+)?(coding\s+agent|sub.?agent|claude\s+code|codex)", re.IGNORECASE),
]


def detect_explicit_delegation(user_message: str) -> bool:
    """Return True if the user is explicitly asking to delegate to a coding agent.

    This is intentionally conservative — only matches clear, unambiguous
    delegation requests. General mentions of coding agents (e.g. "what is
    a coding agent?") should NOT match.

    Args:
        user_message: The current user message.

    Returns:
        True if the user wants immediate delegation.
    """
    text = user_message.strip()
    if not text:
        return False

    for pattern in _DELEGATION_PATTERNS:
        if pattern.search(text):
            return True

    return False
