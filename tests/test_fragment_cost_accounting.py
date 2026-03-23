"""Tests for Design Doc 064: Prompt Fragment Cost Accounting.

Tests cover:
1. CostTracker.attribute_fragment_costs — proportional cost attribution
2. langfuse_client — score emission (mocked)
3. fragment_cost_report — aggregation logic
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestAttributeFragmentCosts(unittest.TestCase):
    """Test CostTracker.attribute_fragment_costs (Phase 2)."""

    def _make_tracker(self):
        from backend.app.agent.cost_tracker import CostTracker
        return CostTracker(conversation_id="test-conv", max_iterations=10)

    def _make_response(self, prompt_tokens=1000, completion_tokens=500, cost=0.01):
        resp = MagicMock()
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = prompt_tokens
        resp.usage.completion_tokens = completion_tokens
        return resp

    def test_basic_proportional_attribution(self):
        """Fragments should get cost proportional to their token share."""
        tracker = self._make_tracker()
        resp = self._make_response(prompt_tokens=1000, completion_tokens=500)

        fragments = [
            {"name": "safety", "tokens": 200},
            {"name": "tools", "tokens": 300},
        ]

        with patch.object(tracker, "calc_call_cost", return_value=0.015):
            result = tracker.attribute_fragment_costs(resp, "test-model", fragments)

        self.assertEqual(len(result), 2)
        # Both should have usd_cost
        self.assertIn("usd_cost", result[0])
        self.assertIn("usd_cost", result[1])
        # Safety has 200/500 = 40% of fragment tokens
        # Tools has 300/500 = 60% of fragment tokens
        total_frag_cost = result[0]["usd_cost"] + result[1]["usd_cost"]
        self.assertAlmostEqual(
            result[0]["usd_cost"] / total_frag_cost, 0.4, places=2
        )
        self.assertAlmostEqual(
            result[1]["usd_cost"] / total_frag_cost, 0.6, places=2
        )

    def test_empty_fragments_returns_empty(self):
        """Empty fragment list should return empty list."""
        tracker = self._make_tracker()
        resp = self._make_response()
        result = tracker.attribute_fragment_costs(resp, "test-model", [])
        self.assertEqual(result, [])

    def test_zero_input_tokens_returns_unchanged(self):
        """If input tokens is 0, fragments should be returned without usd_cost."""
        tracker = self._make_tracker()
        resp = self._make_response(prompt_tokens=0)
        fragments = [{"name": "frag1", "tokens": 100}]
        result = tracker.attribute_fragment_costs(resp, "test-model", fragments)
        self.assertNotIn("usd_cost", result[0])

    def test_zero_fragment_tokens_returns_unchanged(self):
        """If all fragments have 0 tokens, return without usd_cost."""
        tracker = self._make_tracker()
        resp = self._make_response(prompt_tokens=1000)
        fragments = [{"name": "empty", "tokens": 0}]
        with patch.object(tracker, "calc_call_cost", return_value=0.01):
            result = tracker.attribute_fragment_costs(resp, "test-model", fragments)
        self.assertNotIn("usd_cost", result[0])

    def test_fragment_share_clamped_to_1(self):
        """Fragment share should never exceed 1.0 even if estimates overshoot."""
        tracker = self._make_tracker()
        # Fragment tokens (2000) > input tokens (1000) — overshoot
        resp = self._make_response(prompt_tokens=1000, completion_tokens=500)
        fragments = [{"name": "big", "tokens": 2000}]

        with patch.object(tracker, "calc_call_cost", return_value=0.01):
            result = tracker.attribute_fragment_costs(resp, "test-model", fragments)

        # Cost should not exceed total input cost
        self.assertIn("usd_cost", result[0])
        self.assertLessEqual(result[0]["usd_cost"], 0.01)

    def test_tokenEstimate_fallback(self):
        """Should fall back to 'tokenEstimate' key if 'tokens' is missing."""
        tracker = self._make_tracker()
        resp = self._make_response(prompt_tokens=1000, completion_tokens=500)
        fragments = [{"name": "frag1", "tokenEstimate": 250}]

        with patch.object(tracker, "calc_call_cost", return_value=0.01):
            result = tracker.attribute_fragment_costs(resp, "test-model", fragments)

        self.assertIn("usd_cost", result[0])
        self.assertGreater(result[0]["usd_cost"], 0)


class TestLangfuseClient(unittest.TestCase):
    """Test langfuse_client score emission functions (Phase 1)."""

    def setUp(self):
        # Reset module state between tests
        import backend.app.agent.langfuse_client as lc
        lc._client = None
        lc._initialized = False

    def test_get_langfuse_returns_none_without_env(self):
        """Without LANGFUSE_PUBLIC_KEY, get_langfuse should return None."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
            import backend.app.agent.langfuse_client as lc
            lc._initialized = False
            lc._client = None
            result = lc.get_langfuse()
            self.assertIsNone(result)

    @patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test"})
    @patch("backend.app.agent.langfuse_client.Langfuse", create=True)
    def test_emit_fragment_scores_calls_score(self, mock_langfuse_cls):
        """emit_fragment_scores should call lf.score() for each fragment."""
        import backend.app.agent.langfuse_client as lc
        lc._initialized = False
        lc._client = None

        mock_client = MagicMock()
        mock_langfuse_cls.return_value = mock_client

        # Manually set client since the import mock may not work perfectly
        lc._client = mock_client
        lc._initialized = True

        fragments = [
            {"name": "safety", "tokens": 200, "path": "universal/safety.md"},
            {"name": "tools", "tokens": 300, "path": "tier1/tools.md"},
        ]

        lc.emit_fragment_scores(
            trace_id="trace-123",
            fragments=fragments,
            model="test-model",
            session_id="sess-123",
        )

        self.assertEqual(mock_client.score.call_count, 2)
        calls = mock_client.score.call_args_list
        self.assertEqual(calls[0].kwargs["name"], "fragment_token_est:safety")
        self.assertEqual(calls[0].kwargs["value"], 200)
        self.assertEqual(calls[1].kwargs["name"], "fragment_token_est:tools")
        self.assertEqual(calls[1].kwargs["value"], 300)

    @patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test"})
    def test_emit_fragment_cost_scores_skips_without_usd_cost(self):
        """Fragments without usd_cost should be skipped."""
        import backend.app.agent.langfuse_client as lc

        mock_client = MagicMock()
        lc._client = mock_client
        lc._initialized = True

        fragments = [
            {"name": "no-cost", "tokens": 100},
            {"name": "has-cost", "tokens": 200, "usd_cost": 0.001},
        ]

        lc.emit_fragment_cost_scores(
            trace_id="trace-123",
            fragments=fragments,
            model="test-model",
            session_id="sess-123",
        )

        # Only the fragment with usd_cost should generate a score
        self.assertEqual(mock_client.score.call_count, 1)
        call = mock_client.score.call_args
        self.assertEqual(call.kwargs["name"], "fragment_cost:has-cost")
        self.assertAlmostEqual(call.kwargs["value"], 0.001)

    @patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-test", "FRAGMENT_COST_SCORES": "false"})
    def test_emit_respects_feature_flag(self):
        """When FRAGMENT_COST_SCORES=false, no scores should be emitted."""
        import backend.app.agent.langfuse_client as lc

        mock_client = MagicMock()
        lc._client = mock_client
        lc._initialized = True

        fragments = [{"name": "frag1", "tokens": 100}]

        lc.emit_fragment_scores(
            trace_id="trace-123",
            fragments=fragments,
            model="test-model",
            session_id="sess-123",
        )

        mock_client.score.assert_not_called()


class TestFragmentCostReport(unittest.TestCase):
    """Test the report formatting logic (Phase 3)."""

    def test_format_table_empty(self):
        """Empty stats should return 'No fragment data found.'"""
        from scripts.fragment_cost_report import format_table
        self.assertEqual(format_table([]), "No fragment data found.")

    def test_format_table_basic(self):
        """Table should contain fragment names and costs."""
        from scripts.fragment_cost_report import format_table

        stats = [
            {"name": "safety", "loads": 100, "avg_tokens": 200, "total_tokens": 20000, "total_cost": 0.05, "avg_cost": 0.0005},
            {"name": "tools", "loads": 80, "avg_tokens": 300, "total_tokens": 24000, "total_cost": 0.03, "avg_cost": 0.000375},
        ]

        result = format_table(stats)
        self.assertIn("safety", result)
        self.assertIn("tools", result)
        self.assertIn("TOTAL", result)

    def test_format_table_top_n(self):
        """Top N should limit output."""
        from scripts.fragment_cost_report import format_table

        stats = [
            {"name": f"frag{i}", "loads": 10, "avg_tokens": 100, "total_tokens": 1000, "total_cost": 0.01 - i * 0.001, "avg_cost": 0.001}
            for i in range(5)
        ]

        result = format_table(stats, top=2)
        self.assertIn("frag0", result)
        self.assertIn("frag1", result)
        self.assertNotIn("frag2", result)

    def test_format_csv(self):
        """CSV should have header and data rows."""
        from scripts.fragment_cost_report import format_csv

        stats = [
            {"name": "safety", "loads": 100, "avg_tokens": 200, "total_tokens": 20000, "total_cost": 0.05, "avg_cost": 0.0005},
        ]

        result = format_csv(stats)
        self.assertIn("name,loads,avg_tokens,total_tokens,total_cost,avg_cost", result)
        self.assertIn("safety", result)


if __name__ == "__main__":
    unittest.main()
