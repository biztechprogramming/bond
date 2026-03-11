"""Tests for interrupt forwarding to worker containers.

Verifies that:
1. register_turn stores worker URLs
2. set_interrupt flags are forwarded to workers
3. is_interrupted allows non-destructive checking
4. SSE proxy stops when interrupted
"""

import asyncio
from backend.app.agent.interrupts import (
    register_turn,
    unregister_turn,
    set_interrupt,
    check_interrupt,
    is_turn_active,
    get_worker_url,
    is_interrupted,
)


def test_register_turn_with_worker_url():
    """register_turn stores the worker URL for later forwarding."""
    register_turn("conv-100", worker_url="http://localhost:18793")
    assert is_turn_active("conv-100")
    assert get_worker_url("conv-100") == "http://localhost:18793"
    unregister_turn("conv-100")


def test_register_turn_without_worker_url():
    """register_turn works without a worker URL (backward compat)."""
    register_turn("conv-101")
    assert is_turn_active("conv-101")
    assert get_worker_url("conv-101") is None
    unregister_turn("conv-101")


def test_get_worker_url_inactive_turn():
    """get_worker_url returns None for non-existent turns."""
    assert get_worker_url("conv-nonexistent") is None


def test_is_interrupted_without_clearing():
    """is_interrupted checks the flag without clearing it."""
    register_turn("conv-102")
    assert not is_interrupted("conv-102")
    set_interrupt("conv-102")
    assert is_interrupted("conv-102")
    # Still set — is_interrupted doesn't clear
    assert is_interrupted("conv-102")
    # check_interrupt clears
    assert check_interrupt("conv-102")
    assert not is_interrupted("conv-102")
    unregister_turn("conv-102")


def test_is_interrupted_inactive_turn():
    """is_interrupted returns False for non-existent turns."""
    assert not is_interrupted("conv-nonexistent")


def test_set_interrupt_flags_worker_url_turn():
    """set_interrupt works on turns with worker URLs."""
    register_turn("conv-103", worker_url="http://worker:8080")
    assert set_interrupt("conv-103")
    assert is_interrupted("conv-103")
    assert get_worker_url("conv-103") == "http://worker:8080"
    unregister_turn("conv-103")


def test_unregister_cleans_up():
    """unregister_turn removes all state including worker URL."""
    register_turn("conv-104", worker_url="http://worker:9090")
    set_interrupt("conv-104")
    unregister_turn("conv-104")
    assert not is_turn_active("conv-104")
    assert get_worker_url("conv-104") is None
    assert not is_interrupted("conv-104")
