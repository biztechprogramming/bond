"""In-memory interrupt flags for agent turns.

Uses asyncio.Event per conversation for fast, lock-free signaling.
Tracks worker URLs so interrupts can be forwarded to containers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class _TurnState:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    worker_url: str | None = None


_interrupts: dict[str, _TurnState] = {}


def register_turn(conversation_id: str, worker_url: str | None = None) -> None:
    """Register an interrupt slot for a conversation turn."""
    _interrupts[conversation_id] = _TurnState(worker_url=worker_url)


def unregister_turn(conversation_id: str) -> None:
    """Clean up the interrupt slot after a turn completes."""
    _interrupts.pop(conversation_id, None)


def set_interrupt(conversation_id: str) -> bool:
    """Signal an interrupt for a running turn. Returns True if a turn was active."""
    state = _interrupts.get(conversation_id)
    if state is not None:
        state.event.set()
        return True
    return False


def check_interrupt(conversation_id: str) -> bool:
    """Check and clear the interrupt flag. Returns True if interrupted."""
    state = _interrupts.get(conversation_id)
    if state and state.event.is_set():
        state.event.clear()
        return True
    return False


def is_turn_active(conversation_id: str) -> bool:
    """Check if a turn is currently running for this conversation."""
    return conversation_id in _interrupts


def get_worker_url(conversation_id: str) -> str | None:
    """Return the worker URL for an active turn, if any."""
    state = _interrupts.get(conversation_id)
    return state.worker_url if state else None


def is_interrupted(conversation_id: str) -> bool:
    """Check if the interrupt flag is set (without clearing it)."""
    state = _interrupts.get(conversation_id)
    return state.event.is_set() if state else False
