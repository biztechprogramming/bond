"""Tests for container host bug fixes.

Bug Fix 1: test_host endpoint bypasses registry when host not loaded.
Bug Fix 2: _row_to_dict strips auth_token and exposes has_auth_token boolean.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.container_host_service import ContainerHostService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRow:
    """Simulates a SQLAlchemy row mapping (supports dict())."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    @property
    def rowcount(self):
        return len(self._rows)


def _make_db_row(**kwargs) -> dict:
    defaults = {
        "id": "test-1",
        "name": "Test Host",
        "host": "192.168.1.10",
        "port": 22,
        "user": "bond",
        "ssh_key_encrypted": None,
        "daemon_port": 8990,
        "max_agents": 4,
        "memory_mb": 0,
        "labels": "[]",
        "enabled": 1,
        "status": "active",
        "is_local": 0,
        "auth_token": None,
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    }
    defaults.update(kwargs)
    return defaults


def _host_dict_from_service(**kwargs) -> dict:
    """Simulate what ContainerHostService.get() returns after _row_to_dict."""
    row = _make_db_row(**kwargs)
    svc = ContainerHostService()
    with patch("backend.app.services.container_host_service.decrypt_value", return_value="decrypted"):
        return svc._row_to_dict(row)


# ---------------------------------------------------------------------------
# Bug Fix 2: _row_to_dict strips auth_token, exposes has_auth_token
# ---------------------------------------------------------------------------


class TestRowToDictAuthToken:
    """Verify _row_to_dict strips raw auth_token and sets has_auth_token."""

    def test_has_auth_token_true_when_token_present(self):
        svc = ContainerHostService()
        row = _make_db_row(auth_token="enc:sometoken")
        with patch("backend.app.services.container_host_service.decrypt_value", return_value=""):
            result = svc._row_to_dict(row)

        assert result["has_auth_token"] is True
        assert "auth_token" not in result

    def test_has_auth_token_false_when_no_token(self):
        svc = ContainerHostService()
        row = _make_db_row(auth_token=None)
        result = svc._row_to_dict(row)

        assert result["has_auth_token"] is False
        assert "auth_token" not in result

    def test_has_auth_token_false_when_empty_string(self):
        svc = ContainerHostService()
        row = _make_db_row(auth_token="")
        result = svc._row_to_dict(row)

        assert result["has_auth_token"] is False
        assert "auth_token" not in result


class TestDaemonInstalledInAPIResponse:
    """Verify the API response maps has_auth_token -> daemon_installed."""

    def test_daemon_installed_true(self):
        from backend.app.api.v1.hosts import _db_host_to_response

        host_dict = {"id": "h1", "name": "H1", "host": "1.2.3.4", "port": 22,
                     "user": "bond", "daemon_port": 8990, "max_agents": 4,
                     "memory_mb": 0, "labels": [], "enabled": True,
                     "status": "active", "is_local": False,
                     "has_auth_token": True}

        with patch("backend.app.api.v1.hosts._get_registry", return_value=None):
            resp = _db_host_to_response(host_dict)

        assert resp["daemon_installed"] is True

    def test_daemon_installed_false(self):
        from backend.app.api.v1.hosts import _db_host_to_response

        host_dict = {"id": "h1", "name": "H1", "host": "1.2.3.4", "port": 22,
                     "user": "bond", "daemon_port": 8990, "max_agents": 4,
                     "memory_mb": 0, "labels": [], "enabled": True,
                     "status": "active", "is_local": False,
                     "has_auth_token": False}

        with patch("backend.app.api.v1.hosts._get_registry", return_value=None):
            resp = _db_host_to_response(host_dict)

        assert resp["daemon_installed"] is False

    def test_raw_auth_token_not_in_api_response(self):
        """Ensure raw auth_token never leaks into API responses."""
        from backend.app.api.v1.hosts import _db_host_to_response

        host_dict = {"id": "h1", "name": "H1", "host": "1.2.3.4", "port": 22,
                     "user": "bond", "daemon_port": 8990, "max_agents": 4,
                     "memory_mb": 0, "labels": [], "enabled": True,
                     "status": "active", "is_local": False,
                     "has_auth_token": True}

        with patch("backend.app.api.v1.hosts._get_registry", return_value=None):
            resp = _db_host_to_response(host_dict)

        assert "auth_token" not in resp


# ---------------------------------------------------------------------------
# Bug Fix 1: test_host bypasses registry when host not loaded
# ---------------------------------------------------------------------------


class TestTestHostEndpoint:
    """Test the POST /{host_id}/test endpoint logic."""

    def _mock_manager(self, registry_host=None):
        """Build a mock sandbox manager with configurable registry behavior."""
        registry = MagicMock()
        registry.load_from_db = AsyncMock()
        registry.refresh = AsyncMock()
        registry.get_host = MagicMock(return_value=registry_host)

        tunnel = MagicMock()
        tunnel.local_url = "http://localhost:19000"

        tunnel_manager = MagicMock()
        tunnel_manager.ensure_tunnel = AsyncMock(return_value=tunnel)

        manager = MagicMock()
        manager._registry = registry
        manager._tunnel_manager = tunnel_manager

        return manager, tunnel

    @pytest.mark.asyncio
    async def test_host_in_registry_normal_path(self):
        """When host is in the registry, use it directly."""
        from backend.app.sandbox.host_registry import RemoteHost

        reg_host = RemoteHost(id="test-1", name="Test", host="192.168.1.10")
        manager, tunnel = self._mock_manager(registry_host=reg_host)

        host_dict = _host_dict_from_service()

        mock_db = AsyncMock()
        svc_mock = AsyncMock(return_value=host_dict)

        health_resp = MagicMock()
        health_resp.status_code = 200
        health_resp.json.return_value = {"daemon_version": "1.0", "api_version": "1"}

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=health_resp)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.v1.hosts._service") as svc,
            patch("backend.app.sandbox.manager.get_sandbox_manager", return_value=manager),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            svc.get = svc_mock
            from backend.app.api.v1.hosts import test_host
            result = await test_host("test-1", mock_db)

        assert result["ssh"]["status"] == "ok"
        assert result["daemon"]["status"] == "ok"
        # Registry was used — ensure_tunnel called with the registry host
        manager._tunnel_manager.ensure_tunnel.assert_called_once_with(reg_host)

    @pytest.mark.asyncio
    async def test_host_not_in_registry_bypass_path(self):
        """When host is NOT in registry, a temp RemoteHost is created from DB data."""
        # Registry returns None for get_host (even after load_from_db and refresh)
        manager, tunnel = self._mock_manager(registry_host=None)

        host_dict = _host_dict_from_service(
            id="new-host", name="New Host", host="10.0.0.5",
            port=2222, user="admin", daemon_port=9000, max_agents=8,
            labels='["gpu"]', enabled=1,
        )

        mock_db = AsyncMock()

        health_resp = MagicMock()
        health_resp.status_code = 200
        health_resp.json.return_value = {"daemon_version": "2.0"}

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=health_resp)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.v1.hosts._service") as svc,
            patch("backend.app.sandbox.manager.get_sandbox_manager", return_value=manager),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            svc.get = AsyncMock(return_value=host_dict)
            from backend.app.api.v1.hosts import test_host
            result = await test_host("new-host", mock_db)

        assert result["ssh"]["status"] == "ok"
        # Verify ensure_tunnel was called with a RemoteHost (the temp one)
        call_args = manager._tunnel_manager.ensure_tunnel.call_args[0][0]
        assert call_args.id == "new-host"
        assert call_args.host == "10.0.0.5"
        assert call_args.port == 2222
        assert call_args.user == "admin"

    @pytest.mark.asyncio
    async def test_host_disabled_bypass_path(self):
        """Disabled hosts aren't in registry but should still be testable."""
        manager, tunnel = self._mock_manager(registry_host=None)

        host_dict = _host_dict_from_service(
            id="disabled-host", name="Disabled", host="10.0.0.99",
            enabled=0,
        )

        mock_db = AsyncMock()

        health_resp = MagicMock()
        health_resp.status_code = 200
        health_resp.json.return_value = {}

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=health_resp)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("backend.app.api.v1.hosts._service") as svc,
            patch("backend.app.sandbox.manager.get_sandbox_manager", return_value=manager),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            svc.get = AsyncMock(return_value=host_dict)
            from backend.app.api.v1.hosts import test_host
            result = await test_host("disabled-host", mock_db)

        assert result["ssh"]["status"] == "ok"
        call_args = manager._tunnel_manager.ensure_tunnel.call_args[0][0]
        assert call_args.enabled is False

    @pytest.mark.asyncio
    async def test_host_not_in_db_returns_404(self):
        """When host doesn't exist in DB at all, raise 404."""
        mock_db = AsyncMock()

        with patch("backend.app.api.v1.hosts._service") as svc:
            svc.get = AsyncMock(return_value=None)
            from backend.app.api.v1.hosts import test_host
            with pytest.raises(Exception) as exc_info:
                await test_host("nonexistent", mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_local_host_returns_404(self):
        """test_host should reject local host with 404."""
        mock_db = AsyncMock()

        with patch("backend.app.api.v1.hosts._service") as svc:
            svc.get = AsyncMock(return_value={"id": "local"})
            from backend.app.api.v1.hosts import test_host
            with pytest.raises(Exception) as exc_info:
                await test_host("local", mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_ssh_failure_returns_error(self):
        """When SSH tunnel fails, result should contain the error."""
        manager, _ = self._mock_manager(registry_host=None)
        manager._tunnel_manager.ensure_tunnel = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )

        host_dict = _host_dict_from_service()
        mock_db = AsyncMock()

        with (
            patch("backend.app.api.v1.hosts._service") as svc,
            patch("backend.app.sandbox.manager.get_sandbox_manager", return_value=manager),
        ):
            svc.get = AsyncMock(return_value=host_dict)
            from backend.app.api.v1.hosts import test_host
            result = await test_host("test-1", mock_db)

        assert result["ssh"]["status"] == "error"
        assert "Connection refused" in result["ssh"]["error"]
