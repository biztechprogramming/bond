"""Lightweight per-turn skill activation tracker.

Collects implicit signals (reference reads, task completion) during a turn
and flushes pending updates to skills_db at turn end.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)


@dataclass
class _Activation:
    activation_id: str
    skill_id: str
    skill_path: str  # path to SKILL.md — used to detect reference reads
    session_id: str
    references_read: int = 0
    task_completed: bool = False


class SkillTracker:
    """Tracks active skill activations for the current turn.

    Instantiate per-turn, call the event methods, then flush() at turn end.
    """

    def __init__(self) -> None:
        self._activations: dict[str, _Activation] = {}  # activation_id -> Activation

    @property
    def has_activations(self) -> bool:
        return bool(self._activations)

    def on_skill_activated(
        self, activation_id: str, skill_id: str, skill_path: str, session_id: str
    ) -> None:
        """Record that a skill was activated (SKILL.md read)."""
        self._activations[activation_id] = _Activation(
            activation_id=activation_id,
            skill_id=skill_id,
            skill_path=skill_path,
            session_id=session_id,
        )
        logger.debug("Skill activated: %s (%s)", skill_id, activation_id)

    def on_file_read(self, filepath: str) -> None:
        """Check if a file read is in a skill's references/ directory.

        If so, increment references_read for that activation.
        """
        if not self._activations:
            return

        fp = PurePosixPath(filepath)
        for act in self._activations.values():
            # Skill path points to SKILL.md; references/ is a sibling directory
            skill_dir = PurePosixPath(act.skill_path).parent
            refs_dir = skill_dir / "references"
            try:
                fp.relative_to(refs_dir)
                act.references_read += 1
                logger.debug(
                    "Reference read for %s: %s (total: %d)",
                    act.skill_id, filepath, act.references_read,
                )
            except ValueError:
                pass  # not under this skill's references/

    def on_turn_complete(self) -> None:
        """Mark all active activations as task_completed."""
        for act in self._activations.values():
            act.task_completed = True

    async def flush(self) -> None:
        """Write all pending updates to skills_db."""
        if not self._activations:
            return

        from backend.app.agent.tools.skills_db import _get_db

        db = await _get_db()
        try:
            now = time.time()
            for act in self._activations.values():
                await db.execute(
                    """UPDATE skill_usage
                       SET references_read = ?,
                           task_completed = ?,
                           loaded_at = COALESCE(loaded_at, ?)
                       WHERE id = ?""",
                    (act.references_read, int(act.task_completed), now, act.activation_id),
                )
                # Update total_uses in skill_scores if task was completed
                if act.task_completed:
                    await db.execute(
                        """UPDATE skill_scores
                           SET total_uses = total_uses + 1, updated_at = ?
                           WHERE skill_id = ?""",
                        (now, act.skill_id),
                    )
            await db.commit()
            logger.debug("Flushed %d skill activations", len(self._activations))
        finally:
            await db.close()
            self._activations.clear()
