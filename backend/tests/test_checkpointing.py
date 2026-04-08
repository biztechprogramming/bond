"""Tests for Design Doc 096: Progress Checkpointing."""

import pytest

from backend.app.agent.continuation import (
    LightweightCheckpoint,
    ToolCallRecord,
    IterationBudget,
    format_checkpoint_context,
)
from backend.app.agent.loop import _classify_stop_reason, _summarize_progress


# ---------------------------------------------------------------------------
# ToolCallRecord serialization
# ---------------------------------------------------------------------------

class TestToolCallRecord:
    def test_to_dict(self):
        rec = ToolCallRecord(
            tool_name="file_read",
            success=True,
            output_summary="Read 42 lines",
            timestamp="2026-01-01T00:00:00Z",
            duration_ms=150,
        )
        d = rec.to_dict()
        assert d["tool_name"] == "file_read"
        assert d["success"] is True
        assert d["output_summary"] == "Read 42 lines"
        assert d["timestamp"] == "2026-01-01T00:00:00Z"
        assert d["duration_ms"] == 150

    def test_from_dict(self):
        data = {
            "tool_name": "shell_exec",
            "success": False,
            "output_summary": "Command failed",
            "timestamp": "2026-01-01T00:00:00Z",
            "duration_ms": 300,
        }
        rec = ToolCallRecord.from_dict(data)
        assert rec.tool_name == "shell_exec"
        assert rec.success is False
        assert rec.duration_ms == 300

    def test_from_dict_defaults(self):
        rec = ToolCallRecord.from_dict({})
        assert rec.tool_name == ""
        assert rec.success is False
        assert rec.output_summary == ""
        assert rec.duration_ms == 0

    def test_roundtrip(self):
        original = ToolCallRecord(
            tool_name="code_execute", success=True,
            output_summary="ok", timestamp="2026-04-08T12:00:00Z", duration_ms=42,
        )
        restored = ToolCallRecord.from_dict(original.to_dict())
        assert restored.tool_name == original.tool_name
        assert restored.success == original.success
        assert restored.duration_ms == original.duration_ms


# ---------------------------------------------------------------------------
# LightweightCheckpoint to_dict / from_dict
# ---------------------------------------------------------------------------

class TestLightweightCheckpoint:
    def test_to_dict_includes_new_fields(self):
        cp = LightweightCheckpoint(
            last_user_request="fix the bug",
            progress_summary="3 iterations, 2 tool calls",
            stop_reason="completed",
            turn_number=3,
            successful_tool_calls=2,
            failed_tool_calls=0,
            git_branch="main",
            git_head_sha="abc123",
            files_modified=["foo.py"],
            failed_approaches=["tried X"],
            completed_actions=[
                ToolCallRecord(tool_name="file_read", success=True, output_summary="ok"),
            ],
        )
        d = cp.to_dict()
        assert d["progress_summary"] == "3 iterations, 2 tool calls"
        assert d["stop_reason"] == "completed"
        assert d["turn_number"] == 3
        assert d["git_branch"] == "main"
        assert d["files_modified"] == ["foo.py"]
        assert len(d["completed_actions"]) == 1
        assert d["completed_actions"][0]["tool_name"] == "file_read"

    def test_from_dict_roundtrip(self):
        cp = LightweightCheckpoint(
            last_user_request="do stuff",
            stop_reason="interrupted",
            turn_number=5,
            successful_tool_calls=3,
            failed_tool_calls=1,
            files_modified=["a.py", "b.py"],
            failed_approaches=["approach A"],
            completed_actions=[
                ToolCallRecord(tool_name="t1", success=True, output_summary="ok"),
                ToolCallRecord(tool_name="t2", success=False, output_summary="fail"),
            ],
        )
        restored = LightweightCheckpoint.from_dict(cp.to_dict())
        assert restored.stop_reason == "interrupted"
        assert restored.turn_number == 5
        assert restored.successful_tool_calls == 3
        assert restored.failed_tool_calls == 1
        assert len(restored.completed_actions) == 2
        assert restored.completed_actions[1].success is False
        assert restored.files_modified == ["a.py", "b.py"]

    def test_from_dict_missing_fields(self):
        cp = LightweightCheckpoint.from_dict({"last_user_request": "hello"})
        assert cp.last_user_request == "hello"
        assert cp.turn_number == 0
        assert cp.completed_actions == []
        assert cp.files_modified == []


# ---------------------------------------------------------------------------
# format_checkpoint_context truncation tiers
# ---------------------------------------------------------------------------

class TestFormatCheckpointContext:
    def _full_checkpoint(self) -> LightweightCheckpoint:
        return LightweightCheckpoint(
            last_user_request="implement feature X",
            last_assistant_action="edited foo.py",
            progress_summary="5 iterations, 4 tool calls",
            stop_reason="budget_exhausted",
            files_modified=["foo.py", "bar.py"],
            failed_approaches=["tried monkey-patching"],
            open_todos=["write tests"],
            decisions=["use dataclass"],
            git_branch="feature/x",
            git_head_sha="deadbeef1234",
            completed_actions=[
                ToolCallRecord(tool_name="file_edit", success=True, output_summary="edited"),
            ],
        )

    def test_full_tier(self):
        cp = self._full_checkpoint()
        result = format_checkpoint_context(cp, max_tokens=2000)
        assert "# Resuming Previous Work" in result
        assert "implement feature X" in result
        assert "edited foo.py" in result
        assert "Recent Actions" in result
        assert "Git State" in result
        assert "feature/x" in result

    def test_medium_tier(self):
        cp = self._full_checkpoint()
        result = format_checkpoint_context(cp, max_tokens=1000)
        assert "Progress" in result
        assert "Failed Approaches" in result
        assert "Files Modified" in result
        # Should NOT have full last request/action
        assert "Last Request" not in result

    def test_minimal_tier(self):
        cp = self._full_checkpoint()
        result = format_checkpoint_context(cp, max_tokens=500)
        assert "Progress" in result
        assert "Failed Approaches" in result
        assert "Files Modified" not in result

    def test_ultra_minimal_tier(self):
        cp = self._full_checkpoint()
        result = format_checkpoint_context(cp, max_tokens=200)
        assert "Resuming" in result
        assert "5 iterations" in result
        assert "monkey-patching" in result
        # Should be very short
        assert "Last Request" not in result
        assert "Git State" not in result


# ---------------------------------------------------------------------------
# _classify_stop_reason
# ---------------------------------------------------------------------------

class TestClassifyStopReason:
    def test_budget_exhausted(self):
        cp = LightweightCheckpoint()
        assert _classify_stop_reason(79, 80, cp) == "budget_exhausted"

    def test_completed(self):
        cp = LightweightCheckpoint()
        assert _classify_stop_reason(5, 80, cp) == "completed"

    def test_existing_stop_reason(self):
        cp = LightweightCheckpoint(stop_reason="interrupted")
        assert _classify_stop_reason(5, 80, cp) == "interrupted"

    def test_transient_error(self):
        cp = LightweightCheckpoint()
        err = Exception("rate_limit exceeded, retry after 30s")
        assert _classify_stop_reason(5, 80, cp, error=err) == "transient_error"

    def test_transient_error_429(self):
        cp = LightweightCheckpoint()
        err = Exception("HTTP 429 Too Many Requests")
        assert _classify_stop_reason(5, 80, cp, error=err) == "transient_error"

    def test_fatal_error(self):
        cp = LightweightCheckpoint()
        err = ValueError("something broke badly")
        result = _classify_stop_reason(5, 80, cp, error=err)
        assert result.startswith("error: ValueError:")
        assert "something broke badly" in result

    def test_error_does_not_override_existing_stop_reason(self):
        cp = LightweightCheckpoint(stop_reason="interrupted")
        err = Exception("some error")
        assert _classify_stop_reason(5, 80, cp, error=err) == "interrupted"


# ---------------------------------------------------------------------------
# _summarize_progress
# ---------------------------------------------------------------------------

class TestSummarizeProgress:
    def test_with_tool_calls(self):
        cp = LightweightCheckpoint(
            successful_tool_calls=5,
            failed_tool_calls=2,
            files_modified=["a.py"],
        )
        result = _summarize_progress(9, cp)
        assert "10 iterations" in result
        assert "7 tool calls" in result
        assert "5 ok" in result
        assert "2 failed" in result
        assert "1 files modified" in result

    def test_no_progress(self):
        cp = LightweightCheckpoint()
        result = _summarize_progress(0, cp)
        assert "1 iterations" in result

    def test_no_tool_calls(self):
        cp = LightweightCheckpoint()
        result = _summarize_progress(4, cp)
        assert "5 iterations" in result
        assert "tool calls" not in result


# ---------------------------------------------------------------------------
# IterationBudget thresholds
# ---------------------------------------------------------------------------

class TestIterationBudget:
    def test_initial_state(self):
        b = IterationBudget(total=100)
        assert b.used == 0
        assert b.remaining == 100
        assert b.pct_used == 0.0
        assert not b.should_checkpoint
        assert not b.should_nudge
        assert not b.should_wrap_up
        assert not b.should_stop

    def test_tick(self):
        b = IterationBudget(total=100)
        for _ in range(50):
            b.tick()
        assert b.used == 50
        assert b.remaining == 50
        assert b.pct_used == 0.5

    def test_checkpoint_threshold_at_50pct(self):
        b = IterationBudget(total=100)
        for _ in range(49):
            b.tick()
        assert not b.should_checkpoint
        b.tick()  # 50th
        assert b.should_checkpoint
        assert not b.should_nudge

    def test_nudge_threshold_at_65pct(self):
        b = IterationBudget(total=100)
        for _ in range(65):
            b.tick()
        assert b.should_nudge
        assert not b.should_wrap_up

    def test_wrap_up_threshold_at_80pct(self):
        b = IterationBudget(total=100)
        for _ in range(80):
            b.tick()
        assert b.should_wrap_up
        assert not b.should_stop

    def test_stop_threshold_at_95pct(self):
        b = IterationBudget(total=100)
        for _ in range(95):
            b.tick()
        assert b.should_stop

    def test_budget_message_none_below_50pct(self):
        b = IterationBudget(total=100)
        for _ in range(49):
            b.tick()
        assert b.get_budget_message() is None

    def test_budget_message_checkpoint_at_50pct(self):
        b = IterationBudget(total=100)
        for _ in range(50):
            b.tick()
        msg = b.get_budget_message()
        assert msg is not None
        assert "BUDGET NOTE" in msg

    def test_budget_message_stop_at_95pct(self):
        b = IterationBudget(total=100)
        for _ in range(95):
            b.tick()
        msg = b.get_budget_message()
        assert msg is not None
        assert "URGENT" in msg

    def test_zero_total(self):
        b = IterationBudget(total=0)
        assert b.pct_used == 1.0
        assert b.should_stop
