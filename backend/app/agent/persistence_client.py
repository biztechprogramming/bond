"""Persistence Client for Bond Agent Worker.

Routes message and tool-log writes based on BOND_PERSISTENCE_MODE:
  - "api"    → HTTP to Gateway → SpacetimeDB (default for same-machine)
  - "sqlite" → local agent.db via aiosqlite (edge/remote deployments)

The mode is set once at startup. There is no runtime switching.
If "api" mode cannot reach the Gateway, the client raises — it does NOT
silently degrade to sqlite.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.persistence")


def _resolve_gateway_url() -> str:
    """Resolve the Gateway URL for container-to-host networking.

    Priority: BOND_GATEWAY_URL env var > OS-based heuristic.
    """
    explicit = os.environ.get("BOND_GATEWAY_URL")
    if explicit:
        return explicit.rstrip("/")

    # Docker networking heuristic
    import platform
    system = platform.system().lower()
    if system in ("darwin", "windows") or "microsoft" in platform.release().lower():
        # macOS, Windows, WSL — Docker Desktop provides host.docker.internal
        return "http://host.docker.internal:18789"
    else:
        # Native Linux — Docker bridge gateway
        return "http://172.17.0.1:18789"


async def _detect_persistence_mode(gateway_url: str) -> str:
    """One-time auto-detection: probe Gateway to determine default mode.

    Called only when BOND_PERSISTENCE_MODE is not explicitly set.
    Returns "api" if Gateway is reachable, "sqlite" otherwise.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{gateway_url}/health")
            if resp.status_code == 200:
                logger.info("Gateway reachable at %s — auto-detected persistence mode: api", gateway_url)
                return "api"
    except Exception as e:
        logger.info("Gateway unreachable at %s (%s) — auto-detected persistence mode: sqlite", gateway_url, e)
    return "sqlite"


class PersistenceClient:
    """Unified persistence client for agent workers.

    Configured once at init. Mode is strict after that.
    """

    def __init__(
        self,
        agent_id: str | None = None,
        mode: str | None = None,
        gateway_url: str | None = None,
    ):
        self.agent_id = agent_id or os.environ.get("BOND_AGENT_ID", "unknown")
        self.token = os.environ.get("BOND_AGENT_TOKEN", "")
        self.gateway_url = gateway_url or _resolve_gateway_url()

        # Mode will be set in async init if not provided
        self._mode: str | None = mode or os.environ.get("BOND_PERSISTENCE_MODE")
        self._client: httpx.AsyncClient | None = None
        self._initialized = False

    async def init(self) -> None:
        """Async initialization — must be called before use.

        Performs auto-detection if mode was not explicitly configured.
        """
        if self._initialized:
            return

        # Auto-detect mode if not set
        if not self._mode:
            self._mode = await _detect_persistence_mode(self.gateway_url)
        else:
            self._mode = self._mode.lower().strip()

        if self._mode not in ("api", "sqlite"):
            raise ValueError(f"Invalid BOND_PERSISTENCE_MODE: {self._mode!r} (must be 'api' or 'sqlite')")

        if self._mode == "api":
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.AsyncClient(
                base_url=f"{self.gateway_url}/api/v1",
                headers=headers,
                timeout=10.0,
            )
            logger.info(
                "Persistence client initialized: mode=api gateway=%s agent=%s",
                self.gateway_url, self.agent_id,
            )
        else:
            logger.info(
                "Persistence client initialized: mode=sqlite agent=%s",
                self.agent_id,
            )

        self._initialized = True

    @property
    def mode(self) -> str:
        """Return the configured persistence mode."""
        if not self._mode:
            raise RuntimeError("PersistenceClient not initialized — call await client.init() first")
        return self._mode

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
        *,
        agent_db: Any | None = None,
    ) -> dict | bool:
        """Save a message. Routes based on configured mode.

        In api mode: POST to Gateway. Raises on failure.
        In sqlite mode: INSERT into local agent.db. Requires agent_db param.

        Returns the Gateway response dict (api) or True (sqlite).
        """
        if not self._initialized:
            await self.init()

        if self._mode == "api":
            return await self._api_save_message(session_id, role, content, metadata)
        else:
            return await self._sqlite_save_message(session_id, role, content, metadata, agent_db)

    async def log_tool(
        self,
        session_id: str,
        tool_name: str,
        input: dict,
        output: Any,
        duration: float,
        *,
        agent_db: Any | None = None,
    ) -> dict | bool:
        """Log a tool invocation. Routes based on configured mode.

        In api mode: POST to Gateway. Raises on failure.
        In sqlite mode: INSERT into local agent.db. Requires agent_db param.

        Returns the Gateway response dict (api) or True (sqlite).
        """
        if not self._initialized:
            await self.init()

        if self._mode == "api":
            return await self._api_log_tool(session_id, tool_name, input, output, duration)
        else:
            return await self._sqlite_log_tool(session_id, tool_name, input, output, duration, agent_db)

    async def set_setting(self, key: str, value: Any) -> dict | bool:
        """Set a global setting via Gateway."""
        if not self._initialized:
            await self.init()
        if self._mode == "api":
            assert self._client is not None
            resp = await self._client.post("/settings", json={"key": key, "value": value})
            if resp.status_code != 200:
                raise RuntimeError(f"Gateway set_setting failed: {resp.text}")
            return resp.json()
        return False

    async def get_setting(self, key: str) -> str | None:
        """Get a global setting from Gateway/SpacetimeDB."""
        if not self._initialized:
            await self.init()
        if self._mode == "api":
            assert self._client is not None
            resp = await self._client.get(f"/settings/{key}")
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise RuntimeError(f"Gateway get_setting failed: {resp.text}")
            data = resp.json()
            return data.get("value")
        # In sqlite mode, we can't get global settings
        return None

    async def get_provider_api_key(self, provider_id: str) -> str | None:
        """Get an encrypted API key for a provider from Gateway/SpacetimeDB."""
        if not self._initialized:
            await self.init()
        if self._mode == "api":
            assert self._client is not None
            resp = await self._client.get(f"/provider-api-keys/{provider_id}")
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise RuntimeError(f"Gateway get_provider_api_key failed: {resp.text}")
            data = resp.json()
            return data.get("encryptedValue")
        # In sqlite mode, we can't get provider API keys from SpacetimeDB
        return None

    async def save_conversation_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        agent_db: Any | None = None,
    ) -> dict | bool:
        """Save a conversation message to conversationMessages table."""
        if not self._initialized:
            await self.init()
        if self._mode == "api":
            assert self._client is not None
            payload = {
                "conversationId": conversation_id,
                "role": role,
                "content": content,
            }
            resp = await self._client.post("/conversation-messages", json=payload)
            if resp.status_code != 201:
                raise RuntimeError(
                    f"Gateway save_conversation_message failed ({resp.status_code}): {resp.text}"
                )
            return resp.json()
        # In sqlite mode, save to local agent.db
        if agent_db is None:
            raise RuntimeError("sqlite mode requires agent_db to be provided")
        import json
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        await agent_db.execute(
            """INSERT INTO conversation_messages (id, conversation_id, role, content, tool_calls, tool_call_id, token_count, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _ulid(),
                conversation_id,
                role,
                content,
                "[]",  # tool_calls
                "",    # tool_call_id
                0,     # token_count
                "delivered",
                now,
            ),
        )
        await agent_db.commit()
        return True

    async def add_mcp_server(self, name: str, command: str, args: list, env: dict) -> dict | bool:
        """Register an MCP server via Gateway."""
        if not self._initialized:
            await self.init()
        if self._mode == "api":
            assert self._client is not None
            resp = await self._client.post("/mcp", json={
                "name": name,
                "command": command,
                "args": args,
                "env": env,
                "agentId": self.agent_id,
            })
            if resp.status_code != 201:
                raise RuntimeError(f"Gateway add_mcp_server failed: {resp.text}")
            return resp.json()
        return False

    # ---- API mode ----

    async def _api_save_message(
        self, session_id: str, role: str, content: str, metadata: dict | None
    ) -> dict:
        assert self._client is not None
        payload = {
            "agentId": self.agent_id,
            "sessionId": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        resp = await self._client.post("/messages", json=payload)
        if resp.status_code != 201:
            raise RuntimeError(
                f"Gateway save_message failed ({resp.status_code}): {resp.text}"
            )
        return resp.json()

    async def _api_log_tool(
        self, session_id: str, tool_name: str, input: dict, output: Any, duration: float
    ) -> dict:
        assert self._client is not None
        payload = {
            "agentId": self.agent_id,
            "sessionId": session_id,
            "toolName": tool_name,
            "input": input,
            "output": output,
            "duration": duration,
        }
        resp = await self._client.post("/tool-logs", json=payload)
        if resp.status_code != 201:
            raise RuntimeError(
                f"Gateway log_tool failed ({resp.status_code}): {resp.text}"
            )
        return resp.json()

    # ---- SQLite mode ----

    async def _sqlite_save_message(
        self, session_id: str, role: str, content: str, metadata: dict | None, agent_db: Any
    ) -> bool:
        if agent_db is None:
            raise RuntimeError("sqlite mode requires agent_db to be provided")
        import json
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        await agent_db.execute(
            """INSERT INTO messages (id, agent_id, session_id, role, content, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                _ulid(),
                self.agent_id,
                session_id,
                role,
                content,
                json.dumps(metadata or {}),
                now,
            ),
        )
        await agent_db.commit()
        return True

    async def _sqlite_log_tool(
        self, session_id: str, tool_name: str, input: dict, output: Any,
        duration: float, agent_db: Any
    ) -> bool:
        if agent_db is None:
            raise RuntimeError("sqlite mode requires agent_db to be provided")
        import json
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        await agent_db.execute(
            """INSERT INTO tool_logs (id, agent_id, session_id, tool_name, input, output, duration, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _ulid(),
                self.agent_id,
                session_id,
                tool_name,
                json.dumps(input),
                json.dumps(output) if not isinstance(output, str) else output,
                int(duration * 1000),
                now,
            ),
        )
        await agent_db.commit()
        return True

    # ---- Lifecycle ----

    async def close(self) -> None:
        """Close the HTTP client if in api mode."""
        if self._client:
            await self._client.aclose()
            self._client = None


def _ulid() -> str:
    """Generate a ULID-like ID (timestamp + random)."""
    import secrets
    ts = int(time.time() * 1000)
    # 10-char base32 timestamp + 16-char random
    ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    t_part = ""
    for _ in range(10):
        t_part = ENCODING[ts & 31] + t_part
        ts >>= 5
    r_part = "".join(ENCODING[b & 31] for b in secrets.token_bytes(16))
    return t_part + r_part
