"""Tests for StuckDetector (Design Doc 093)."""

import pytest

from backend.app.agent.loop_guard import StuckDetector


class TestStuckDetection:
    def test_not_stuck_with_no_calls(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        assert not detector.is_stuck()

    def test_not_stuck_with_one_call(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        detector.record_tool_call("file_read", {"path": "foo.py"})
        assert not detector.is_stuck()

    def test_stuck_after_consecutive_identical_calls(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        detector.record_tool_call("file_read", {"path": "foo.py"})
        detector.record_tool_call("file_read", {"path": "foo.py"})
        assert detector.is_stuck()

    def test_not_stuck_with_different_calls(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        detector.record_tool_call("file_read", {"path": "foo.py"})
        detector.record_tool_call("file_read", {"path": "bar.py"})
        assert not detector.is_stuck()

    def test_not_stuck_with_different_tools(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        detector.record_tool_call("file_read", {"path": "foo.py"})
        detector.record_tool_call("file_write", {"path": "foo.py"})
        assert not detector.is_stuck()

    def test_stuck_with_higher_threshold(self):
        detector = StuckDetector(max_consecutive_repeats=3)
        detector.record_tool_call("grep", {"pattern": "x"})
        detector.record_tool_call("grep", {"pattern": "x"})
        assert not detector.is_stuck()  # only 2, need 3
        detector.record_tool_call("grep", {"pattern": "x"})
        assert detector.is_stuck()

    def test_clear_resets_history(self):
        detector = StuckDetector(max_consecutive_repeats=2)
        detector.record_tool_call("file_read", {"path": "foo.py"})
        detector.record_tool_call("file_read", {"path": "foo.py"})
        assert detector.is_stuck()
        detector.clear()
        assert not detector.is_stuck()

    def test_stuck_interventions_counter(self):
        detector = StuckDetector()
        assert detector.stuck_interventions == 0
        detector.stuck_interventions += 1
        assert detector.stuck_interventions == 1

    def test_stuck_message_content(self):
        detector = StuckDetector(max_consecutive_repeats=3)
        msg = detector.get_stuck_message()
        assert "3 times" in msg
        assert "different approach" in msg

    def test_hash_is_deterministic(self):
        h1 = StuckDetector._hash_call("tool", {"a": 1, "b": 2})
        h2 = StuckDetector._hash_call("tool", {"b": 2, "a": 1})
        assert h1 == h2  # key order doesn't matter

    def test_hash_differs_for_different_args(self):
        h1 = StuckDetector._hash_call("tool", {"a": 1})
        h2 = StuckDetector._hash_call("tool", {"a": 2})
        assert h1 != h2

    def test_hash_differs_for_different_tools(self):
        h1 = StuckDetector._hash_call("tool_a", {"a": 1})
        h2 = StuckDetector._hash_call("tool_b", {"a": 1})
        assert h1 != h2

    def test_window_trimming(self):
        """Ensure internal hash list doesn't grow unbounded."""
        detector = StuckDetector(max_consecutive_repeats=2)
        for i in range(100):
            detector.record_tool_call("tool", {"i": i})
        assert len(detector._recent_call_hashes) <= 4  # 2 * max_consecutive_repeats
