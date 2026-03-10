"""Async client for the Permission Broker (gateway-side)."""

from __future__ import annotations

import os
import logging
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.broker_client")


class BrokerError(Exception):
    """Raised when the broker denies a command or returns an error."""

    def __init__(self, message: str, decision: str = "error", policy_rule: str | None = None):
        super().__init__(message)
        self.decision = decision
        self.policy_rule = policy_rule


class BrokerClient:
    """Async HTTP client for the Permission Broker API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 120.0,
    ):
        self.base_url = (base_url or os.environ.get("BOND_BROKER_URL", "")).rstrip("/")
        self.token = token or os.environ.get("BOND_BROKER_TOKEN", "")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a command via the broker. Raises BrokerError on deny."""
        body: dict[str, Any] = {"command": command}
        if cwd:
            body["cwd"] = cwd
        if timeout is not None:
            body["timeout"] = timeout
        if env:
            body["env"] = env

        resp = await self._client.post("/exec", json=body)
        resp.raise_for_status()
        data = resp.json()

        if data.get("decision") == "deny":
            raise BrokerError(
                data.get("reason", "Command denied"),
                decision="deny",
                policy_rule=data.get("policy_rule"),
            )

        return data

    async def renew_token(self) -> str:
        """Renew the broker token. Returns the new token string."""
        resp = await self._client.post("/token/renew")
        resp.raise_for_status()
        data = resp.json()
        new_token = data["token"]
        self.token = new_token
        self._client.headers["Authorization"] = f"Bearer {new_token}"
        return new_token

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
