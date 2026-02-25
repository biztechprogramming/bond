"""HostExecutor — code execution on the host for agents without sandbox_image."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("bond.sandbox.host")


class HostExecutor:
    """Execute code directly on the host via subprocess with timeout."""

    async def execute(
        self,
        language: str,
        code: str,
        timeout: int = 30,
        workspace_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute code on the host.

        Args:
            language: 'python' or 'shell'.
            code: The code to execute.
            timeout: Max execution time in seconds.
            workspace_dirs: Allowed directories (informational for shell).
        """
        if language == "python":
            cmd = ["python3", "-c", code]
        elif language == "shell":
            cmd = ["sh", "-c", code]
        else:
            return {"error": f"Unsupported language: {language}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"Execution timed out after {timeout}s", "exit_code": -1}
        except Exception as e:
            return {"error": str(e), "exit_code": -1}


# Singleton
_host_executor: HostExecutor | None = None


def get_host_executor() -> HostExecutor:
    global _host_executor
    if _host_executor is None:
        _host_executor = HostExecutor()
    return _host_executor
