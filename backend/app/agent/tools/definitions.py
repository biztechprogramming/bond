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
            "description": "Execute code in a sandboxed environment. Supports Python and shell scripts. PREFER shell_find, shell_grep, shell_ls, git_info, shell_head, shell_wc, or shell_tree for read-only operations — they are cheaper and faster. Use code_execute only for mutations (install, build, test) or multi-step scripts.",
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
            "description": "Read a file from the workspace. Good for small/medium files (<500 lines). For large files, use file_open instead — it loads the file server-side and lets you view windows, search, and edit without filling context. Supports line ranges (line_start/line_end) and outline mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (relative to workspace root).",
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "First line to read (1-indexed). Use for head: line_start=1, line_end=N. Use for mid-file ranges.",
                    },
                    "line_end": {
                        "type": "integer",
                        "description": "Last line to read (inclusive). Omit to read to end of file.",
                    },
                    "outline": {
                        "type": "boolean",
                        "description": "If true, return function/class signatures with line numbers instead of full content. Use on first read of unfamiliar files.",
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
    # --- Server-side file buffer tools ---
    # These hold files in Python memory (not LLM context) and let the
    # agent view windows, search, and edit large files efficiently.
    {
        "type": "function",
        "function": {
            "name": "file_open",
            "description": (
                "Open a file into a server-side buffer for efficient large-file operations. "
                "The file is held in Python memory — NOT loaded into your context. "
                "Returns a summary and first N lines. Use file_view to see other sections, "
                "file_search to find patterns, file_replace to edit. "
                "Best for files >500 lines. Up to 10 files can be open simultaneously."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to open.",
                    },
                    "preview_lines": {
                        "type": "integer",
                        "description": "Number of lines to preview from the start (default: 100, max: 300).",
                        "default": 100,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_view",
            "description": (
                "View a window of lines from a buffered file. Auto-opens the file if not already open. "
                "Returns numbered lines for easy reference. Max window: 300 lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to show (1-indexed, default: 1).",
                        "default": 1,
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to show (inclusive). Default: start_line + 99.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": (
                "Search for a regex pattern in a buffered file. Auto-opens if needed. "
                "Returns matching line numbers and text. Use to find the exact lines "
                "before calling file_replace or file_view."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for (case-insensitive).",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines around each match (default: 2, max: 10).",
                        "default": 2,
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "Maximum matches to return (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_replace",
            "description": (
                "Replace a range of lines in a buffered file. Writes to disk immediately. "
                "Use file_search or file_view first to confirm the exact line range. "
                "Returns the old content that was replaced for verification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to replace (1-indexed, inclusive).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to replace (1-indexed, inclusive).",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New content to insert (replaces the specified line range).",
                    },
                },
                "required": ["path", "start_line", "end_line", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_smart_edit",
            "description": (
                "Compound search + edit in ONE call. Finds a section of a file by pattern, "
                "shows it (preview mode) or replaces it (edit mode). Replaces the typical "
                "3-4 call sequence of search → view → edit. "
                "Use 'search' to find the start, 'end_search' to find the end boundary. "
                "Omit new_content to preview, include it to apply the edit. "
                "IMPORTANT: new_content REPLACES the ENTIRE matched section (start through end). "
                "It must include ALL code for that range — unchanged lines too — not just the diff. "
                "Keep search/end_search tight around the lines you actually want to change. "
                "The file is buffered server-side — only the selected section enters context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "search": {
                        "type": "string",
                        "description": "Regex pattern to find the start of the section.",
                    },
                    "end_search": {
                        "type": "string",
                        "description": "Regex pattern for the end of the section (inclusive). Scans forward from the start match. If omitted, uses lines_after.",
                    },
                    "lines_before": {
                        "type": "integer",
                        "description": "Extra lines to include before the start match (default: 0).",
                        "default": 0,
                    },
                    "lines_after": {
                        "type": "integer",
                        "description": "Lines after start match if no end_search (default: 20).",
                        "default": 20,
                    },
                    "occurrence": {
                        "type": "integer",
                        "description": "Which occurrence of the search pattern to use (default: 1 = first).",
                        "default": 1,
                    },
                    "new_content": {
                        "type": "string",
                        "description": "COMPLETE replacement for the matched section. Must include ALL lines for the range — both changed and unchanged. If omitted, returns a preview of the matched section without editing. Always preview first for large sections.",
                    },
                },
                "required": ["path", "search"],
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
    {
        "type": "function",
        "function": {
            "name": "repo_pr",
            "description": "Propose a change to the Bond repo. Creates a feature branch, writes the specified files, commits, pushes, and opens a GitHub PR. Use this when you need to add, fix, or update code. IMPORTANT: each file in 'files' must contain the COMPLETE file content (not a diff) — the file will be overwritten entirely. Read existing files with file_read first if modifying them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name e.g. feat/add-weather-tool",
                    },
                    "title": {
                        "type": "string",
                        "description": "PR title",
                    },
                    "body": {
                        "type": "string",
                        "description": "PR description — what and why",
                    },
                    "files": {
                        "type": "object",
                        "description": "Relative paths to file contents: {path: content}",
                        "additionalProperties": {"type": "string"},
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Git commit message",
                    },
                },
                "required": ["branch", "title", "body", "files", "commit_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_context",
            "description": "Load specialized prompt context for coding/engineering tasks. Pick the most specific relevant category from the manifest. Only call when you need domain-specific guidance (e.g. git workflow, database patterns, security rules). Do NOT call for conversational messages, greetings, or simple questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Dot-separated category path e.g. engineering.git.commits or infrastructure.docker.sandbox",
                    }
                },
                "required": ["category"],
            },
        },
    },
    # ── Shell utility tools ──────────────────────────────────────────────
    # These replace common code_execute patterns with structured, schema-driven
    # tools that qualify for utility model routing (cheaper, faster).
    {
        "type": "function",
        "function": {
            "name": "shell_find",
            "description": "Low-level file finder using glob/regex patterns. DO NOT use this for discovery — use project_search instead, which tries multiple strategies automatically. Only use shell_find when you already know the exact glob pattern (e.g. '*.py', 'test_*.ts'). Case-insensitive. Auto-excludes .venv, node_modules, __pycache__, .git.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default: current directory).",
                        "default": ".",
                    },
                    "name": {
                        "type": "string",
                        "description": "File name glob pattern (case-insensitive). E.g. '*.py', '*027*', 'test_*.ts'.",
                    },
                    "regex": {
                        "type": "string",
                        "description": "POSIX extended regex to match against the full path (case-insensitive). E.g. '.*/0?27.*\\.md$'. Use instead of name for complex patterns.",
                    },
                    "type": {
                        "type": "string",
                        "description": "File type: f=file, d=directory, l=symlink.",
                        "enum": ["f", "d", "l"],
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum directory depth to search.",
                    },
                    "exclude": {
                        "type": "array",
                        "description": "Additional directory names to exclude.",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_ls",
            "description": "List directory contents. Replaces 'ls' commands in code_execute.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list (default: current directory).",
                        "default": ".",
                    },
                    "long": {
                        "type": "boolean",
                        "description": "Show detailed listing with sizes and dates.",
                        "default": False,
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Include hidden files.",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_grep",
            "description": "Search file contents for a specific text/regex pattern. For finding FILES (not content), use project_search instead. Use shell_grep when you need exact pattern matching with line numbers, context lines, or per-file match counts. Auto-excludes .venv, node_modules, __pycache__, .git.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or regex pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search (default: current directory).",
                        "default": ".",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Search recursively in directories.",
                        "default": True,
                    },
                    "include": {
                        "type": "string",
                        "description": "File pattern to include (e.g. '*.py', '*.ts').",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case-insensitive matching.",
                        "default": False,
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines around matches.",
                        "default": 0,
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Max matches per file.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_info",
            "description": "Read-only git operations: status, log, diff, branch, show. Replaces git commands in code_execute. Cannot modify the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Git operation to perform.",
                        "enum": ["status", "log", "diff", "branch", "show"],
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of log entries (for action=log, max 50).",
                        "default": 10,
                    },
                    "format": {
                        "type": "string",
                        "description": "Log format (for action=log).",
                        "enum": ["oneline", "full"],
                        "default": "oneline",
                    },
                    "target": {
                        "type": "string",
                        "description": "Diff target (for action=diff), e.g. 'HEAD~3', 'main', '--staged'.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref to show (for action=show).",
                        "default": "HEAD",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_wc",
            "description": "Count lines, words, or characters in files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to count.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "What to count.",
                        "enum": ["lines", "words", "chars"],
                        "default": "lines",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_head",
            "description": "View the first or last N lines of a file. Use file_read with line_start/line_end for mid-file ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of lines to show.",
                        "default": 20,
                    },
                    "from_end": {
                        "type": "boolean",
                        "description": "If true, show last N lines (tail). If false, show first N lines (head).",
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
            "name": "shell_tree",
            "description": "Show directory tree structure. Auto-excludes .venv, node_modules, __pycache__, .git.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Root directory.",
                        "default": ".",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to display.",
                        "default": 3,
                    },
                    "dirs_only": {
                        "type": "boolean",
                        "description": "Show only directories.",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_search",
            "description": (
                "Smart project search: finds files by searching EVERY word in the query independently "
                "across filenames, directory paths (any depth), and file contents — all in one call. "
                "Returns path, file size, and last-modified date for each match (no content preview). "
                "Use ONLY when you do NOT already have the exact file path. If you have a path, use "
                "file_read or shell_head directly — never search first. "
                "To peek at multiple results, pass their paths to batch_head. Example: "
                "project_search(query='inspection defect entry blazor') finds files matching ANY of "
                "those words in their name, parent directories, or contents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you're looking for. Can be a name, number, topic, or natural language description.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search (default: /workspace).",
                        "default": "/workspace",
                    },
                    "type": {
                        "type": "string",
                        "description": "Filter by type: f=file, d=directory.",
                        "enum": ["f", "d"],
                    },
                    "include": {
                        "type": "string",
                        "description": (
                            "Required file extension filter. Comma-separated glob patterns. "
                            "Examples: '*.cs', '*.html,*.razor', '*.py,*.md,*.yml'. "
                            "Choose extensions relevant to your search to keep results focused."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results per category (default: 30).",
                        "default": 30,
                    },
                },
                "required": ["query", "include"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_head",
            "description": (
                "Peek at the first N lines of multiple files in one call. "
                "Use after project_search to quickly inspect several candidate files "
                "without making one tool call per file. Returns content and total line count for each file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of file paths to read the head of (max 20).",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of lines to read from each file (default: 40, max: 200).",
                        "default": 40,
                    },
                },
                "required": ["files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_tail",
            "description": (
                "Read the last N lines of a file. Complement to shell_head. "
                "Great for log files, build output, recent changes. "
                "Also returns total line count."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of lines from the end (default: 50, max: 500).",
                        "default": 50,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_sed",
            "description": (
                "Extract line ranges or transform text with sed. "
                "Use 'lines' for quick range extraction (e.g. '50,100' for lines 50-100). "
                "Use 'expression' for pattern-based extraction (e.g. '/BEGIN/,/END/p'). "
                "Best tool for reading a specific section of a large file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "lines": {
                        "type": "string",
                        "description": "Line range shorthand, e.g. '50,100' to extract lines 50-100.",
                    },
                    "expression": {
                        "type": "string",
                        "description": "Full sed expression, e.g. '/pattern_start/,/pattern_end/p'.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_diff",
            "description": (
                "Compare two files and return a unified diff. "
                "Shows additions, deletions, and context. "
                "Use to compare versions, check what changed after an edit, or diff configs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file1": {
                        "type": "string",
                        "description": "Path to the first file (original).",
                    },
                    "file2": {
                        "type": "string",
                        "description": "Path to the second file (changed).",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines around changes (default: 3).",
                        "default": 3,
                    },
                },
                "required": ["file1", "file2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_awk",
            "description": (
                "Run an awk program on a file for structured text extraction. "
                "Great for: column extraction ('{print $1, $3}'), "
                "pattern ranges ('/BEGIN/,/END/'), line ranges ('NR>=50 && NR<=100'), "
                "CSV/TSV processing (set separator=','). "
                "More powerful than grep for structured data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file.",
                    },
                    "program": {
                        "type": "string",
                        "description": "The awk program/expression to run.",
                    },
                    "separator": {
                        "type": "string",
                        "description": "Field separator (default: whitespace). Use ',' for CSV, '\\t' for TSV.",
                    },
                },
                "required": ["path", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_jq",
            "description": (
                "Query and transform JSON files with jq. "
                "Use to extract specific keys, filter arrays, reshape data. "
                "Examples: '.dependencies', '.scripts | keys', "
                "'.[] | select(.type == \"bug\")', '{name, version}'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the JSON file.",
                    },
                    "filter": {
                        "type": "string",
                        "description": "jq filter expression (default: '.' for full file).",
                        "default": ".",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coding_agent",
            "description": "Spawn a coding sub-agent to perform complex coding tasks. The sub-agent will have access to the specified working directory and can read/write files, run commands, and commit changes. Use for tasks that require multi-step file exploration, writing code across multiple files, running tests, and iterating. This tool blocks until the sub-agent completes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Detailed description of the coding task. Include: what to build/fix, acceptance criteria, files to focus on, and any constraints.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Absolute path to the project root. The sub-agent will be scoped to this directory.",
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["claude", "codex", "pi"],
                        "description": "Which coding agent to use. Defaults to 'claude' if not specified.",
                        "default": "claude",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Git branch to create/checkout before starting. Optional.",
                    },
                    "timeout_minutes": {
                        "type": "integer",
                        "description": "Maximum time the sub-agent can run. Default: 30.",
                        "default": 30,
                    },
                },
                "required": ["task", "working_directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "host_exec",
            "description": (
                "Execute a command on the host machine via the Permission Broker. "
                "Use this for git operations, build commands, GitHub CLI, and other "
                "host-side tools that require credentials or host filesystem access. "
                "Commands are subject to policy evaluation — some may be denied or "
                "require user approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute on the host.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for execution (host path).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 60).",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
        },
    },
    # ── Deployment agent tools (Design Doc 039) ───────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "deploy_action",
            "description": (
                "Execute a deployment action via the permission broker. "
                "The broker validates promotion status, loads secrets, and executes scripts on the host. "
                "You never see script content, file paths, or secrets — only stdout/stderr results. "
                "Environment is automatically derived from your agent identity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "info",
                            "validate",
                            "dry-run",
                            "pre-hook",
                            "deploy",
                            "post-hook",
                            "health-check",
                            "rollback",
                            "receipt",
                            "status",
                            "lock-status",
                        ],
                        "description": "The deployment action to execute.",
                    },
                    "script_id": {
                        "type": "string",
                        "description": "Script ID (e.g., '001-migrate-user-table'). Required for most actions.",
                    },
                    "version": {
                        "type": "string",
                        "description": "Script version (e.g., 'v1'). Defaults to 'v1'.",
                        "default": "v1",
                    },
                    "environment": {
                        "type": "string",
                        "description": "For 'receipt' action only — which environment's receipt to fetch.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Override script timeout in seconds (capped at environment maximum).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_bug_ticket",
            "description": (
                "Create a detailed GitHub issue for a deployment failure or environment problem. "
                "Include enough context for a developer to reproduce and fix the issue. "
                "The issue will be created in the configured repository with appropriate labels. "
                "Use this when a deployment fails or a health check detects a problem."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Clear, specific issue title describing the problem.",
                    },
                    "environment": {
                        "type": "string",
                        "description": "Which environment this affects (e.g., 'qa', 'staging').",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                        "description": "Impact severity level.",
                    },
                    "script_id": {
                        "type": "string",
                        "description": "Deployment script that failed (if applicable).",
                    },
                    "error_output": {
                        "type": "string",
                        "description": "Relevant stdout/stderr from the failure.",
                    },
                    "code_context": {
                        "type": "string",
                        "description": "Relevant code snippets from the workspace (read-only access).",
                    },
                    "steps_to_reproduce": {
                        "type": "string",
                        "description": "How to reproduce the issue.",
                    },
                    "expected_behavior": {
                        "type": "string",
                        "description": "What should have happened.",
                    },
                    "actual_behavior": {
                        "type": "string",
                        "description": "What actually happened.",
                    },
                    "suggested_fix": {
                        "type": "string",
                        "description": "Agent's analysis and suggested fix (from reading the code).",
                    },
                    "receipt_id": {
                        "type": "string",
                        "description": "Deployment receipt ID for full context.",
                    },
                },
                "required": ["title", "environment", "severity", "actual_behavior"],
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

class RepoPr(ToolCall):
    """Propose a change to the Bond repo via PR."""
    branch: str = Field(description="Branch name e.g. feat/add-weather-tool")
    title: str = Field(description="PR title")
    body: str = Field(description="PR description")
    files: dict[str, str] = Field(description="Relative paths to file contents")
    commit_message: str = Field(description="Git commit message")

class LoadContext(ToolCall):
    """Load prompt context for the current task. Pick the most specific relevant category from the manifest."""
    category: str = Field(description="Dot-separated category path e.g. engineering.git.commits")


# ── Shell utility Pydantic models ─────────────────────────────────────

class ShellFind(ToolCall):
    """Find files by name, pattern, or type."""
    path: str = Field(default=".", description="Directory to search.")
    name: Optional[str] = Field(None, description="File name or glob pattern.")
    type: Optional[Literal["f", "d", "l"]] = Field(None, description="File type.")
    max_depth: Optional[int] = Field(None, description="Max directory depth.")
    exclude: Optional[List[str]] = Field(None, description="Extra dirs to exclude.")

class ShellLs(ToolCall):
    """List directory contents."""
    path: str = Field(default=".", description="Directory to list.")
    long: bool = Field(default=False, description="Detailed listing.")
    all: bool = Field(default=False, description="Include hidden files.")

class ShellGrep(ToolCall):
    """Search for text patterns in files."""
    pattern: str = Field(description="Text or regex pattern.")
    path: str = Field(default=".", description="File or directory to search.")
    recursive: bool = Field(default=True, description="Search recursively.")
    include: Optional[str] = Field(None, description="File pattern filter.")
    ignore_case: bool = Field(default=False, description="Case-insensitive.")
    context_lines: int = Field(default=0, description="Context lines around matches.")
    max_count: Optional[int] = Field(None, description="Max matches per file.")

class GitInfo(ToolCall):
    """Read-only git operations."""
    action: Literal["status", "log", "diff", "branch", "show"]
    count: int = Field(default=10, description="Log entries (max 50).")
    format: Optional[Literal["oneline", "full"]] = Field(default="oneline")
    target: Optional[str] = Field(None, description="Diff target.")
    ref: str = Field(default="HEAD", description="Git ref to show.")

class ShellWc(ToolCall):
    """Count lines, words, or characters."""
    path: str = Field(description="File path.")
    mode: Literal["lines", "words", "chars"] = Field(default="lines")

class ShellHead(ToolCall):
    """View first or last N lines of a file."""
    path: str = Field(description="File path.")
    lines: int = Field(default=20, description="Lines to show.")
    from_end: bool = Field(default=False, description="Tail mode.")

class CodingAgent(ToolCall):
    """Spawn a coding sub-agent to perform complex coding tasks."""
    task: str = Field(description="Detailed description of the coding task.")
    working_directory: str = Field(description="Absolute path to the project root.")
    agent_type: Optional[Literal["claude", "codex", "pi"]] = Field(default="claude", description="Which coding agent to use.")
    branch: Optional[str] = Field(None, description="Git branch to create/checkout.")
    timeout_minutes: int = Field(default=30, description="Max time in minutes.")

class HostExec(ToolCall):
    """Execute a command on the host via the Permission Broker."""
    command: str = Field(description="Shell command to execute on the host.")
    cwd: Optional[str] = Field(None, description="Working directory (host path).")
    timeout: int = Field(default=60, description="Timeout in seconds.")

class ShellTree(ToolCall):
    """Show directory tree structure."""
    path: str = Field(default=".", description="Root directory.")
    max_depth: int = Field(default=3, description="Max depth.")
    dirs_only: bool = Field(default=False, description="Dirs only.")


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
    "repo_pr": RepoPr,
    "load_context": LoadContext,
    "shell_find": ShellFind,
    "shell_ls": ShellLs,
    "shell_grep": ShellGrep,
    "git_info": GitInfo,
    "shell_wc": ShellWc,
    "shell_head": ShellHead,
    "shell_tree": ShellTree,
    "coding_agent": CodingAgent,
    "host_exec": HostExec,
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
