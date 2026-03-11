# Sandbox execution — Docker containers, host execution, and OpenSandbox

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bond.sandbox")


def get_sandbox_backend() -> str:
    """Return the configured sandbox backend: 'opensandbox' or 'legacy'."""
    from backend.app.config import load_bond_json

    config = load_bond_json()
    return config.get("sandbox_backend", "opensandbox")


def get_executor() -> Any:
    """Return the appropriate sandbox executor based on config.

    Returns either a SandboxManager (legacy) or OpenSandboxAdapter instance.
    """
    backend = get_sandbox_backend()

    if backend == "opensandbox":
        from backend.app.sandbox.opensandbox_adapter import get_opensandbox_adapter

        logger.info("Using OpenSandbox backend")
        return get_opensandbox_adapter()
    else:
        from backend.app.sandbox.manager import get_sandbox_manager

        return get_sandbox_manager()
