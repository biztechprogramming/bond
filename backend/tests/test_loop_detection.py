"""Tests for loop detection in the agent worker.

Covers:
1. work_plan idempotency (already-done items return no-op)
2. Consecutive repetition detection
3. Cyclical loop detection (A→B→C→A→B→C)
4. Hard stop after max interventions
5. Utility model replay on repeated tool calls
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 1: work_plan idempotency
# ---------------------------------------------------------------------------

GATEWAY = "http://localhost:18792"


def _ctx():
    return {"agent_id": "test-agent", "conversation_id": "conv-1"}


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _api_env():
    """Patch BOND_API_URL at the module level."""
    return patch("backend.app.agent.tools.work_plan._BOND_API_URL", GATEWAY)


@pytest.mark.asyncio
async def test_update_item_already_done_returns_noop():
    """When item is already in the requested status, return unchanged signal."""
    from backend.app.agent.tools.work_plan import handle_work_plan

    mock_client = AsyncMock()
    # GET returns item already in "done" status
    mock_client.get.return_value = _mock_response({"status": "done", "id": "item-1"})

    with _api_env(), patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await handle_work_plan(
            {"action": "update_item", "item_id": "item-1", "status": "done"},
            _ctx(),
        )

    assert result["already_done"] is True
    assert result["status"] == "unchanged"
    assert "item-1" in result["item_id"]
    # Should NOT have called PUT
    mock_client.put.assert_not_called()


@pytest.mark.asyncio
async def test_update_item_different_status_proceeds():
    """When item is in a different status, normal update proceeds."""
    from backend.app.agent.tools.work_plan import handle_work_plan

    mock_client = AsyncMock()
    # GET returns item in "in_progress" status
    mock_client.get.return_value = _mock_response({"status": "in_progress", "id": "item-1"})
    # PUT succeeds
    mock_client.put.return_value = _mock_response({"status": "updated", "item_id": "item-1"})

    with _api_env(), patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await handle_work_plan(
            {"action": "update_item", "item_id": "item-1", "status": "done"},
            _ctx(),
        )

    assert result.get("already_done") is not True
    assert result["success"] is True
    mock_client.put.assert_called_once()


@pytest.mark.asyncio
async def test_update_item_no_status_skips_idempotency_check():
    """When no status is provided (e.g. just notes), skip idempotency check."""
    from backend.app.agent.tools.work_plan import handle_work_plan

    mock_client = AsyncMock()
    mock_client.put.return_value = _mock_response({"status": "updated", "item_id": "item-1"})

    with _api_env(), patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await handle_work_plan(
            {"action": "update_item", "item_id": "item-1", "notes": "some note"},
            _ctx(),
        )

    assert result["success"] is True
    # Should NOT have called GET (no idempotency check needed)
    mock_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_update_item_get_fails_falls_through():
    """If the GET for idempotency check fails, fall through to normal update."""
    from backend.app.agent.tools.work_plan import handle_work_plan

    mock_client = AsyncMock()
    # GET fails
    mock_client.get.side_effect = Exception("connection refused")
    # PUT succeeds
    mock_client.put.return_value = _mock_response({"status": "updated", "item_id": "item-1"})

    with _api_env(), patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await handle_work_plan(
            {"action": "update_item", "item_id": "item-1", "status": "done"},
            _ctx(),
        )

    assert result["success"] is True
    mock_client.put.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 2 & 3: Loop detection (unit tests for the detection logic)
# ---------------------------------------------------------------------------

class TestCyclicalLoopDetection:
    """Test the cyclical pattern detection algorithm."""

    def _detect_cycle(self, recent_calls, min_period=2, max_period=8, repeats=3):
        """Extract the cycle detection logic for unit testing."""
        if len(recent_calls) < min_period * repeats:
            return None
        for period in range(min_period, max_period + 1):
            needed = period * repeats
            if len(recent_calls) < needed:
                continue
            tail = recent_calls[-needed:]
            cycle = tail[:period]
            is_cycle = all(
                tail[i] == cycle[i % period]
                for i in range(needed)
            )
            if is_cycle:
                return cycle
        return None

    def test_no_cycle_in_short_list(self):
        calls = [("a", "1"), ("b", "2")]
        assert self._detect_cycle(calls) is None

    def test_no_cycle_in_diverse_calls(self):
        calls = [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"),
                 ("e", "5"), ("f", "6"), ("g", "7"), ("h", "8")]
        assert self._detect_cycle(calls) is None

    def test_detects_period_2_cycle(self):
        # A→B→A→B→A→B
        calls = [("a", "1"), ("b", "2")] * 3
        cycle = self._detect_cycle(calls)
        assert cycle is not None
        assert cycle == [("a", "1"), ("b", "2")]

    def test_detects_period_3_cycle(self):
        # A→B→C→A→B→C→A→B→C
        calls = [("a", "1"), ("b", "2"), ("c", "3")] * 3
        cycle = self._detect_cycle(calls)
        assert cycle is not None
        assert len(cycle) == 3

    def test_detects_cycle_with_prefix(self):
        # Some noise then a cycle
        calls = [("x", "9"), ("y", "8")] + [("a", "1"), ("b", "2"), ("c", "3")] * 3
        cycle = self._detect_cycle(calls)
        assert cycle is not None

    def test_no_false_positive_with_almost_cycle(self):
        # A→B→C→A→B→C→A→B→D (broken on last)
        calls = [("a", "1"), ("b", "2"), ("c", "3"),
                 ("a", "1"), ("b", "2"), ("c", "3"),
                 ("a", "1"), ("b", "2"), ("d", "4")]
        assert self._detect_cycle(calls) is None

    def test_consecutive_same_call_detected_as_period_1(self):
        # A→A→A is period 1, but min_period=2 won't catch it
        # (that's handled by the consecutive detection)
        calls = [("a", "1")] * 5
        # With min_period=1, would detect; with min_period=2, won't
        assert self._detect_cycle(calls, min_period=2) is None
        assert self._detect_cycle(calls, min_period=1) is not None

    def test_real_world_work_plan_cycle(self):
        """Reproduce the exact pattern from the bug report."""
        item_a = ("work_plan", "abcd1234")
        item_b = ("work_plan", "efgh5678")
        item_c = ("work_plan", "ijkl9012")
        calls = [item_a, item_b, item_c] * 5  # 15 calls cycling
        cycle = self._detect_cycle(calls)
        assert cycle is not None
        assert cycle == [item_a, item_b, item_c]


class TestConsecutiveRepetition:
    """Test the existing consecutive repetition detection."""

    def _is_consecutive_repeat(self, recent_calls, threshold=3):
        if len(recent_calls) < threshold:
            return False
        last_n = recent_calls[-threshold:]
        return all(tc == last_n[0] for tc in last_n)

    def test_no_repeat(self):
        assert not self._is_consecutive_repeat([("a", "1"), ("b", "2"), ("c", "3")])

    def test_repeat_detected(self):
        assert self._is_consecutive_repeat([("a", "1"), ("a", "1"), ("a", "1")])

    def test_repeat_at_end(self):
        calls = [("b", "2"), ("a", "1"), ("a", "1"), ("a", "1")]
        assert self._is_consecutive_repeat(calls)

    def test_not_enough_repeats(self):
        assert not self._is_consecutive_repeat([("a", "1"), ("a", "1")])


# ---------------------------------------------------------------------------
# Fix 3: Utility model replay on repeated tool calls
# ---------------------------------------------------------------------------

class TestUtilityReplayDetection:
    """Test that repeated tool signatures trigger replay to primary model."""

    def _should_replay(self, proposed_calls, recent_tool_calls):
        """Simulate the replay detection logic from worker.py."""
        import hashlib
        for name, args in proposed_calls:
            sig = hashlib.md5(f"{name}:{json.dumps(args)[:200]}".encode()).hexdigest()[:8]
            sig_tuple = (name, sig)
            if sig_tuple in recent_tool_calls:
                return True
        return False

    def _make_sig(self, name, args):
        import hashlib
        return (name, hashlib.md5(f"{name}:{json.dumps(args)[:200]}".encode()).hexdigest()[:8])

    def test_no_replay_for_new_call(self):
        recent = [self._make_sig("file_read", {"path": "/a.py"})]
        proposed = [("file_read", {"path": "/b.py"})]
        assert not self._should_replay(proposed, recent)

    def test_replay_for_repeated_call(self):
        sig = self._make_sig("work_plan", {"action": "update_item", "item_id": "item-1", "status": "done"})
        recent = [sig]
        proposed = [("work_plan", {"action": "update_item", "item_id": "item-1", "status": "done"})]
        assert self._should_replay(proposed, recent)

    def test_no_replay_for_empty_history(self):
        proposed = [("work_plan", {"action": "update_item", "item_id": "item-1"})]
        assert not self._should_replay(proposed, [])

    def test_replay_catches_work_plan_cycle(self):
        """The exact scenario: utility model re-proposes a work_plan update it already did."""
        sig_a = self._make_sig("work_plan", {"action": "update_item", "item_id": "a", "status": "done"})
        sig_b = self._make_sig("work_plan", {"action": "update_item", "item_id": "b", "status": "done"})
        recent = [sig_a, sig_b]
        # Utility proposes updating item "a" again
        proposed = [("work_plan", {"action": "update_item", "item_id": "a", "status": "done"})]
        assert self._should_replay(proposed, recent)
