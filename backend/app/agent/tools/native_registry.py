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
        handle_host_exec,
        handle_load_context,
        handle_memory_delete,
        handle_memory_save,
        handle_memory_update,
        handle_repo_pr,
        handle_respond,
        handle_search_memory,
        handle_parallel_orchestrate,
    )
    from .shell_utils import (
        handle_git_info,
        handle_project_search,
        handle_shell_find,
        handle_shell_grep,
        handle_shell_head,
        handle_shell_ls,
        handle_shell_tree,
        handle_shell_wc,
    )
    from .web import handle_web_read, handle_web_search
    from .work_plan import handle_work_plan

    registry = ToolRegistry()
    
    # Register MCP tools if available
    try:
        from backend.app.mcp import mcp_manager
        # We need to make sure tools are refreshed. 
        # In worker, this might need a different approach if manager isn't initialized yet.
        # But we'll assume the registry caller will handle refresh if needed.
        for name in mcp_manager._dynamic_definitions:
            registry.register(name, mcp_manager._create_handler_from_name(name))
    except (ImportError, Exception):
        pass

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
    registry.register("work_plan", handle_work_plan)
    registry.register("parallel_orchestrate", handle_parallel_orchestrate)
    registry.register("repo_pr", handle_repo_pr)
    registry.register("load_context", handle_load_context)
    registry.register("host_exec", handle_host_exec)

    # Shell utility tools (info-gathering, routed to utility model)
    registry.register("shell_find", handle_shell_find)
    registry.register("shell_ls", handle_shell_ls)
    registry.register("shell_grep", handle_shell_grep)
    registry.register("git_info", handle_git_info)
    registry.register("shell_wc", handle_shell_wc)
    registry.register("shell_head", handle_shell_head)
    registry.register("shell_tree", handle_shell_tree)
    registry.register("project_search", handle_project_search)
    return registry
