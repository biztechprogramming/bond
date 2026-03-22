"""Database schema discovery using tbls."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("bond.agent.tools.db_discover")

CACHE_TTL = 3600  # 1 hour default
TOKEN_THRESHOLD = 8000  # auto-summarize above this


def _cache_dir() -> Path:
    """Resolve cache directory relative to workspace or fallback."""
    for candidate in [Path("/bond/data/db-discovery-cache"), Path("data/db-discovery-cache")]:
        if candidate.parent.exists():
            return candidate
    return Path("/bond/data/db-discovery-cache")


def _cache_key(connection_string: str) -> str:
    """Hash connection string for cache filename (avoids leaking creds to filesystem)."""
    return hashlib.sha256(connection_string.encode()).hexdigest()


def _get_cached(connection_string: str) -> Optional[dict]:
    """Return cached schema if fresh, else None."""
    cache_file = _cache_dir() / f"{_cache_key(connection_string)}.json"
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_TTL:
            return json.loads(cache_file.read_text())
    return None


def _set_cache(connection_string: str, data: dict) -> None:
    """Write schema to cache."""
    d = _cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    cache_file = d / f"{_cache_key(connection_string)}.json"
    cache_file.write_text(json.dumps(data))


def _redact_connection_string(conn: str) -> str:
    """Redact password from connection string for logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", conn)


def _maybe_summarize(schema: dict, max_tokens: int = TOKEN_THRESHOLD) -> dict:
    """If schema is too large, return overview only."""
    raw = json.dumps(schema)
    estimated_tokens = len(raw) // 4

    if estimated_tokens <= max_tokens:
        return schema

    return {
        "name": schema.get("name"),
        "table_count": len(schema.get("tables", [])),
        "tables": [
            {
                "name": t["name"],
                "type": t.get("type", "TABLE"),
                "column_count": len(t.get("columns", [])),
                "columns": [c["name"] for c in t.get("columns", [])],
            }
            for t in schema.get("tables", [])
        ],
        "relations": schema.get("relations", []),
        "_metadata": schema.get("_metadata"),
        "_note": "Schema too large for full output. Use the `table` parameter to get full details for a specific table.",
    }


async def handle_db_discover(args: dict, context: Any = None) -> dict:
    """Discover database schema using tbls.

    Returns a dict with tables, columns, types, constraints, foreign keys,
    indexes, and relationships.
    """
    connection_string = args.get("connection_string")
    if not connection_string:
        return {"error": "connection_string is required"}

    table = args.get("table")
    refresh = args.get("refresh", False)

    # Check cache (unless refresh requested or single-table query)
    if not refresh and table is None:
        cached = _get_cached(connection_string)
        if cached:
            cached["_from_cache"] = True
            return _maybe_summarize(cached)

    # Build tbls command
    cmd = ["tbls", "out", connection_string, "-t", "json"]
    if table:
        cmd.extend(["--table", table])

    env = {**os.environ, "TBLS_DSN": connection_string}

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return {"error": f"Discovery timed out (30s) for {_redact_connection_string(connection_string)}"}
    except FileNotFoundError:
        return {"error": "tbls is not installed. Install with: go install github.com/k1LoW/tbls@latest"}

    if proc.returncode != 0:
        return {
            "error": f"tbls failed: {stderr.decode().strip()}",
            "connection": _redact_connection_string(connection_string),
        }

    try:
        schema = json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {"error": "tbls returned invalid JSON", "raw": stdout.decode()[:500]}

    # Enrich with metadata
    schema["_metadata"] = {
        "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "connection": _redact_connection_string(connection_string),
        "cached": False,
    }

    # Cache full-database results
    if table is None:
        _set_cache(connection_string, schema)

    return _maybe_summarize(schema)
