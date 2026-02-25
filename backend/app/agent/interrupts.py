"""In-memory interrupt flags for agent turns.

Uses asyncio.Event per conversation for fast, lock-free signaling.
"""

from __future__ import annotations

import asyncio

_interrupts: dict[str, asyncio.Event] = {}


def register_turn(conversation_id: str) -> None:
    """Register an interrupt slot for a conversation turn."""
    _interrupts[conversation_id] = asyncio.Event()


def unregister_turn(conversation_id: str) -> None:
    """Clean up the interrupt slot after a turn completes."""
    _interrupts.pop(conversation_id, None)


def set_interrupt(conversation_id: str) -> bool:
    """Signal an interrupt for a running turn. Returns True if a turn was active."""
    event = _interrupts.get(conversation_id)
    if event is not None:
        event.set()
        return True
    return False


def check_interrupt(conversation_id: str) -> bool:
    """Check and clear the interrupt flag. Returns True if interrupted."""
    event = _interrupts.get(conversation_id)
    if event and event.is_set():
        event.clear()
        return True
    return False


def is_turn_active(conversation_id: str) -> bool:
    """Check if a turn is currently running for this conversation."""
    return conversation_id in _interrupts
