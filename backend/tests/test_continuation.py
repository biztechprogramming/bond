"""Tests for conversation continuation (Design Doc 034)."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.agent.continuation import (
    ContinuationIntent,
    IterationBudget,
    LightweightCheckpoint,
    PlanPosition,
    build_checkpoint_from_history,
    build_continuation_context,
    classify_intent,
    format_checkpoint_context,
    resolve_plan_position,
)


# ---------------------------------------------------------------------------
# Intent Classification Tests
# ---------------------------------------------------------------------------

class TestClassifyIntent:
    """Test the intent classifier for continuation messages."""

    def test_continue_basic(self):
        assert classify_intent("continue", True) == ContinuationIntent.CONTINUE

    def test_continue_please(self):
        assert classify_intent("please continue", True) == ContinuationIntent.CONTINUE

    def test_continue_keep_going(self):
        assert classify_intent("keep going", True) == ContinuationIntent.CONTINUE

    def test_continue_carry_on(self):
        assert classify_intent("carry on", True) == ContinuationIntent.CONTINUE

    def test_continue_resume(self):
        assert classify_intent("resume", True) == ContinuationIntent.CONTINUE

    def test_continue_go_ahead(self):
        assert classify_intent("go ahead", True) == ContinuationIntent.CONTINUE

    def test_continue_pick_up(self):
        assert classify_intent("pick up where you left off", True) == ContinuationIntent.CONTINUE

    def test_continue_finish_the(self):
        assert classify_intent("finish the remaining items", True) == ContinuationIntent.CONTINUE

    def test_continue_next_item(self):
        assert classify_intent("next item", True) == ContinuationIntent.CONTINUE

    def test_continue_next_step(self):
        assert classify_intent("next step", True) == ContinuationIntent.CONTINUE

    def test_adjust_change_then_continue(self):
        assert classify_intent("change the API endpoint then continue", True) == ContinuationIntent.ADJUST

    def test_adjust_skip_item(self):
        assert classify_intent("skip item 3", True) == ContinuationIntent.ADJUST

    def test_adjust_skip_step(self):
        assert classify_intent("skip step 2", True) == ContinuationIntent.ADJUST

    def test_new_task_instead(self):
        assert classify_intent("do something else instead", True) == ContinuationIntent.NEW_TASK

    def test_new_task_abandon(self):
        assert classify_intent("abandon the current plan", True) == ContinuationIntent.NEW_TASK

    def test_new_task_forget(self):
        assert classify_intent("forget the current task", True) == ContinuationIntent.NEW_TASK

    def test_normal_message(self):
        assert classify_intent("how do I add a new route?", True) == ContinuationIntent.NORMAL

    def test_normal_without_plan(self):
        """Without an active plan, continuation messages are treated as normal."""
        assert classify_intent("continue", False) == ContinuationIntent.NORMAL

    def test_empty_message(self):
        assert classify_intent("", True) == ContinuationIntent.NORMAL

    def test_case_insensitive(self):
        assert classify_intent("CONTINUE", True) == ContinuationIntent.CONTINUE
        assert classify_intent("Keep Going", True) == ContinuationIntent.CONTINUE

    def test_no_plan_means_normal(self):
        """All continuation intents should be NORMAL when no plan exists."""
        assert classify_intent("keep going", False) == ContinuationIntent.NORMAL
        assert classify_intent("resume", False) == ContinuationIntent.NORMAL
        assert classify_intent("skip item 3", False) == ContinuationIntent.NORMAL


# ---------------------------------------------------------------------------
# Plan Position Resolver Tests
# ---------------------------------------------------------------------------

class TestResolvePlanPosition:
    """Test plan position resolution against filesystem/git state."""

    @pytest.fixture
    def sample_plan(self):
        return {
            "id": "plan-001",
            "title": "Implement API endpoints",
            "status": "active",
            "items": [
                {"id": "item-1", "title": "Create GET /users", "status": "done", "ordinal": 0,
                 "files_changed": ["src/routes/users.ts"], "notes": []},
                {"id": "item-2", "title": "Create POST /users", "status": "in_progress", "ordinal": 1,
                 "files_changed": ["src/routes/users.ts"], "notes": [{"text": "Started handler"}],
                 "description": "POST endpoint for creating users"},
                {"id": "item-3", "title": "Add validation", "status": "new", "ordinal": 2,
                 "files_changed": [], "notes": []},
                {"id": "item-4", "title": "Write tests", "status": "new", "ordinal": 3,
                 "files_changed": [], "notes": []},
            ],
        }

    @pytest.mark.asyncio
    async def test_categorize_items(self, sample_plan):
        position = await resolve_plan_position(sample_plan)
        assert len(position.completed_items) == 1
        assert len(position.in_progress_items) == 1
        assert len(position.pending_items) == 2
        assert position.total_items == 4
        assert position.progress_pct == 25.0

    @pytest.mark.asyncio
    async def test_next_item_is_in_progress(self, sample_plan):
        position = await resolve_plan_position(sample_plan)
        assert position.next_item is not None
        assert position.next_item["id"] == "item-2"

    @pytest.mark.asyncio
    async def test_next_item_is_first_pending_when_none_in_progress(self, sample_plan):
        # Make item-2 done
        sample_plan["items"][1]["status"] = "done"
        position = await resolve_plan_position(sample_plan)
        assert position.next_item is not None
        assert position.next_item["id"] == "item-3"

    @pytest.mark.asyncio
    async def test_verify_files_with_workspace(self, sample_plan):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create one of the expected files
            os.makedirs(os.path.join(tmpdir, "src", "routes"), exist_ok=True)
            Path(os.path.join(tmpdir, "src", "routes", "users.ts")).write_text("export {};\n")

            position = await resolve_plan_position(sample_plan, tmpdir)
            assert position.verified_files.get("src/routes/users.ts") is True

    @pytest.mark.asyncio
    async def test_git_state_with_real_repo(self, sample_plan):
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmpdir, capture_output=True)

            position = await resolve_plan_position(sample_plan, tmpdir)
            assert position.git_branch is not None  # Should have a branch
            assert not position.git_has_uncommitted

    @pytest.mark.asyncio
    async def test_empty_plan(self):
        plan = {"id": "plan-empty", "title": "Empty", "items": []}
        position = await resolve_plan_position(plan)
        assert position.total_items == 0
        assert position.next_item is None
        assert position.progress_pct == 0.0

    @pytest.mark.asyncio
    async def test_all_complete(self):
        plan = {
            "id": "plan-done",
            "title": "All Done",
            "items": [
                {"id": "i1", "title": "A", "status": "done", "ordinal": 0, "files_changed": [], "notes": []},
                {"id": "i2", "title": "B", "status": "complete", "ordinal": 1, "files_changed": [], "notes": []},
            ],
        }
        position = await resolve_plan_position(plan)
        assert position.progress_pct == 100.0
        assert position.next_item is None


# ---------------------------------------------------------------------------
# Continuation Context Builder Tests
# ---------------------------------------------------------------------------

class TestBuildContinuationContext:
    """Test that continuation context is minimal and focused."""

    def test_basic_context(self):
        position = PlanPosition(
            plan_id="plan-001",
            plan_title="Build API",
            completed_items=[{"id": "i1", "title": "Setup project", "files_changed": ["package.json"]}],
            in_progress_items=[{"id": "i2", "title": "Create routes", "description": "REST endpoints", "notes": [], "files_changed": []}],
            pending_items=[{"id": "i3", "title": "Add tests"}],
            next_item={"id": "i2", "title": "Create routes"},
        )
        plan = {"id": "plan-001", "title": "Build API"}

        ctx = build_continuation_context(position, plan)
        assert "Resuming: Build API" in ctx
        assert "1/3 items complete" in ctx
        assert "✅ Setup project" in ctx
        assert "Create routes" in ctx
        assert "Add tests" in ctx

    def test_context_size_is_small(self):
        """Continuation context should be well under 5K tokens (~20K chars)."""
        position = PlanPosition(
            plan_id="plan-001",
            plan_title="Big Plan",
            completed_items=[
                {"id": f"i{i}", "title": f"Item {i}", "files_changed": [f"file{i}.ts"]}
                for i in range(10)
            ],
            in_progress_items=[{"id": "i10", "title": "Current item", "description": "Working on it", "notes": [{"text": "Some note"}], "files_changed": ["current.ts"]}],
            pending_items=[{"id": f"i{i}", "title": f"Future item {i}"} for i in range(11, 20)],
            next_item={"id": "i10", "title": "Current item"},
        )
        plan = {"id": "plan-001", "title": "Big Plan"}

        ctx = build_continuation_context(position, plan)
        # ~4 chars per token, so 20K chars = ~5K tokens
        assert len(ctx) < 20000, f"Context too large: {len(ctx)} chars (~{len(ctx)//4} tokens)"

    def test_with_adjustment(self):
        position = PlanPosition(
            plan_id="plan-001",
            plan_title="Plan",
            pending_items=[{"id": "i1", "title": "Task 1"}],
            next_item={"id": "i1", "title": "Task 1"},
        )
        plan = {"id": "plan-001", "title": "Plan"}

        ctx = build_continuation_context(position, plan, adjustment="change the database to PostgreSQL then continue")
        assert "Adjustment Requested" in ctx
        assert "PostgreSQL" in ctx

    def test_git_verification_included(self):
        position = PlanPosition(
            plan_id="plan-001",
            plan_title="Plan",
            git_branch="feature/my-branch",
            git_has_uncommitted=True,
            git_recent_commits=["abc1234 Add feature X"],
            next_item={"id": "i1", "title": "Task 1"},
            pending_items=[{"id": "i1", "title": "Task 1"}],
        )
        plan = {"id": "plan-001", "title": "Plan"}

        ctx = build_continuation_context(position, plan)
        assert "feature/my-branch" in ctx
        assert "Uncommitted changes detected" in ctx
        assert "abc1234" in ctx


# ---------------------------------------------------------------------------
# Fallback: Lightweight Checkpoint Tests
# ---------------------------------------------------------------------------

class TestLightweightCheckpoint:
    """Test the no-plan fallback checkpoint builder."""

    def test_extracts_last_messages(self):
        history = [
            {"role": "user", "content": "Build me a REST API"},
            {"role": "assistant", "content": "I'll start with the project setup."},
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": "Working on routes now."},
        ]
        checkpoint = build_checkpoint_from_history(history)
        assert "continue" in checkpoint.last_user_request
        assert "routes" in checkpoint.last_assistant_action

    def test_empty_history(self):
        checkpoint = build_checkpoint_from_history([])
        assert checkpoint.last_user_request == ""
        assert checkpoint.last_assistant_action == ""

    def test_format_is_small(self):
        checkpoint = LightweightCheckpoint(
            last_user_request="Build a REST API with authentication",
            last_assistant_action="I created the auth middleware and user routes.",
            uncommitted_changes=["src/auth.ts", "src/routes/users.ts"],
            decisions=["Using JWT for auth", "PostgreSQL for storage"],
        )
        ctx = format_checkpoint_context(checkpoint)
        # Should be well under 2K tokens (~8K chars)
        assert len(ctx) < 8000, f"Checkpoint context too large: {len(ctx)} chars"
        assert "REST API" in ctx
        assert "auth middleware" in ctx
        assert "src/auth.ts" in ctx


# ---------------------------------------------------------------------------
# Iteration Budget Tracker Tests
# ---------------------------------------------------------------------------

class TestIterationBudget:
    """Test the iteration budget tracker with thresholds."""

    def test_initial_state(self):
        budget = IterationBudget(total=100)
        assert budget.remaining == 100
        assert budget.pct_used == 0.0
        assert not budget.should_checkpoint
        assert not budget.should_wrap_up
        assert not budget.should_stop

    def test_at_50_percent(self):
        budget = IterationBudget(total=100, used=50)
        assert budget.should_checkpoint
        assert not budget.should_wrap_up
        assert not budget.should_stop

    def test_at_80_percent(self):
        budget = IterationBudget(total=100, used=80)
        assert budget.should_checkpoint
        assert budget.should_wrap_up
        assert not budget.should_stop

    def test_at_95_percent(self):
        budget = IterationBudget(total=100, used=95)
        assert budget.should_checkpoint
        assert budget.should_wrap_up
        assert budget.should_stop

    def test_tick(self):
        budget = IterationBudget(total=10)
        budget.tick()
        assert budget.used == 1
        assert budget.remaining == 9

    def test_budget_message_none_when_low(self):
        budget = IterationBudget(total=100, used=10)
        assert budget.get_budget_message() is None

    def test_budget_message_at_50(self):
        budget = IterationBudget(total=100, used=50)
        msg = budget.get_budget_message()
        assert msg is not None
        assert "checkpoint" in msg.lower()

    def test_budget_message_at_80(self):
        budget = IterationBudget(total=100, used=80)
        msg = budget.get_budget_message()
        assert msg is not None
        assert "finish" in msg.lower() or "wrap" in msg.lower()

    def test_budget_message_at_95(self):
        budget = IterationBudget(total=100, used=95)
        msg = budget.get_budget_message()
        assert msg is not None
        assert "critical" in msg.lower() or "do not" in msg.lower()

    def test_zero_total_budget(self):
        budget = IterationBudget(total=0)
        assert budget.pct_used == 1.0
        assert budget.should_stop

    def test_remaining_never_negative(self):
        budget = IterationBudget(total=10, used=15)
        assert budget.remaining == 0
