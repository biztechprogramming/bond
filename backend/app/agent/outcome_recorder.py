"""Outcome recording for closed-loop optimization (Doc 049).

Extracted from worker._run_agent_loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger("bond.agent.worker")


class OutcomeRecorder:
    """Records outcome observations after agent turns complete."""

    def __init__(
        self,
        conversation_id: str,
        user_message: str,
        agent_db: Any,
        config: dict[str, Any],
        tier1_meta: list[dict],
        tier3_meta: list[dict],
        lessons_content: str | None,
        state: Any,
    ):
        self.conversation_id = conversation_id
        self.user_message = user_message
        self.agent_db = agent_db
        self.config = config
        self.lessons_content = lessons_content
        self._state = state

        self.tool_names: list[str] = []
        self.fragment_names: list[str] = []
        self.had_loop_intervention = False
        self.had_continuation = False
        self.had_compression = False
        self.turn_counter = getattr(state, "_optimization_turn_counter", 0)
        self.start_time_ms = int(time.time() * 1000)

        # Collect fragment names from tiers 1 and 3
        try:
            for m in tier1_meta:
                n = m.get("name") or m.get("path", "")
                if n:
                    self.fragment_names.append(n)
            for m in tier3_meta:
                n = m.get("name") or m.get("path", "")
                if n:
                    self.fragment_names.append(n)
        except Exception:
            pass

        # Capture config snapshot for experiment tracking
        self.config_snapshot: dict[str, Any] = {}
        self.cohort = "control"
        try:
            from backend.app.agent.optimizer import TUNABLE_PARAMS, apply_experiment_overrides
            for pk in TUNABLE_PARAMS:
                if pk in config:
                    self.config_snapshot[pk] = config[pk]
        except Exception:
            logger.debug("Experiment config snapshot skipped", exc_info=True)

    async def apply_experiment_overrides(self) -> dict[str, Any]:
        """Apply active experiment overrides to config. Returns updated config."""
        try:
            from backend.app.agent.optimizer import apply_experiment_overrides
            if self.agent_db:
                updated_config, self.cohort = await apply_experiment_overrides(
                    self.config, self.conversation_id, self.agent_db,
                )
                return updated_config
        except Exception:
            logger.debug("Experiment override check skipped", exc_info=True)
        return self.config

    def track_tool(self, tool_name: str):
        """Track a tool name for outcome signals."""
        if tool_name not in self.tool_names:
            self.tool_names.append(tool_name)

    async def record(self, tool_calls_made: int, cost_tracking: dict[str, Any]):
        """Record outcome observation after the turn completes."""
        try:
            from backend.app.agent.outcome import collect_signals, classify_task
            from ulid import ULID
            import hashlib as _hl

            wall_time = int(time.time() * 1000) - self.start_time_ms
            task_cat = classify_task(self.user_message, self.tool_names)

            signals = collect_signals(
                tool_calls=tool_calls_made,
                iterations=cost_tracking["iterations_used"],
                total_cost=cost_tracking["total_cost"],
                input_tokens=cost_tracking["total_input_tokens"],
                output_tokens=cost_tracking["total_output_tokens"],
                wall_time_ms=wall_time,
                had_loop_intervention=self.had_loop_intervention,
                had_continuation=self.had_continuation,
                had_compression=self.had_compression,
                fragments_selected=len(self.fragment_names),
                fragment_names=self.fragment_names,
                user_correction=False,
                task_category=task_cat,
                user_message_preview=self.user_message[:200] if self.user_message else "",
                tool_names=self.tool_names,
            )

            if self.agent_db:
                obs_id = str(ULID())
                lessons_hash = _hl.md5((self.lessons_content or "").encode()).hexdigest()[:12]
                await self.agent_db.execute(
                    """
                    INSERT INTO optimization_observations
                        (id, conversation_id, turn_index, task_category,
                         user_message_preview, signals_json, outcome_score,
                         config_snapshot_json, active_lessons_hash, cohort)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs_id, self.conversation_id, tool_calls_made, task_cat,
                        self.user_message[:200] if self.user_message else "",
                        json.dumps(signals), signals["outcome_score"],
                        json.dumps(self.config_snapshot), lessons_hash,
                        self.cohort,
                    ),
                )
                await self.agent_db.commit()

                # Every 50 turns, trigger background analysis
                self._state._optimization_turn_counter = self.turn_counter + 1
                if (self.turn_counter + 1) % 50 == 0:
                    asyncio.ensure_future(self._run_optimization_analysis())

        except Exception:
            logger.debug("Outcome recording failed (non-fatal)", exc_info=True)

    async def _run_optimization_analysis(self):
        """Spawn background optimization analysis."""
        try:
            from backend.app.agent.optimizer import run_analysis
            from backend.app.agent.tools.skills import _router_settings
            from backend.app.foundations.embeddings.engine import EmbeddingEngine
            engine = EmbeddingEngine(
                settings=_router_settings or {"embedding.provider": "local"},
                db_engine=None,
            )
            await run_analysis(self.agent_db, engine)
        except Exception:
            logger.debug("Background optimization analysis failed", exc_info=True)
