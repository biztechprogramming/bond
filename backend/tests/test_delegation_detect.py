"""Tests for explicit delegation detection."""

import pytest
from backend.app.agent.delegation_detect import detect_explicit_delegation


class TestDetectExplicitDelegation:
    """Test cases for detect_explicit_delegation."""

    @pytest.mark.parametrize("message", [
        "Can you delegate this to a coding agent?",
        "Delegate this investigation to a coding agent",
        "Please delegate to a coding agent",
        "delegate this to claude code",
        "Can you delegate this to a sub-agent?",
        "Send this to a coding agent",
        "Hand this off to a coding agent",
        "Hand off to a coding agent",
        "Use a coding agent for this",
        "Spawn a coding agent to handle this",
        "Have a coding agent do this",
        "Let a coding agent handle this",
        "Could you delegate this to a coding agent?",
        "Run a coding agent on this",
        "Launch a coding agent",
        "Use Claude Code for this",
        "Pass this to a coding agent",
        "Give this to a coding agent",
        "Have a sub-agent fix this",
        "Let a sub-agent implement this",
        "Can you delegate this investigation to a coding agent?",
    ])
    def test_positive_matches(self, message: str):
        assert detect_explicit_delegation(message) is True, f"Should match: {message!r}"

    @pytest.mark.parametrize("message", [
        "What is a coding agent?",
        "How does the coding agent work?",
        "Tell me about Claude Code",
        "Fix the bug in the login page",
        "Implement rate limiting",
        "Can you help me with this?",
        "Read the file at backend/app/worker.py",
        "",
        "   ",
        "continue",
        "yes",
        "Build a REST API",
        "The coding agent seems broken",
        "I heard about coding agents",
    ])
    def test_negative_matches(self, message: str):
        assert detect_explicit_delegation(message) is False, f"Should NOT match: {message!r}"
