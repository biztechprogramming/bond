"""File read/write tools with workspace allowlist enforcement.

File operations always run on the host filesystem (not in the sandbox).
The sandbox container is only used for code_execute.
"""

from __future__ import annotations

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


async def handle_file_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Read a file from an allowed workspace directory."""
    path_str = arguments.get("path", "")
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
        # Truncate very large files
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
