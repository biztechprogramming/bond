"""Tool JSON schemas for all 14 agent tools."""

from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "respond",
            "description": "Send a text response to the user. Use this when you have a final answer or want to communicate something. This ends your turn.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to send to the user.",
                    }
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search your memory for relevant information. Use this to recall facts, preferences, past conversations, or any stored knowledge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant memories.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "Save new information to long-term memory. Use this to remember facts, preferences, or important details for future reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The content to save to memory.",
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "Type of memory: fact, preference, event, person, project, or general.",
                        "default": "general",
                    },
                    "summary": {
                        "type": "string",
                        "description": "A brief summary of the memory.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_update",
            "description": "Update an existing memory with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to update.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The new content for the memory.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the update.",
                    },
                },
                "required": ["memory_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": "Soft-delete a memory. The memory will no longer appear in search results but is retained for audit purposes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The ID of the memory to delete.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for deletion.",
                    },
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": "Execute code in a sandboxed environment. Supports Python and shell scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Programming language: python or shell.",
                        "enum": ["python", "shell"],
                    },
                    "code": {
                        "type": "string",
                        "description": "The code to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds.",
                        "default": 30,
                    },
                },
                "required": ["language", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read the contents of a file from the workspace. Supports line-range reads and outline mode for context-efficient exploration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to workspace root).",
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "First line to read (1-indexed). If omitted, reads from the beginning.",
                    },
                    "line_end": {
                        "type": "integer",
                        "description": "Last line to read (1-indexed, inclusive). If omitted, reads to the end.",
                    },
                    "outline": {
                        "type": "boolean",
                        "description": "If true, return a structural outline (function/class signatures with line numbers) instead of full content. Ignores line_start/line_end.",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write (relative to workspace root).",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Apply surgical text replacements to a file. Each edit replaces an exact match of old_text with new_text. More efficient than file_write for small changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit (relative to workspace root).",
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of text replacements to apply sequentially.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "Exact text to find in the file.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "Text to replace old_text with.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_subordinate",
            "description": "Delegate a task to a subordinate agent for specialized processing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Name of the subordinate agent to call.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task description for the subordinate agent.",
                    },
                },
                "required": ["agent_name", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Returns titles, URLs, and snippets. Use web_read to get full page content for specific results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (default 10, max 20)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_read",
            "description": "Fetch and read the content of a web page. Returns extracted text. Use after web_search to read specific results, or to read any URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to read",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum content length in characters (default 5000)",
                        "default": 5000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser",
            "description": "Open a URL in a headless browser and extract content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to.",
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to perform: get_text, get_html, screenshot.",
                        "default": "get_text",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "email",
            "description": "Send or read email messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: send, read, list.",
                        "enum": ["send", "read", "list"],
                    },
                    "to": {
                        "type": "string",
                        "description": "Recipient email address (for send).",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject (for send).",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body (for send).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cron",
            "description": "Schedule a recurring task or one-time delayed execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: create, list, delete.",
                        "enum": ["create", "list", "delete"],
                    },
                    "schedule": {
                        "type": "string",
                        "description": "Cron expression or ISO timestamp for when to run.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Description of the task to execute.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify",
            "description": "Send a notification to the user via configured channels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The notification message.",
                    },
                    "urgency": {
                        "type": "string",
                        "description": "Urgency level: low, normal, high.",
                        "default": "normal",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skills",
            "description": "List or execute a saved skill (reusable tool sequence).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: list, execute, create.",
                        "enum": ["list", "execute", "create"],
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill to execute or create.",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Parameters to pass to the skill.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "work_plan",
            "description": "Create and manage work plans with trackable items. REQUIRED: Create a plan within your first 2-3 tool calls for any multi-step task. Add items incrementally as you discover work — do not wait until you have read everything. The user sees plan updates in real-time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action to perform.",
                        "enum": ["create_plan", "add_item", "update_item", "complete_plan", "get_plan"],
                    },
                    "plan_id": {
                        "type": "string",
                        "description": "ID of existing plan (for add_item, update_item, complete_plan, get_plan).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for plan or item (for create_plan, add_item).",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "ID of item to update (for update_item).",
                    },
                    "status": {
                        "type": "string",
                        "description": "New status (for update_item, complete_plan).",
                    },
                    "ordinal": {
                        "type": "integer",
                        "description": "Sort order within the plan (for add_item, auto-increments if omitted).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Note to append (for update_item). Timestamped automatically.",
                    },
                    "context_snapshot": {
                        "type": "object",
                        "description": "JSON context to save (for update_item).",
                    },
                    "files_changed": {
                        "type": "array",
                        "description": "Array of file paths modified (for update_item).",
                        "items": {"type": "string"},
                    },
                },
                "required": ["action"],
            },
        },
    },
]

# Quick lookup: tool name -> short description (used by the tools listing API)
TOOL_SUMMARIES: dict[str, str] = {
    d["function"]["name"]: d["function"]["description"]
    for d in TOOL_DEFINITIONS
}

# Quick lookup: tool name -> full definition
TOOL_MAP: dict[str, dict] = {
    d["function"]["name"]: d for d in TOOL_DEFINITIONS
}
