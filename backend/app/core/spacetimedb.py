"""SpacetimeDB Client for the FastAPI Backend.

Provides direct access to SpacetimeDB HTTP API.
Loads token from CLI config by default.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("bond.backend.spacetimedb")


def _resolve_token() -> str:
    """Read SpacetimeDB token from environment or ~/.config/spacetime/cli.toml."""
    token = os.environ.get("SPACETIMEDB_TOKEN")
    if token:
        # Strip surrounding quotes — common .env loading pitfall where
        # SPACETIMEDB_TOKEN="eyJ..." includes the literal '"' characters.
        token = token.strip('"').strip("'")
        return token

    cli_config = Path.home() / ".config" / "spacetime" / "cli.toml"
    if cli_config.exists():
        try:
            content = cli_config.read_text()
            import re
            match = re.search(r'spacetimedb_token\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
        except Exception as e:
            logger.warning("Failed to read SpacetimeDB token from %s: %s", cli_config, e)
    
    return ""


class StdbClient:
    def __init__(
        self,
        base_url: str | None = None,
        module_name: str | None = None,
        token: str | None = None,
    ):
        # Default to localhost:18787 (SpacetimeDB default in this environment)
        self.base_url = (base_url or os.environ.get("BOND_SPACETIMEDB_URL") or "http://localhost:18787").rstrip("/")
        self.module = module_name or os.environ.get("BOND_SPACETIMEDB_MODULE") or "bond-core-v2"
        self.token = token or _resolve_token()
        self._client = httpx.AsyncClient(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query and return rows as dictionaries."""
        url = f"{self.base_url}/v1/database/{self.module}/sql"
        try:
            resp = await self._client.post(url, headers=self._headers(), content=sql)
            if resp.status_code != 200:
                error_msg = f"SpacetimeDB SQL failed ({resp.status_code}): {resp.text}"
                logger.error(error_msg)
                # Raise on write operations so callers know something failed
                sql_upper = sql.strip().upper()
                if sql_upper.startswith(("INSERT", "UPDATE", "DELETE")):
                    raise RuntimeError(error_msg)
                return []
            
            data = resp.json()
            if not data or not isinstance(data, list):
                return []
            
            result_set = data[0]
            rows = result_set.get("rows", [])
            schema = result_set.get("schema", {}).get("elements", [])
            
            # Extract column names, handling SpacetimeDB's Option wrapper
            columns = []
            for e in schema:
                name = e.get("name")
                if isinstance(name, dict) and "some" in name:
                    columns.append(name["some"])
                else:
                    columns.append(name)
            
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error("SpacetimeDB query error: %s", e)
            return []

    async def call_reducer(self, reducer: str, args: list[Any]) -> bool:
        """Call a SpacetimeDB reducer."""
        url = f"{self.base_url}/v1/database/{self.module}/call/{reducer}"
        try:
            # SpacetimeDB positional args are passed as a JSON array
            resp = await self._client.post(url, headers=self._headers(), json=args)
            if resp.status_code != 200:
                logger.error("SpacetimeDB reducer %s failed (%d): %s", reducer, resp.status_code, resp.text)
                return False
            return True
        except Exception as e:
            logger.error("SpacetimeDB reducer error (%s): %s", reducer, e)
            return False

    async def close(self):
        await self._client.aclose()


# Global instance
_instance: StdbClient | None = None

def get_stdb() -> StdbClient:
    global _instance
    if _instance is None:
        _instance = StdbClient()
    return _instance
