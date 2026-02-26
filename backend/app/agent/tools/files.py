"""File read/write tools with workspace allowlist enforcement.

When a sandbox image is configured, file operations route through
docker exec on the sandbox container. Otherwise, they operate on
the host filesystem with workspace allowlist enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("bond.agent.tools.files")


def _resolve_and_check(path_str: str, allowed_dirs: list[str]) -> Path | None:
    """Resolve a path and check it falls within an allowed directory.

    Returns the resolved Path if allowed, None if rejected.
    """
    if not allowed_dirs:
        return None

    resolved = Path(os.path.expanduser(path_str)).resolve()
    for allowed in allowed_dirs:
        allowed_resolved = Path(os.path.expanduser(allowed)).resolve()
        try:
            resolved.relative_to(allowed_resolved)
            return resolved
        except ValueError:
            continue
    return None


def _shell_escape(s: str) -> str:
    """Escape a string for safe use in shell commands."""
    return "'" + s.replace("'", "'\\''") + "'"


async def _get_sandbox_container(context: dict[str, Any]) -> str | None:
    """Get or create the sandbox container if sandbox_image is configured."""
    sandbox_image = context.get("sandbox_image")
    if not sandbox_image:
        return None

    from backend.app.sandbox.manager import get_sandbox_manager
    manager = get_sandbox_manager()
    try:
        container_id = await manager.get_or_create_container(
            context.get("agent_id", "default"),
            sandbox_image,
            context.get("workspace_mounts", []),
        )
        return container_id
    except Exception as e:
        logger.warning("Failed to get sandbox container: %s", e)
        return None


async def handle_file_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Read a file from an allowed workspace directory."""
    path_str = arguments.get("path", "")

    # Sandbox mode: read via docker exec cat
    container_id = await _get_sandbox_container(context)
    if container_id:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "cat", path_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            return {"error": "Timed out reading file"}

        if proc.returncode == 0:
            content = stdout.decode("utf-8", errors="replace")
            if len(content) > 100_000:
                content = content[:100_000] + "\n... [truncated at 100KB]"
            return {"content": content, "path": path_str, "size": len(stdout)}
        else:
            return {"error": stderr.decode("utf-8", errors="replace").strip() or "File not found or not readable"}

    # Host mode: direct filesystem access with allowlist
    allowed_dirs = context.get("workspace_dirs", [])
    if not allowed_dirs:
        return {"error": "No workspace directories configured for this agent."}

    resolved = _resolve_and_check(path_str, allowed_dirs)
    if resolved is None:
        return {"error": f"Path '{path_str}' is outside allowed workspace directories."}

    try:
        if not resolved.exists():
            return {"error": f"File not found: {path_str}"}
        if not resolved.is_file():
            return {"error": f"Not a file: {path_str}"}
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if len(content) > 100_000:
            content = content[:100_000] + "\n... [truncated at 100KB]"
        return {"content": content, "path": str(resolved), "size": resolved.stat().st_size}
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


async def handle_file_write(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Write content to a file in an allowed workspace directory."""
    path_str = arguments.get("path", "")
    content = arguments.get("content", "")

    # Sandbox mode: write via docker exec tee with stdin piping
    container_id = await _get_sandbox_container(context)
    if container_id:
        # Ensure parent directory exists
        parent_dir = str(Path(path_str).parent)
        try:
            mkdir_proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id, "mkdir", "-p", parent_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(mkdir_proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            return {"error": "Timed out creating parent directory"}

        # Write content via stdin piping to tee (no heredoc, no escaping needed)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-i", container_id, "tee", path_str,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=content.encode("utf-8")),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return {"error": "Timed out writing file"}

        if proc.returncode == 0:
            return {"status": "written", "path": path_str, "bytes": len(content.encode("utf-8"))}
        else:
            return {"error": stderr.decode("utf-8", errors="replace").strip() or "Failed to write file"}

    # Host mode: direct filesystem access with allowlist
    allowed_dirs = context.get("workspace_dirs", [])
    if not allowed_dirs:
        return {"error": "No workspace directories configured for this agent."}

    resolved = _resolve_and_check(path_str, allowed_dirs)
    if resolved is None:
        return {"error": f"Path '{path_str}' is outside allowed workspace directories."}

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"status": "written", "path": str(resolved), "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}
