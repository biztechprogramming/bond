"""Tests for Bond Host Daemon — Design Doc 089 Phase 2.

Tests the FastAPI daemon endpoints using httpx.ASGITransport (no real Docker).
All asyncio.create_subprocess_exec calls are mocked.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_subprocess(stdout=b"", stderr=b"", returncode=0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    """Reset module-level state between tests."""
    from backend.app.sandbox import bond_host_daemon as mod

    mod._tracked_containers.clear()
    mod._config.auth_token = ""
    mod._config.max_agents = 4
    mod._config.workspace_root = "/tmp/bond-test-workspaces"
    mod._config.shared_root = "/tmp/bond-test-shared"
    yield
    mod._tracked_containers.clear()


@pytest.fixture
def mock_docker():
    with patch("backend.app.sandbox.bond_host_daemon.asyncio.create_subprocess_exec") as mock:
        mock.return_value = _mock_subprocess(stdout=b"abc123def456\n")
        yield mock


@pytest.fixture
async def client():
    from backend.app.sandbox.bond_host_daemon import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(stdout=b"")
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "daemon_version" in data
        assert "api_version" in data
        assert "cpu_percent" in data
        assert "max_agents" in data


class TestCreateContainer:
    @pytest.mark.asyncio
    async def test_create_container_success(self, client, mock_docker):
        # Mock sequence: _find_container (inspect fails) -> _pull_or_build_image (inspect ok)
        # -> docker run -> _get_container_port
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else ""
            full_cmd = " ".join(str(a) for a in args)

            if "inspect" in full_cmd and "-f" in full_cmd:
                # _find_container — not found
                return _mock_subprocess(returncode=1)
            if "image" in full_cmd and "inspect" in full_cmd:
                # image exists
                return _mock_subprocess(returncode=0)
            if "run" in full_cmd:
                return _mock_subprocess(stdout=b"abc123def456\n")
            if "port" in full_cmd:
                return _mock_subprocess(stdout=b"0.0.0.0:32768\n")
            return _mock_subprocess()

        mock_docker.side_effect = _side_effect

        resp = await client.post("/containers", json={
            "key": "test-agent-1",
            "image": "bond-agent:latest",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["container_id"] == "abc123def456"
        assert "32768" in data["worker_url"]

    @pytest.mark.asyncio
    async def test_create_container_idempotent(self, client, mock_docker):
        from backend.app.sandbox.bond_host_daemon import _tracked_containers

        _tracked_containers["test-agent-1"] = {
            "id": "existing123",
            "name": "test-agent-1",
            "running": True,
            "port": 32000,
        }

        resp = await client.post("/containers", json={
            "key": "test-agent-1",
            "image": "bond-agent:latest",
        })
        assert resp.status_code == 200
        assert resp.json()["container_id"] == "existing123"

    @pytest.mark.asyncio
    async def test_create_container_at_capacity(self, client, mock_docker):
        from backend.app.sandbox.bond_host_daemon import _tracked_containers, _config

        _config.max_agents = 2

        # Docker inspect returns not-found for the new key
        mock_docker.return_value = _mock_subprocess(returncode=1)

        for i in range(2):
            _tracked_containers[f"agent-{i}"] = {
                "id": f"id{i}",
                "name": f"agent-{i}",
                "running": True,
                "port": 32000 + i,
            }

        resp = await client.post("/containers", json={
            "key": "agent-new",
            "image": "bond-agent:latest",
        })
        assert resp.status_code == 429


class TestDestroyContainer:
    @pytest.mark.asyncio
    async def test_destroy_container(self, client, mock_docker):
        from backend.app.sandbox.bond_host_daemon import _tracked_containers

        _tracked_containers["test-agent-1"] = {
            "id": "abc123",
            "name": "test-agent-1",
            "running": True,
        }

        resp = await client.delete("/containers/test-agent-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "destroyed"
        assert "test-agent-1" not in _tracked_containers


class TestContainerHealth:
    @pytest.mark.asyncio
    async def test_container_health(self, client, mock_docker):
        # Mock inspect returning a running container
        async def _side_effect(*args, **kwargs):
            full_cmd = " ".join(str(a) for a in args)
            if "inspect" in full_cmd and "-f" in full_cmd:
                return _mock_subprocess(stdout=b"abc123456789\ttrue\t/test-agent-1")
            if "port" in full_cmd:
                return _mock_subprocess(stdout=b"0.0.0.0:32768\n")
            return _mock_subprocess()

        mock_docker.side_effect = _side_effect

        resp = await client.get("/containers/test-agent-1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True

    @pytest.mark.asyncio
    async def test_container_health_not_found(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(returncode=1)
        resp = await client.get("/containers/nonexistent/health")
        assert resp.status_code == 404


class TestContainerLogs:
    @pytest.mark.asyncio
    async def test_container_logs(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(
            stdout=b"line1\nline2\n", stderr=b"err1\n",
        )
        resp = await client.get("/containers/test-agent-1/logs")
        assert resp.status_code == 200
        assert "line1" in resp.json()["logs"]


class TestListContainers:
    @pytest.mark.asyncio
    async def test_list_containers(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(
            stdout=b"abc123\tbond-agent-1\tUp 5 minutes\n"
                   b"def456\tbond-agent-2\tExited (0) 1 hour ago\n",
        )
        resp = await client.get("/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["containers"]) == 2


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_auth_middleware_rejects_bad_token(self, client, mock_docker):
        from backend.app.sandbox.bond_host_daemon import _config

        _config.auth_token = "secret-token"
        resp = await client.get("/containers")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_middleware_allows_health(self, client, mock_docker):
        from backend.app.sandbox.bond_host_daemon import _config

        _config.auth_token = "secret-token"
        mock_docker.return_value = _mock_subprocess(stdout=b"")
        resp = await client.get("/health")
        assert resp.status_code == 200


class TestStartupReconciliation:
    @pytest.mark.asyncio
    async def test_startup_reconciliation(self, mock_docker):
        mock_docker.return_value = _mock_subprocess(
            stdout=b"abc123\tbond-agent-1\tUp 5 minutes\n"
                   b"def456\tbond-agent-2\tUp 10 minutes\n",
        )
        from backend.app.sandbox.bond_host_daemon import (
            _reconcile_running_containers,
            _tracked_containers,
        )

        await _reconcile_running_containers()
        assert len(_tracked_containers) == 2
        assert "bond-agent-1" in _tracked_containers
        assert "bond-agent-2" in _tracked_containers


class TestCredentialCleanup:
    @pytest.mark.asyncio
    async def test_credential_cleanup(self, mock_docker):
        """Verify stale credential dirs are identified for cleanup."""
        # Container doesn't exist
        mock_docker.return_value = _mock_subprocess(returncode=1)

        with patch("backend.app.sandbox.bond_host_daemon.os.path.exists") as mock_exists, \
             patch("backend.app.sandbox.bond_host_daemon.os.listdir") as mock_listdir, \
             patch("backend.app.sandbox.bond_host_daemon.shutil.rmtree") as mock_rmtree:
            mock_exists.return_value = True
            mock_listdir.return_value = ["bond-creds-stale-agent", "other-dir"]

            from backend.app.sandbox.bond_host_daemon import _cleanup_stale_credentials
            await _cleanup_stale_credentials()

            mock_rmtree.assert_called_once_with(
                "/dev/shm/bond-creds-stale-agent", ignore_errors=True,
            )


class TestGitClone:
    @pytest.mark.asyncio
    async def test_git_clone_with_verify(self, mock_docker):
        call_log = []

        async def _side_effect(*args, **kwargs):
            full_cmd = " ".join(str(a) for a in args)
            call_log.append(full_cmd)
            if "clone" in full_cmd:
                return _mock_subprocess()
            if "rev-parse" in full_cmd:
                return _mock_subprocess(stdout=b"abc123\n")
            # credential remove and sanitize
            return _mock_subprocess(returncode=128)

        mock_docker.side_effect = _side_effect

        with patch("backend.app.sandbox.bond_host_daemon.os.path.exists", return_value=False), \
             patch("backend.app.sandbox.bond_host_daemon.shutil.rmtree"):
            from backend.app.sandbox.bond_host_daemon import _git_clone_with_verify
            await _git_clone_with_verify(
                "https://github.com/org/repo.git", "main", "/tmp/test-ws",
            )

        clone_calls = [c for c in call_log if "clone" in c]
        assert len(clone_calls) == 1
        verify_calls = [c for c in call_log if "rev-parse" in c]
        assert len(verify_calls) == 1


class TestVersionHeaders:
    @pytest.mark.asyncio
    async def test_version_headers(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(stdout=b"")
        resp = await client.get("/health")
        assert "X-Daemon-Version" in resp.headers
        assert "X-API-Version" in resp.headers


class TestExecEndpoint:
    @pytest.mark.asyncio
    async def test_exec_in_container(self, client, mock_docker):
        async def _side_effect(*args, **kwargs):
            full_cmd = " ".join(str(a) for a in args)
            if "inspect" in full_cmd:
                return _mock_subprocess(returncode=0)
            if "exec" in full_cmd:
                return _mock_subprocess(stdout=b"hello world\n")
            return _mock_subprocess()

        mock_docker.side_effect = _side_effect

        resp = await client.post("/containers/test-agent-1/exec", json={
            "command": ["echo", "hello"],
        })
        assert resp.status_code == 200
        assert "hello world" in resp.json()["stdout"]

    @pytest.mark.asyncio
    async def test_exec_not_found(self, client, mock_docker):
        mock_docker.return_value = _mock_subprocess(returncode=1)
        resp = await client.post("/containers/nonexistent/exec", json={
            "command": ["echo", "hi"],
        })
        assert resp.status_code == 404
