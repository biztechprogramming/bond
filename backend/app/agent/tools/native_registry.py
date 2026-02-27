"""Native tool registry for container-side execution.

Builds a ToolRegistry using native handlers (direct file I/O, subprocess,
local aiosqlite) instead of the host-side handlers.  Web tools are reused
as-is since they're self-contained HTTP calls.
"""

from __future__ import annotations

from backend.app.agent.tools import ToolRegistry


def build_native_registry() -> ToolRegistry:
    """Build a ToolRegistry with native (container-side) tool handlers."""
    from .native import (
        handle_code_execute,
        handle_file_edit,
        handle_file_read,
        handle_file_write,
        handle_memory_delete,
        handle_memory_save,
        handle_memory_update,
        handle_respond,
        handle_search_memory,
    )
    from .web import handle_web_read, handle_web_search

    registry = ToolRegistry()
    registry.register("respond", handle_respond)
    registry.register("search_memory", handle_search_memory)
    registry.register("memory_save", handle_memory_save)
    registry.register("memory_update", handle_memory_update)
    registry.register("memory_delete", handle_memory_delete)
    registry.register("code_execute", handle_code_execute)
    registry.register("file_read", handle_file_read)
    registry.register("file_write", handle_file_write)
    registry.register("file_edit", handle_file_edit)
    registry.register("web_search", handle_web_search)
    registry.register("web_read", handle_web_read)
    return registry
