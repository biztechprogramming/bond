"""Replica routing prototype — instance selection logic.

Tests the pure router (_select_instance / _replica_count) without Docker or
config side effects, by constructing a bare SandboxManager and wiring only the
state the router touches.
"""
import os

import pytest

from backend.app.sandbox.manager import SandboxManager


def _mgr() -> SandboxManager:
    # Bypass __init__ (loads bond.json + builds registry/adapters); the router
    # only needs _rr_counter.
    m = object.__new__(SandboxManager)
    m._rr_counter = {}
    return m


def test_single_instance_returns_none():
    """replicas == 1 -> no instance suffix, identical to legacy behaviour."""
    m = _mgr()
    assert m._select_instance({"id": "agent-a"}, "conv-1") is None
    assert m._select_instance({"id": "agent-a", "replicas": 1}, None) is None


def test_sticky_by_conversation_is_stable():
    """Same conversation always routes to the same instance (in range)."""
    m = _mgr()
    agent = {"id": "agent-a", "replicas": 4}
    first = m._select_instance(agent, "conversation-xyz")
    again = m._select_instance(agent, "conversation-xyz")
    assert first == again
    assert 0 <= first < 4


def test_round_robin_fanout_when_no_conversation():
    """No conversation_id -> spread across the pool round-robin."""
    m = _mgr()
    agent = {"id": "agent-a", "replicas": 3}
    picks = [m._select_instance(agent, None) for _ in range(6)]
    assert picks == [0, 1, 2, 0, 1, 2]


def test_env_override_forces_replicas(monkeypatch):
    """BOND_AGENT_REPLICAS lets you demo replicas without a schema change."""
    m = _mgr()
    monkeypatch.setenv("BOND_AGENT_REPLICAS", "2")
    agent = {"id": "agent-a"}  # no replicas field
    assert m._replica_count(agent) == 2
    assert m._select_instance(agent, None) in (0, 1)


def test_bad_replica_values_fall_back_to_one():
    m = _mgr()
    assert m._replica_count({"id": "a", "replicas": "not-a-number"}) == 1
    assert m._replica_count({"id": "a", "replicas": 0}) == 1
    assert m._replica_count({"id": "a", "replicas": None}) == 1


@pytest.mark.asyncio
async def test_ensure_running_for_passes_instance(monkeypatch):
    """ensure_running_for tags the scoped agent with _instance and surfaces it."""
    m = _mgr()
    captured = {}

    async def fake_ensure_running(agent):
        captured["agent"] = agent
        return {"worker_url": "http://localhost:1", "container_id": "cid"}

    m.ensure_running = fake_ensure_running  # type: ignore[assignment]
    monkeypatch.setenv("BOND_AGENT_REPLICAS", "3")

    result = await m.ensure_running_for({"id": "agent-a"}, "conv-1")

    assert "_instance" in captured["agent"]
    assert result["instance"] == captured["agent"]["_instance"]
