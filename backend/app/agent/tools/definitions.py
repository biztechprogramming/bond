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
            "description": "Read the full contents of a file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to workspace root).",
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
            "description": "Create and manage work plans with trackable items. REQUIRED: Create a plan within your first 2-3 tool calls for any multi-step task. Add items incrementally as you discover work.\n\nCRITICAL RULES:\n1. STATUS: Always update item status — new → in_progress (before starting) → done (when finished). Never describe progress in text instead of updating status.\n2. DESCRIPTION (required on add_item): Every item MUST have a description with: which codebase/repo (e.g. ~/bond, ~/inspections), relevant file paths, what to implement, approach, and acceptance criteria. This is the only context another agent has when picking up this item.\n3. NOTES (use on update_item while working): Append notes as you make progress — decisions made, problems encountered, what was changed and why. Notes are timestamped and visible to anyone resuming the work.\n4. FILES_CHANGED: Always record files you modified on update_item when marking done.\n5. CHECK RESULT: The tool returns {\"success\": true} on success. If the response contains \"success\": false or an \"error\" key, the call FAILED — do NOT proceed as if it succeeded, do NOT claim the item was updated.",
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
                        "description": "ID of existing plan (required for add_item, complete_plan, get_plan; optional for update_item).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for plan or item (for create_plan, add_item).",
                    },
                    "item_id": {
                        "type": "string",
                        "description": "ID of item to update (required for update_item).",
                    },
                    "title": {
                        "type": "string",
                        "description": "New title for the item (for update_item — renames it).",
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
                        "description": "Note to append while working (for update_item). Use this to document: what you did, decisions made, problems encountered, what changed and why. Timestamped automatically. Append notes frequently — they are the audit trail.",
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
                    "description": {
                        "type": "string",
                        "description": "Execution context for add_item (REQUIRED) and update_item. Must specify: which codebase/repo (e.g. ~/bond, ~/inspections), relevant file paths, what to implement, approach, and acceptance criteria. This is the only context a different agent has when picking up this item.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parallel_orchestrate",
            "description": (
                "Execute multiple independent tool calls in parallel batches. "
                "Use when a work plan has multiple items that can be implemented concurrently. "
                "Decompose the work into batches: batch 1 runs all calls simultaneously, then batch 2, etc. "
                "Each call in a batch runs in parallel — use this aggressively for independent file edits, "
                "reads, or code execution steps. Do NOT use for sequential steps that depend on each other."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "description": "The parallel execution plan.",
                        "properties": {
                            "reasoning": {
                                "type": "string",
                                "description": "Why this decomposition makes sense — which items are independent.",
                            },
                            "batches": {
                                "type": "array",
                                "description": "Sequential list of batches. All calls within a batch run simultaneously.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "batch_name": {"type": "string"},
                                        "calls": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "tool_name": {"type": "string", "description": "Name of the tool to call."},
                                                    "arguments": {"type": "object", "description": "Arguments matching the tool's schema."},
                                                    "description": {"type": "string", "description": "What this specific call does."},
                                                },
                                                "required": ["tool_name", "arguments", "description"],
                                            },
                                        },
                                    },
                                    "required": ["batch_name", "calls"],
                                },
                            },
                        },
                        "required": ["reasoning", "batches"],
                    },
                },
                "required": ["plan"],
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

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal, Union, Type

class ToolCall(BaseModel):
    """Base class for all tools used by Instructor"""
    pass

class Respond(ToolCall):
    """Send a text response to the user. Ends your turn."""
    message: str = Field(description="The message to send to the user.")

class SearchMemory(ToolCall):
    """Search long-term memory for relevant information."""
    query: str = Field(description="The search query.")
    limit: int = Field(default=5, description="Max results.")

class MemorySave(ToolCall):
    """Save new information to long-term memory."""
    content: str = Field(description="The content to save.")
    memory_type: str = Field(default="general", description="fact, preference, etc.")
    summary: Optional[str] = Field(None, description="Brief summary.")

class MemoryUpdate(ToolCall):
    """Update existing memory."""
    memory_id: str = Field(description="ID of memory to update.")
    content: str = Field(description="New content.")
    reason: Optional[str] = Field(None, description="Reason for update.")

class MemoryDelete(ToolCall):
    """Soft-delete a memory."""
    memory_id: str = Field(description="ID of memory to delete.")

class CodeExecute(ToolCall):
    """Execute code or shell commands in the sandbox."""
    language: Literal["python", "shell", "javascript", "typescript"]
    code: str
    timeout: int = 30

class FileRead(ToolCall):
    """Read one or more files from the workspace. Supports parallel reading."""
    path: Optional[str] = Field(None, description="Path to a single file to read.")
    paths: Optional[List[str]] = Field(None, description="Array of file paths to read in parallel.")
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    outline: bool = False

class FileWrite(ToolCall):
    """Write file content to the workspace."""
    path: str
    content: str

class FileEdit(ToolCall):
    """Apply precise edits to a file using old/new string matching."""
    path: str
    old_string: str
    new_string: str

class CallSubordinate(ToolCall):
    """Delegate a task to a specialized agent."""
    agent_name: str
    task: str

class WebSearch(ToolCall):
    """Search the web using DuckDuckGo."""
    query: str
    max_results: int = 5

class WebRead(ToolCall):
    """Extract readable text and metadata from a URL."""
    url: str

class Browser(ToolCall):
    """Interact with a web browser (navigate, click, type)."""
    action: Literal["navigate", "click", "type", "screenshot", "scroll"]
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None

class Email(ToolCall):
    """Search or send emails."""
    action: Literal["search", "send", "read"]
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    query: Optional[str] = None

class WorkPlan(ToolCall):
    """Manage agentic work plans and items."""
    action: Literal["create_plan", "add_item", "update_item", "complete_plan", "get_plan"]
    plan_id: Optional[str] = None
    item_id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    parent_plan_id: Optional[str] = None

class ToolInvocation(BaseModel):
    """Execution details for a single tool call."""
    tool_name: str = Field(..., description="The name of the tool to call.")
    arguments: Dict[str, Any] = Field(..., description="Arguments for the tool (matches its schema).")
    model_override: Optional[str] = Field(None, description="Optional cheaper model to use for this execution.")
    description: str = Field(..., description="Purpose of this specific call.")

class ParallelBatch(BaseModel):
    """A collection of tool calls to be executed simultaneously."""
    batch_name: str
    calls: List[ToolInvocation] = Field(..., min_length=1)

class ParallelWorkPlan(BaseModel):
    """An orchestration plan containing one or more batches of parallel tool calls."""
    reasoning: str = Field(..., description="Architect's reasoning for this decomposition.")
    batches: List[ParallelBatch] = Field(..., description="Sequential list of batches. Batch 2 runs only after Batch 1 finishes.")

class ParallelOrchestrate(ToolCall):
    """Execute multiple tool calls in parallel batches using a High-Power Architect / Low-Power Worker pattern."""
    plan: ParallelWorkPlan

# Mapping for Instructor
INSTRUCTOR_TOOL_MAP = {
    "respond": Respond,
    "search_memory": SearchMemory,
    "memory_save": MemorySave,
    "memory_update": MemoryUpdate,
    "memory_delete": MemoryDelete,
    "code_execute": CodeExecute,
    "file_read": FileRead,
    "file_write": FileWrite,
    "file_edit": FileEdit,
    "call_subordinate": CallSubordinate,
    "web_search": WebSearch,
    "web_read": WebRead,
    "browser": Browser,
    "email": Email,
    "work_plan": WorkPlan,
    "parallel_orchestrate": ParallelOrchestrate,
}

def get_pydantic_definitions(enabled_tools: List[str]) -> List[Type[BaseModel]]:
    # 1. Get static native tool models
    models: List[Type[BaseModel]] = [INSTRUCTOR_TOOL_MAP[name] for name in enabled_tools if name in INSTRUCTOR_TOOL_MAP]
    
    # 2. Add dynamic MCP tool models
    try:
        from backend.app.mcp import mcp_manager
        mcp_models = mcp_manager.get_pydantic_models(enabled_tools)
        models.extend(mcp_models)
    except (ImportError, Exception):
        pass
        
    return models
