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
        handle_say,
        handle_search_memory,
        handle_parallel_orchestrate,
    )
    from .file_buffer import handle_file_smart_edit
    from .shell_utils import (
        handle_git_info,
        handle_project_search,
        handle_shell_awk,
        handle_shell_diff,
        handle_shell_find,
        handle_shell_grep,
        handle_shell_jq,
        handle_shell_ls,
        handle_shell_tree,
        handle_shell_wc,
        handle_file_list,
        handle_file_search_unified,
    )
    from .coding_agent import handle_coding_agent
    from .image_gen import handle_generate_image
    from .db_discover import handle_db_discover
    from .web import handle_web_read, handle_web_search
    from .work_plan import handle_work_plan

    registry = ToolRegistry()
    # MCP tools are registered via MCPProxyClient.register_proxy_handlers() in worker.py
    # (Design Doc 054: no direct mcp_manager usage in containers)

    registry.register("respond", handle_respond)
    registry.register("say", handle_say)
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
    registry.register("coding_agent", handle_coding_agent)
    registry.register("host_exec", handle_host_exec)
    registry.register("db_discover", handle_db_discover)

    # Shell utility tools (info-gathering, routed to utility model)
    import functools

    def _make_deprecated(old_name: str, new_name: str, handler):
        @functools.wraps(handler)
        async def wrapper(arguments, context):
            import logging
            logging.getLogger("bond.agent.tools.native_registry").warning(
                "Deprecated tool '%s' called. Use '%s' instead.", old_name, new_name,
            )
            return await handler(arguments, context)
        return wrapper

    registry.register("file_list", handle_file_list)
    registry.register("file_search", handle_file_search_unified)
    registry.register("shell_find", _make_deprecated("shell_find", "file_list", handle_shell_find))
    registry.register("shell_ls", _make_deprecated("shell_ls", "file_list", handle_shell_ls))
    registry.register("git_info", handle_git_info)
    registry.register("shell_wc", _make_deprecated("shell_wc", "file_list", handle_shell_wc))
    registry.register("shell_tree", _make_deprecated("shell_tree", "file_list", handle_shell_tree))
    registry.register("project_search", _make_deprecated("project_search", "file_search", handle_project_search))
    registry.register("file_smart_edit", handle_file_smart_edit)
    registry.register("shell_diff", handle_shell_diff)
    registry.register("shell_awk", handle_shell_awk)
    registry.register("shell_jq", handle_shell_jq)
    registry.register("generate_image", handle_generate_image)
    return registry
