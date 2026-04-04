"""Tests for ContainerHostService — Design Doc 089 Phase 2.5."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.container_host_service import ContainerHostService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeRow:
    """Simulates a SQLAlchemy row mapping."""

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

    def fetchone(self):
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
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture
def service():
    return ContainerHostService()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------


class TestContainerHostCRUD:
    @pytest.mark.asyncio
    async def test_list_all(self, service, mock_db):
        local_row = _make_db_row(id="local", name="Local Machine", is_local=1)
        remote_row = _make_db_row()
        mock_db.execute = AsyncMock(return_value=FakeResult([local_row, remote_row]))

        hosts = await service.list_all(mock_db)
        assert len(hosts) == 2
        assert hosts[0]["id"] == "local"
        assert hosts[0]["is_local"] is True

    @pytest.mark.asyncio
    async def test_get(self, service, mock_db):
        row = _make_db_row()
        mock_db.execute = AsyncMock(return_value=FakeResult([row]))

        host = await service.get(mock_db, "test-1")
        assert host is not None
        assert host["id"] == "test-1"
        assert host["labels"] == []

    @pytest.mark.asyncio
    async def test_get_not_found(self, service, mock_db):
        mock_db.execute = AsyncMock(return_value=FakeResult([]))
        host = await service.get(mock_db, "nonexistent")
        assert host is None

    @pytest.mark.asyncio
    async def test_create(self, service, mock_db):
        row = _make_db_row()
        # First call: INSERT, second call: SELECT (from get)
        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([]),  # insert
            FakeResult([row]),  # get after create
        ])

        result = await service.create(mock_db, {
            "id": "test-1",
            "name": "Test Host",
            "host": "192.168.1.10",
            "labels": ["gpu"],
        })
        assert result["id"] == "test-1"
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_delete_local_blocked(self, service, mock_db):
        result = await service.delete(mock_db, "local")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_remote(self, service, mock_db):
        fake_result = MagicMock()
        fake_result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=fake_result)

        result = await service.delete(mock_db, "test-1")
        assert result is True


class TestContainerSettings:
    @pytest.mark.asyncio
    async def test_get_container_settings(self, service, mock_db):
        rows = [
            MagicMock(key="container.default_image", value="bond-worker:latest"),
            MagicMock(key="container.memory_limit_mb", value="2048"),
        ]
        mock_db.execute = AsyncMock(return_value=rows)

        settings = await service.get_container_settings(mock_db)
        assert settings["container.default_image"] == "bond-worker:latest"

    @pytest.mark.asyncio
    async def test_update_container_settings(self, service, mock_db):
        # update calls execute multiple times then get_container_settings
        rows = [
            MagicMock(key="container.default_image", value="new-image:v2"),
        ]
        mock_db.execute = AsyncMock(return_value=rows)

        result = await service.update_container_settings(mock_db, {
            "container.default_image": "new-image:v2",
            "not.container.key": "ignored",
        })
        assert mock_db.commit.called


class TestSSHKeyEncryption:
    @pytest.mark.asyncio
    async def test_ssh_key_encrypted_on_create(self, service, mock_db):
        row = _make_db_row(ssh_key_encrypted="enc:faketoken")
        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([]),  # insert
            FakeResult([row]),  # get
        ])

        with patch("backend.app.services.container_host_service.encrypt_value", return_value="enc:faketoken") as mock_enc:
            result = await service.create(mock_db, {
                "id": "test-1",
                "name": "Test",
                "host": "1.2.3.4",
                "ssh_key": "my-secret-key",
            })
            mock_enc.assert_called_once_with("my-secret-key")

    @pytest.mark.asyncio
    async def test_ssh_key_decrypted_on_read(self, service, mock_db):
        row = _make_db_row(ssh_key_encrypted="enc:faketoken")
        mock_db.execute = AsyncMock(return_value=FakeResult([row]))

        with patch("backend.app.services.container_host_service.decrypt_value", return_value="decrypted-key") as mock_dec:
            host = await service.get(mock_db, "test-1")
            assert host["ssh_key_decrypted"] == "decrypted-key"
            assert "ssh_key_encrypted" not in host


class TestImportFromConfig:
    @pytest.mark.asyncio
    async def test_import_skips_existing(self, service, mock_db):
        existing_row = _make_db_row(id="existing-host")
        # get returns existing for first host, None for second
        mock_db.execute = AsyncMock(side_effect=[
            FakeResult([existing_row]),  # get existing
            FakeResult([]),  # get non-existing
            FakeResult([]),  # insert
            FakeResult([_make_db_row(id="new-host")]),  # get after create
        ])

        result = await service.import_from_config(mock_db, {
            "remote_hosts": [
                {"id": "existing-host", "host": "1.1.1.1"},
                {"id": "new-host", "host": "2.2.2.2"},
            ]
        })
        assert len(result) == 1
        assert result[0]["id"] == "new-host"
