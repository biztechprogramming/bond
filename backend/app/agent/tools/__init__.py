"""Tool registry — maps tool names to async handler functions."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from .definitions import TOOL_DEFINITIONS, TOOL_MAP, TOOL_SUMMARIES  # noqa: F401
from ._dynamic_tools import load_dynamic_tool_definitions  # noqa: F401

logger = logging.getLogger("bond.agent.tools")

# Type alias for tool handler: takes (arguments_dict, context_dict) -> result_dict
ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRegistry:
    """Registry mapping tool names to their async handler functions."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        """Register a handler for a tool name."""
        self._handlers[name] = handler

    def get(self, name: str) -> ToolHandler | None:
        """Get the handler for a tool name."""
        return self._handlers.get(name)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool by name with arguments and context.

        Returns the tool result as a dict. If the tool is not registered,
        returns an error result.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"Tool '{name}' is not registered."}
        try:
            return await handler(arguments, context)
        except Exception as e:
            logger.exception("Tool '%s' raised an exception", name)
            return {"error": f"Tool '{name}' failed: {str(e)}"}

    def get_definitions_for(self, tool_names: list[str]) -> list[dict]:
        """Return LiteLLM-compatible tool definitions filtered by tool names."""
        defs = [TOOL_MAP[name] for name in tool_names if name in TOOL_MAP]
        
        # Check for MCP tools
        try:
            from backend.app.mcp import mcp_manager
            defs.extend(mcp_manager.get_definitions(tool_names))
        except ImportError:
            pass
        
        return defs

    @property
    def registered_names(self) -> list[str]:
        return list(self._handlers.keys())


def build_registry() -> ToolRegistry:
    """Build a ToolRegistry with all implemented tool handlers."""
    from .respond import handle_respond
    from .search import handle_search_memory
    from .memory import handle_memory_save, handle_memory_update, handle_memory_delete
    from .code import handle_code_execute
    from .files import handle_file_read, handle_file_write, handle_file_edit
    from .web import handle_web_search, handle_web_read
    from .browser import handle_browser
    from .email_tool import handle_email
    from .cron import handle_cron
    from .notify import handle_notify
    from .subordinate import handle_call_subordinate
    from .skills import handle_skills
    from .work_plan import handle_work_plan
    from .file_buffer import (
        handle_file_open, handle_file_view, handle_file_search, handle_file_replace,
        handle_file_smart_edit,
    )
    from .shell_utils import (
        handle_batch_head, handle_shell_find, handle_shell_ls, handle_shell_grep,
        handle_git_info, handle_shell_wc, handle_shell_head, handle_shell_tail,
        handle_shell_tree, handle_project_search, handle_shell_sed,
        handle_shell_diff, handle_shell_awk, handle_shell_jq,
    )
    from .coding_agent import handle_coding_agent
    from .deploy_tools import handle_deploy_tool
    from .deployment_query import handle_deployment_query

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
    registry.register("call_subordinate", handle_call_subordinate)
    registry.register("web_search", handle_web_search)
    registry.register("web_read", handle_web_read)
    registry.register("browser", handle_browser)
    registry.register("email", handle_email)
    registry.register("cron", handle_cron)
    registry.register("notify", handle_notify)
    registry.register("skills", handle_skills)
    registry.register("work_plan", handle_work_plan)

    # Shell utility tools (info-gathering, routed to utility model)
    registry.register("shell_find", handle_shell_find)
    registry.register("shell_ls", handle_shell_ls)
    registry.register("shell_grep", handle_shell_grep)
    registry.register("git_info", handle_git_info)
    registry.register("shell_wc", handle_shell_wc)
    registry.register("shell_head", handle_shell_head)
    registry.register("shell_tree", handle_shell_tree)
    registry.register("project_search", handle_project_search)
    registry.register("batch_head", handle_batch_head)
    registry.register("file_open", handle_file_open)
    registry.register("file_view", handle_file_view)
    registry.register("file_search", handle_file_search)
    registry.register("file_replace", handle_file_replace)
    registry.register("file_smart_edit", handle_file_smart_edit)
    registry.register("shell_tail", handle_shell_tail)
    registry.register("shell_sed", handle_shell_sed)
    registry.register("shell_diff", handle_shell_diff)
    registry.register("shell_awk", handle_shell_awk)
    registry.register("shell_jq", handle_shell_jq)
    registry.register("coding_agent", handle_coding_agent)
    # Deployment agent tools (Design Doc 039) — only available to deploy-* agents
    registry.register("deploy_action", lambda args, ctx: handle_deploy_tool("deploy_action", args))
    registry.register("file_bug_ticket", lambda args, ctx: handle_deploy_tool("file_bug_ticket", args))
    # Deployment query — read-only access to deployment data via Gateway APIs
    registry.register("deployment_query", handle_deployment_query)

    # Register dynamic tools from dynamic/ directory
    from ._dynamic_tools import register_dynamic_tools
    register_dynamic_tools(registry)

    return registry
