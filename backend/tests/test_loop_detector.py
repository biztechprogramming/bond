"""Tests for loop detection logic (design doc 098 changes)."""

from backend.app.agent.loop_state import LoopState
from backend.app.agent.iteration_handlers import detect_loop


def _make_loop_state() -> LoopState:
    return LoopState.create(max_iterations=100, preturn_msg_count=0, cache_bp2_index=0)


def test_exempt_tool_different_args_no_loop():
    """Reading 5 different files (file_read with different path args) should NOT trigger."""
    ls = _make_loop_state()
    for i in range(5):
        detected, _ = detect_loop("file_read", {"path": f"/workspace/file{i}.py"}, ls)
    assert not detected


def test_same_args_triggers_consecutive_repetition():
    """Reading the same file 2x with identical args DOES trigger (mechanism #1)."""
    ls = _make_loop_state()
    _, _ = detect_loop("file_read", {"path": "/workspace/a.py"}, ls)
    detected, msg = detect_loop("file_read", {"path": "/workspace/a.py"}, ls)
    assert detected
    assert "same arguments" in msg


def test_non_exempt_tool_name_only_detection():
    """Calling a non-exempt tool 5 times with different args DOES trigger name-only detection."""
    ls = _make_loop_state()
    for i in range(4):
        detected, _ = detect_loop("code_execute", {"code": f"print({i})"}, ls)
        assert not detected
    detected, msg = detect_loop("code_execute", {"code": "print(4)"}, ls)
    assert detected
    assert "different arguments" in msg


def test_cyclical_detection_triggers():
    """file_read -> shell_grep repeated 3 times triggers cyclical detection."""
    ls = _make_loop_state()
    detected = False
    for _ in range(3):
        _, _ = detect_loop("file_read", {"path": f"/workspace/a.py"}, ls)
        detected, msg = detect_loop("shell_grep", {"pattern": "foo"}, ls)
    assert detected
    assert "cyclical loop" in msg.lower() or "cyclical" in msg.lower()


def test_no_cyclical_detection_for_varied_pattern():
    """file_read -> shell_grep -> file_read -> shell_ls does NOT trigger cyclical detection."""
    ls = _make_loop_state()
    calls = [
        ("file_read", {"path": "/a.py"}),
        ("shell_grep", {"pattern": "foo"}),
        ("file_read", {"path": "/b.py"}),
        ("shell_ls", {"path": "/workspace"}),
    ]
    for name, args in calls:
        detected, _ = detect_loop(name, args, ls)
    assert not detected
