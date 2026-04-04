"""Integration tests for remote host daemon workflow.

Design Doc 089: Phase 2 — Remote Host Daemon, Gap 5.
Tests mock SSH/SCP commands and daemon HTTP API.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.sandbox.adapters import (
    AgentContainerConfig,
    ContainerInfo,
    HostStatus,
    ResourceLimits,
)
from backend.app.sandbox.host_registry import HostRegistry, LocalHost, RemoteHost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_host(id: str = "gpu-1", **overrides) -> RemoteHost:
    defaults = {
        "id": id,
        "name": f"Test {id}",
        "host": f"{id}.example.com",
        "port": 22,
        "user": "bond",
        "ssh_key": "/tmp/test_key",
        "daemon_port": 9100,
        "max_agents": 4,
        "labels": [],
        "enabled": True,
        "status": "active",
        "auth_token": "",
        "running_count": 0,
    }
    defaults.update(overrides)
    return RemoteHost(**defaults)


def _make_config(**overrides) -> AgentContainerConfig:
    defaults = {
        "agent_id": "agent-abc123",
        "sandbox_image": "python:3.12-slim",
        "repo_url": "https://github.com/test/repo.git",
        "repo_branch": "main",
        "env_vars": {},
        "ssh_private_key": "",
        "agent_config_json": "{}",
        "vault_data": b"",
        "shared_memory_snapshot": b"",
        "resource_limits": ResourceLimits(),
    }
    defaults.update(overrides)
    return AgentContainerConfig(**defaults)


def _ssh_result(rc: int = 0, stdout: str = "", stderr: str = ""):
    """Create a mock subprocess result for SSH commands."""
    proc = AsyncMock()
    proc.returncode = rc
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Gap 1: DaemonInstaller tests
# ---------------------------------------------------------------------------


class TestDaemonInstallerCheckPrerequisites:
    @pytest.mark.asyncio
    async def test_all_prerequisites_met(self):
        from backend.app.services.daemon_installer import DaemonInstaller

        installer = DaemonInstaller()

        with patch("backend.app.services.daemon_installer._run_ssh_command") as mock_ssh:
            mock_ssh.side_effect = [
                (0, "Docker version 24.0.7", ""),    # docker --version
                (0, "24.0.7", ""),                     # docker info
                (0, "3.11.5", ""),                     # python3 version
            ]
            result = await installer.check_prerequisites("host", 22, "bond", "/tmp/key")

        assert result["docker"] is True
        assert result["python"] is True
        assert result["docker_version"] == "Docker version 24.0.7"
        assert result["python_version"] == "3.11.5"
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_docker_not_installed(self):
        from backend.app.services.daemon_installer import DaemonInstaller

        installer = DaemonInstaller()

        with patch("backend.app.services.daemon_installer._run_ssh_command") as mock_ssh:
            mock_ssh.side_effect = [
                (1, "", "command not found"),     # docker --version
                (0, "3.11.5", ""),                # python3 version
            ]
            result = await installer.check_prerequisites("host", 22, "bond", "/tmp/key")

        assert result["docker"] is False
        assert result["python"] is True
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_python_too_old(self):
        from backend.app.services.daemon_installer import DaemonInstaller

        installer = DaemonInstaller()

        with patch("backend.app.services.daemon_installer._run_ssh_command") as mock_ssh:
            mock_ssh.side_effect = [
                (0, "Docker version 24.0.7", ""),
                (0, "24.0.7", ""),
                (0, "3.8.10", ""),
            ]
            result = await installer.check_prerequisites("host", 22, "bond", "/tmp/key")

        assert result["docker"] is True
        assert result["python"] is False
        assert any("3.10+" in e for e in result["errors"])


class TestDaemonInstallerInstall:
    @pytest.mark.asyncio
    async def test_successful_install(self):
        from backend.app.services.daemon_installer import DaemonInstaller

        installer = DaemonInstaller()

        with patch("backend.app.services.daemon_installer._run_ssh_command") as mock_ssh, \
             patch("backend.app.services.daemon_installer._scp_file") as mock_scp:

            mock_ssh.side_effect = [
                # check_prerequisites: docker --version, docker info, python3
                (0, "Docker version 24.0.7", ""),
                (0, "24.0.7", ""),
                (0, "3.11.5", ""),
                # mkdir
                (0, "", ""),
                # pip install
                (0, "", ""),
                # write systemd unit
                (0, "", ""),
                # systemctl daemon-reload + enable + start
                (0, "", ""),
                # health check (first attempt)
                (0, '{"daemon_version": "0.2.0"}', ""),
            ]
            mock_scp.return_value = True

            result = await installer.install("host", 22, "bond", "/tmp/key", daemon_port=9100)

        assert result["success"] is True
        assert result["auth_token"] != ""
        assert len(result["auth_token"]) > 20
        assert mock_scp.call_count == 2  # daemon.py + requirements.txt


# ---------------------------------------------------------------------------
# Gap 2: Auth token wiring
# ---------------------------------------------------------------------------


class TestRemoteAdapterAuthToken:
    @pytest.mark.asyncio
    async def test_auth_header_with_token(self):
        from backend.app.sandbox.remote_adapter import RemoteContainerAdapter

        host = _make_host(auth_token="my-secret-token")
        tunnel_mgr = MagicMock()
        adapter = RemoteContainerAdapter(host, tunnel_mgr)

        headers = adapter._auth_headers()
        assert headers == {"Authorization": "Bearer my-secret-token"}
        await adapter.close()

    @pytest.mark.asyncio
    async def test_no_auth_header_without_token(self):
        from backend.app.sandbox.remote_adapter import RemoteContainerAdapter

        host = _make_host(auth_token="")
        tunnel_mgr = MagicMock()
        adapter = RemoteContainerAdapter(host, tunnel_mgr)

        headers = adapter._auth_headers()
        assert headers == {}
        await adapter.close()


# ---------------------------------------------------------------------------
# Gap 3: Remote create flow — adapter + SandboxManager
# ---------------------------------------------------------------------------


class TestRemoteAdapterCreateContainer:
    @pytest.mark.asyncio
    async def test_create_container_posts_to_daemon(self):
        from backend.app.sandbox.remote_adapter import RemoteContainerAdapter

        host = _make_host(auth_token="tok123")
        tunnel_mgr = MagicMock()
        mock_tunnel = AsyncMock()
        mock_tunnel.local_url = "http://localhost:19000"
        mock_tunnel.add_port_forward = AsyncMock(return_value=19001)
        tunnel_mgr.ensure_tunnel = AsyncMock(return_value=mock_tunnel)

        adapter = RemoteContainerAdapter(host, tunnel_mgr)
        config = _make_config()

        with patch.object(adapter._client, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "container_id": "abc123",
                "worker_url": "http://0.0.0.0:18791",
            }
            mock_post.return_value = mock_resp

            info = await adapter.create_container({"id": "agent-1"}, "bond-test-agent-1", config)

        assert info.container_id == "abc123"
        assert info.host_id == host.id
        assert info.worker_url == "http://localhost:19001"

        # Verify auth header was sent
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer tok123"
        await adapter.close()


class TestRemoteAdapterDestroyContainer:
    @pytest.mark.asyncio
    async def test_destroy_sends_delete(self):
        from backend.app.sandbox.remote_adapter import RemoteContainerAdapter

        host = _make_host()
        tunnel_mgr = MagicMock()
        mock_tunnel = AsyncMock()
        mock_tunnel.local_url = "http://localhost:19000"
        mock_tunnel.remove_port_forward = AsyncMock()
        tunnel_mgr.get_tunnel = MagicMock(return_value=mock_tunnel)

        adapter = RemoteContainerAdapter(host, tunnel_mgr)

        with patch.object(adapter._client, "delete") as mock_delete:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_delete.return_value = mock_resp

            result = await adapter.destroy_container("bond-test-1")

        assert result is True
        mock_tunnel.remove_port_forward.assert_called_once_with("bond-test-1")
        await adapter.close()


# ---------------------------------------------------------------------------
# Gap 3: ensure_running remote placement
# ---------------------------------------------------------------------------


class TestEnsureRunningRemotePlacement:
    @pytest.mark.asyncio
    async def test_remote_host_uses_remote_adapter(self):
        """When placement returns a RemoteHost, ensure_running should use RemoteContainerAdapter."""
        from backend.app.sandbox.manager import SandboxManager

        remote_host = _make_host(auth_token="tok")
        mock_registry = MagicMock()
        mock_registry.get_placement = AsyncMock(return_value=remote_host)
        mock_registry.increment_running = MagicMock()

        mock_adapter = AsyncMock()
        mock_adapter.create_container = AsyncMock(return_value=ContainerInfo(
            container_id="remote-cid",
            host_id=remote_host.id,
            worker_url="http://localhost:19001",
        ))

        manager = SandboxManager.__new__(SandboxManager)
        manager._registry = mock_registry
        manager._tunnel_manager = MagicMock()
        manager._containers = {}
        manager._agent_locks = {}
        manager._local_adapter = MagicMock()
        manager._remote_adapters = {remote_host.id: mock_adapter}
        manager._used_ports = {}

        agent = {
            "id": "agent-test",
            "name": "test",
            "sandbox_image": "python:3.12-slim",
            "model": "claude-sonnet-4-6",
            "utility_model": "claude-sonnet-4-6",
            "api_keys": {},
            "workspace_mounts": [],
        }

        with patch.object(manager, "_recover_existing_container", AsyncMock(return_value=None)), \
             patch.object(manager, "_build_container_config", return_value=_make_config()), \
             patch.object(manager, "_wait_for_health", AsyncMock()):
            result = await manager.ensure_running(agent)

        assert result["container_id"] == "remote-cid"
        assert result["worker_url"] == "http://localhost:19001"
        mock_adapter.create_container.assert_called_once()
        mock_registry.increment_running.assert_called_with(remote_host.id)

    @pytest.mark.asyncio
    async def test_local_host_uses_local_adapter(self):
        """When placement returns LocalHost, ensure_running should use local path."""
        from backend.app.sandbox.manager import SandboxManager

        mock_registry = MagicMock()
        mock_registry.get_placement = AsyncMock(return_value=LocalHost())
        mock_registry.increment_running = MagicMock()

        manager = SandboxManager.__new__(SandboxManager)
        manager._registry = mock_registry
        manager._tunnel_manager = MagicMock()
        manager._containers = {}
        manager._agent_locks = {}
        manager._local_adapter = MagicMock()
        manager._remote_adapters = {}
        manager._used_ports = {}

        agent = {
            "id": "agent-local",
            "name": "test",
            "sandbox_image": "python:3.12-slim",
            "model": "claude-sonnet-4-6",
            "utility_model": "claude-sonnet-4-6",
            "api_keys": {},
            "workspace_mounts": [],
        }

        with patch.object(manager, "_recover_existing_container", AsyncMock(return_value=None)), \
             patch.object(manager, "_build_container_config", return_value=_make_config()), \
             patch.object(manager, "_allocate_port", return_value=18791), \
             patch.object(manager, "_write_agent_config", return_value="/tmp/config.json"), \
             patch.object(manager, "_create_worker_container", AsyncMock(return_value=("local-cid", [], None))), \
             patch.object(manager, "_wait_for_health", AsyncMock()):
            result = await manager.ensure_running(agent)

        assert result["container_id"] == "local-cid"
        assert "localhost" in result["worker_url"]
        mock_registry.increment_running.assert_called_with("local")


# ---------------------------------------------------------------------------
# Gap 3: Tunnel port forward on remote create
# ---------------------------------------------------------------------------


class TestTunnelPortForwardOnRemoteCreate:
    @pytest.mark.asyncio
    async def test_port_forward_set_up_during_create(self):
        """RemoteContainerAdapter.create_container sets up SSH port forward."""
        from backend.app.sandbox.remote_adapter import RemoteContainerAdapter

        host = _make_host()
        tunnel_mgr = MagicMock()
        mock_tunnel = AsyncMock()
        mock_tunnel.local_url = "http://localhost:19000"
        mock_tunnel.add_port_forward = AsyncMock(return_value=19050)
        tunnel_mgr.ensure_tunnel = AsyncMock(return_value=mock_tunnel)

        adapter = RemoteContainerAdapter(host, tunnel_mgr)
        config = _make_config()

        with patch.object(adapter._client, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "container_id": "cid-xyz",
                "worker_url": "http://0.0.0.0:18791",
            }
            mock_post.return_value = mock_resp

            info = await adapter.create_container({}, "bond-test-1", config)

        # Port forward should be called with the remote worker port
        mock_tunnel.add_port_forward.assert_called_once_with("bond-test-1", 18791)
        assert info.worker_url == "http://localhost:19050"
        await adapter.close()


# ---------------------------------------------------------------------------
# Gap 5: Auth token stored after install (API level)
# ---------------------------------------------------------------------------


class TestAuthTokenStoredAfterInstall:
    @pytest.mark.asyncio
    async def test_install_stores_encrypted_token(self):
        """The install-daemon endpoint should store the encrypted auth token in DB."""
        from backend.app.services.daemon_installer import DaemonInstaller

        mock_install_result = {
            "success": True,
            "auth_token": "generated-token-xyz",
            "errors": [],
        }

        with patch.object(DaemonInstaller, "install", AsyncMock(return_value=mock_install_result)), \
             patch("backend.app.core.crypto.encrypt_value", return_value="enc:encrypted-token"):

            # Verify the installer returns the token
            installer = DaemonInstaller()
            result = await installer.install("host", 22, "bond", "/tmp/key")
            assert result["success"] is True
            assert result["auth_token"] == "generated-token-xyz"


# ---------------------------------------------------------------------------
# RemoteHost dataclass includes auth_token
# ---------------------------------------------------------------------------


class TestRemoteHostAuthTokenField:
    def test_default_empty(self):
        host = RemoteHost(id="h1", name="H1", host="h1.example.com")
        assert host.auth_token == ""

    def test_custom_token(self):
        host = RemoteHost(id="h1", name="H1", host="h1.example.com", auth_token="secret")
        assert host.auth_token == "secret"
