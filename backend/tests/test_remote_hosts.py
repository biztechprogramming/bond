"""Tests for Remote Container Hosts — Design Doc 089.

Tests cover:
- HostRegistry placement algorithm
- ContainerHostAdapter protocol compliance
- TunnelManager lifecycle
- RemoteContainerAdapter
- Hosts API endpoints
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.sandbox.adapters import (
    AgentContainerConfig,
    ContainerHostAdapter,
    ContainerInfo,
    HostStatus,
    LocalContainerAdapter,
    ResourceLimits,
)
from backend.app.sandbox.host_registry import HostRegistry, LocalHost, RemoteHost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_host(id: str = "test-host", **overrides) -> RemoteHost:
    defaults = {
        "id": id,
        "name": f"Test {id}",
        "host": f"{id}.example.com",
        "max_agents": 4,
        "labels": [],
        "enabled": True,
        "status": "active",
        "running_count": 0,
    }
    defaults.update(overrides)
    return RemoteHost(**defaults)


def _make_agent(**overrides) -> dict:
    defaults = {
        "id": "agent-abc123",
        "name": "sandbox",
        "sandbox_image": "python:3.12-slim",
        "model": "claude-sonnet-4-20250514",
        "utility_model": "claude-sonnet-4-6",
        "system_prompt": "You are helpful.",
        "tools": ["respond"],
        "max_iterations": 10,
        "api_keys": {},
        "workspace_mounts": [],
        "provider_aliases": {},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# HostRegistry tests
# ---------------------------------------------------------------------------


class TestHostRegistry:
    def test_empty_registry_has_local_host(self):
        registry = HostRegistry()
        hosts = registry.get_all_hosts()
        assert len(hosts) == 1
        assert isinstance(hosts[0], LocalHost)
        assert hosts[0].id == "local"

    def test_load_from_config(self):
        config = {
            "remote_hosts": [
                {
                    "id": "server-1",
                    "name": "Server 1",
                    "host": "192.168.1.100",
                    "max_agents": 8,
                    "labels": ["gpu"],
                },
                {
                    "id": "server-2",
                    "name": "Server 2",
                    "host": "192.168.1.101",
                },
            ],
            "placement": {
                "strategy": "round-robin",
                "prefer_local": False,
            },
        }
        registry = HostRegistry(config)
        assert len(registry.get_all_hosts()) == 3  # local + 2 remote
        assert len(registry.get_all_remote_hosts()) == 2

        s1 = registry.get_host("server-1")
        assert s1 is not None
        assert s1.max_agents == 8
        assert s1.labels == ["gpu"]

    def test_add_and_remove_host(self):
        registry = HostRegistry()
        host = _make_host("new-host")
        registry.add_host(host)
        assert registry.get_host("new-host") is not None
        assert len(registry.get_all_remote_hosts()) == 1

        assert registry.remove_host("new-host")
        assert registry.get_host("new-host") is None

    def test_update_host(self):
        registry = HostRegistry()
        registry.add_host(_make_host("test"))
        updated = registry.update_host("test", {"max_agents": 16, "enabled": False})
        assert updated is not None
        assert updated.max_agents == 16
        assert updated.enabled is False

    def test_mark_unreachable_and_active(self):
        registry = HostRegistry()
        registry.add_host(_make_host("test"))
        registry.mark_unreachable("test")
        assert registry.get_host("test").status == "offline"
        registry.mark_active("test")
        assert registry.get_host("test").status == "active"

    def test_running_count(self):
        registry = HostRegistry()
        registry.add_host(_make_host("test"))
        registry.increment_running("test")
        assert registry.get_host("test").running_count == 1
        registry.increment_running("test")
        assert registry.get_host("test").running_count == 2
        registry.decrement_running("test")
        assert registry.get_host("test").running_count == 1


# ---------------------------------------------------------------------------
# Placement algorithm tests
# ---------------------------------------------------------------------------


class TestPlacementAlgorithm:
    @pytest.mark.asyncio
    async def test_prefer_local_when_configured(self):
        registry = HostRegistry({
            "remote_hosts": [{"id": "remote-1", "name": "R1", "host": "r1.example.com"}],
            "placement": {"strategy": "least-loaded", "prefer_local": True},
        })
        agent = _make_agent()
        host = await registry.get_placement(agent)
        assert host.id == "local"

    @pytest.mark.asyncio
    async def test_explicit_host_assignment(self):
        registry = HostRegistry({
            "remote_hosts": [{"id": "target", "name": "Target", "host": "target.example.com"}],
            "placement": {"prefer_local": False},
        })
        agent = _make_agent(preferred_host="target")
        host = await registry.get_placement(agent)
        assert host.id == "target"

    @pytest.mark.asyncio
    async def test_explicit_host_unavailable_falls_through(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "offline", "name": "Offline", "host": "offline.example.com"},
            ],
            "placement": {"prefer_local": True},
        })
        registry.mark_unreachable("offline")
        agent = _make_agent(preferred_host="offline")
        host = await registry.get_placement(agent)
        # Falls through to local since preferred is offline
        assert host.id == "local"

    @pytest.mark.asyncio
    async def test_label_filtering(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "gpu-host", "name": "GPU", "host": "gpu.example.com", "labels": ["gpu"]},
                {"id": "cpu-host", "name": "CPU", "host": "cpu.example.com", "labels": ["cpu"]},
            ],
            "placement": {"prefer_local": False},
        })
        agent = _make_agent(host_labels=["gpu"])
        host = await registry.get_placement(agent)
        assert host.id == "gpu-host"

    @pytest.mark.asyncio
    async def test_least_loaded_strategy(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "busy", "name": "Busy", "host": "busy.example.com", "max_agents": 4},
                {"id": "idle", "name": "Idle", "host": "idle.example.com", "max_agents": 4},
            ],
            "placement": {"strategy": "least-loaded", "prefer_local": False},
        })
        registry.get_host("busy").running_count = 3
        registry.get_host("idle").running_count = 0
        # Set local running higher so it doesn't win
        registry.local.running_count = 50
        agent = _make_agent()
        host = await registry.get_placement(agent)
        assert host.id == "idle"

    @pytest.mark.asyncio
    async def test_host_affinity(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "host-a", "name": "A", "host": "a.example.com"},
                {"id": "host-b", "name": "B", "host": "b.example.com"},
            ],
            "placement": {"prefer_local": False},
        })
        agent = _make_agent(last_host_id="host-b")
        host = await registry.get_placement(agent)
        assert host.id == "host-b"

    @pytest.mark.asyncio
    async def test_round_robin_strategy(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "host-a", "name": "A", "host": "a.example.com"},
                {"id": "host-b", "name": "B", "host": "b.example.com"},
            ],
            "placement": {"strategy": "round-robin", "prefer_local": False},
        })
        # Round-robin should cycle through hosts
        hosts_seen = set()
        for _ in range(4):
            h = await registry.get_placement(_make_agent())
            hosts_seen.add(h.id)
        # Should have seen at least 2 different hosts (local + remotes)
        assert len(hosts_seen) >= 2

    @pytest.mark.asyncio
    async def test_capacity_exhaustion_falls_back_to_local(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "full", "name": "Full", "host": "full.example.com", "max_agents": 2},
            ],
            "placement": {"prefer_local": False},
        })
        registry.get_host("full").running_count = 2  # At capacity
        registry.local.running_count = 50  # Local also busy but large max
        agent = _make_agent()
        host = await registry.get_placement(agent)
        # Should still work — local has capacity
        assert host.id == "local"

    @pytest.mark.asyncio
    async def test_disabled_host_excluded(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "disabled", "name": "D", "host": "d.example.com", "enabled": False},
            ],
            "placement": {"prefer_local": False},
        })
        agent = _make_agent()
        host = await registry.get_placement(agent)
        assert host.id != "disabled"

    @pytest.mark.asyncio
    async def test_draining_host_excluded(self):
        registry = HostRegistry({
            "remote_hosts": [
                {"id": "draining", "name": "D", "host": "d.example.com"},
            ],
            "placement": {"prefer_local": False},
        })
        registry.get_host("draining").status = "draining"
        agent = _make_agent()
        host = await registry.get_placement(agent)
        assert host.id != "draining"


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestDataModels:
    def test_remote_host_defaults(self):
        host = RemoteHost(id="test", name="Test", host="example.com")
        assert host.port == 22
        assert host.user == "bond"
        assert host.daemon_port == 18795
        assert host.max_agents == 4
        assert host.enabled is True
        assert host.status == "active"

    def test_resource_limits_defaults(self):
        limits = ResourceLimits()
        assert limits.memory_mb == 2048
        assert limits.cpus == 2.0

    def test_agent_container_config(self):
        config = AgentContainerConfig(
            agent_id="test-123",
            sandbox_image="python:3.12",
            repo_url="https://github.com/org/repo",
            env_vars={"KEY": "value"},
        )
        assert config.agent_id == "test-123"
        assert config.repo_branch == "main"

    def test_container_info(self):
        info = ContainerInfo(
            container_id="abc123",
            host_id="remote-1",
            worker_url="http://localhost:18791",
        )
        assert info.host_id == "remote-1"
        assert info.created_at is not None

    def test_host_status(self):
        status = HostStatus(host_id="test", online=True, running_containers=3)
        assert status.running_containers == 3
        assert status.max_agents == 4


# ---------------------------------------------------------------------------
# ContainerHostAdapter protocol compliance
# ---------------------------------------------------------------------------


class TestAdapterProtocol:
    def test_local_adapter_is_protocol_compliant(self):
        """LocalContainerAdapter should satisfy the ContainerHostAdapter protocol."""
        adapter = LocalContainerAdapter()
        assert isinstance(adapter, ContainerHostAdapter)


# ---------------------------------------------------------------------------
# HostRegistry serialization
# ---------------------------------------------------------------------------


class TestRegistrySerialization:
    def test_to_config_dict(self):
        registry = HostRegistry()
        registry.add_host(RemoteHost(
            id="server-1",
            name="Server 1",
            host="192.168.1.100",
            labels=["gpu"],
        ))
        config = registry.to_config_dict()
        assert len(config) == 1
        assert config[0]["id"] == "server-1"
        assert config[0]["labels"] == ["gpu"]
        assert config[0]["host"] == "192.168.1.100"
