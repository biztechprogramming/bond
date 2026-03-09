"""Tests for lifecycle-triggered prompt injection (Doc 024).

Covers:
- Phase detection from tool call signals
- Pre-commit / pre-push / pre-PR command detection
- Fragment loading for each phase
- Injection formatting
- Edge cases (empty tool calls, mixed signals, etc.)
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.agent.lifecycle import (
    LifecycleState,
    Phase,
    detect_phase,
    format_lifecycle_injection,
    format_precommit_injection,
    is_git_commit_command,
    is_git_push_command,
    is_pr_create_command,
    load_lifecycle_fragments,
)
from backend.app.agent.manifest import FragmentMeta, invalidate_cache


# ---------------------------------------------------------------------------
# Phase detection tests
# ---------------------------------------------------------------------------


class TestDetectPhase:
    """Tests for detect_phase() heuristic."""

    def test_idle_no_tool_calls_late_turn(self):
        """No tool calls on a late turn → IDLE."""
        state = LifecycleState(turn_number=10, last_tool_calls=[])
        assert detect_phase(state) == Phase.IDLE

    def test_planning_early_turn_no_tools(self):
        """Early turn with no tool calls → PLANNING."""
        state = LifecycleState(turn_number=1, last_tool_calls=[])
        assert detect_phase(state) == Phase.PLANNING

    def test_planning_early_turn_2(self):
        state = LifecycleState(turn_number=3, last_tool_calls=[])
        assert detect_phase(state) == Phase.PLANNING

    def test_planning_explicit_work_plan(self):
        """work_plan tool call → PLANNING regardless of turn number."""
        state = LifecycleState(
            turn_number=15,
            last_tool_calls=['work_plan:{"action": "create_plan", "title": "Add feature"}'],
        )
        assert detect_phase(state) == Phase.PLANNING

    def test_implementing_file_edit(self):
        state = LifecycleState(
            turn_number=5,
            last_tool_calls=['file_edit:{"path": "src/main.py", "old_text": "x", "new_text": "y"}'],
        )
        assert detect_phase(state) == Phase.IMPLEMENTING

    def test_implementing_file_write(self):
        state = LifecycleState(
            turn_number=5,
            last_tool_calls=['file_write:{"path": "src/new.py", "content": "print(1)"}'],
        )
        assert detect_phase(state) == Phase.IMPLEMENTING

    def test_implementing_code_execute_non_git(self):
        """code_execute that isn't git → IMPLEMENTING."""
        state = LifecycleState(
            turn_number=5,
            last_tool_calls=['code_execute:{"code": "python -m pytest"}'],
        )
        assert detect_phase(state) == Phase.IMPLEMENTING

    def test_committing_git_commit(self):
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['code_execute:{"code": "git add . && git commit -m \\"feat: add thing\\""}'],
        )
        assert detect_phase(state) == Phase.COMMITTING

    def test_committing_git_add_only(self):
        """git add alone also triggers COMMITTING — agent is staging for commit."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['code_execute:{"code": "git add src/main.py"}'],
        )
        assert detect_phase(state) == Phase.COMMITTING

    def test_pushing_git_push(self):
        state = LifecycleState(
            turn_number=12,
            last_tool_calls=['code_execute:{"code": "git push origin feature/my-branch"}'],
        )
        assert detect_phase(state) == Phase.PUSHING

    def test_reviewing_gh_pr_create(self):
        state = LifecycleState(
            turn_number=15,
            last_tool_calls=['code_execute:{"code": "gh pr create --title \\"feat: new thing\\" --body \\"desc\\""}'],
        )
        assert detect_phase(state) == Phase.REVIEWING

    def test_committing_overrides_implementing(self):
        """If both file_edit and git commit in same turn, COMMITTING wins."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=[
                'file_edit:{"path": "src/main.py"}',
                'code_execute:{"code": "git add . && git commit -m \\"fix\\""}',
            ],
        )
        assert detect_phase(state) == Phase.COMMITTING

    def test_reviewing_overrides_pushing(self):
        """PR creation is highest priority."""
        state = LifecycleState(
            turn_number=15,
            last_tool_calls=[
                'code_execute:{"code": "git push origin feature/x"}',
                'code_execute:{"code": "gh pr create --title \\"feat\\""}',
            ],
        )
        assert detect_phase(state) == Phase.REVIEWING

    def test_git_log_is_not_commit(self):
        """git log should NOT trigger COMMITTING."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['code_execute:{"code": "git log --oneline -5"}'],
        )
        # git log is code_execute but doesn't match commit signals
        assert detect_phase(state) == Phase.IMPLEMENTING

    def test_git_diff_is_not_commit(self):
        """git diff should NOT trigger COMMITTING."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['code_execute:{"code": "git diff HEAD"}'],
        )
        assert detect_phase(state) == Phase.IMPLEMENTING

    def test_file_read_is_not_implementation(self):
        """file_read is not an implementation tool."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['file_read:{"path": "src/main.py"}'],
        )
        assert detect_phase(state) == Phase.IDLE

    def test_search_memory_is_idle(self):
        """Info-gathering tools don't trigger any phase."""
        state = LifecycleState(
            turn_number=10,
            last_tool_calls=['search_memory:{"query": "how to do X"}'],
        )
        assert detect_phase(state) == Phase.IDLE

    def test_empty_tool_calls_late_turn(self):
        """Empty tool call list on a late turn → IDLE."""
        state = LifecycleState(turn_number=20, last_tool_calls=[])
        assert detect_phase(state) == Phase.IDLE

    def test_planning_turn_4_with_tools_not_planning(self):
        """Turn 4 with non-planning tools → not PLANNING (early turn threshold is 3)."""
        state = LifecycleState(
            turn_number=4,
            last_tool_calls=['file_read:{"path": "README.md"}'],
        )
        assert detect_phase(state) == Phase.IDLE


# ---------------------------------------------------------------------------
# Pre-execution command detection tests
# ---------------------------------------------------------------------------


class TestPreExecutionDetection:
    """Tests for is_git_commit_command, is_git_push_command, is_pr_create_command."""

    def test_git_commit_simple(self):
        assert is_git_commit_command("code_execute", {"code": 'git commit -m "feat: x"'})

    def test_git_add_and_commit(self):
        assert is_git_commit_command("code_execute", {"code": "git add . && git commit -m 'fix'"})

    def test_git_add_only(self):
        """git add alone is not a commit command (for pre-commit hook purposes)."""
        assert not is_git_commit_command("code_execute", {"code": "git add ."})

    def test_not_code_execute(self):
        """Only code_execute triggers pre-commit check."""
        assert not is_git_commit_command("file_edit", {"code": "git commit"})

    def test_no_code_arg(self):
        assert not is_git_commit_command("code_execute", {})

    def test_git_push(self):
        assert is_git_push_command("code_execute", {"code": "git push origin feature/x"})

    def test_git_push_not_commit(self):
        assert not is_git_commit_command("code_execute", {"code": "git push origin main"})

    def test_pr_create(self):
        assert is_pr_create_command("code_execute", {"code": 'gh pr create --title "feat" --body "desc"'})

    def test_pr_create_not_push(self):
        assert not is_git_push_command("code_execute", {"code": "gh pr create"})

    def test_git_log_not_commit(self):
        assert not is_git_commit_command("code_execute", {"code": "git log --oneline"})

    def test_git_status_not_commit(self):
        assert not is_git_commit_command("code_execute", {"code": "git status"})

    def test_case_insensitive_commit(self):
        assert is_git_commit_command("code_execute", {"code": "GIT COMMIT -m 'msg'"})


# ---------------------------------------------------------------------------
# Fragment loading tests (using real manifest if available, mock otherwise)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_manifest_cache():
    """Ensure manifest cache is clean between tests."""
    invalidate_cache()
    yield
    invalidate_cache()


class TestLoadLifecycleFragments:
    """Tests for load_lifecycle_fragments with the real prompts directory."""

    @pytest.fixture
    def prompts_dir(self) -> Path:
        """Return the real prompts directory."""
        d = Path(__file__).resolve().parent.parent.parent / "prompts"
        if not d.exists():
            pytest.skip("prompts/ directory not found")
        return d

    def test_idle_returns_empty(self, prompts_dir: Path):
        result = load_lifecycle_fragments(Phase.IDLE, prompts_dir)
        assert result == []

    def test_committing_returns_git_fragments(self, prompts_dir: Path):
        result = load_lifecycle_fragments(Phase.COMMITTING, prompts_dir)
        assert len(result) >= 1
        paths = [f.path for f in result]
        # Should include git-related fragments
        assert any("git" in p for p in paths), f"Expected git fragments, got: {paths}"
        # All should be Tier 2 with phase=committing
        for f in result:
            assert f.tier == 2
            assert f.phase == "committing"

    def test_implementing_returns_implementation_fragments(self, prompts_dir: Path):
        result = load_lifecycle_fragments(Phase.IMPLEMENTING, prompts_dir)
        assert len(result) >= 1
        for f in result:
            assert f.tier == 2
            assert f.phase == "implementing"

    def test_reviewing_returns_review_fragments(self, prompts_dir: Path):
        result = load_lifecycle_fragments(Phase.REVIEWING, prompts_dir)
        assert len(result) >= 1
        paths = [f.path for f in result]
        assert any("review" in p or "pull-request" in p for p in paths), (
            f"Expected review fragments, got: {paths}"
        )

    def test_planning_returns_planning_fragments(self, prompts_dir: Path):
        result = load_lifecycle_fragments(Phase.PLANNING, prompts_dir)
        assert len(result) >= 1
        for f in result:
            assert f.tier == 2
            assert f.phase == "planning"

    def test_pushing_returns_git_fragments(self, prompts_dir: Path):
        """Pushing phase — currently no manifest entries for 'pushing',
        so should fall back to empty or be mapped to committing-adjacent."""
        result = load_lifecycle_fragments(Phase.PUSHING, prompts_dir)
        # Pushing doesn't have its own manifest entries yet;
        # this is expected to return empty unless we add them
        # Just verify it doesn't crash
        for f in result:
            assert f.tier == 2

    def test_fragments_have_content(self, prompts_dir: Path):
        """All loaded fragments should have non-empty content."""
        result = load_lifecycle_fragments(Phase.COMMITTING, prompts_dir)
        for f in result:
            assert f.content, f"Fragment {f.path} has no content"
            assert f.token_estimate > 0

    def test_fragments_are_deterministic(self, prompts_dir: Path):
        """Same phase should return same fragments in same order."""
        r1 = load_lifecycle_fragments(Phase.COMMITTING, prompts_dir)
        r2 = load_lifecycle_fragments(Phase.COMMITTING, prompts_dir)
        assert [f.path for f in r1] == [f.path for f in r2]


# ---------------------------------------------------------------------------
# Formatting tests
# ---------------------------------------------------------------------------


class TestFormatting:
    """Tests for format_lifecycle_injection and format_precommit_injection."""

    def _make_fragment(self, path: str, content: str) -> FragmentMeta:
        return FragmentMeta(
            path=path,
            tier=2,
            phase="committing",
            content=content,
            token_estimate=len(content) // 4,
        )

    def test_lifecycle_injection_format(self):
        frags = [
            self._make_fragment("git.md", "Use atomic commits"),
            self._make_fragment("commits.md", "Format: type(scope): desc"),
        ]
        result = format_lifecycle_injection(Phase.COMMITTING, frags)
        assert "## Current Phase: COMMITTING" in result
        assert "Use atomic commits" in result
        assert "Format: type(scope): desc" in result

    def test_lifecycle_injection_empty(self):
        result = format_lifecycle_injection(Phase.IDLE, [])
        assert result == ""

    def test_lifecycle_injection_empty_content(self):
        frags = [self._make_fragment("empty.md", "")]
        result = format_lifecycle_injection(Phase.IMPLEMENTING, frags)
        assert result == ""

    def test_precommit_injection_format(self):
        frags = [self._make_fragment("git.md", "Use feature branches")]
        result = format_precommit_injection(frags)
        assert "## Before You Commit" in result
        assert "Use feature branches" in result
        assert "git diff --cached" in result

    def test_precommit_injection_empty(self):
        result = format_precommit_injection([])
        assert result == ""

    def test_precommit_injection_multiple(self):
        frags = [
            self._make_fragment("git.md", "Branch rules"),
            self._make_fragment("commits.md", "Commit format"),
        ]
        result = format_precommit_injection(frags)
        assert "Branch rules" in result
        assert "Commit format" in result


# ---------------------------------------------------------------------------
# Integration test: end-to-end phase detection → fragment loading
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Integration tests: detect phase → load fragments → format injection."""

    @pytest.fixture
    def prompts_dir(self) -> Path:
        d = Path(__file__).resolve().parent.parent.parent / "prompts"
        if not d.exists():
            pytest.skip("prompts/ directory not found")
        return d

    def test_commit_flow(self, prompts_dir: Path):
        """User says 'add webhook handler' → agent commits → git guidance appears."""
        # Agent just ran git add + git commit
        state = LifecycleState(
            turn_number=8,
            last_tool_calls=[
                'code_execute:{"code": "git add . && git commit -m \\"feat: add webhook handler\\""}',
            ],
        )
        phase = detect_phase(state)
        assert phase == Phase.COMMITTING

        frags = load_lifecycle_fragments(phase, prompts_dir)
        assert len(frags) >= 1

        injection = format_lifecycle_injection(phase, frags)
        assert "COMMITTING" in injection
        assert len(injection) > 50  # Non-trivial content

    def test_implement_then_commit_transition(self, prompts_dir: Path):
        """Phase transitions correctly from IMPLEMENTING to COMMITTING."""
        # First: implementing
        impl_state = LifecycleState(
            turn_number=5,
            last_tool_calls=['file_edit:{"path": "src/handler.py"}'],
        )
        assert detect_phase(impl_state) == Phase.IMPLEMENTING

        # Then: committing
        commit_state = LifecycleState(
            turn_number=6,
            last_tool_calls=['code_execute:{"code": "git commit -m \\"feat: handler\\""}'],
        )
        assert detect_phase(commit_state) == Phase.COMMITTING

        # Verify different fragments
        impl_frags = load_lifecycle_fragments(Phase.IMPLEMENTING, prompts_dir)
        commit_frags = load_lifecycle_fragments(Phase.COMMITTING, prompts_dir)
        impl_paths = {f.path for f in impl_frags}
        commit_paths = {f.path for f in commit_frags}
        assert impl_paths != commit_paths, "Implementing and committing should have different fragments"

    def test_user_never_mentions_git(self, prompts_dir: Path):
        """Critical test case from Doc 024: user asks about webhooks, never mentions
        git, but agent still gets git guidance at commit time."""
        # User message is about webhooks — nothing about git
        user_message = "Add a Stripe webhook handler for payment events"

        # Agent implements...
        state_impl = LifecycleState(
            turn_number=5,
            last_tool_calls=['file_edit:{"path": "src/webhooks.py"}'],
        )
        phase_impl = detect_phase(state_impl)
        assert phase_impl == Phase.IMPLEMENTING

        # Agent commits — user never mentioned git
        state_commit = LifecycleState(
            turn_number=10,
            last_tool_calls=['code_execute:{"code": "git add . && git commit -m \\"feat: stripe webhook\\""}'],
        )
        phase_commit = detect_phase(state_commit)
        assert phase_commit == Phase.COMMITTING

        frags = load_lifecycle_fragments(phase_commit, prompts_dir)
        assert len(frags) >= 1, "Git guidance must be injected at commit time"
        assert any("git" in f.path for f in frags), "Must include git-related fragments"
