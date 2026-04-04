"""Container Host Service — CRUD and settings for container hosts.

Design Doc 089 Phase 2.5: Settings-Driven Configuration
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.crypto import decrypt_value, encrypt_value

logger = logging.getLogger("bond.services.container_hosts")


class ContainerHostService:
    """Database-backed CRUD for container hosts and container settings."""

    async def list_all(self, db: AsyncSession) -> list[dict]:
        result = await db.execute(text("SELECT * FROM container_hosts ORDER BY is_local DESC, name ASC"))
        rows = result.mappings().all()
        return [self._row_to_dict(r) for r in rows]

    async def get(self, db: AsyncSession, host_id: str) -> dict | None:
        result = await db.execute(
            text("SELECT * FROM container_hosts WHERE id = :id"),
            {"id": host_id},
        )
        row = result.mappings().first()
        return self._row_to_dict(row) if row else None

    async def create(self, db: AsyncSession, data: dict) -> dict:
        ssh_key = data.pop("ssh_key", None)
        ssh_key_encrypted = encrypt_value(ssh_key) if ssh_key else None

        labels = json.dumps(data.pop("labels", []))
        host_id = data["id"]

        await db.execute(
            text("""
                INSERT INTO container_hosts (id, name, host, port, user, ssh_key_encrypted,
                    daemon_port, max_agents, memory_mb, labels, enabled, status, is_local)
                VALUES (:id, :name, :host, :port, :user, :ssh_key_encrypted,
                    :daemon_port, :max_agents, :memory_mb, :labels, :enabled, :status, 0)
            """),
            {
                "id": host_id,
                "name": data.get("name", host_id),
                "host": data["host"],
                "port": data.get("port", 22),
                "user": data.get("user", "bond"),
                "ssh_key_encrypted": ssh_key_encrypted,
                "daemon_port": data.get("daemon_port", 8990),
                "max_agents": data.get("max_agents", 4),
                "memory_mb": data.get("memory_mb", 0),
                "labels": labels,
                "enabled": 1 if data.get("enabled", True) else 0,
                "status": data.get("status", "active"),
            },
        )
        await db.commit()
        return await self.get(db, host_id)  # type: ignore[return-value]

    async def update(self, db: AsyncSession, host_id: str, data: dict) -> dict | None:
        existing = await self.get(db, host_id)
        if not existing:
            return None

        updates = {}
        if "ssh_key" in data:
            ssh_key = data.pop("ssh_key")
            if ssh_key:
                updates["ssh_key_encrypted"] = encrypt_value(ssh_key)
            else:
                updates["ssh_key_encrypted"] = None

        if "labels" in data:
            updates["labels"] = json.dumps(data.pop("labels"))

        if "enabled" in data:
            updates["enabled"] = 1 if data.pop("enabled") else 0

        updates.update(data)

        if not updates:
            return existing

        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = host_id
        await db.execute(
            text(f"UPDATE container_hosts SET {set_clause}, updated_at = datetime('now') WHERE id = :id"),
            updates,
        )
        await db.commit()
        return await self.get(db, host_id)

    async def delete(self, db: AsyncSession, host_id: str) -> bool:
        if host_id == "local":
            return False
        result = await db.execute(
            text("DELETE FROM container_hosts WHERE id = :id AND is_local = 0"),
            {"id": host_id},
        )
        await db.commit()
        return result.rowcount > 0  # type: ignore[union-attr]

    async def get_container_settings(self, db: AsyncSession) -> dict[str, str]:
        result = await db.execute(text("SELECT key, value FROM settings WHERE key LIKE 'container.%'"))
        return {row.key: row.value for row in result}

    async def update_container_settings(self, db: AsyncSession, data: dict[str, str]) -> dict[str, str]:
        for key, value in data.items():
            if not key.startswith("container."):
                continue
            await db.execute(
                text("""
                    INSERT INTO settings (key, value) VALUES (:key, :value)
                    ON CONFLICT(key) DO UPDATE SET value = :value
                """),
                {"key": key, "value": value},
            )
        await db.commit()
        return await self.get_container_settings(db)

    async def import_from_config(self, db: AsyncSession, config: dict) -> list[dict]:
        """One-time import from bond.json / env vars."""
        imported = []
        for host_cfg in config.get("remote_hosts", []):
            existing = await self.get(db, host_cfg["id"])
            if existing:
                continue
            created = await self.create(db, {
                "id": host_cfg["id"],
                "name": host_cfg.get("name", host_cfg["id"]),
                "host": host_cfg["host"],
                "port": host_cfg.get("port", 22),
                "user": host_cfg.get("user", "bond"),
                "ssh_key": host_cfg.get("ssh_key", ""),
                "daemon_port": host_cfg.get("daemon_port", 8990),
                "max_agents": host_cfg.get("max_agents", 4),
                "memory_mb": host_cfg.get("memory_mb", 0),
                "labels": host_cfg.get("labels", []),
                "enabled": host_cfg.get("enabled", True),
            })
            imported.append(created)
        return imported

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        d["labels"] = json.loads(d.get("labels", "[]"))
        d["enabled"] = bool(d.get("enabled", 1))
        d["is_local"] = bool(d.get("is_local", 0))
        # Decrypt SSH key for internal use but don't expose raw key in API
        ssh_key = d.get("ssh_key_encrypted")
        if ssh_key:
            d["ssh_key_decrypted"] = decrypt_value(ssh_key)
        d.pop("ssh_key_encrypted", None)
        return d
