"""SpacetimeDB storage backend.

Primary backend used when SPACETIMEDB_TOKEN is available.
Wraps SpacetimeDB HTTP API calls for key-value storage operations.
"""

import json
import logging
import os
from typing import List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import StorageBackend

logger = logging.getLogger(__name__)



class SpacetimeDBBackend(StorageBackend):
    """SpacetimeDB storage backend.

    Communicates with SpacetimeDB via its HTTP API. Requires SPACETIMEDB_TOKEN
    to be set in the environment.

    Args:
        token: SpacetimeDB authentication token. If None, reads from
               SPACETIMEDB_TOKEN environment variable.
        base_url: Base URL for the SpacetimeDB API.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self._token = token or os.environ.get("SPACETIMEDB_TOKEN")
        self._base_url = (
            base_url
            or os.environ["BOND_SPACETIMEDB_URL"]
        ).rstrip("/")

        if not self._token:
            raise ValueError(
                "SpacetimeDB token is required. Set SPACETIMEDB_TOKEN env var "
                "or pass token= to the constructor."
            )

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
    ) -> Optional[dict]:
        """Make an authenticated HTTP request to SpacetimeDB.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            path: API path (appended to base_url).
            data: Optional JSON body.

        Returns:
            Parsed JSON response, or None for 204/DELETE responses.

        Raises:
            URLError: On network or API errors.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode("utf-8") if data else None
        req = Request(url, data=body, headers=headers, method=method)

        with urlopen(req, timeout=10) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))

    def store(self, namespace: str, key: str, value: dict) -> None:
        """Store a value in SpacetimeDB."""
        self._request(
            "POST",
            f"/store/{namespace}/{key}",
            data=value,
        )

    def retrieve(self, namespace: str, key: str) -> Optional[dict]:
        """Retrieve a value from SpacetimeDB."""
        try:
            result = self._request("GET", f"/store/{namespace}/{key}")
            return result
        except URLError as e:
            if hasattr(e, "code") and e.code == 404:
                return None
            raise

    def list_keys(self, namespace: str) -> List[str]:
        """List all keys in a namespace from SpacetimeDB."""
        result = self._request("GET", f"/store/{namespace}")
        if result and isinstance(result, dict) and "keys" in result:
            return result["keys"]
        return []

    def delete(self, namespace: str, key: str) -> bool:
        """Delete a key-value pair from SpacetimeDB."""
        try:
            self._request("DELETE", f"/store/{namespace}/{key}")
            return True
        except URLError as e:
            if hasattr(e, "code") and e.code == 404:
                return False
            raise

    def is_available(self) -> bool:
        """Check if SpacetimeDB is reachable and authenticated."""
        try:
            self._request("GET", "/health")
            return True
        except (URLError, OSError, ValueError):
            return False

    @property
    def backend_name(self) -> str:
        return f"SpacetimeDB ({self._base_url})"
