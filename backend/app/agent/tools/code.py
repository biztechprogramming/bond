"""Code execution tool — uses SandboxManager or HostExecutor."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bond.agent.tools.code")


async def handle_code_execute(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Execute code in sandbox container or on host."""
    language = arguments.get("language", "python")
    code = arguments.get("code", "")
    timeout = arguments.get("timeout", 30)

    sandbox_image = context.get("sandbox_image")
    agent_id = context.get("agent_id", "default")

    if sandbox_image:
        # Use Docker sandbox
        from backend.app.sandbox.manager import get_sandbox_manager

        manager = get_sandbox_manager()
        try:
            workspace_mounts = context.get("workspace_mounts", [])
            container_id = await manager.get_or_create_container(
                agent_id, sandbox_image, workspace_mounts
            )
            return await manager.execute(container_id, language, code, timeout)
        except Exception as e:
            logger.warning("Sandbox execution failed: %s", e)
            return {"error": f"Sandbox execution failed: {e}", "exit_code": -1}
    else:
        # Use host execution
        from backend.app.sandbox.host import get_host_executor

        executor = get_host_executor()
        return await executor.execute(
            language, code, timeout,
            workspace_dirs=context.get("workspace_dirs", []),
        )
