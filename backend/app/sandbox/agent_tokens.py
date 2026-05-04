"""Persisted per-agent worker auth tokens (Design Doc 116 §3.8).

Worker auth uses simple Bearer-token equality: the worker holds its
BOND_AGENT_TOKEN in env, and bond-bond holds the same value here so it can
authenticate when calling worker endpoints from the registry.

Stored as one file per agent so we can rotate or invalidate individually
without touching unrelated rows.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _tokens_dir() -> Path:
    from backend.app.sandbox.adapters import _agent_bind_data_root
    d = _agent_bind_data_root() / "agent-tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_agent_token(agent_id: str, token: str) -> None:
    """Persist an agent's worker auth token. Best-effort — never raises."""
    if not agent_id or not token:
        return
    try:
        path = _tokens_dir() / f"{agent_id}.txt"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
    except Exception as e:
        logger.warning("Failed to save agent token for %s: %s", agent_id, e)


def load_agent_token(agent_id: str) -> str:
    """Read an agent's worker auth token, or '' if not stored."""
    if not agent_id:
        return ""
    try:
        path = _tokens_dir() / f"{agent_id}.txt"
        if path.is_file():
            return path.read_text().strip()
    except Exception as e:
        logger.warning("Failed to load agent token for %s: %s", agent_id, e)
    return ""


def clear_agent_token(agent_id: str) -> None:
    """Remove a stored agent token. Used when destroying the container."""
    if not agent_id:
        return
    try:
        path = _tokens_dir() / f"{agent_id}.txt"
        if path.is_file():
            path.unlink()
    except Exception as e:
        logger.warning("Failed to clear agent token for %s: %s", agent_id, e)
