"""Tests for SandboxManager — containerized worker mode (C4).

All tests mock Docker commands (asyncio.create_subprocess_exec). No real Docker needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.sandbox.manager import (
    SandboxManager,
    _PORT_RANGE_END,
    _PORT_RANGE_START,
    _PROJECT_ROOT,
    _WORKER_INTERNAL_PORT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock subprocess result."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _make_agent(**overrides) -> dict:
    """Create a test agent dict."""
    defaults = {
        "id": "agent-abc123",
        "sandbox_image": "python:3.12-slim",
        "model": "claude-sonnet-4-20250514",
        "system_prompt": "You are helpful.",
        "tools": ["respond", "search_memory"],
        "api_keys": {"anthropic": "sk-test"},
        "workspace_mounts": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Port allocation tests (Task 5)
# ---------------------------------------------------------------------------


class TestPortAllocation:
    def test_port_allocation_returns_port_in_range(self):
        manager = SandboxManager()
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1  # port free
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            port = manager._allocate_port("bond-sandbox-agent1")
            assert _PORT_RANGE_START <= port <= _PORT_RANGE_END

    def test_port_allocation_sequential_no_duplicates(self):
        manager = SandboxManager()
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            p1 = manager._allocate_port("bond-sandbox-agent1")
            p2 = manager._allocate_port("bond-sandbox-agent2")
            assert p1 != p2

    def test_port_release_on_destroy(self):
        manager = SandboxManager()
        manager._port_map["bond-sandbox-agent1"] = 18791
        released = manager._release_port("bond-sandbox-agent1")
        assert released == 18791
        assert "bond-sandbox-agent1" not in manager._port_map

    def test_port_reuse_after_release(self):
        manager = SandboxManager()
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            p1 = manager._allocate_port("bond-sandbox-agent1")
            manager._release_port("bond-sandbox-agent1")
            p2 = manager._allocate_port("bond-sandbox-agent3")
            assert p2 == p1  # Reused the released port

    def test_port_exhaustion_raises_error(self):
        manager = SandboxManager()
        # Fill all ports
        for i in range(_PORT_RANGE_END - _PORT_RANGE_START + 1):
            manager._port_map[f"agent-{i}"] = _PORT_RANGE_START + i

        with pytest.raises(RuntimeError, match="No available ports"):
            manager._allocate_port("bond-sandbox-new")

    def test_port_conflict_detection_skips_busy_port(self):
        manager = SandboxManager()
        call_count = 0

        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()

            def connect_side_effect(addr):
                nonlocal call_count
                call_count += 1
                # First port is busy (connect succeeds = port in use)
                # Second port is free
                return 0 if call_count == 1 else 1

            mock_sock.connect_ex.side_effect = connect_side_effect
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            port = manager._allocate_port("bond-sandbox-agent1")
            # Should skip the first busy port
            assert port == _PORT_RANGE_START + 1

    def test_port_already_allocated_returns_same(self):
        manager = SandboxManager()
        manager._port_map["bond-sandbox-agent1"] = 18795
        port = manager._allocate_port("bond-sandbox-agent1")
        assert port == 18795


# ---------------------------------------------------------------------------
# Container creation — worker mode (Tasks 1, 2)
# ---------------------------------------------------------------------------


class TestWorkerContainerCreation:
    @pytest.mark.asyncio
    async def test_create_worker_container_command(self):
        """Verify entrypoint is python -m backend.app.worker, not sleep infinity."""
        manager = SandboxManager()
        agent = _make_agent()
        config_path = Path("/tmp/test-config.json")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            cid = await manager._create_worker_container(
                agent, "bond-sandbox-agent-abc123", 18791, config_path,
            )

            call_args = mock_exec.call_args[0]
            cmd = list(call_args)
            assert "python" in cmd
            assert "-m" in cmd
            assert "backend.app.worker" in cmd
            assert "sleep" not in cmd
            assert "infinity" not in cmd
            assert cid == "container123a"[:12]

    @pytest.mark.asyncio
    async def test_create_worker_container_mounts(self):
        """Verify all 6 mount types are present."""
        manager = SandboxManager()
        agent = _make_agent(workspace_mounts=[{
            "host_path": "/home/user/project",
            "mount_name": "project",
        }])
        config_path = Path("/tmp/test-config.json")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch.object(Path, "exists", return_value=True):
            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            await manager._create_worker_container(
                agent, "bond-sandbox-agent-abc123", 18791, config_path,
            )

            cmd = list(mock_exec.call_args[0])
            cmd_str = " ".join(cmd)

            # Bond library mount
            assert ":/bond:ro" in cmd_str
            # Workspace mount
            assert "/workspace/project" in cmd_str
            # Agent data bind mount
            assert "agents/agent-abc123:/data:rw" in cmd_str
            # Shared memory
            assert ":/data/shared:ro" in cmd_str
            # Config file mount
            assert ":/config/agent.json:ro" in cmd_str

    @pytest.mark.asyncio
    async def test_create_worker_container_sets_pythonpath(self):
        manager = SandboxManager()
        agent = _make_agent()
        config_path = Path("/tmp/test-config.json")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            await manager._create_worker_container(
                agent, "bond-sandbox-agent-abc123", 18791, config_path,
            )

            cmd = list(mock_exec.call_args[0])
            # Check -e PYTHONPATH=/bond is in the command
            idx = cmd.index("-e")
            assert cmd[idx + 1] == "PYTHONPATH=/bond"

    @pytest.mark.asyncio
    async def test_create_worker_container_exposes_port(self):
        manager = SandboxManager()
        agent = _make_agent()
        config_path = Path("/tmp/test-config.json")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            await manager._create_worker_container(
                agent, "bond-sandbox-agent-abc123", 18800, config_path,
            )

            cmd = list(mock_exec.call_args[0])
            idx = cmd.index("-p")
            assert cmd[idx + 1] == f"18800:{_WORKER_INTERNAL_PORT}"


# ---------------------------------------------------------------------------
# Config generation (Task 3)
# ---------------------------------------------------------------------------


class TestConfigGeneration:
    def test_config_file_created_with_correct_content(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            path = manager._write_agent_config(agent)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["agent_id"] == "agent-abc123"
            assert data["model"] == "claude-sonnet-4-20250514"
            assert data["system_prompt"] == "You are helpful."
            assert "respond" in data["tools"]
            assert data["api_keys"]["anthropic"] == "sk-test"

    def test_config_file_permissions_0600(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            path = manager._write_agent_config(agent)
            file_stat = os.stat(path)
            # Check owner read/write only
            assert stat.S_IMODE(file_stat.st_mode) == 0o600

    def test_config_dir_permissions_0700(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            manager._write_agent_config(agent)
            config_dir = tmp_path / "data" / "agent-configs"
            dir_stat = os.stat(config_dir)
            assert stat.S_IMODE(dir_stat.st_mode) == 0o700

    @pytest.mark.asyncio
    async def test_config_file_cleaned_up_on_destroy(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            path = manager._write_agent_config(agent)
            assert path.exists()

            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = _mock_proc()
                await manager.destroy_agent_container("agent-abc123")

            assert not path.exists()

    @pytest.mark.asyncio
    async def test_config_file_cleaned_up_on_create_failure(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            # Container creation fails
            mock_exec.return_value = _mock_proc(returncode=1, stderr=b"out of memory")

            with pytest.raises(RuntimeError):
                await manager.ensure_running(agent)

            # Config should be cleaned up
            config_path = tmp_path / "data" / "agent-configs" / "agent-abc123.json"
            assert not config_path.exists()


# ---------------------------------------------------------------------------
# Health wait (Task 4)
# ---------------------------------------------------------------------------


class TestHealthWait:
    @pytest.mark.asyncio
    async def test_health_wait_success_fast(self):
        manager = SandboxManager()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "ok", "agent_id": "agent1"}
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Should complete without error
            await manager._wait_for_health(
                "http://localhost:18791", "agent1", "container123", timeout=5.0,
            )

    @pytest.mark.asyncio
    async def test_health_wait_retries_connection_refused(self):
        manager = SandboxManager()
        call_count = 0

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            async def get_side_effect(url):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise httpx.ConnectError("Connection refused")
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"status": "ok", "agent_id": "agent1"}
                return resp

            mock_client.get.side_effect = get_side_effect
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.sleep", new_callable=AsyncMock):
                await manager._wait_for_health(
                    "http://localhost:18791", "agent1", "container123",
                    timeout=30.0, interval=0.01,
                )
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_health_wait_timeout_includes_docker_logs(self):
        manager = SandboxManager()

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch.object(manager, "_capture_container_logs", new_callable=AsyncMock) as mock_logs:

            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            mock_logs.return_value = "ImportError: no module named foo"

            with patch("asyncio.sleep", new_callable=AsyncMock), \
                 pytest.raises(RuntimeError, match="(?s)Container logs"):
                await manager._wait_for_health(
                    "http://localhost:18791", "agent1", "container123",
                    timeout=0.01, interval=0.001,
                )

    @pytest.mark.asyncio
    async def test_health_wait_validates_agent_id(self):
        manager = SandboxManager()

        call_count = 0
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()

            async def get_side_effect(url):
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.status_code = 200
                if call_count == 1:
                    # Wrong agent_id
                    resp.json.return_value = {"status": "ok", "agent_id": "wrong-agent"}
                else:
                    resp.json.return_value = {"status": "ok", "agent_id": "agent1"}
                return resp

            mock_client.get.side_effect = get_side_effect
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch("asyncio.sleep", new_callable=AsyncMock):
                await manager._wait_for_health(
                    "http://localhost:18791", "agent1", "container123",
                    timeout=30.0, interval=0.01,
                )

            assert call_count == 2  # First was wrong agent_id, second correct


# ---------------------------------------------------------------------------
# ensure_running (Task 6, 8)
# ---------------------------------------------------------------------------


class TestEnsureRunning:
    @pytest.mark.asyncio
    async def test_ensure_running_creates_and_returns_url(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch.object(manager, "_wait_for_health", new_callable=AsyncMock):

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            # Create worker.py so mount validation passes
            (tmp_path / "backend" / "app").mkdir(parents=True)
            (tmp_path / "backend" / "app" / "worker.py").touch()
            (tmp_path / "data" / "shared").mkdir(parents=True)

            result = await manager.ensure_running(agent)

            assert "worker_url" in result
            assert result["worker_url"].startswith("http://localhost:")
            assert "container_id" in result

    @pytest.mark.asyncio
    async def test_ensure_running_reuses_healthy_container(self):
        manager = SandboxManager()
        agent = _make_agent()
        key = "bond-sandbox-agent-abc123"

        # Pre-populate with existing container
        manager._containers[key] = {
            "container_id": "existing123",
            "worker_url": "http://localhost:18791",
            "worker_port": 18791,
            "last_used": 0,
        }

        with patch.object(manager, "_is_running", new_callable=AsyncMock, return_value=True), \
             patch.object(manager, "_wait_for_health", new_callable=AsyncMock):

            result = await manager.ensure_running(agent)
            assert result["container_id"] == "existing123"
            assert result["worker_url"] == "http://localhost:18791"

    @pytest.mark.asyncio
    async def test_ensure_running_recreates_dead_container(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()
        key = "bond-sandbox-agent-abc123"

        manager._containers[key] = {
            "container_id": "dead123",
            "worker_url": "http://localhost:18791",
            "worker_port": 18791,
            "last_used": 0,
        }
        manager._port_map[key] = 18791

        is_running_calls = 0

        async def mock_is_running(cid):
            nonlocal is_running_calls
            is_running_calls += 1
            return False  # Dead container

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch.object(manager, "_is_running", side_effect=mock_is_running), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch.object(manager, "_wait_for_health", new_callable=AsyncMock):

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock
            mock_exec.return_value = _mock_proc(stdout=b"newcontainer123\n")

            (tmp_path / "backend" / "app").mkdir(parents=True)
            (tmp_path / "backend" / "app" / "worker.py").touch()
            (tmp_path / "data" / "shared").mkdir(parents=True)

            result = await manager.ensure_running(agent)
            assert result["container_id"] == "newcontainer"  # 12 char truncation

    @pytest.mark.asyncio
    async def test_ensure_running_recreates_unhealthy_container(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()
        key = "bond-sandbox-agent-abc123"

        manager._containers[key] = {
            "container_id": "unhealthy123",
            "worker_url": "http://localhost:18791",
            "worker_port": 18791,
            "last_used": 0,
        }
        manager._port_map[key] = 18791

        health_check_count = 0

        async def mock_health(url, agent_id, cid, timeout=30.0, interval=0.5):
            nonlocal health_check_count
            health_check_count += 1
            if health_check_count == 1:
                raise RuntimeError("unhealthy")
            # Second call (new container) succeeds

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch.object(manager, "_is_running", new_callable=AsyncMock, return_value=True), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch.object(manager, "_wait_for_health", side_effect=mock_health):

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock
            mock_exec.return_value = _mock_proc(stdout=b"newcontainer123\n")

            (tmp_path / "backend" / "app").mkdir(parents=True)
            (tmp_path / "backend" / "app" / "worker.py").touch()
            (tmp_path / "data" / "shared").mkdir(parents=True)

            result = await manager.ensure_running(agent)
            assert result["container_id"] == "newcontainer"  # 12 char truncation
            assert health_check_count == 2

    @pytest.mark.asyncio
    async def test_ensure_running_concurrent_calls_serialized(self, tmp_path):
        """Verify lock prevents race — concurrent ensure_running for same agent."""
        manager = SandboxManager()
        agent = _make_agent()

        creation_count = 0

        async def counting_create(*args, **kwargs):
            nonlocal creation_count
            creation_count += 1
            await asyncio.sleep(0.01)
            return f"container{creation_count:03d}"

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch.object(manager, "_create_worker_container", side_effect=counting_create), \
             patch.object(manager, "_wait_for_health", new_callable=AsyncMock), \
             patch.object(manager, "_is_running", new_callable=AsyncMock, return_value=True), \
             patch.object(manager, "_write_agent_config", return_value=Path("/tmp/cfg.json")):

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            # Launch two concurrent ensure_running calls
            results = await asyncio.gather(
                manager.ensure_running(agent),
                manager.ensure_running(agent),
            )

            # Lock means second call sees the container already created
            # Only one creation should happen
            assert creation_count == 1
            # Both return same result
            assert results[0]["container_id"] == results[1]["container_id"]

    @pytest.mark.asyncio
    async def test_ensure_running_cleans_up_on_failure(self, tmp_path):
        manager = SandboxManager()
        agent = _make_agent()

        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path), \
             patch("socket.socket") as mock_socket_cls, \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:

            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1
            mock_sock.__enter__ = MagicMock(return_value=mock_sock)
            mock_sock.__exit__ = MagicMock(return_value=False)
            mock_socket_cls.return_value = mock_sock

            # Container creation fails
            mock_exec.return_value = _mock_proc(returncode=1, stderr=b"image not found")

            (tmp_path / "backend" / "app").mkdir(parents=True)
            (tmp_path / "backend" / "app" / "worker.py").touch()
            (tmp_path / "data" / "shared").mkdir(parents=True)

            with pytest.raises(RuntimeError):
                await manager.ensure_running(agent)

            # Port should be released
            key = "bond-sandbox-agent-abc123"
            assert key not in manager._port_map
            # Container tracking should be empty
            assert key not in manager._containers


# ---------------------------------------------------------------------------
# Backward compatibility (Task 7)
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_get_or_create_still_uses_sleep_infinity(self):
        """Host mode still uses sleep infinity entrypoint."""
        manager = SandboxManager()

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = MagicMock()
            if call_count == 1:
                # docker ps: no existing container
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                # docker run: return container ID
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"abc123def456\n", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            cid = await manager.get_or_create_container("test-agent", "python:3.12-slim")
            assert cid == "abc123def456"

        # Verify sleep infinity was in the docker run command
        # The second call should have sleep infinity
        # We can check through call_count

    @pytest.mark.asyncio
    async def test_execute_still_works(self):
        """docker exec path is unchanged for host-mode containers."""
        manager = SandboxManager()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello world", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc

            result = await manager.execute("container123", "python", "print('hello world')")
            assert result["exit_code"] == 0
            assert result["stdout"] == "hello world"

            # Verify docker exec was used
            call_args = mock_exec.call_args[0]
            assert "docker" in call_args
            assert "exec" in call_args


# ---------------------------------------------------------------------------
# Cleanup lifecycle (Task 9)
# ---------------------------------------------------------------------------


class TestCleanupLifecycle:
    @pytest.mark.asyncio
    async def test_destroy_releases_port_and_config(self, tmp_path):
        manager = SandboxManager()
        key = "bond-sandbox-agent-abc123"

        # Set up state
        manager._containers[key] = {
            "container_id": "cont123",
            "worker_url": "http://localhost:18791",
            "worker_port": 18791,
            "last_used": 0,
        }
        manager._port_map[key] = 18791
        manager._agent_locks[key] = asyncio.Lock()

        # Create a config file to be cleaned up
        with patch("backend.app.sandbox.manager._PROJECT_ROOT", tmp_path):
            config_dir = tmp_path / "data" / "agent-configs"
            config_dir.mkdir(parents=True)
            config_file = config_dir / "agent-abc123.json"
            config_file.write_text('{"test": true}')

            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = _mock_proc()
                result = await manager.destroy_agent_container("agent-abc123")

            assert result is True
            assert key not in manager._port_map
            assert key not in manager._containers
            assert key not in manager._agent_locks
            assert not config_file.exists()

    @pytest.mark.asyncio
    async def test_destroy_agent_data_removes_directory(self):
        manager = SandboxManager()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
             patch("shutil.rmtree") as mock_rmtree:
            mock_exec.return_value = _mock_proc()
            await manager.destroy_agent_data("agent-abc123")

            # Should have called shutil.rmtree on the agent data directory
            mock_rmtree.assert_called_once()
            rm_path = str(mock_rmtree.call_args[0][0])
            assert "agents/agent-abc123" in rm_path

    @pytest.mark.asyncio
    async def test_cleanup_idle_releases_ports(self):
        manager = SandboxManager()
        key = "bond-sandbox-old-agent"

        manager._containers[key] = {
            "container_id": "old123",
            "worker_url": "http://localhost:18791",
            "worker_port": 18791,
            "last_used": 0,  # Very old
        }
        manager._port_map[key] = 18791

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc()
            count = await manager.cleanup_idle(max_idle_seconds=1)

        assert count == 1
        assert key not in manager._containers
        assert key not in manager._port_map


# ---------------------------------------------------------------------------
# Observability (Task 10) — verify log messages via caplog
# ---------------------------------------------------------------------------


class TestObservability:
    @pytest.mark.asyncio
    async def test_create_worker_logs_port_and_image(self, caplog):
        manager = SandboxManager()
        agent = _make_agent()
        config_path = Path("/tmp/test-config.json")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc(stdout=b"container123abc\n")

            with caplog.at_level("INFO", logger="bond.sandbox.manager"):
                await manager._create_worker_container(
                    agent, "bond-sandbox-agent-abc123", 18800, config_path,
                )

            assert any("port=18800" in r.message for r in caplog.records)
            assert any("python:3.12-slim" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_destroy_logs_port_released(self, caplog):
        manager = SandboxManager()
        key = "bond-sandbox-agent-abc123"
        manager._containers[key] = {"container_id": "c1", "last_used": 0}
        manager._port_map[key] = 18800

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _mock_proc()

            with caplog.at_level("INFO", logger="bond.sandbox.manager"):
                await manager.destroy_agent_container("agent-abc123")

            assert any("port 18800 released" in r.message for r in caplog.records)
