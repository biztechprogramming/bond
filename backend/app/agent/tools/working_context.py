"""Working context tool for maintaining a transient scratchpad of snippets."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bond.agent.tools.working_context")

async def handle_working_context(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Manage the working context (scratchpad) for the current task."""
    action = arguments.get("action")
    key = arguments.get("key")
    content = arguments.get("content")
    
    # In-memory storage attached to the agent's context for the duration of the session
    if "working_context" not in context:
        context["working_context"] = {}
    
    storage = context["working_context"]
    
    if action == "add":
        if not key or not content:
            return {"error": "key and content are required for 'add'"}
        storage[key] = content
        return {"status": "added", "key": key}
    
    elif action == "remove":
        if not key:
            return {"error": "key is required for 'remove'"}
        if key in storage:
            del storage[key]
            return {"status": "removed", "key": key}
        return {"error": f"key '{key}' not found"}
    
    elif action == "list":
        return {"keys": list(storage.keys()), "context": storage}
    
    elif action == "clear":
        storage.clear()
        return {"status": "cleared"}
    
    else:
        return {"error": f"Unknown action: {action}"}
