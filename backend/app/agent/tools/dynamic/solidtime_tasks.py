"""SolidTime tasks — list and create tasks."""
from __future__ import annotations

SCHEMA = {
    "name": "solidtime_tasks",
    "description": "List or create SolidTime tasks. Use action='list' to see tasks (optionally filtered by project), or action='create' to make a new one.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create"],
                "description": "Action to perform"
            },
            "project_id": {
                "type": "string",
                "description": "Filter by project UUID (for list) or assign to project (required for create)"
            },
            "name": {
                "type": "string",
                "description": "Task name (required for create)"
            }
        },
        "required": ["action"]
    }
}


def execute(action: str, project_id: str = None, name: str = None) -> dict:
    from ._solidtime_config import load_solidtime_config, solidtime_request
    config = load_solidtime_config()

    if action == "list":
        params = {}
        if project_id:
            params["project_id"] = project_id
        return solidtime_request("GET", "/tasks", config, params=params)

    elif action == "create":
        if not name:
            return {"error": "name is required for creating a task"}
        if not project_id:
            return {"error": "project_id is required for creating a task"}
        return solidtime_request("POST", "/tasks", config, json={"name": name, "project_id": project_id})

    return {"error": f"Unknown action: {action}"}
