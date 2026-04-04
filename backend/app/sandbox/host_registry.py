"""Host Registry — manages the set of available container hosts.

Design Doc 089: Remote Container Hosts §4.1
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger("bond.sandbox.host_registry")

_DB_CACHE_TTL = 30  # seconds


@dataclass
class RemoteHost:
    """Configuration for a remote container host."""

    id: str
    name: str
    host: str
    port: int = 22
    user: str = "bond"
    ssh_key: str = "~/.ssh/id_ed25519"
    daemon_port: int = 18795
    max_agents: int = 4
    labels: list[str] = field(default_factory=list)
    enabled: bool = True
    status: Literal["active", "draining", "offline"] = "active"
    auth_token: str = ""
    # Runtime state
    running_count: int = 0
    last_health_check: str | None = None


@dataclass
class LocalHost:
    """Represents the local machine as a container host."""

    id: str = "local"
    name: str = "Local"
    host: str = "localhost"
    max_agents: int = 100  # Port range limit
    labels: list[str] = field(default_factory=list)
    enabled: bool = True
    status: Literal["active", "draining", "offline"] = "active"
    running_count: int = 0


class HostRegistry:
    """Manages the set of available container hosts."""

    def __init__(self, config: dict | None = None):
        self._hosts: dict[str, RemoteHost] = {}
        self._local = LocalHost()
        self._strategy: str = "least-loaded"
        self._prefer_local: bool = True
        self._round_robin_idx: int = 0
        self._cache_ts: float = 0.0
        self._db_loaded: bool = False
        if config:
            self._load_from_config(config)

    def _load_from_config(self, config: dict) -> None:
        """Load remote hosts from bond.json config."""
        for host_cfg in config.get("remote_hosts", []):
            host = RemoteHost(
                id=host_cfg["id"],
                name=host_cfg.get("name", host_cfg["id"]),
                host=host_cfg["host"],
                port=host_cfg.get("port", 22),
                user=host_cfg.get("user", "bond"),
                ssh_key=host_cfg.get("ssh_key", "~/.ssh/id_ed25519"),
                daemon_port=host_cfg.get("daemon_port", 18795),
                max_agents=host_cfg.get("max_agents", 4),
                labels=host_cfg.get("labels", []),
                enabled=host_cfg.get("enabled", True),
            )
            self._hosts[host.id] = host

        placement = config.get("placement", {})
        self._strategy = placement.get("strategy", "least-loaded")
        self._prefer_local = placement.get("prefer_local", True)

    async def load_from_db(self) -> None:
        """Load hosts from the container_hosts DB table with cache TTL."""
        if self._db_loaded and (time.time() - self._cache_ts) < _DB_CACHE_TTL:
            return

        try:
            from backend.app.db.session import get_session_factory

            factory = get_session_factory()
            async with factory() as db:
                from sqlalchemy import text as sql_text

                result = await db.execute(sql_text("SELECT * FROM container_hosts WHERE enabled = 1"))
                rows = result.mappings().all()

                # Preserve running counts from existing hosts
                running_counts = {h.id: h.running_count for h in self._hosts.values()}
                running_counts["local"] = self._local.running_count

                new_hosts: dict[str, RemoteHost] = {}
                for row in rows:
                    row = dict(row)
                    if row.get("is_local"):
                        self._local.max_agents = row.get("max_agents", 100)
                        self._local.running_count = running_counts.get("local", 0)
                        continue

                    labels = json.loads(row.get("labels", "[]")) if isinstance(row.get("labels"), str) else row.get("labels", [])
                    # Decrypt auth_token if present
                    auth_token = ""
                    raw_token = row.get("auth_token", "")
                    if raw_token:
                        try:
                            from backend.app.core.crypto import decrypt_value
                            auth_token = decrypt_value(raw_token)
                        except Exception:
                            logger.warning("Failed to decrypt auth_token for host %s", row["id"])

                    host = RemoteHost(
                        id=row["id"],
                        name=row["name"],
                        host=row["host"],
                        port=row.get("port", 22),
                        user=row.get("user", "bond"),
                        ssh_key=row.get("ssh_key_encrypted", ""),
                        daemon_port=row.get("daemon_port", 8990),
                        max_agents=row.get("max_agents", 4),
                        labels=labels,
                        enabled=bool(row.get("enabled", 1)),
                        status=row.get("status", "active"),
                        auth_token=auth_token,
                        running_count=running_counts.get(row["id"], 0),
                    )
                    new_hosts[host.id] = host

                self._hosts = new_hosts

                # Load placement strategy from settings
                strat_result = await db.execute(
                    sql_text("SELECT value FROM settings WHERE key = 'container.placement_strategy'")
                )
                strat_row = strat_result.fetchone()
                if strat_row:
                    self._strategy = strat_row[0]

            self._cache_ts = time.time()
            self._db_loaded = True
            logger.debug("Loaded %d remote hosts from DB", len(self._hosts))

        except Exception as e:
            if not self._db_loaded:
                logger.debug("DB not available, using config fallback: %s", e)
            else:
                logger.warning("Failed to refresh hosts from DB: %s", e)

    async def refresh(self) -> None:
        """Force reload from DB (bypasses cache)."""
        self._cache_ts = 0.0
        self._db_loaded = False
        await self.load_from_db()

    @property
    def local(self) -> LocalHost:
        return self._local

    def get_host(self, host_id: str) -> RemoteHost | LocalHost | None:
        if host_id == "local":
            return self._local
        return self._hosts.get(host_id)

    def get_all_remote_hosts(self) -> list[RemoteHost]:
        return list(self._hosts.values())

    def get_all_hosts(self) -> list[RemoteHost | LocalHost]:
        return [self._local] + list(self._hosts.values())

    def add_host(self, host: RemoteHost) -> None:
        self._hosts[host.id] = host

    def remove_host(self, host_id: str) -> bool:
        return self._hosts.pop(host_id, None) is not None

    def update_host(self, host_id: str, updates: dict) -> RemoteHost | None:
        host = self._hosts.get(host_id)
        if not host:
            return None
        for key, value in updates.items():
            if hasattr(host, key):
                setattr(host, key, value)
        return host

    def mark_unreachable(self, host_id: str) -> None:
        host = self._hosts.get(host_id)
        if host:
            host.status = "offline"
            logger.warning("Host %s marked as offline", host_id)

    def mark_active(self, host_id: str) -> None:
        host = self._hosts.get(host_id)
        if host:
            host.status = "active"
            logger.info("Host %s marked as active", host_id)

    async def get_placement(self, agent: dict) -> RemoteHost | LocalHost:
        """Decide where to place an agent container.

        Full placement algorithm per Design Doc 089 §3.2 Decision 4.
        """
        # 1. Explicit host assignment
        preferred = agent.get("preferred_host")
        if preferred:
            host = self.get_host(preferred)
            if host and host.enabled and host.status == "active":
                return host
            # Fall through to auto-placement

        # 2. Build candidate list
        candidates: list[RemoteHost | LocalHost] = []
        for h in self.get_all_hosts():
            if h.enabled and h.status == "active" and h.running_count < h.max_agents:
                candidates.append(h)

        # 3. Label filtering
        required_labels = agent.get("host_labels", [])
        if required_labels:
            candidates = [
                h for h in candidates
                if all(label in getattr(h, "labels", []) for label in required_labels)
            ]

        # 4. Host affinity — prefer last host
        last_host_id = agent.get("last_host_id")
        if last_host_id:
            affinity_match = [h for h in candidates if h.id == last_host_id]
            if affinity_match:
                return affinity_match[0]

        # 5. Apply strategy
        if not candidates:
            # No capacity — return local as fallback (SandboxManager will handle error)
            logger.warning("No hosts with available capacity, falling back to local")
            return self._local

        if self._prefer_local and self._local in candidates:
            return self._local

        if self._strategy == "least-loaded":
            return min(candidates, key=lambda h: h.running_count / max(h.max_agents, 1))
        elif self._strategy == "round-robin":
            return self._next_round_robin(candidates)

        # Default: first available
        return candidates[0]

    def _next_round_robin(self, candidates: list) -> RemoteHost | LocalHost:
        if not candidates:
            return self._local
        idx = self._round_robin_idx % len(candidates)
        self._round_robin_idx += 1
        return candidates[idx]

    def increment_running(self, host_id: str) -> None:
        host = self.get_host(host_id)
        if host:
            host.running_count += 1

    def decrement_running(self, host_id: str) -> None:
        host = self.get_host(host_id)
        if host and host.running_count > 0:
            host.running_count -= 1

    def to_config_dict(self) -> list[dict]:
        """Serialize remote hosts back to config format."""
        result = []
        for h in self._hosts.values():
            result.append({
                "id": h.id,
                "name": h.name,
                "host": h.host,
                "port": h.port,
                "user": h.user,
                "ssh_key": h.ssh_key,
                "daemon_port": h.daemon_port,
                "max_agents": h.max_agents,
                "labels": h.labels,
                "enabled": h.enabled,
            })
        return result
