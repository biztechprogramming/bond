"""Per-session cost tracking for LLM calls.

Extracted from worker._run_agent_loop (Phase 4B/4C).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from litellm.cost_calculator import completion_cost as _litellm_completion_cost

logger = logging.getLogger("bond.agent.worker")


class CostTracker:
    """Tracks cost, token usage, and iteration counts for a single agent session."""

    def __init__(self, conversation_id: str, max_iterations: int):
        self.conversation_id = conversation_id
        self.tracking: dict[str, Any] = {
            "primary_calls": 0,
            "filter_calls": 0,
            "compression_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost": 0.0,
            "iterations_used": 0,
            "iteration_budget": max_iterations,
        }

        # Cost alert thresholds
        _raw_cost = os.environ.get("LLM_COST_ALERT_THRESHOLD")
        _raw_iter = os.environ.get("LLM_ITERATION_ALERT_THRESHOLD")
        try:
            self.cost_alert_threshold = float(_raw_cost) if isinstance(_raw_cost, str) else 0.25
        except (TypeError, ValueError):
            self.cost_alert_threshold = 0.25
        try:
            self.iteration_alert_threshold = int(_raw_iter) if isinstance(_raw_iter, str) else 20
        except (TypeError, ValueError):
            self.iteration_alert_threshold = 20

    def calc_call_cost(self, resp: Any, resp_model: str) -> float:
        """Calculate real cost for a single LLM call via litellm's cost calculator.

        Falls back to token-based estimate if the calculator doesn't have pricing
        for the model (e.g. custom/self-hosted models).
        """
        try:
            return _litellm_completion_cost(completion_response=resp, model=resp_model)
        except Exception:
            _usage = getattr(resp, "usage", None)
            _in = getattr(_usage, "prompt_tokens", 0) or 0
            _out = getattr(_usage, "completion_tokens", 0) or 0
            return _in * 15.0 / 1_000_000 + _out * 75.0 / 1_000_000

    def track_primary_call(self, response: Any, model: str, iteration: int, input_tokens: int, output_tokens: int):
        """Track a primary LLM call."""
        cost = self.calc_call_cost(response, model)
        self.tracking["primary_calls"] += 1
        self.tracking["total_input_tokens"] += input_tokens
        self.tracking["total_output_tokens"] += output_tokens
        self.tracking["total_cost"] += cost
        self.tracking["iterations_used"] = iteration + 1
        logger.debug("Iteration %d cost: $%.4f (cumulative: $%.4f)", iteration, cost, self.tracking["total_cost"])

    def track_filter_cost(self, cost: float):
        """Track cost from a tool result filter call."""
        self.tracking["filter_calls"] += 1
        self.tracking["total_cost"] += cost

    def emit_summary(self, langfuse_meta: dict[str, Any] | None = None):
        """Log per-session cost summary (Phase 4B) and check for cost alerts (Phase 4C)."""
        t = self.tracking
        total = t["total_cost"]

        logger.info(
            "Cost summary: calls=%d (primary=%d, filter=%d, compression=%d) "
            "tokens_in=%d tokens_out=%d cost=$%.4f iterations=%d/%d",
            t["primary_calls"] + t["filter_calls"] + t["compression_calls"],
            t["primary_calls"],
            t["filter_calls"],
            t["compression_calls"],
            t["total_input_tokens"],
            t["total_output_tokens"],
            total,
            t["iterations_used"],
            t["iteration_budget"],
        )

        # Phase 4C: Cost alerting
        try:
            exceeded = total > self.cost_alert_threshold or t["iterations_used"] > self.iteration_alert_threshold
        except TypeError:
            exceeded = False
        if exceeded:
            logger.warning(
                "COST ALERT: session %s exceeded thresholds (cost=$%.4f > $%.2f or iterations=%d > %d)",
                self.conversation_id, total, self.cost_alert_threshold,
                t["iterations_used"], self.iteration_alert_threshold,
            )
            if langfuse_meta:
                langfuse_meta.setdefault("tags", []).append("cost:high")
